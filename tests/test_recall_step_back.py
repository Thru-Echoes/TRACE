"""Recall steps back from what a person rejected or replaced, and exposure alone keeps nothing evergreen.

A learning whose source decision a person rejected, or replaced by accepting a
decision that revises it, is left out of recall and kept in the store; when the
replacement has a learning of its own, that learning is returned in its place.
A correction of the source is shown as a pointer, never as a mark of its own.
The evergreen floor (a learning recalled three or more times does not fade
below it) applies only to a learning whose source a person accepted.

Everything runs on the local BM25 ranker against a real session record.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from _mcp_helpers import call_tool_json
from _shadow_helpers import last_server, learn_env

from trace_mcp import extension_hooks
from trace_mcp.extensions.learn import store
from trace_mcp.schema import Session
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import decision_tools, logging_tools, session_tools

PROJECT = "collab-a"
QUERY = "upload retries"


async def _decide(
    storage: JsonFileStorage,
    session: Session,
    description: str,
    disposition: str,
    *,
    resolver: str = "human",
    revises: str | None = None,
) -> None:
    await decision_tools.propose_decision(
        storage,
        session,
        description=description,
        proposed_by_type="ai",
        proposed_by_id="ai-assistant",
        revises_event_id=revises,
    )
    event_id = session.events[-1].id
    await decision_tools.resolve_decision(
        storage,
        session,
        event_id=event_id,
        disposition=disposition,
        resolved_by_type=resolver,
        resolved_by_id="alice" if resolver == "human" else "ai-assistant",
    )


async def _history(storage: JsonFileStorage) -> Session:
    """A session whose decisions were rejected, replaced, corrected and revised by an AI.

    evt_001 rejected by a person; evt_002 accepted, then replaced by evt_003 (accepted, revises
    evt_002); evt_004 an AI note corrected by evt_005; evt_006 accepted, replaced by evt_007,
    which has no learning; evt_008 resolved by an AI and revised by evt_009, also resolved by an
    AI; evt_010 revises evt_003 and a person rejected it.
    """
    active: dict[str, Session] = {}
    session = await session_tools.create_session(storage, active, project=PROJECT)
    await _decide(storage, session, "Retry uploads forever.", "rejected")
    await _decide(storage, session, "Cap upload retries at five.", "accepted")
    await _decide(storage, session, "Cap upload retries at three.", "accepted", revises="evt_002")
    await logging_tools.log_annotation(storage, session, category="gotcha", content="Uploads retry on every 503.")
    await logging_tools.log_annotation(
        storage, session, category="correction", content="Only gateway 503s retry.", corrects_event_ids=["evt_004"]
    )
    await _decide(storage, session, "Cap upload retries per file at ten.", "accepted")
    await _decide(storage, session, "Cap upload retries per file at four.", "accepted", revises="evt_006")
    await _decide(storage, session, "Upload retries wait one second.", "accepted", resolver="ai")
    await _decide(storage, session, "Upload retries wait two seconds.", "accepted", resolver="ai", revises="evt_008")
    await _decide(storage, session, "Cap upload retries at two.", "rejected", revises="evt_003")
    await session_tools.end_session(storage, active, session_id=session.id)
    return await storage.get_session(session.id)


LEARNINGS = (
    ("upload retries forever were proposed", "evt_001"),  # lrn_001: source rejected
    ("upload retries are capped at five", "evt_002"),  # lrn_002: source replaced by evt_003
    ("upload retries are capped at three", "evt_003"),  # lrn_003: the replacement
    ("upload retries happen on every 503", "evt_004"),  # lrn_004: source corrected by evt_005
    ("upload retries per file are capped at ten", "evt_006"),  # lrn_005: replaced by evt_007, no learning from it
    ("upload retries wait one second", "evt_008"),  # lrn_006: revised only by an AI
)
FILLER = (
    "the build runs ruff first",
    "the docs site is static",
    "release notes go in the changelog",
    "tests use pytest",
)


async def _store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[JsonFileStorage, str]:
    storage = learn_env(tmp_path, monkeypatch, llm=False)
    session = await _history(storage)
    ks = store.load_store(PROJECT)
    for content, event in LEARNINGS:
        store.add_learning(ks, content=content, source_session=session.id, source_event=event)
    for content in FILLER:
        store.add_learning(ks, content=content)
    store.save_store(ks)
    return storage, session.id


def _by_id(results: list[dict]) -> dict[str, dict]:
    return {r["learning"]["id"]: r for r in results}


async def test_recall_leaves_out_what_a_person_rejected_or_replaced_and_returns_the_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _storage, sid = await _store(tmp_path, monkeypatch)
    result = await extension_hooks.recall_if_available(PROJECT, QUERY, None, 10)
    shown = _by_id(list(result))
    assert "lrn_001" not in shown  # a person rejected its source
    assert "lrn_002" not in shown  # a person accepted a replacement for its source
    assert "lrn_005" not in shown  # replaced too; the replacement has no learning
    assert shown["lrn_003"]["replaces"] == "lrn_002"
    assert [r["learning"]["id"] for r in result].count("lrn_003") == 1
    assert "lrn_006" in shown  # an AI's revision replaces nothing
    assert shown["lrn_004"]["source_corrected_by"] == [f"{sid}#evt_005"]
    assert "source_corrected_by" not in shown["lrn_003"]
    assert result.notice is not None
    assert "lrn_001 (a person rejected its source)" in result.notice
    assert f"lrn_002 (a person replaced its source with {sid}#evt_003, shown as lrn_003)" in result.notice
    assert f"lrn_005 (a person replaced its source with {sid}#evt_007)" in result.notice
    assert "kept in the store" in result.notice


async def test_a_learning_left_out_stays_in_the_store_and_gains_no_exposure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _store(tmp_path, monkeypatch)
    await extension_hooks.recall_if_available(PROJECT, QUERY, None, 10)
    counts = {lrn.id: lrn.recall_count for lrn in store.load_store(PROJECT).learnings}
    assert counts["lrn_001"] == counts["lrn_002"] == counts["lrn_005"] == 0
    assert counts["lrn_003"] == 1
    listed = await call_tool_json(last_server(), "trace_learn_list", {"project": PROJECT})
    assert {"lrn_001", "lrn_002", "lrn_005"} <= {lrn["id"] for lrn in listed["learnings"]}


async def test_the_recall_tool_reports_what_it_left_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _storage, sid = await _store(tmp_path, monkeypatch)
    payload = await call_tool_json(
        last_server(), "trace_learn_recall", {"project": PROJECT, "context": QUERY, "limit": 10}
    )
    assert {r["learning"]["id"] for r in payload["results"]}.isdisjoint({"lrn_001", "lrn_002", "lrn_005"})
    assert payload["stepped_back"] == [
        {"id": "lrn_001", "reason": "rejected", "replaced_by": None, "replacement": None},
        {"id": "lrn_002", "reason": "replaced", "replaced_by": f"{sid}#evt_003", "replacement": "lrn_003"},
        {"id": "lrn_005", "reason": "replaced", "replaced_by": f"{sid}#evt_007", "replacement": None},
    ]


async def test_the_session_banner_shows_the_replacement_and_the_correction_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _storage, sid = await _store(tmp_path, monkeypatch)
    result = await extension_hooks.recall_if_available(PROJECT, QUERY, None, 10)
    text = extension_hooks.format_recalled_learnings(list(result))
    assert f"upload retries are capped at three (lrn_003, replaces lrn_002; source [accepted] {sid}#evt_003;" in text
    assert f"(lrn_004; source [ai-note] {sid}#evt_004; source corrected, see {sid}#evt_005;" in text


async def test_the_evergreen_floor_holds_only_for_a_learning_whose_source_a_person_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _storage, _sid = await _store(tmp_path, monkeypatch)
    ks = store.load_store(PROJECT)
    two_years_ago = datetime.now(UTC) - timedelta(days=730)
    for lrn in ks.learnings:
        lrn.created = two_years_ago
        if lrn.id in ("lrn_003", "lrn_004", "lrn_007"):  # accepted source, AI note, no source
            lrn.recall_count = 5
            lrn.last_surfaced = two_years_ago
    store.save_store(ks)
    payload = await call_tool_json(
        last_server(), "trace_learn_recall", {"project": PROJECT, "context": QUERY + " ruff", "limit": 10}
    )
    decay = {r["learning"]["id"]: r["signals"]["decay"] for r in payload["results"]}
    assert decay["lrn_003"] == 0.8  # a person accepted its source: the floor holds
    assert decay["lrn_004"] == pytest.approx(0.25, abs=0.01)  # an AI's note: no floor, however often recalled
    assert decay["lrn_007"] == pytest.approx(0.25, abs=0.01)  # no source: no floor


async def test_a_replacement_the_backend_did_not_score_is_not_shown_and_recall_still_answers() -> None:
    """On the explicit-threshold path only scored learnings have a score to show."""
    from trace_mcp.extensions.learn import matching
    from trace_mcp.extensions.learn.models import Learning

    old = Learning(id="lrn_1", content="retries capped at five", source_session="trace_a", source_event="evt_1")
    new = Learning(id="lrn_2", content="unrelated wording", source_session="trace_a", source_event="evt_2")

    class OnlyFirst:
        """A pre-filtering backend that scores only the first learning."""

        default_threshold = 0.1

        async def score_batch(self, learnings, context, context_tags=None):  # noqa: ANN001, ANN201
            return [(0, 0.9)]

    async def replaced(lrn: Learning) -> matching.StepBack | None:
        return matching.StepBack("replaced", "trace_a#evt_2") if lrn.id == "lrn_1" else None

    outcome = await matching.recall_ranked(
        [old, new],
        "retries",
        threshold=0.1,
        limit=5,
        backend=OnlyFirst(),
        step_back=replaced,  # type: ignore[arg-type]
    )
    assert outcome.results == []
    assert outcome.summary.stepped_back == (
        matching.SteppedBack(id="lrn_1", reason="replaced", replaced_by="trace_a#evt_2", replacement=None),
    )


async def test_a_replacement_standing_in_for_a_matched_learning_reaches_the_decision_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The replacement's own text need not match: it is shown because what it replaces matched."""
    storage = learn_env(tmp_path, monkeypatch, llm=False)
    session = await _history(storage)
    ks = store.load_store(PROJECT)
    old = store.add_learning(
        ks, content="upload retries are capped at five", source_session=session.id, source_event="evt_002"
    )
    new = store.add_learning(ks, content="the limit moved to three", source_session=session.id, source_event="evt_003")
    for content in (*FILLER, "the changelog has sections", "the lockfile is committed", "ci runs on push"):
        store.add_learning(ks, content=content)
    store.save_store(ks)
    monkeypatch.delenv("TRACE_DECISION_RECALL", raising=False)

    block = await extension_hooks.recall_for_decision(PROJECT, QUERY, None)
    assert f"  - [learning] the limit moved to three ({new.id}, replaces {old.id}; source [accepted]" in block
    assert "upload retries are capped at five" not in block

    banner = extension_hooks.format_recalled_learnings(
        list(await extension_hooks.recall_if_available(PROJECT, QUERY, None, 5))
    )
    line = next(x for x in banner.splitlines() if "the limit moved to three" in x)
    assert "weak match" not in line
    # Shown twice, each time in a matched learning's place: both exposures are saved.
    saved = {lrn.id: lrn for lrn in store.load_store(PROJECT).learnings}
    assert saved[new.id].recall_count == 2 and saved[new.id].last_surfaced is not None
    assert saved[old.id].recall_count == 0


async def test_the_best_ranked_eligible_learning_from_the_replacement_is_chosen() -> None:
    """Several learnings may come from the replacement; one the backend did not score is passed over."""
    from trace_mcp.extensions.learn import matching
    from trace_mcp.extensions.learn.models import Learning

    learnings = [
        Learning(id="lrn_1", content="capped at five", source_session="trace_a", source_event="evt_1"),
        Learning(id="lrn_2", content="first account of three", source_session="trace_a", source_event="evt_2"),
        Learning(id="lrn_3", content="second account of three", source_session="trace_a", source_event="evt_2"),
    ]

    class SomeScored:
        default_threshold = 0.1

        async def score_batch(self, learnings, context, context_tags=None):  # noqa: ANN001, ANN201
            return [(0, 0.9), (2, 0.05)]

    async def replaced(lrn: Learning) -> matching.StepBack | None:
        return matching.StepBack("replaced", "trace_a#evt_2") if lrn.id == "lrn_1" else None

    outcome = await matching.recall_ranked(
        learnings,
        "retries",
        threshold=0.1,
        limit=5,
        backend=SomeScored(),
        step_back=replaced,  # type: ignore[arg-type]
    )
    assert [(r["learning"]["id"], r.get("replaces")) for r in outcome.results] == [("lrn_3", "lrn_1")]
    assert outcome.summary.stepped_back[0].replacement == "lrn_3"
