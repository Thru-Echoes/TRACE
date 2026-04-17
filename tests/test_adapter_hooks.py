"""Integration tests for the bundled hook scripts.

These tests invoke the shell scripts with controlled env vars and temp
directories, so behavior is verified end-to-end including the inline Python
logic.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

_HOOKS = Path(__file__).parent.parent / "src" / "trace_mcp" / "adapters" / "claude_code" / "assets" / "hooks"
SESSION_REMINDER = _HOOKS / "session-reminder.sh"


def _today() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


def _make_session(
    sessions_dir: Path,
    *,
    session_id: str,
    project: str,
    status: str = "active",
) -> Path:
    data = {
        "id": session_id,
        "created": f"{datetime.now(UTC).isoformat()}",
        "status": status,
        "metadata": {"project": project},
        "events": [],
    }
    path = sessions_dir / f"{session_id}.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def _run_hook(
    script: Path,
    *,
    project_dir: Path,
    sessions_dir: Path,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "CLAUDE_PROJECT_DIR": str(project_dir),
        "TRACE_SESSIONS_DIR": str(sessions_dir),
    }
    return subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


# ── session-reminder.sh ───────────────────────────────────────────────────


class TestSessionReminderProjectAware:
    def test_silent_when_active_session_exists_for_project(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text('TRACE project name: "my-proj"\n')
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        _make_session(sessions, session_id=f"trace_{_today()}_aaaaaa", project="my-proj", status="active")

        result = _run_hook(SESSION_REMINDER, project_dir=project_dir, sessions_dir=sessions)
        assert result.returncode == 0
        assert result.stdout.strip() == "", f"expected silence, got {result.stdout!r}"

    def test_nudges_when_active_session_belongs_to_other_project(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text('TRACE project name: "my-proj"\n')
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        # Active session exists — but for a different project.
        _make_session(sessions, session_id=f"trace_{_today()}_bbbbbb", project="other-proj", status="active")

        result = _run_hook(SESSION_REMINDER, project_dir=project_dir, sessions_dir=sessions)
        assert result.returncode == 0
        assert "no active session" in result.stdout
        assert "my-proj" in result.stdout

    def test_nudges_when_session_matches_project_but_is_completed(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text('TRACE project name: "my-proj"\n')
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        _make_session(sessions, session_id=f"trace_{_today()}_cccccc", project="my-proj", status="completed")

        result = _run_hook(SESSION_REMINDER, project_dir=project_dir, sessions_dir=sessions)
        assert "no active session" in result.stdout
        assert "my-proj" in result.stdout

    def test_silent_when_sessions_dir_missing(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text('TRACE project name: "my-proj"\n')
        sessions = tmp_path / "does-not-exist"

        result = _run_hook(SESSION_REMINDER, project_dir=project_dir, sessions_dir=sessions)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_falls_back_to_git_basename(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "gitproj"
        project_dir.mkdir()
        subprocess.run(["git", "init"], cwd=project_dir, capture_output=True, check=True)
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        # No CLAUDE.md; git basename is "gitproj"
        _make_session(sessions, session_id=f"trace_{_today()}_dddddd", project="gitproj", status="active")

        result = _run_hook(SESSION_REMINDER, project_dir=project_dir, sessions_dir=sessions)
        assert result.stdout.strip() == ""

    def test_falls_back_to_cwd_basename(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "bare-dir"
        project_dir.mkdir()
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        # No CLAUDE.md, not a git repo; falls back to dir basename "bare-dir"
        _make_session(sessions, session_id=f"trace_{_today()}_eeeeee", project="bare-dir", status="active")

        result = _run_hook(SESSION_REMINDER, project_dir=project_dir, sessions_dir=sessions)
        assert result.stdout.strip() == ""

    def test_handles_malformed_session_file_without_failing(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text('TRACE project name: "my-proj"\n')
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        # A corrupt file mixed in with a good one
        (sessions / f"trace_{_today()}_ffffff.json").write_text("{not valid json")
        _make_session(sessions, session_id=f"trace_{_today()}_gggggg", project="my-proj", status="active")

        result = _run_hook(SESSION_REMINDER, project_dir=project_dir, sessions_dir=sessions)
        assert result.returncode == 0
        assert result.stdout.strip() == "", "corrupt file should not mask a valid active session"

    def test_ignores_sessions_from_other_days(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text('TRACE project name: "my-proj"\n')
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        # Session from a different day — file prefix doesn't match
        _make_session(sessions, session_id="trace_20200101_oldies", project="my-proj", status="active")

        result = _run_hook(SESSION_REMINDER, project_dir=project_dir, sessions_dir=sessions)
        assert "no active session" in result.stdout


@pytest.fixture(autouse=True)
def _no_user_env_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """Don't let the developer's real ~/.trace/ sneak into test runs."""
    monkeypatch.delenv("TRACE_SESSIONS_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
