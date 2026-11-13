"""Offline replay: whole-store against nearest-50 extraction on past sessions (ADR 008 D5).

Extraction sends every stored learning with a session's events so the model can
avoid restating them. Whether sending only the 50 nearest would do as well is a
question about past sessions, so it is answered off-session and on demand, not
by a second model call at every session end. ``replay_session`` extracts one
ended session twice, on the same frozen events and the same snapshot of the
stored learnings as they stood before that session was extracted:

- the **whole** arm sends every learning of the snapshot (the method in use);
- the **nearest** arm sends the ``NEAREST_N`` learnings nearest to the session's
  events (``select_nearest``).

Both arms build their prompt with the extractor's own prompt builder, call the
model through its one attested call site under their own purposes, and apply the
extractor's code-side duplicate check against the whole snapshot. Neither arm
writes to a knowledge store: what each would have added is returned in a
``ReplayRecord`` (cost, each new learning with its text, its marks as of the
session's end, and its similarity to the closest stored learning).
``run_replay`` appends those records to ``<shadow root>/<key>/replay.jsonl``.

The snapshot is the live store as it is now, less the learnings that came from
the session and those created at or after its end. A learning forgotten since
then is not restored, so the snapshot is a close reconstruction, not an archive.

Exports:
    NEAREST_N, QUERY_MAX_CHARS, ARMS
    LearningAtExtraction, ArmRecord, ReplayRecord, ReplaySummary, ReplayUnavailableError
    select_nearest, get_nearest_existing, get_store_snapshot, load_sessions,
    replay_session, run_replay

Side effects: ``replay_session`` makes two attested model calls and up to three
embedding calls and reads the project's words files; it writes nothing.
``run_replay`` reads the session records and the live store and appends
records, and failures, under the shadow root.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, ValidationError

from trace_mcp import project_identity as pident
from trace_mcp.extensions.learn import embeddings as emb
from trace_mcp.extensions.learn import extraction, store
from trace_mcp.extensions.learn.config import effective_learn_config, load_config
from trace_mcp.extensions.learn.egress import egress_project
from trace_mcp.extensions.learn.embeddings import get_embedding_provider
from trace_mcp.extensions.learn.extraction import (
    _add_with_optional_dedup,
    _already_extracted,
    _call_extraction_model,
    _format_events_for_llm,
    _format_learnings,
    _is_imported_record,
    build_extraction_prompt,
    get_known_source_event,
)
from trace_mcp.extensions.learn.models import KnowledgeStore, Learning
from trace_mcp.extensions.learn.shadow.state import append_record, get_project_dir, get_project_key, record_error
from trace_mcp.extensions.learn.shadow.words import load_words
from trace_mcp.provenance_marks import SOURCE_UNKNOWN_TAG, WordsEntry, get_event_marks
from trace_mcp.schema import Session
from trace_mcp.storage.json_file import JsonFileStorage

if TYPE_CHECKING:
    from trace_mcp.extensions.learn.config import LearnConfig
    from trace_mcp.extensions.learn.embeddings import EmbeddingProvider

NEAREST_N = 50
QUERY_MAX_CHARS = 8000
ARMS = ("whole", "nearest")

Selection = Literal["all", "embedding", "recent"]


class ReplayUnavailableError(Exception):
    """Replay compares two model calls, and cloud extraction is off or has no key for this project."""


class LearningAtExtraction(BaseModel):
    """One learning an arm would have added, as it stood when the session ended."""

    id: str = Field(..., description="Arm and ordinal (whole-1, nearest-2); never a stored learning's id.")
    content: str = Field(default="", description="The learning's text.")
    source_event: str | None = Field(default=None, description="The event the learning names as its source.")
    category: str = Field(..., description="The learning's category.")
    tags: list[str] = Field(default_factory=list, description="Provenance-mark tags of the source event, then.")
    nearest_existing_id: str | None = Field(default=None, description="Closest stored learning by vector.")
    nearest_existing_cosine: float | None = Field(default=None, description="Its cosine similarity, 4 decimals.")


class ArmRecord(BaseModel):
    """What one arm did for one replayed session."""

    path: Literal["llm"] = Field(default="llm", description="Which extractor ran.")
    prompt_tokens: int | None = Field(default=None, description="Provider-reported prompt tokens.")
    completion_tokens: int | None = Field(default=None, description="Provider-reported completion tokens.")
    seconds: float | None = Field(default=None, description="Wall-clock seconds of the model request.")
    existing_chars: int | None = Field(default=None, description="Length of the existing-learnings block sent.")
    learnings: list[LearningAtExtraction] = Field(default_factory=list, description="What the arm would have added.")


class ReplayRecord(BaseModel):
    """One line of ``replay.jsonl``: both arms on one past session."""

    ts: datetime = Field(..., description="UTC time the replay ran.")
    project_key: str = Field(..., description="Canonical project key.")
    session_id: str = Field(..., description="The TRACE session replayed.")
    whole: ArmRecord = Field(..., description="The arm that sends every stored learning.")
    nearest: ArmRecord = Field(..., description="The arm that sends the nearest NEAREST_N.")
    nearest_n: int = Field(..., description="How many learnings the nearest arm's prompt may carry.")
    store_size: int = Field(..., description="Learnings in the snapshot both arms ran against.")
    selection: Selection = Field(..., description="How the nearest arm's learnings were chosen.")
    query_chars: int = Field(..., description="Length of the text the selection ranked against.")
    query_truncated: bool = Field(..., description="Whether that text was cut to its last QUERY_MAX_CHARS.")


class ReplaySummary(BaseModel):
    """What one ``run_replay`` call did."""

    project_key: str = Field(..., description="Canonical project key.")
    candidates: int = Field(default=0, description="Ended sessions of the project in range.")
    already_done: int = Field(default=0, description="Of those, sessions replayed by an earlier run.")
    replayed: int = Field(default=0, description="Sessions replayed by this run.")
    skipped: int = Field(default=0, description="Sessions with nothing to extract (imported, or no usable event).")
    failed: int = Field(default=0, description="Sessions whose replay raised; see errors.jsonl.")
    records: list[ReplayRecord] = Field(default_factory=list, description="This run's records, in the order run.")


def _created_key(lrn: Learning) -> float:
    return lrn.created.timestamp()


def _most_recent(indexed: Sequence[tuple[int, Learning]], n: int) -> list[int]:
    return [i for i, _ in sorted(indexed, key=lambda pair: _created_key(pair[1]), reverse=True)[:n]]


async def select_nearest(
    learnings: Sequence[Learning], query_text: str, provider: EmbeddingProvider | None, *, n: int = NEAREST_N
) -> tuple[list[Learning], Selection]:
    """Choose the learnings the nearest arm's prompt carries, returned in store order.

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


def _utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def get_store_snapshot(live_store: KnowledgeStore, session: Session) -> list[Learning]:
    """Return the stored learnings as they stood before ``session`` was extracted, in store order.

    The learnings that did not come from the session and were created before it
    ended. Pure: the store is not modified.
    """
    ended = _utc(session.ended) if session.ended is not None else None
    return [
        lrn
        for lrn in live_store.learnings
        if lrn.source_session != session.id and (ended is None or _utc(lrn.created) < ended)
    ]


def load_sessions() -> tuple[list[Session], int]:
    """Return every readable session record, and how many files could not be read."""
    directory = Path(JsonFileStorage().location())
    sessions: list[Session] = []
    unreadable = 0
    for path in sorted(directory.glob("trace_*.json")) if directory.is_dir() else []:
        try:
            sessions.append(Session.model_validate_json(path.read_bytes()))
        except Exception:
            unreadable += 1
    return sessions, unreadable


def _can_call_model(config: LearnConfig) -> bool:
    return bool(config.llm_enabled and config.openai_api_key and extraction._HAS_OPENAI)


async def _run_arm(
    name: str,
    existing: Sequence[Learning],
    *,
    snapshot: Sequence[Learning],
    project: str,
    next_id: int,
    session: Session,
    config: LearnConfig,
    provider: EmbeddingProvider | None,
    words: Sequence[WordsEntry],
) -> ArmRecord:
    existing_text = _format_learnings(existing)
    items, usage = await _call_extraction_model(
        config,
        build_extraction_prompt(session, existing_text),
        session=session,
        purpose=f"replay-extraction-{name}",
        content_class="session-events+existing-learnings" if name == "whole" else "session-events+nearest-learnings",
        existing_chars=len(existing_text),
    )
    # The extractor's own add path, on a scratch copy that is never saved: one learning per
    # source event, and the token-overlap duplicate check against the whole snapshot.
    scratch = KnowledgeStore(
        project=project, learnings=[lrn.model_copy(deep=True) for lrn in snapshot], next_id=next_id
    )
    dedup_threshold = config.dedup_threshold if config.dedup_enabled else None
    made: list[Learning] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        source_event = get_known_source_event(session, item.get("source_event"))
        if source_event and _already_extracted(scratch, session.id, source_event):
            continue
        lrn = _add_with_optional_dedup(
            scratch,
            content=item.get("content", ""),
            category=item.get("category", "learning"),
            source_session=session.id,
            source_event=source_event,
            corrects_event_ids=item.get("corrects_event_ids"),
            tags=item.get("tags", []),
            dedup_threshold=dedup_threshold,
            extraction_method="llm",
            generated_by=config.llm_extraction_model,
        )
        if lrn is not None:
            made.append(lrn)
    if provider is not None and made:
        vectors = await provider.embed_texts([lrn.content for lrn in made])
        for lrn, vector in zip(made, vectors, strict=False):
            lrn.embedding = vector
            lrn.embedding_model = provider.model_name
    events = {e.id: e for e in session.events if e.id}
    entries: list[LearningAtExtraction] = []
    for number, lrn in enumerate(made, 1):
        event = events.get(lrn.source_event) if lrn.source_event else None
        if event is None:
            tags = [SOURCE_UNKNOWN_TAG]
        else:
            tags = get_event_marks(event, words, session_created=session.created, as_of=session.ended).tags()
        nearest_id, cosine = get_nearest_existing(lrn, snapshot)
        entries.append(
            LearningAtExtraction(
                id=f"{name}-{number}",
                content=lrn.content,
                source_event=lrn.source_event,
                category=lrn.category,
                tags=tags,
                nearest_existing_id=nearest_id,
                nearest_existing_cosine=cosine,
            )
        )
    return ArmRecord(
        path="llm",
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        seconds=usage.seconds,
        existing_chars=usage.existing_chars,
        learnings=entries,
    )


async def replay_session(
    *,
    project: str,
    session: Session,
    config: LearnConfig,
    provider: EmbeddingProvider | None,
    live_store: KnowledgeStore,
) -> ReplayRecord | None:
    """Extract one past session with both arms and return what each would have added; ``None`` if nothing to extract.

    ``None`` for an imported record or a session with no usable event. Nothing
    is written: ``live_store`` is read, never modified or saved.

    Raises:
        ReplayUnavailableError: cloud extraction is off or has no key for the project.
        Exception: attestation, a model request or parsing a response failed.

    Side effects: two attested model calls, up to three embedding calls, and a
    read of the project's words files.
    """
    if not _can_call_model(config):
        raise ReplayUnavailableError(
            "replay compares two model calls, and cloud extraction is off or has no key for this project"
        )
    if _is_imported_record(session):
        return None
    events_text = _format_events_for_llm(session)
    if not events_text.strip():
        return None
    key = get_project_key(project)
    snapshot = get_store_snapshot(live_store, session)
    query_text = events_text[-QUERY_MAX_CHARS:]
    arms: dict[str, ArmRecord] = {}
    # Every egress the replay attests (the selection's query embedding, both model
    # calls, the results' embeddings) is attributed to this project.
    with egress_project(key or None):
        nearest, selection = await select_nearest(snapshot, query_text, provider)
        words = load_words(key)
        for name, existing in (("whole", snapshot), ("nearest", nearest)):
            arms[name] = await _run_arm(
                name,
                existing,
                snapshot=snapshot,
                project=live_store.project,
                next_id=live_store.next_id,
                session=session,
                config=config,
                provider=provider,
                words=words,
            )
    return ReplayRecord(
        ts=datetime.now(UTC),
        project_key=key,
        session_id=session.id,
        whole=arms["whole"],
        nearest=arms["nearest"],
        nearest_n=NEAREST_N,
        store_size=len(snapshot),
        selection=selection,
        query_chars=len(query_text),
        query_truncated=len(events_text) > QUERY_MAX_CHARS,
    )


def _replayed_ids(key: str) -> set[str]:
    try:
        lines = (get_project_dir(key) / "replay.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return set()
    done: set[str] = set()
    for line in lines:
        try:
            done.add(ReplayRecord.model_validate_json(line).session_id)
        except ValidationError:
            continue
    return done


async def run_replay(
    *,
    project: str,
    limit: int = 10,
    date_from: date | None = None,
    date_to: date | None = None,
    session_ids: Sequence[str] | None = None,
) -> ReplaySummary:
    """Replay the project's most recently ended sessions that have no record yet, newest first, up to ``limit``.

    ``date_from`` and ``date_to`` bound the day (UTC) a session ended;
    ``session_ids`` restricts the run to those sessions. A session whose replay
    raises is counted, appended to the project's ``errors.jsonl`` with step
    ``replay``, and the run goes on.

    Raises:
        ReplayUnavailableError: cloud extraction is off or has no key for the project.

    Side effects: reads every session record and the project's live store;
    makes two attested model calls per replayed session; appends to
    ``replay.jsonl`` and ``errors.jsonl`` under the shadow root.
    """
    key = get_project_key(project)
    config = effective_learn_config(load_config(), project)
    if not _can_call_model(config):
        raise ReplayUnavailableError(
            "replay compares two model calls, and cloud extraction is off or has no key for this project"
        )
    provider = get_embedding_provider(config)
    live_store = store.load_store(project)
    wanted = set(session_ids) if session_ids else None
    sessions, _unreadable = load_sessions()
    candidates = [
        s
        for s in sessions
        if s.ended is not None
        and pident.session_project_key(s.metadata) == key
        and (wanted is None or s.id in wanted)
        and (date_from is None or _utc(s.ended).date() >= date_from)
        and (date_to is None or _utc(s.ended).date() <= date_to)
    ]
    candidates.sort(key=lambda s: _utc(s.ended) if s.ended else datetime.min.replace(tzinfo=UTC), reverse=True)
    done = _replayed_ids(key)
    summary = ReplaySummary(
        project_key=key, candidates=len(candidates), already_done=sum(1 for s in candidates if s.id in done)
    )
    for session in [s for s in candidates if s.id not in done][: max(0, limit)]:
        try:
            record = await replay_session(
                project=project, session=session, config=config, provider=provider, live_store=live_store
            )
        except ReplayUnavailableError:
            raise
        except Exception as exc:
            summary.failed += 1
            record_error(key, "replay", exc)
            continue
        if record is None:
            summary.skipped += 1
            continue
        append_record(get_project_dir(key) / "replay.jsonl", record)
        summary.replayed += 1
        summary.records.append(record)
    return summary
