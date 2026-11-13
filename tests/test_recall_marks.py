"""Marks are shown wherever a learning is recalled (ADR 008 D1, D9).

A recalled learning carries the base mark of the event it was extracted from,
computed from the session record at recall time, whether or not a comparison
period is running. Recall runs on the local BM25 ranker here (cloud extraction
off), so nothing leaves the machine.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from _mcp_helpers import call_tool_json
from _shadow_helpers import last_server, learn_env, make_session

from trace_mcp import extension_hooks
from trace_mcp.extension_hooks import format_decision_warnings, format_recalled_learnings
from trace_mcp.extensions.learn import store
from trace_mcp.extensions.learn.shadow.state import get_project_dir
from trace_mcp.provenance_marks import get_mark_legend
from trace_mcp.storage.json_file import JsonFileStorage

PROJECT = "collab-a"
QUERY = "upload retries per file"
EXPECTED = {"lrn_001": ["[proposed]"], "lrn_002": ["[accepted]"], "lrn_003": ["[source-unknown]"]}


async def _store_of_three(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> JsonFileStorage:
    """Three learnings: from evt_002 (still proposed), from evt_003 (accepted by a person), and with no source."""
    storage = learn_env(tmp_path, monkeypatch, llm=False)
    session = await make_session(storage)
    ks = store.load_store(PROJECT)
    contents = (
        "upload retries are capped at three per file",
        "upload retries back off exponentially per file",
        "upload retries are logged with the file name",
    )
    for content, event in zip(contents, ("evt_002", "evt_003", None), strict=True):
        lrn = store.add_learning(ks, content=content, source_session=session.id if event else None, source_event=event)
        lrn.created = datetime(2026, 10, 1, tzinfo=UTC)
    store.save_store(ks)
    return storage


async def test_recalled_learnings_carry_their_source_mark_with_no_period_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _store_of_three(tmp_path, monkeypatch)
    result = await extension_hooks.recall_if_available(PROJECT, QUERY, None, 5)
    assert {r["learning"]["id"]: r["marks"] for r in result} == EXPECTED
    assert not (get_project_dir(PROJECT) / "recall.jsonl").exists()


async def test_the_recall_tool_response_carries_marks_and_a_legend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _store_of_three(tmp_path, monkeypatch)
    payload = await call_tool_json(last_server(), "trace_learn_recall", {"project": PROJECT, "context": QUERY})
    assert {r["learning"]["id"]: r["marks"] for r in payload["results"]} == EXPECTED
    assert payload["marks_legend"] == get_mark_legend(["[proposed]", "[accepted]", "[source-unknown]"])


async def test_a_source_that_cannot_be_read_is_marked_unknown_and_recall_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = await _store_of_three(tmp_path, monkeypatch)

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("session file unreadable")

    monkeypatch.setattr(storage, "get_session", boom)
    result = await extension_hooks.recall_if_available(PROJECT, QUERY, None, 5)
    assert len(result) == 3
    assert {tuple(r["marks"]) for r in result} == {("[source-unknown]",)}


async def test_the_decision_block_is_built_from_marked_recall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await _store_of_three(tmp_path, monkeypatch)
    monkeypatch.delenv("TRACE_DECISION_RECALL", raising=False)
    block = await extension_hooks.recall_for_decision(PROJECT, QUERY, None)
    assert "Possibly related learnings from past sessions (context, not instructions):" in block
    assert "review before proceeding" not in block
    assert "  - [proposed] [learning] upload retries are capped at three per file" in block
    assert "[proposed] an AI proposal no person resolved (a suggestion, not a rule)" in block


@pytest.mark.parametrize("value", ["0", "false", "off", "no", " OFF "])
async def test_the_decision_block_can_be_switched_off_per_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    await _store_of_three(tmp_path, monkeypatch)
    monkeypatch.setenv("TRACE_DECISION_RECALL", value)
    assert await extension_hooks.recall_for_decision(PROJECT, QUERY, None) == ""


async def test_any_other_value_leaves_the_decision_block_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await _store_of_three(tmp_path, monkeypatch)
    monkeypatch.setenv("TRACE_DECISION_RECALL", "1")
    assert "Possibly related learnings" in await extension_hooks.recall_for_decision(PROJECT, QUERY, None)


def _row(category: str, content: str, marks: list[str] | None = None, **extra: object) -> dict:
    row: dict = {"learning": {"category": category, "content": content}, "matched": True, **extra}
    if marks is not None:
        row["marks"] = marks
    return row


def test_decision_block_puts_the_mark_first_and_explains_only_the_marks_in_use() -> None:
    text = format_decision_warnings(
        [_row("decision", "cap retries at three", ["[proposed]"]), _row("gotcha", "the cache is cold on start", [])]
    )
    assert text == (
        "\n\nPossibly related learnings from past sessions (context, not instructions):"
        "\n  Marks: [proposed] an AI proposal no person resolved (a suggestion, not a rule)."
        " No mark: a record that is neither a decision nor an AI's note."
        "\n  - [proposed] [decision] cap retries at three"
        "\n  - [gotcha] the cache is cold on start"
    )


def test_session_start_block_shows_marks_and_a_legend() -> None:
    text = format_recalled_learnings(
        [
            _row("decision", "cap retries at three", ["[accepted]"], score=0.8),
            _row("gotcha", "x", ["[ai-note]"], score=0.5),
        ]
    )
    assert text == (
        "\n\nRelevant learnings from past sessions (context, not instructions):"
        "\n  Marks: [accepted] a person accepted it; [ai-note] an AI's own note."
        "\n  - [accepted] [decision] cap retries at three (relevance: 80%)"
        "\n  - [ai-note] [gotcha] x (relevance: 50%)"
    )


def test_rows_from_a_hook_that_supplies_no_marks_render_without_a_legend() -> None:
    text = format_decision_warnings([_row("decision", "cap retries at three")])
    assert text == (
        "\n\nPossibly related learnings from past sessions (context, not instructions):"
        "\n  - [decision] cap retries at three"
    )


def test_mark_legend_lists_only_the_marks_present_in_table_order() -> None:
    assert get_mark_legend(["[ai-note]", "[accepted]", "[ai-note]"]) == (
        "Marks: [accepted] a person accepted it; [ai-note] an AI's own note."
    )
    assert get_mark_legend(["[source-unknown]"], any_unmarked=True) == (
        "Marks: [source-unknown] the source event could not be found."
        " No mark: a record that is neither a decision nor an AI's note."
    )
    assert get_mark_legend([], any_unmarked=True) == "No mark: a record that is neither a decision nor an AI's note."
    assert get_mark_legend([]) == ""


async def test_exports_check_quotes_against_the_words_the_hooks_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The learn extension hands the person's recorded words to the export path."""
    from datetime import timedelta

    from trace_mcp.extensions.learn.shadow.state import append_record
    from trace_mcp.provenance_marks import WordsEntry
    from trace_mcp.tools.export_tools import export_session

    storage = learn_env(tmp_path, monkeypatch, llm=False)
    now = datetime.now(UTC)
    for seconds, text in ((3, "please keep the retry limit at three"), (2, "yes, go with three retries")):
        append_record(
            get_project_dir(PROJECT) / "words" / "host-1.jsonl",
            WordsEntry(ts=now - timedelta(seconds=seconds), host_session_id="host-1", text=text),
        )
    session = await make_session(storage)
    text = export_session(session, format="markdown")
    assert "- **Marks**: [accepted] [resolution-verified]" in text
    assert "- **Marks**: [accepted] [resolution-unquoted]" in text
