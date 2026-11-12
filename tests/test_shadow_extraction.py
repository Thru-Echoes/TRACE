"""The session-end shadow extraction: a nearest-50 arm beside the live one (ADR 008 D5).

The tests drive the real registered extract hook (or ``run_shadow_step``
directly) with a scripted OpenAI client — the only substitution — and check
that the live result never changes, that the shadow arm is attested, seeded,
counted and marked as specified, and that every failure is recorded rather
than raised.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from _shadow_helpers import FakeProvider, ScriptedClient, learn_env, make_session, reply

from trace_mcp import extension_hooks
from trace_mcp.extensions.learn import extraction, store
from trace_mcp.extensions.learn.config import LearnConfig
from trace_mcp.extensions.learn.extraction import PROPOSAL_WORDING_INSTRUCTION, ExtractionUsage
from trace_mcp.extensions.learn.models import KnowledgeStore, Learning
from trace_mcp.extensions.learn.shadow import extract as shadow_extract
from trace_mcp.extensions.learn.shadow.extract import (
    NEAREST_N,
    get_nearest_existing,
    load_shadow_store,
    run_shadow_step,
    select_nearest,
)
from trace_mcp.extensions.learn.shadow.state import get_project_dir, get_store_dir, set_excluded, start_trial
from trace_mcp.schema import Session, SessionMetadata
from trace_mcp.schema.events import AnnotationData, TraceEvent
from trace_mcp.schema.session import Actor

PROJECT = "collab-a"
BASE = datetime(2026, 10, 1, tzinfo=UTC)


def _use(monkeypatch: pytest.MonkeyPatch, client: ScriptedClient) -> None:
    monkeypatch.setattr(extraction, "AsyncOpenAI", lambda **_kw: client)


def _records(project: str = PROJECT) -> list[dict]:
    path = get_project_dir(project) / "extraction.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _errors(project: str = PROJECT) -> list[dict]:
    path = get_project_dir(project) / "errors.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _purposes(tmp_path: Path) -> list[str]:
    ledger = tmp_path / "egress.jsonl"
    return [json.loads(line)["purpose"] for line in ledger.read_text().splitlines()] if ledger.exists() else []


def _preload(n: int, *, prefix: str = "an earlier finding about subsystem") -> None:
    ks = store.load_store(PROJECT)
    for i in range(n):
        lrn = store.add_learning(ks, content=f"{prefix} number {i:03d} with padding text", category="learning")
        lrn.created = BASE + timedelta(minutes=i)
    store.save_store(ks)


def _vector_store(n: int = 120, *, usable: int | None = None) -> KnowledgeStore:
    """``n`` learnings; lrn_001..lrn_050 point at [1,0,0], the rest at [0,1,0]; only the first ``usable`` carry one."""
    learnings = []
    for i in range(1, n + 1):
        has_vector = usable is None or i <= usable
        learnings.append(
            Learning(
                id=f"lrn_{i:03d}",
                content=f"stored learning {i:03d} about topic {i % 7}",
                created=BASE + timedelta(minutes=i),
                embedding=([1.0, 0.0, 0.0] if i <= 50 else [0.0, 1.0, 0.0]) if has_vector else None,
                embedding_model="fake" if has_vector else None,
            )
        )
    return KnowledgeStore(project=PROJECT, learnings=learnings)


async def _run_hook(project: str, session: Session) -> extension_hooks.ExtractionResult:
    return await extension_hooks.extract_if_available(project, session.id)


# ── the trial on and off ──────────────────────────────────────────────────


async def test_trial_off_makes_one_call_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    client = ScriptedClient([reply(("Cap retries at three for uploads.", "evt_002"))])
    _use(monkeypatch, client)
    session = await make_session(storage)

    result = await _run_hook(PROJECT, session)

    assert result.error is None and result.new_ids == ["lrn_001"]
    assert len(client.requests) == 1
    shadow_root = tmp_path / "shadow"
    assert not shadow_root.exists() or not any(p.is_file() for p in shadow_root.rglob("*"))


async def test_trial_on_makes_a_second_attested_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    start_trial()
    client = ScriptedClient([reply(("Cap retries at three for uploads.", "evt_002"))] * 2)
    _use(monkeypatch, client)
    session = await make_session(storage)

    result = await _run_hook(PROJECT, session)

    assert result.error is None
    assert len(client.requests) == 2
    assert _purposes(tmp_path) == ["extraction", "shadow-extraction"]
    assert PROPOSAL_WORDING_INSTRUCTION not in client.user_message(0)
    assert PROPOSAL_WORDING_INSTRUCTION in client.user_message(1)
    assert len(_records()) == 1


async def _live_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, trial: bool) -> tuple:
    tmp_path.mkdir(parents=True)
    storage = learn_env(tmp_path, monkeypatch)
    if trial:
        start_trial()
    _preload(3)
    replies = [reply(("Cap retries at three for uploads.", "evt_002"), ("Back off exponentially.", "evt_003"))] * 2
    _use(monkeypatch, ScriptedClient(replies))
    result = await _run_hook(PROJECT, await make_session(storage))
    ks = store.load_store(PROJECT)
    return result.new_ids, [(lrn.id, lrn.content) for lrn in ks.learnings], ks.next_id


async def test_the_live_result_and_live_store_match_a_run_with_the_trial_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    off = await _live_outcome(tmp_path / "off", monkeypatch, trial=False)
    on = await _live_outcome(tmp_path / "on", monkeypatch, trial=True)
    assert on == off
    assert len(_records()) == 1


async def test_the_seed_excludes_this_sessions_live_learnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    start_trial()
    _preload(3)
    _use(
        monkeypatch,
        ScriptedClient(
            [
                reply(("Cap retries at three for uploads.", "evt_002")),
                reply(("It was proposed that retries are capped at three.", "evt_002")),
            ]
        ),
    )
    session = await make_session(storage)

    result = await _run_hook(PROJECT, session)

    assert result.new_ids == ["lrn_004"]
    shadow = load_shadow_store(PROJECT)
    assert shadow is not None
    ids = [lrn.id for lrn in shadow.learnings]
    assert ids[:3] == ["lrn_001", "lrn_002", "lrn_003"]
    assert ids[3:] and all(i.startswith("slrn_") for i in ids[3:])
    assert not [lrn for lrn in shadow.learnings if lrn.source_session == session.id and lrn.id.startswith("lrn_")]
    assert _records()[0]["seeded_now"] is True


async def test_shadow_ids_continue_across_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    start_trial()
    replies = [
        reply(("Cap retries at three for uploads.", "evt_002")),
        reply(("It was proposed that retries are capped.", "evt_002"), ("Backoff was accepted.", "evt_003")),
        reply(("Log every retry attempt in the upload loop.", "evt_004")),
        reply(("Every retry is now logged by the uploader.", "evt_004")),
    ]
    _use(monkeypatch, ScriptedClient(replies))
    await _run_hook(PROJECT, await make_session(storage))
    await _run_hook(PROJECT, await make_session(storage))

    shadow = load_shadow_store(PROJECT)
    assert shadow is not None
    made = [lrn.id for lrn in shadow.learnings if lrn.id.startswith("slrn_")]
    assert made == ["slrn_001", "slrn_002", "slrn_003"]
    assert store.load_store(PROJECT).next_id == 3
    assert [r["seeded_now"] for r in _records()] == [True, False]


# ── selection ─────────────────────────────────────────────────────────────


async def test_nearest_fifty_of_one_hundred_twenty() -> None:
    chosen, how = await select_nearest(_vector_store().learnings, "query", FakeProvider())
    assert how == "embedding"
    assert sorted(lrn.id for lrn in chosen) == [f"lrn_{i:03d}" for i in range(1, 51)]


async def test_a_store_of_fifty_or_fewer_is_sent_whole() -> None:
    learnings = _vector_store(50).learnings
    chosen, how = await select_nearest(learnings, "query", FakeProvider())
    assert how == "all"
    assert [lrn.id for lrn in chosen] == [lrn.id for lrn in learnings]


async def test_without_an_embedding_backend_the_most_recent_fifty_are_sent() -> None:
    chosen, how = await select_nearest(_vector_store().learnings, "query", None)
    assert how == "recent"
    assert sorted(lrn.id for lrn in chosen) == [f"lrn_{i:03d}" for i in range(71, 121)]


async def test_learnings_without_a_usable_vector_fill_from_the_most_recent() -> None:
    chosen, how = await select_nearest(_vector_store(usable=30).learnings, "query", FakeProvider())
    assert how == "embedding"
    expected = [f"lrn_{i:03d}" for i in range(1, 31)] + [f"lrn_{i:03d}" for i in range(101, 121)]
    assert sorted(lrn.id for lrn in chosen) == expected


async def test_the_shadow_prompt_carries_only_the_selected_learnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    client = ScriptedClient([reply(("A new shadow learning.", "evt_002"))])
    _use(monkeypatch, client)
    session = await make_session(storage)

    await run_shadow_step(
        project=PROJECT,
        session=session,
        config=LearnConfig(openai_api_key="test-key", llm_enabled=True),
        provider=FakeProvider(),
        live_store=_vector_store(),
        live_new_ids=[],
        live_usage=None,
    )

    assert _errors() == []
    lines = [line for line in client.user_message(0).splitlines() if line.startswith("- [lrn_")]
    assert len(lines) == 50
    record = _records()[0]
    assert (record["selection"], record["nearest_n"], record["store_size"]) == ("embedding", NEAREST_N, 120)
    assert record["live"]["path"] == "skipped"


async def test_a_long_session_uses_the_last_eight_thousand_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    _use(monkeypatch, ScriptedClient(['{"learnings": []}']))
    session = await make_session(storage)
    long_note = TraceEvent(
        id="evt_005",
        session_id=session.id,
        type="annotation",
        actor=Actor(type="ai", id="ai-assistant"),
        annotation=AnnotationData(category="observation", content="a long observation " * 600),
    )
    session = session.model_copy(update={"events": [*session.events, long_note]})
    assert len(extraction._format_events_for_llm(session)) > 8000

    await run_shadow_step(
        project=PROJECT,
        session=session,
        config=LearnConfig(openai_api_key="test-key", llm_enabled=True),
        provider=None,
        live_store=KnowledgeStore(project=PROJECT),
        live_new_ids=[],
        live_usage=None,
    )

    record = _records()[0]
    assert record["query_chars"] == 8000
    assert record["query_truncated"] is True


# ── marks, similarity, usage ──────────────────────────────────────────────


async def test_marks_as_of_extraction_are_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    start_trial()
    shadow_reply = reply(
        ("It was proposed that retries are capped at three.", "evt_002"),
        ("Exponential backoff was accepted for uploads.", "evt_003"),
        ("Every retry attempt is logged by the uploader.", "evt_004"),
        ("Something whose source event does not exist.", "evt_999"),
    )
    _use(monkeypatch, ScriptedClient(['{"learnings": []}', shadow_reply]))
    await _run_hook(PROJECT, await make_session(storage))

    shadow = _records()[0]["shadow"]["learnings"]
    assert [x["source_event"] for x in shadow] == ["evt_002", "evt_003", "evt_004", "evt_999"]
    assert [x["tags"] for x in shadow] == [
        ["[proposed]"],
        ["[accepted]"],
        ["[accepted]", "[resolution-unquoted]"],
        ["[source-unknown]"],
    ]


async def test_nearest_existing_is_recorded_for_both_arms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    _use(
        monkeypatch,
        ScriptedClient([reply(("A shadow learning one.", "evt_002"), ("A shadow learning two.", "evt_003"))]),
    )
    session = await make_session(storage)
    live = _vector_store()
    live = KnowledgeStore(
        project=PROJECT,
        learnings=[
            *live.learnings,
            Learning(id="lrn_121", content="the live arm's new one", embedding=[0.0, 1.0, 0.0], embedding_model="fake"),
        ],
    )

    await run_shadow_step(
        project=PROJECT,
        session=session,
        config=LearnConfig(openai_api_key="test-key", llm_enabled=True),
        provider=FakeProvider(doc_vec=lambda _t: [1.0, 0.0, 0.0]),
        live_store=live,
        live_new_ids=["lrn_121"],
        live_usage=ExtractionUsage(path="llm", prompt_tokens=1, completion_tokens=1, seconds=0.1, existing_chars=10),
    )

    record = _records()[0]
    (live_entry,) = record["live"]["learnings"]
    assert live_entry["id"] == "lrn_121"
    assert live_entry["nearest_existing_cosine"] == 1.0
    assert live_entry["nearest_existing_id"] in {f"lrn_{i:03d}" for i in range(51, 121)}
    shadow_entries = record["shadow"]["learnings"]
    assert len(shadow_entries) == 2
    for entry in shadow_entries:
        assert entry["nearest_existing_cosine"] == 1.0
        assert entry["nearest_existing_id"] in {f"lrn_{i:03d}" for i in range(1, 51)}


def _lrn(lid: str, vec: list[float] | None, model: str | None = "fake") -> Learning:
    return Learning(id=lid, content=lid, embedding=vec, embedding_model=model if vec is not None else None)


@pytest.mark.parametrize(
    ("new", "existing", "expected"),
    [
        (_lrn("n", None), [_lrn("a", [1.0, 0.0, 0.0])], (None, None)),
        (_lrn("n", [1.0, 0.0, 0.0]), [_lrn("a", [1.0, 0.0, 0.0], model="other")], (None, None)),
        (_lrn("n", [0.0, 0.0, 0.0]), [_lrn("a", [1.0, 0.0, 0.0])], (None, None)),
        (_lrn("n", [1.0, 0.0, 0.0]), [_lrn("a", [1.0, 0.0])], (None, None)),
        (
            _lrn("n", [1.0, 0.0, 0.0]),
            [_lrn("a", [1.0, 0.0, 0.0]), _lrn("b", [0.6, 0.8, 0.0]), _lrn("c", [0.0, 1.0, 0.0])],
            ("a", 1.0),
        ),
        (_lrn("n", [1.0, 0.0, 0.0]), [_lrn("z", [0.0, 0.0, 0.0]), _lrn("b", [0.6, 0.8, 0.0])], ("b", 0.6)),
    ],
)
def test_get_nearest_existing(new: Learning, existing: list[Learning], expected: tuple) -> None:
    assert get_nearest_existing(new, existing) == expected


async def test_usage_is_recorded_for_both_arms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    start_trial()
    _preload(60)
    _use(
        monkeypatch,
        ScriptedClient(
            [reply(("Cap retries at three for uploads.", "evt_002")), reply(("Retries were proposed.", "evt_002"))]
        ),
    )
    await _run_hook(PROJECT, await make_session(storage))

    record = _records()[0]
    live, shadow = record["live"], record["shadow"]
    assert (live["path"], shadow["path"]) == ("llm", "llm")
    assert live["prompt_tokens"] == 120
    assert shadow["completion_tokens"] == 30
    assert live["seconds"] is not None and shadow["seconds"] is not None
    assert record["selection"] == "recent"
    assert shadow["existing_chars"] < live["existing_chars"]


# ── failures are recorded, never raised ───────────────────────────────────


async def test_a_failing_shadow_call_leaves_the_live_result_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    start_trial()
    _use(monkeypatch, ScriptedClient([reply(("Cap retries at three for uploads.", "evt_002"))], fail_on={1}))

    result = await _run_hook(PROJECT, await make_session(storage))

    assert result.error is None and result.new_ids == ["lrn_001"]
    errors = _errors()
    assert len(errors) == 1 and errors[0]["step"] == "extraction"
    assert _records() == []


async def test_a_hanging_shadow_call_is_cut_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    start_trial()
    monkeypatch.setattr(shadow_extract, "SHADOW_STEP_TIMEOUT_SECONDS", 0.05)
    _use(monkeypatch, ScriptedClient([reply(("Cap retries at three for uploads.", "evt_002"))], delay_on={1: 1.0}))
    session = await make_session(storage)

    began = time.monotonic()
    result = await _run_hook(PROJECT, session)
    elapsed = time.monotonic() - began

    assert result.error is None and result.new_ids == ["lrn_001"]
    assert elapsed < 0.5
    assert [e["error_type"] for e in _errors()] == ["TimeoutError"]


async def test_a_held_shadow_lock_is_recorded_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    start_trial()
    _use(monkeypatch, ScriptedClient([reply(("Cap retries at three for uploads.", "evt_002"))] * 2))
    session = await make_session(storage)
    monkeypatch.setenv("TRACE_LOCK_TIMEOUT", "0.2")

    with store.project_lock(PROJECT, directory=str(get_store_dir())):
        result = await _run_hook(PROJECT, session)

    assert result.error is None and result.new_ids == ["lrn_001"]
    assert len(_errors()) == 1


async def test_a_corrupt_shadow_store_is_never_overwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    start_trial()
    path = get_store_dir() / f"{PROJECT}.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{not json")
    _use(monkeypatch, ScriptedClient([reply(("Cap retries at three for uploads.", "evt_002"))] * 2))

    result = await _run_hook(PROJECT, await make_session(storage))

    assert result.error is None and result.new_ids == ["lrn_001"]
    assert path.read_bytes() == b"{not json"
    assert len(_errors()) == 1


async def test_local_only_makes_no_cloud_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = learn_env(tmp_path, monkeypatch, local_only=True)
    start_trial()
    client = ScriptedClient([])
    _use(monkeypatch, client)

    await _run_hook(PROJECT, await make_session(storage))

    assert client.requests == []
    record = _records()[0]
    assert (record["live"]["path"], record["shadow"]["path"]) == ("rule-based", "rule-based")


async def test_an_excluded_project_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    start_trial()
    set_excluded(PROJECT, True)
    client = ScriptedClient([reply(("Cap retries at three for uploads.", "evt_002"))] * 2)
    _use(monkeypatch, client)

    await _run_hook(PROJECT, await make_session(storage))

    assert len(client.requests) == 1
    assert not (get_project_dir(PROJECT) / "extraction.jsonl").exists()


async def test_an_imported_record_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    start_trial()
    client = ScriptedClient([reply(("An imported decision.", "evt_001"))] * 2)
    _use(monkeypatch, client)
    session = Session(
        id="trace_20261101_imp001",
        status="completed",
        metadata=SessionMetadata(project=PROJECT, custom={"source": "acme-tracker", "importer": "decision-log"}),
        events=[
            TraceEvent(
                id="evt_001",
                session_id="trace_20261101_imp001",
                type="annotation",
                actor=Actor(type="ai", id="importer"),
                annotation=AnnotationData(category="learning", content="Imported content."),
            )
        ],
    )
    await storage.create_session(session)

    result = await _run_hook(PROJECT, session)

    assert result.new_ids == []
    # The live arm sends nothing for an imported record (its event block is empty), and neither arm records.
    assert client.requests == []
    assert not (get_project_dir(PROJECT) / "extraction.jsonl").exists()


async def test_every_shadow_embedding_call_is_attributed_to_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cloud embedding backend attests each call; the row must carry the project key, not None."""
    from trace_mcp.extensions.learn import egress

    storage = learn_env(tmp_path, monkeypatch)
    _use(monkeypatch, ScriptedClient([reply(("A new shadow learning.", "evt_002"))]))
    session = await make_session(storage)
    seen: list[str | None] = []

    class _Recording(FakeProvider):
        async def embed_query(self, text: str) -> list[float]:
            seen.append(egress._current_project_key.get())
            return await super().embed_query(text)

        async def embed_texts(self, texts: list[str]) -> list[list[float]]:
            seen.append(egress._current_project_key.get())
            return await super().embed_texts(texts)

    await run_shadow_step(
        project=PROJECT,
        session=session,
        config=LearnConfig(openai_api_key="test-key", llm_enabled=True),
        provider=_Recording(),
        live_store=_vector_store(),
        live_new_ids=[],
        live_usage=None,
    )

    assert _errors() == []
    assert seen and set(seen) == {PROJECT}
