"""Session end makes one model call; the nearest-50 question is answered by an offline replay (ADR 008 D5).

``replay_session`` extracts one past session twice on the same frozen events
and the same snapshot of the stored learnings: once sending every stored
learning (the method in use) and once sending the 50 nearest. Neither arm
writes to a knowledge store. The tests drive the real functions with a
scripted OpenAI client, the only substitution.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from _shadow_helpers import FakeProvider, ScriptedClient, learn_env, make_session, reply

from trace_mcp import extension_hooks
from trace_mcp.extensions.learn import extraction, store
from trace_mcp.extensions.learn.config import LearnConfig
from trace_mcp.extensions.learn.models import KnowledgeStore, Learning
from trace_mcp.extensions.learn.shadow.replay import (
    NEAREST_N,
    ReplayUnavailableError,
    get_nearest_existing,
    get_store_snapshot,
    replay_session,
    run_replay,
    select_nearest,
)
from trace_mcp.extensions.learn.shadow.state import get_project_dir, start_trial
from trace_mcp.schema import Session, SessionMetadata
from trace_mcp.schema.events import AnnotationData, TraceEvent
from trace_mcp.schema.session import Actor
from trace_mcp.tools import decision_tools

PROJECT = "collab-a"
BASE = datetime(2026, 10, 1, tzinfo=UTC)
CONFIG = LearnConfig(openai_api_key="test-key", llm_enabled=True)
TWO = reply(
    ("It was proposed that upload retries are capped at three.", "evt_002"), ("Back off between retries.", "evt_003")
)
ONE = reply(("It was proposed that upload retries are capped at three.", "evt_002"))


def _use(monkeypatch: pytest.MonkeyPatch, client: ScriptedClient) -> None:
    monkeypatch.setattr(extraction, "AsyncOpenAI", lambda **_kw: client)


def _records(project: str = PROJECT) -> list[dict]:
    path = get_project_dir(project) / "replay.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _purposes(tmp_path: Path) -> list[str]:
    ledger = tmp_path / "egress.jsonl"
    return [json.loads(line)["purpose"] for line in ledger.read_text().splitlines()] if ledger.exists() else []


def _preload(n: int, session: Session | None = None) -> None:
    """``n`` learnings stored before the session; with ``session``, also one from it and one made after it ended."""
    ks = store.load_store(PROJECT)
    for i in range(n):
        lrn = store.add_learning(ks, content=f"an earlier finding about subsystem number {i:03d} with padding text")
        lrn.created = BASE + timedelta(minutes=i)
    if session is not None:
        assert session.ended is not None
        store.add_learning(
            ks, content="a learning from this very session", source_session=session.id, source_event="evt_001"
        )
        later = store.add_learning(ks, content="a learning written after the session ended")
        later.created = session.ended + timedelta(hours=1)
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
    return KnowledgeStore(project=PROJECT, learnings=learnings, next_id=n + 1)


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ── session end ───────────────────────────────────────────────────────────


async def test_a_session_end_makes_one_model_call_while_a_period_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    start_trial()
    client = ScriptedClient([ONE, ONE])
    _use(monkeypatch, client)
    session = await make_session(storage)

    result = await extension_hooks.extract_if_available(PROJECT, session.id)

    assert result.error is None and result.new_ids == ["lrn_001"]
    assert len(client.requests) == 1
    assert _purposes(tmp_path) == ["extraction"]
    assert not get_project_dir(PROJECT).exists() or not any(get_project_dir(PROJECT).iterdir())
    assert not (tmp_path / "shadow" / "stores").exists()


# ── which learnings the nearest arm sends ─────────────────────────────────


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


# ── one replayed session ──────────────────────────────────────────────────


async def test_the_snapshot_is_the_store_as_it_stood_before_the_session_was_extracted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    session = await make_session(storage)
    _preload(3, session)
    snapshot = get_store_snapshot(store.load_store(PROJECT), session)
    assert [lrn.content[-16:] for lrn in snapshot] == [f"{i:03d} with padding text"[-16:] for i in range(3)]


async def test_both_arms_run_on_the_same_snapshot_and_nothing_is_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    session = await make_session(storage)
    _preload(3, session)
    client = ScriptedClient([TWO, ONE], usage=(900, 40))
    _use(monkeypatch, client)
    before = _tree(tmp_path / "knowledge")

    record = await replay_session(
        project=PROJECT, session=session, config=CONFIG, provider=None, live_store=store.load_store(PROJECT)
    )

    assert record is not None
    assert len(client.requests) == 2
    assert _purposes(tmp_path) == ["replay-extraction-whole", "replay-extraction-nearest"]
    for index in (0, 1):
        message = client.user_message(index)
        assert "subsystem number 000" in message and "subsystem number 002" in message
        assert "from this very session" not in message and "after the session ended" not in message
    assert [(x.id, x.source_event, x.tags) for x in record.whole.learnings] == [
        ("whole-1", "evt_002", ["[proposed]"]),
        ("whole-2", "evt_003", ["[accepted]"]),
    ]
    assert [x.content for x in record.whole.learnings] == [
        "It was proposed that upload retries are capped at three.",
        "Back off between retries.",
    ]
    assert [(x.id, x.source_event) for x in record.nearest.learnings] == [("nearest-1", "evt_002")]
    assert (record.whole.path, record.whole.prompt_tokens, record.whole.completion_tokens) == ("llm", 900, 40)
    assert (record.nearest.path, record.nearest.prompt_tokens) == ("llm", 900)
    assert (record.store_size, record.selection, record.nearest_n) == (3, "all", NEAREST_N)
    assert record.whole.existing_chars == record.nearest.existing_chars
    assert _tree(tmp_path / "knowledge") == before
    assert not (tmp_path / "shadow").exists()


async def test_the_nearest_arm_sends_only_the_selected_learnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    session = await make_session(storage)
    client = ScriptedClient([ONE, ONE])
    _use(monkeypatch, client)
    provider = FakeProvider(doc_vec=lambda _text: [1.0, 0.0, 0.0])

    record = await replay_session(
        project=PROJECT, session=session, config=CONFIG, provider=provider, live_store=_vector_store()
    )

    assert record is not None
    whole, nearest = client.user_message(0), client.user_message(1)
    assert "stored learning 001 " in whole and "stored learning 120 " in whole
    assert "stored learning 050 " in nearest and "stored learning 051 " not in nearest
    assert (record.store_size, record.selection) == (120, "embedding")
    assert record.nearest.existing_chars is not None and record.whole.existing_chars is not None
    assert record.nearest.existing_chars < record.whole.existing_chars
    # Each new learning is compared with the closest stored one, by vector.
    assert record.whole.learnings[0].nearest_existing_cosine == 1.0
    assert record.whole.learnings[0].nearest_existing_id == "lrn_001"


async def test_marks_are_those_the_source_had_when_the_session_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    session = await make_session(storage)
    await decision_tools.resolve_decision(
        storage,
        session,
        event_id="evt_002",
        disposition="accepted",
        resolved_by_type="human",
        resolved_by_id="alice",
        conversation_snippet="yes, cap them at three",
    )
    session = await storage.get_session(session.id)
    _use(monkeypatch, ScriptedClient([ONE, ONE]))

    record = await replay_session(
        project=PROJECT, session=session, config=CONFIG, provider=None, live_store=KnowledgeStore(project=PROJECT)
    )

    assert record is not None
    assert record.whole.learnings[0].tags == ["[proposed]"]


async def test_replay_needs_cloud_extraction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    session = await make_session(storage)
    client = ScriptedClient([ONE, ONE])
    _use(monkeypatch, client)
    with pytest.raises(ReplayUnavailableError):
        await replay_session(
            project=PROJECT,
            session=session,
            config=LearnConfig(openai_api_key="test-key", llm_enabled=False),
            provider=None,
            live_store=KnowledgeStore(project=PROJECT),
        )
    assert client.requests == []


async def test_an_imported_record_is_not_replayed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    learn_env(tmp_path, monkeypatch)
    client = ScriptedClient([ONE, ONE])
    _use(monkeypatch, client)
    session = Session(
        id="trace_20261101_imp001",
        status="completed",
        ended=BASE,
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
    record = await replay_session(
        project=PROJECT, session=session, config=CONFIG, provider=None, live_store=KnowledgeStore(project=PROJECT)
    )
    assert record is None and client.requests == []


async def test_every_replay_egress_is_attributed_to_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cloud embedding backend attests each call; the row must carry the project key, not None."""
    from trace_mcp.extensions.learn import egress

    storage = learn_env(tmp_path, monkeypatch)
    _use(monkeypatch, ScriptedClient([ONE, ONE]))
    session = await make_session(storage)
    seen: list[str | None] = []

    class _Recording(FakeProvider):
        async def embed_query(self, text: str) -> list[float]:
            seen.append(egress._current_project_key.get())
            return await super().embed_query(text)

        async def embed_texts(self, texts: list[str]) -> list[list[float]]:
            seen.append(egress._current_project_key.get())
            return await super().embed_texts(texts)

    await replay_session(
        project=PROJECT, session=session, config=CONFIG, provider=_Recording(), live_store=_vector_store()
    )

    assert len(seen) == 3 and set(seen) == {PROJECT}


# ── the command's loop ────────────────────────────────────────────────────


async def test_run_replay_records_each_session_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    session = await make_session(storage)
    _preload(2)
    client = ScriptedClient([TWO, ONE, TWO, ONE])
    _use(monkeypatch, client)

    first = await run_replay(project=PROJECT, limit=5)
    second = await run_replay(project=PROJECT, limit=5)

    assert (first.replayed, first.skipped, first.failed) == (1, 0, 0)
    assert (second.replayed, second.already_done) == (0, 1)
    assert len(client.requests) == 2
    (record,) = _records()
    assert record["session_id"] == session.id and record["project_key"] == PROJECT
    assert [x["id"] for x in record["whole"]["learnings"]] == ["whole-1", "whole-2"]


async def test_run_replay_takes_the_most_recent_ended_sessions_of_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    older = await make_session(storage)
    newer = await make_session(storage)
    await make_session(storage, project="collab-b")
    _use(monkeypatch, ScriptedClient([ONE] * 8))

    summary = await run_replay(project=PROJECT, limit=1)

    assert summary.replayed == 1
    assert [r["session_id"] for r in _records()] == [newer.id]
    assert older.id != newer.id
    assert _records("collab-b") == []


async def test_a_failed_replay_is_recorded_and_the_loop_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    await make_session(storage)
    await make_session(storage)
    _use(monkeypatch, ScriptedClient([ONE, ONE, ONE, ONE], fail_on={0}))

    summary = await run_replay(project=PROJECT, limit=5)

    assert (summary.replayed, summary.failed) == (1, 1)
    errors = (get_project_dir(PROJECT) / "errors.jsonl").read_text().splitlines()
    assert [json.loads(line)["step"] for line in errors] == ["replay"]
