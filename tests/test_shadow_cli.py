"""The ``trace-mcp shadow`` command."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from _shadow_helpers import ScriptedClient, learn_env, make_session, reply

from trace_mcp.extensions.learn import extraction
from trace_mcp.extensions.learn.shadow import cli


@pytest.fixture(autouse=True)
def _shadow_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "shadow"
    monkeypatch.setenv("TRACE_SHADOW_DIR", str(root))
    monkeypatch.setenv("TRACE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    return root


def test_start_then_start_again(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["start"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Capture period started ")
    assert 'It runs until you run "trace-mcp shadow stop".' in out
    assert cli.main(["start"]) == 2
    assert capsys.readouterr().err


def test_status_counts_days(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["start"])
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    assert "day 0" in capsys.readouterr().out


def test_exclude_and_include(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["start"])
    assert cli.main(["exclude", "acme"]) == 0
    assert "acme" in capsys.readouterr().out
    cli.main(["status"])
    assert "acme" in capsys.readouterr().out
    assert cli.main(["include", "acme"]) == 0
    assert "acme" not in capsys.readouterr().out


def test_stop_then_stop_again(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["start"])
    assert cli.main(["stop"]) == 0
    out = capsys.readouterr().out
    assert "Capture period stopped" in out and "after 0 day(s)." in out
    assert cli.main(["stop"]) == 2


def test_report_json_and_csv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["start"])
    capsys.readouterr()
    assert cli.main(["report", "--json"]) == 0
    assert "total" in json.loads(capsys.readouterr().out)
    sheet = tmp_path / "sheet.csv"
    assert cli.main(["report", "--csv", str(sheet)]) == 0
    assert sheet.exists()
    assert "0 review row(s)" in capsys.readouterr().out


def test_replay_prints_one_line_per_session_and_a_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    storage = learn_env(tmp_path, monkeypatch)
    asyncio.run(make_session(storage))
    answer = reply(("Upload retries are capped at three.", "evt_003"))
    monkeypatch.setattr(extraction, "AsyncOpenAI", lambda **_kw: ScriptedClient([answer]))

    assert cli.main(["replay", "--project", "collab-a", "--limit", "3"]) == 0
    out = capsys.readouterr().out
    assert "0 stored, nearest arm sent all; whole 120 prompt tokens, 1 new; nearest 120 prompt tokens, 1 new" in out
    assert (
        "Replayed 1 of 1 ended session(s) of collab-a: 0 already replayed, 0 with nothing to extract, 0 failed." in out
    )
    assert cli.main(["replay", "--project", "collab-a"]) == 0
    assert "Replayed 0 of 1 ended session(s) of collab-a: 1 already replayed" in capsys.readouterr().out
    cli.main(["status"])
    assert "replayed sessions 1" in capsys.readouterr().out


def test_replay_without_cloud_extraction_says_so_and_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    learn_env(tmp_path, monkeypatch, llm=False)
    assert cli.main(["replay", "--project", "collab-a"]) == 2
    assert "cloud extraction is off" in capsys.readouterr().err


def test_corrupt_state_fails_status(_shadow_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _shadow_root.mkdir(parents=True)
    (_shadow_root / "STATE.json").write_text("{not json")
    assert cli.main(["status"]) == 1
    assert capsys.readouterr().err


def test_the_server_entry_point_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    from trace_mcp import server

    monkeypatch.setattr(sys, "argv", ["trace-mcp", "shadow", "status"])
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 0
