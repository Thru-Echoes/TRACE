"""The session-end shadow extraction: a nearest-50 arm beside the live one (ADR 008 D5).

After the live extraction has finished and released its lock, ``run_shadow_step``
extracts the same session a second time with two differences: the prompt
carries the 50 stored learnings nearest to the session's events instead of the
whole store, and it asks that a learning from a still-proposed decision be
worded as a proposal. Its learnings go to a separate shadow store, seeded once
per project from the live store, whose new ids come from its own ``slrn_``
counter so no live id is ever consumed (INV-12). It then appends one record of
both arms — cost, marks as of extraction, and each new learning's similarity to
the closest existing one — to ``<shadow root>/<key>/extraction.jsonl``.

Nothing the live arm returns or stores changes: the step runs after it, under a
time limit and inside its own exception boundary, and a failure is appended to
the project's ``errors.jsonl``.

Exports:
    NEAREST_N, QUERY_MAX_CHARS, SHADOW_STEP_TIMEOUT_SECONDS
    ShadowStore, LearningAtExtraction, ArmRecord, ExtractionRecord
    load_shadow_store, seed_shadow_store, select_nearest, get_nearest_existing, run_shadow_step

Side effects: ``run_shadow_step`` reads and writes the shadow store, may make
one attested model call and one embedding call, and appends to the shadow
logs. The other functions are pure apart from ``load_shadow_store`` (reads a file).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

from trace_mcp import project_identity as pident
from trace_mcp.extensions.learn import embeddings as emb
from trace_mcp.extensions.learn import extraction, store
from trace_mcp.extensions.learn.egress import egress_project
from trace_mcp.extensions.learn.extraction import (
    ExtractionUsage,
    _add_with_optional_dedup,
    _already_extracted,
    _call_extraction_model,
    _format_events_for_llm,
    _format_learnings,
    _is_imported_record,
    build_extraction_prompt,
    extract_from_session,
)
from trace_mcp.extensions.learn.models import KnowledgeStore, Learning
from trace_mcp.extensions.learn.shadow.state import (
    _make_private_dirs,
    append_record,
    get_project_dir,
    get_store_dir,
    record_error,
)
from trace_mcp.extensions.learn.shadow.words import load_words
from trace_mcp.provenance_marks import SOURCE_UNKNOWN_TAG, get_event_marks
from trace_mcp.schema import Session

if TYPE_CHECKING:
    from trace_mcp.extensions.learn.config import LearnConfig
    from trace_mcp.extensions.learn.embeddings import EmbeddingProvider

NEAREST_N = 50
QUERY_MAX_CHARS = 8000
SHADOW_STEP_TIMEOUT_SECONDS = 90.0

_SHADOW_ID_PREFIX = "slrn_"
Selection = Literal["all", "embedding", "recent"]


class ShadowStore(KnowledgeStore):
    """A knowledge store for the shadow arm, with its own id counter in the ``slrn_`` namespace.

    Seeded entries keep their live ids (they are the same learnings); entries
    the shadow arm creates are numbered from ``shadow_next_id``. The inherited
    live counter ``next_id`` is never used to mint.
    """

    shadow_next_id: int = Field(default=1, description="Next number for an slrn_ id; never decreased.")
    seeded_at: datetime | None = Field(default=None, description="When the store was copied from the live store.")

    @model_validator(mode="after")
    def _raise_shadow_counter_above_existing_ids(self) -> ShadowStore:
        """Raise ``shadow_next_id`` past every existing ``slrn_`` id; never lower it."""
        highest = 0
        for lrn in self.learnings:
            if lrn.id.startswith(_SHADOW_ID_PREFIX):
                try:
                    number = int(lrn.id[len(_SHADOW_ID_PREFIX) :])
                except ValueError:
                    continue
                if number > highest:
                    highest = number
        if self.shadow_next_id <= highest:
            self.shadow_next_id = highest + 1
        return self

    def next_learning_id(self) -> str:
        """Return the next shadow id (``slrn_001``, ``slrn_002``, …) and advance ``shadow_next_id``."""
        learning_id = f"{_SHADOW_ID_PREFIX}{self.shadow_next_id:03d}"
        self.shadow_next_id += 1
        return learning_id


class LearningAtExtraction(BaseModel):
    """One new learning of one arm, as it stood when it was extracted."""

    id: str = Field(..., description="Learning id (lrn_ for the live arm, slrn_ for the shadow arm).")
    source_event: str | None = Field(default=None, description="The event the learning names as its source.")
    category: str = Field(..., description="The learning's category.")
    tags: list[str] = Field(default_factory=list, description="Provenance-mark tags of the source event, then.")
    nearest_existing_id: str | None = Field(default=None, description="Closest existing learning by stored vector.")
    nearest_existing_cosine: float | None = Field(default=None, description="Its cosine similarity, 4 decimals.")


class ArmRecord(BaseModel):
    """What one arm did at one session end."""

    path: Literal["llm", "rule-based", "skipped"] = Field(..., description="Which extractor ran, or skipped.")
    prompt_tokens: int | None = Field(default=None, description="Provider-reported prompt tokens.")
    completion_tokens: int | None = Field(default=None, description="Provider-reported completion tokens.")
    seconds: float | None = Field(default=None, description="Wall-clock seconds of the model request.")
    existing_chars: int | None = Field(default=None, description="Length of the existing-learnings block sent.")
    learnings: list[LearningAtExtraction] = Field(default_factory=list, description="The arm's new learnings.")


class ExtractionRecord(BaseModel):
    """One line of ``extraction.jsonl``: both arms at one session end."""

    ts: datetime = Field(..., description="UTC time the record was written.")
    project_key: str = Field(..., description="Canonical project key.")
    session_id: str = Field(..., description="The TRACE session extracted.")
    live: ArmRecord = Field(..., description="The live arm.")
    shadow: ArmRecord = Field(..., description="The shadow arm.")
    nearest_n: int = Field(..., description="How many learnings the shadow prompt may carry.")
    store_size: int = Field(..., description="Learnings in the shadow store before this extraction.")
    selection: Selection = Field(..., description="How the shadow prompt's learnings were chosen.")
    query_chars: int = Field(..., description="Length of the text the selection ranked against.")
    query_truncated: bool = Field(..., description="Whether that text was cut to its last QUERY_MAX_CHARS.")
    seeded_now: bool = Field(..., description="Whether the shadow store was seeded by this run.")


def load_shadow_store(project: str) -> ShadowStore | None:
    """Read a project's shadow store; ``None`` when there is none yet.

    Raises:
        store.StoreLoadError: the file exists but cannot be parsed or validated
            (it is then never replaced by a fresh seed).
    """
    path = store._store_path(project, str(get_store_dir()))
    if not path.exists():
        return None
    try:
        shadow = ShadowStore.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        raise store.StoreLoadError(f"Unreadable shadow store {path}: {exc}") from exc
    store._assert_store_identity(shadow, project, path)
    return shadow


def seed_shadow_store(live: KnowledgeStore, *, exclude_ids: Collection[str]) -> ShadowStore:
    """Return a new shadow store holding deep copies of the live learnings not in ``exclude_ids``.

    Pure: ``live`` is not modified. ``exclude_ids`` are this session's fresh live
    learnings, which would otherwise make the shadow arm deduplicate against its
    own answer.
    """
    excluded = set(exclude_ids)
    return ShadowStore(
        project=live.project,
        learnings=[lrn.model_copy(deep=True) for lrn in live.learnings if lrn.id not in excluded],
        seeded_at=datetime.now(UTC),
    )


def _created_key(lrn: Learning) -> float:
    return lrn.created.timestamp()


def _most_recent(indexed: Sequence[tuple[int, Learning]], n: int) -> list[int]:
    return [i for i, _ in sorted(indexed, key=lambda pair: _created_key(pair[1]), reverse=True)[:n]]


async def select_nearest(
    learnings: Sequence[Learning], query_text: str, provider: EmbeddingProvider | None, *, n: int = NEAREST_N
) -> tuple[list[Learning], Selection]:
    """Choose the learnings the shadow prompt carries, returned in store order.

    All of them when there are ``n`` or fewer; else, with no embedding backend,
    the ``n`` most recently created; else the ``n`` nearest to the query among
    learnings with a vector from the same model, filled from the most recent of
    the rest when fewer than ``n`` have one.

    Side effects: one ``embed_query`` call on ``provider`` in the embedding case.
    """
    items = list(learnings)
    if len(items) <= n:
        return items, "all"
    indexed = list(enumerate(items))
    if provider is None or not emb._HAS_NUMPY:
        return [items[i] for i in sorted(_most_recent(indexed, n))], "recent"
    import numpy as np

    query = await emb.embed_query(provider, query_text)
    usable = [
        (i, lrn)
        for i, lrn in indexed
        if lrn.embedding and lrn.embedding_model == provider.model_name and len(lrn.embedding) == len(query)
    ]
    chosen: list[int] = []
    if usable:
        sims = emb.cosine_similarity_matrix(query, np.asarray([lrn.embedding for _, lrn in usable], dtype=np.float32))
        ranked = sorted(range(len(usable)), key=lambda k: (-float(sims[k]), usable[k][0]))
        chosen = [usable[k][0] for k in ranked[:n]]
    if len(chosen) < n:
        taken = set(chosen)
        chosen += _most_recent([(i, lrn) for i, lrn in indexed if i not in taken], n - len(chosen))
    return [items[i] for i in sorted(chosen)], "embedding"


def get_nearest_existing(new: Learning, existing: Sequence[Learning]) -> tuple[str | None, float | None]:
    """Return the id and cosine (4 decimals) of the existing learning closest to ``new``, by stored vectors.

    ``(None, None)`` when ``new`` has no usable vector or no existing learning
    has a non-zero vector from the same model with the same length. A
    measurement only: nothing is removed or changed, and no model is called.
    """
    vec = new.embedding
    if not vec or not any(vec) or not emb._HAS_NUMPY:
        return None, None
    candidates = [
        lrn
        for lrn in existing
        if lrn.embedding
        and lrn.embedding_model == new.embedding_model
        and len(lrn.embedding) == len(vec)
        and any(lrn.embedding)
    ]
    if not candidates:
        return None, None
    import numpy as np

    sims = emb.cosine_similarity_matrix(vec, np.asarray([lrn.embedding for lrn in candidates], dtype=np.float32))
    best = int(np.argmax(sims))
    return candidates[best].id, round(float(sims[best]), 4)


def _project_key(project: str) -> str:
    try:
        return pident.key_for_label(project) or pident.canonical_project_key(project)
    except Exception:
        return pident.canonical_project_key(project)


def _arm(
    usage: ExtractionUsage | None,
    learnings: Sequence[Learning],
    existing: Sequence[Learning],
    session: Session,
    words: Sequence,
) -> ArmRecord:
    events = {e.id: e for e in session.events if e.id}
    entries: list[LearningAtExtraction] = []
    for lrn in learnings:
        event = events.get(lrn.source_event) if lrn.source_event else None
        if event is None:
            tags = [SOURCE_UNKNOWN_TAG]
        else:
            tags = get_event_marks(event, words, session_created=session.created).tags()
        nearest_id, cosine = get_nearest_existing(lrn, existing)
        entries.append(
            LearningAtExtraction(
                id=lrn.id,
                source_event=lrn.source_event,
                category=lrn.category,
                tags=tags,
                nearest_existing_id=nearest_id,
                nearest_existing_cosine=cosine,
            )
        )
    if usage is None:
        return ArmRecord(path="skipped", learnings=entries)
    return ArmRecord(
        path=usage.path,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        seconds=usage.seconds,
        existing_chars=usage.existing_chars,
        learnings=entries,
    )


async def _run(
    *,
    key: str,
    project: str,
    session: Session,
    config: LearnConfig,
    provider: EmbeddingProvider | None,
    live_store: KnowledgeStore,
    live_new_ids: list[str],
    live_usage: ExtractionUsage | None,
) -> None:
    if _is_imported_record(session):
        return
    store_dir = get_store_dir()
    _make_private_dirs(store_dir)

    # 1. Under the shadow lock: load or seed, and snapshot.
    with store.project_lock(project, directory=str(store_dir)):
        shadow = load_shadow_store(project)
        seeded_now = shadow is None
        if shadow is None:
            shadow = seed_shadow_store(live_store, exclude_ids=live_new_ids)
            store.save_store(shadow, directory=str(store_dir))
        snapshot = list(shadow.learnings)

    # 2. No lock held: select, prompt, call the model, embed the results.
    events_text = _format_events_for_llm(session)
    query_text = events_text[-QUERY_MAX_CHARS:]
    nearest, selection = await select_nearest(snapshot, query_text, provider)
    existing_text = _format_learnings(nearest)
    dedup_threshold = config.dedup_threshold if config.dedup_enabled else None
    use_model = bool(config.llm_enabled and config.openai_api_key and extraction._HAS_OPENAI)

    items: list[dict] = []
    vectors: list[list[float]] | None = None
    shadow_usage: ExtractionUsage | None = None
    if not events_text.strip():
        mode = "skipped"
    elif use_model:
        mode = "llm"
        prompt = build_extraction_prompt(session, existing_text, proposal_wording=True)
        items, shadow_usage = await _call_extraction_model(
            config,
            prompt,
            session=session,
            purpose="shadow-extraction",
            content_class="session-events+nearest-learnings",
            existing_chars=len(existing_text),
        )
        items = [item for item in items if isinstance(item, dict)]
        if provider is not None and items:
            vectors = await provider.embed_texts([str(item.get("content", "")) for item in items])
    else:
        mode = "rule-based"
        shadow_usage = ExtractionUsage(path="rule-based")

    # 3. Under the shadow lock again: reload, add, save.
    new_ids: list[str] = []
    with store.project_lock(project, directory=str(store_dir)):
        current = load_shadow_store(project)
        if current is None:
            raise store.StoreLoadError("the shadow store disappeared between the seed and the write")
        if mode == "llm":
            for index, item in enumerate(items):
                source_event = item.get("source_event", "")
                if source_event and _already_extracted(current, session.id, source_event):
                    continue
                lrn = _add_with_optional_dedup(
                    current,
                    content=item.get("content", ""),
                    category=item.get("category", "learning"),
                    source_session=session.id,
                    source_event=source_event or None,
                    corrects_event_ids=item.get("corrects_event_ids"),
                    tags=item.get("tags", []),
                    dedup_threshold=dedup_threshold,
                    extraction_method="llm",
                    generated_by=config.llm_extraction_model,
                )
                if lrn is None:
                    continue
                new_ids.append(lrn.id)
                if vectors is not None and provider is not None and index < len(vectors):
                    lrn.embedding = vectors[index]
                    lrn.embedding_model = provider.model_name
        elif mode == "rule-based":
            new_ids = extract_from_session(current, session, dedup_threshold=dedup_threshold)
        if new_ids:
            store.save_store(current, directory=str(store_dir))
        made = {lrn.id: lrn for lrn in current.learnings if lrn.id in set(new_ids)}

    # 4. Marks as of extraction and similarity to the closest existing learning, for both arms.
    words = load_words(key)
    live_new = set(live_new_ids)
    live_by_id = {lrn.id: lrn for lrn in live_store.learnings}
    live_learnings = [live_by_id[i] for i in live_new_ids if i in live_by_id]
    live_existing = [lrn for lrn in live_store.learnings if lrn.id not in live_new]
    record = ExtractionRecord(
        ts=datetime.now(UTC),
        project_key=key,
        session_id=session.id,
        live=_arm(live_usage, live_learnings, live_existing, session, words),
        shadow=_arm(shadow_usage, [made[i] for i in new_ids if i in made], snapshot, session, words),
        nearest_n=NEAREST_N,
        store_size=len(snapshot),
        selection=selection,
        query_chars=len(query_text),
        query_truncated=len(events_text) > QUERY_MAX_CHARS,
        seeded_now=seeded_now,
    )
    append_record(get_project_dir(key) / "extraction.jsonl", record)


async def run_shadow_step(
    *,
    project: str,
    session: Session,
    config: LearnConfig,
    provider: EmbeddingProvider | None,
    live_store: KnowledgeStore,
    live_new_ids: list[str],
    live_usage: ExtractionUsage | None,
) -> None:
    """Run the shadow extraction for one session end. Never raises.

    Call it only after the live extraction has finished and released its lock.
    The whole step runs under ``SHADOW_STEP_TIMEOUT_SECONDS``; any failure,
    including the time limit or a held shadow lock, is appended to the
    project's ``errors.jsonl`` with step ``extraction``.

    Args:
        project: the project label the live extraction used.
        session: the session just extracted.
        config: the effective learn config the live extraction used.
        provider: the project's embedding provider, or None.
        live_store: the live store as saved after the live extraction (read only).
        live_new_ids: the ids the live extraction added.
        live_usage: what the live extraction cost, or None when it reported nothing.

    Side effects: see the module docstring.
    """
    key = ""
    try:
        key = _project_key(project)
        # Every egress the step attests (the selection's query embedding, the
        # model call, the results' embeddings) is attributed to this project.
        with egress_project(key or None):
            await asyncio.wait_for(
                _run(
                    key=key,
                    project=project,
                    session=session,
                    config=config,
                    provider=provider,
                    live_store=live_store,
                    live_new_ids=live_new_ids,
                    live_usage=live_usage,
                ),
                SHADOW_STEP_TIMEOUT_SECONDS,
            )
    except Exception as exc:
        record_error(key or "unknown", "extraction", exc)
