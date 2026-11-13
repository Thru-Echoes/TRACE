"""Provenance marks end to end.

One story, real functions and the real hook scripts (run as subprocesses); the
model client is the only substitution. The person types two prompts and answers
one question dialog; a session proposes and resolves four decisions on those
words; the session end extracts with one model call; a recall shows each
learning with the mark of its source; the session is replayed offline with
both arms; the report and review sheet then say what was verified, where, and
what no person accepted. After ``stop``, nothing more is captured.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from _shadow_helpers import ScriptedClient, learn_env, reply

from trace_mcp import extension_hooks
from trace_mcp.extensions.learn import extraction
from trace_mcp.extensions.learn.shadow import cli
from trace_mcp.extensions.learn.shadow.replay import run_replay
from trace_mcp.extensions.learn.shadow.report import build_report, get_review_rows
from trace_mcp.extensions.learn.shadow.state import get_project_dir
from trace_mcp.schema import Session
from trace_mcp.tools import decision_tools, session_tools

PROJECT = "collab-a"
HOOKS = Path(__file__).parent.parent / "src" / "trace_mcp" / "adapters" / "claude_code" / "assets" / "hooks"


def _hook(script: str, payload: dict, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    (project / ".claude").mkdir(parents=True, exist_ok=True)
    (project / ".claude" / "trace.project").write_text(f"{PROJECT}\n")
    env = {
        **os.environ,
        "CLAUDE_PROJECT_DIR": str(project),
        "TRACE_SHADOW_DIR": str(tmp_path / "shadow"),
        "TRACE_SESSIONS_DIR": str(tmp_path / "sessions"),
        "TRACE_RUNTIME_DIR": str(tmp_path / "runtime"),
    }
    result = subprocess.run(
        ["bash", str(HOOKS / script)], input=json.dumps(payload), env=env, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def _prompt(text: str, tmp_path: Path) -> None:
    _hook("prompt-reminder.sh", {"session_id": "host-1", "prompt": text}, tmp_path)


def _dialog(answers: dict[str, str], tmp_path: Path) -> None:
    payload = {"session_id": "host-1", "tool_name": "AskUserQuestion", "tool_response": {"answers": answers}}
    _hook("dialog-words.sh", payload, tmp_path)


async def _propose(storage, session: Session, description: str, snippet: str | None = None) -> None:
    await decision_tools.propose_decision(
        storage,
        session,
        description=description,
        proposed_by_type="ai",
        proposed_by_id="ai-assistant",
        conversation_snippet=snippet,
    )


async def _accept(storage, session: Session, event_id: str, quote: str | None) -> None:
    await decision_tools.resolve_decision(
        storage,
        session,
        event_id=event_id,
        disposition="accepted",
        resolved_by_type="human",
        resolved_by_id="alice",
        conversation_snippet=quote,
    )


async def test_the_shadow_period_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    two = reply(
        ("Upload retries are capped at three.", "evt_001"),
        ("It was proposed that retries use exponential backoff.", "evt_002"),
    )
    client = ScriptedClient([two, two, two, reply(("A learning from the second session.", "evt_001"))])
    monkeypatch.setattr(extraction, "AsyncOpenAI", lambda **_kw: client)

    # 1-2. The period starts; the person types a first prompt.
    assert cli.main(["start"]) == 0
    _prompt("please keep the retry limit at three", tmp_path)

    # 3-6. A session proposes A on those words, the person approves it, B stays proposed,
    #      the person answers a dialog that resolves C, and D is resolved with no quote.
    active: dict[str, Session] = {}
    session = await session_tools.create_session(storage, active, project=PROJECT)
    await _propose(storage, session, "Keep the retry limit at three.", "keep the retry limit at three")
    _prompt("yes, go with three retries", tmp_path)
    await _accept(storage, session, "evt_001", "yes, go with three retries")
    await _propose(storage, session, "Use exponential backoff.")
    _dialog({"Which cache?": "Use the local cache (Recommended)"}, tmp_path)
    await _propose(storage, session, "Use the local cache.")
    await _accept(storage, session, "evt_003", "Use the local cache")
    await _propose(storage, session, "Log every retry.")
    await _accept(storage, session, "evt_004", None)

    # 7. The session ends and is extracted with one model call.
    await session_tools.end_session(storage, active, session_id=session.id)
    result = await extension_hooks.extract_if_available(PROJECT, session.id)
    assert result.error is None and result.new_ids == ["lrn_001", "lrn_002"]
    assert len(client.requests) == 1

    # 8. Later, offline: the session is replayed with both arms. Nothing is stored.
    summary = await run_replay(project=PROJECT, limit=5)
    assert (summary.replayed, summary.failed) == (1, 0) and len(client.requests) == 3

    # 9. A recall, on the local ranker, shows each learning with the mark of its source.
    learn_env(tmp_path, monkeypatch, llm=False)
    recalled = await extension_hooks.recall_if_available(PROJECT, "upload retries", None, 5)
    assert {r["learning"]["source_event"]: r["marks"] for r in recalled} == {
        "evt_001": ["[accepted]"],
        "evt_002": ["[proposed]"],
    }
    block = extension_hooks.format_recalled_learnings(list(recalled))
    assert "[proposed] [learning] It was proposed that retries use exponential backoff." in block
    assert "[proposed] an AI proposal no person resolved (a suggestion, not a rule)" in block

    # 10. What the report says.
    (record,) = [json.loads(x) for x in (get_project_dir(PROJECT) / "replay.jsonl").read_text().splitlines()]
    for arm in ("whole", "nearest"):
        tags = {x["source_event"]: x["tags"] for x in record[arm]["learnings"]}
        assert tags == {
            "evt_001": ["[accepted]", "[quote-verified]", "[resolution-verified]"],
            "evt_002": ["[proposed]"],
        }
    total = build_report().total
    assert total.resolution == {"verified": 2, "absent": 1}
    assert total.resolution_timing == {"person_spoke_between": 1, "same_turn": 2}
    assert total.resolution_position == {"turn": 2}
    assert total.quote_position == {"turn": 1}
    assert total.words_by_kind == {"prompt": 2, "dialog_answer": 1}
    assert total.paired == 2
    assert total.recall_calls == 1
    rows = get_review_rows()
    assert total.stored_by_mark == {"[accepted]": 1, "[proposed]": 1}
    unaccepted = [row for row in rows if row.kind == "unaccepted_source"]
    assert [(row.event_id, row.learning_id) for row in unaccepted] == [("evt_002", "lrn_002")]
    assert [row.event_id for row in rows if row.kind == "resolution_unquoted"] == ["evt_004"]

    # 11. After stop: nothing more is captured. A session end still makes one model call.
    capsys.readouterr()
    assert cli.main(["stop"]) == 0
    words = get_project_dir(PROJECT) / "words" / "host-1.jsonl"
    before = words.read_text()
    _prompt("one more message after the stop", tmp_path)
    _dialog({"Anything else?": "no"}, tmp_path)
    assert words.read_text() == before

    storage = learn_env(tmp_path, monkeypatch)
    second = await session_tools.create_session(storage, active, project=PROJECT)
    await _propose(storage, second, "Something for the second session.")
    await session_tools.end_session(storage, active, session_id=second.id)
    await extension_hooks.extract_if_available(PROJECT, second.id)
    assert len(client.requests) == 4
    assert len((get_project_dir(PROJECT) / "replay.jsonl").read_text().splitlines()) == 1
