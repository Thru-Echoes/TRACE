"""Recall record: while a period runs each recall is recorded by mark, and recording never changes it (ADR 008 D6).

Recall runs on the local BM25 ranker here (cloud extraction off), so nothing
leaves the machine and no client needs substituting.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from _mcp_helpers import call_tool_json
from _shadow_helpers import last_server, learn_env, make_session

from trace_mcp import extension_hooks
from trace_mcp.extensions.learn import store
from trace_mcp.extensions.learn.shadow import recall as shadow_recall
from trace_mcp.extensions.learn.shadow.state import get_project_dir, start_trial
from trace_mcp.provenance_marks import SOURCE_MARKS_LEAD, get_mark_legend

PROJECT = "collab-a"
CREATED = datetime(2026, 10, 1, tzinfo=UTC)
CONTENTS = (
    "upload retries are capped at three per file",
    "upload retries back off exponentially per file",
    "upload retries are logged with the file name",
)


def _records() -> list[dict]:
    path = get_project_dir(PROJECT) / "recall.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _errors() -> list[dict]:
    path = get_project_dir(PROJECT) / "errors.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


async def _scenario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, trial: bool) -> str:
    """A store of three learnings from evt_002 (proposed), evt_003 (accepted) and no source; returns the session id."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    storage = learn_env(tmp_path, monkeypatch, llm=False)
    session = await make_session(storage)
    ks = store.load_store(PROJECT)
    for content, event in zip(CONTENTS, ("evt_002", "evt_003", None), strict=True):
        lrn = store.add_learning(ks, content=content, source_session=session.id if event else None, source_event=event)
        lrn.created = CREATED
    store.save_store(ks)
    if trial:
        start_trial()
    return session.id


def _summary(result: extension_hooks.RecallResult) -> list[tuple]:
    return [(r["learning"]["id"], r["score"], r["matched"]) for r in result] + [("notice", result.notice)]


async def test_trial_off_records_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await _scenario(tmp_path, monkeypatch, trial=False)
    await extension_hooks.recall_if_available(PROJECT, "upload retries per file", None, 5)
    assert not (get_project_dir(PROJECT) / "recall.jsonl").exists()


async def test_trial_on_records_marks_and_leaves_the_result_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _scenario(tmp_path / "off", monkeypatch, trial=False)
    control = await extension_hooks.recall_if_available(PROJECT, "upload retries per file", None, 5)
    await _scenario(tmp_path / "on", monkeypatch, trial=True)
    result = await extension_hooks.recall_if_available(PROJECT, "upload retries per file", None, 5)

    assert _summary(result) == _summary(control)
    assert len(result) == 3
    (record,) = _records()
    by_id = {item["id"]: item["tags"] for item in record["items"]}
    assert by_id == {"lrn_001": ["[proposed]"], "lrn_002": ["[accepted]"], "lrn_003": ["[source-unknown]"]}
    assert [item["id"] for item in record["items"]] == [r["learning"]["id"] for r in result]
    bare = [f"  - [{r['learning']['category']}] {r['learning']['content']}" for r in result]

    def source(r: dict) -> str:
        lrn = r["learning"]
        ref = f" {lrn['source_session']}#{lrn['source_event']}" if lrn.get("source_event") else ""
        return f"{lrn['id']}; source {r['source_marks'][0]}{ref}"

    shown = [f"{line} ({source(r)})" for line, r in zip(bare, result, strict=True)]
    legend = "  " + get_mark_legend(["[proposed]", "[accepted]", "[source-unknown]"], lead=SOURCE_MARKS_LEAD)
    assert record["chars_without"] == sum(len(line) + 1 for line in bare)
    assert record["chars_with"] == sum(len(line) + 1 for line in [legend, *shown])
    assert record["limit"] == 5 and record["context_chars"] == len("upload retries per file")


async def test_a_recall_with_no_results_is_recorded_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    learn_env(tmp_path, monkeypatch, llm=False)
    start_trial()
    await extension_hooks.recall_if_available(PROJECT, "nothing is stored yet", None, 5)
    (record,) = _records()
    assert record["items"] == []
    assert record["chars_with"] == record["chars_without"] == 0


async def test_a_failure_is_recorded_and_the_result_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await _scenario(tmp_path / "off", monkeypatch, trial=False)
    control = await extension_hooks.recall_if_available(PROJECT, "upload retries per file", None, 5)
    await _scenario(tmp_path / "on", monkeypatch, trial=True)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("the record could not be written")

    monkeypatch.setattr(shadow_recall, "append_record", boom)
    result = await extension_hooks.recall_if_available(PROJECT, "upload retries per file", None, 5)

    assert _summary(result) == _summary(control)
    assert _records() == []
    assert [e["step"] for e in _errors()] == ["recall"]


def _stable(response: dict) -> dict:
    """The recall response without what differs between two temp roots: bookkeeping times and minted session ids."""
    for item in response.get("results", []):
        learning = item.get("learning", {})
        learning.pop("last_surfaced", None)
        if learning.get("source_session"):
            learning["source_session"] = "<session>"
    return response


async def test_the_recall_tool_response_is_identical_with_the_trial_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = {"project": PROJECT, "context": "upload retries per file"}
    await _scenario(tmp_path / "off", monkeypatch, trial=False)
    off = await call_tool_json(last_server(), "trace_learn_recall", args)
    assert _records() == []
    await _scenario(tmp_path / "on", monkeypatch, trial=True)
    on = await call_tool_json(last_server(), "trace_learn_recall", args)

    assert "error" not in on
    assert _stable(on) == _stable(off)
    assert len(_records()) == 1
