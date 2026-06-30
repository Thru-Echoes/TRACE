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
PROMPT_REMINDER = _HOOKS / "prompt-reminder.sh"
PRETOOL_GUARD = _HOOKS / "pretool-guard.sh"


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
    runtime_dir: Path | None = None,
    env_overrides: dict[str, str] | None = None,
    stdin: str = "",
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "CLAUDE_PROJECT_DIR": str(project_dir),
        "TRACE_SESSIONS_DIR": str(sessions_dir),
    }
    if runtime_dir is not None:
        env["TRACE_RUNTIME_DIR"] = str(runtime_dir)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(script)],
        env=env,
        input=stdin,
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


# ── prompt-reminder.sh ────────────────────────────────────────────────────


class TestPromptReminder:
    def _setup(self, tmp_path: Path, project: str = "my-proj") -> tuple[Path, Path, Path]:
        """Return (project_dir, sessions_dir, runtime_dir) with project configured."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text(f'TRACE project name: "{project}"\n')
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        runtime = tmp_path / "runtime"
        return project_dir, sessions, runtime

    def test_silent_when_active_session_exists(self, tmp_path: Path) -> None:
        project_dir, sessions, runtime = self._setup(tmp_path)
        _make_session(sessions, session_id=f"trace_{_today()}_aaaaaa", project="my-proj", status="active")

        result = _run_hook(PROMPT_REMINDER, project_dir=project_dir, sessions_dir=sessions, runtime_dir=runtime)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_silent_for_first_two_turns_without_session(self, tmp_path: Path) -> None:
        project_dir, sessions, runtime = self._setup(tmp_path)
        # Default MIN_TURNS=3 → turns 1 and 2 should be silent
        for _ in range(2):
            result = _run_hook(PROMPT_REMINDER, project_dir=project_dir, sessions_dir=sessions, runtime_dir=runtime)
            assert result.stdout.strip() == "", f"unexpected nudge: {result.stdout!r}"

    def test_nudges_on_third_turn_without_session(self, tmp_path: Path) -> None:
        project_dir, sessions, runtime = self._setup(tmp_path)
        for _ in range(2):
            _run_hook(PROMPT_REMINDER, project_dir=project_dir, sessions_dir=sessions, runtime_dir=runtime)

        result = _run_hook(PROMPT_REMINDER, project_dir=project_dir, sessions_dir=sessions, runtime_dir=runtime)
        assert "TRACE:" in result.stdout
        assert "my-proj" in result.stdout

    def test_cooldown_suppresses_follow_up_nudges(self, tmp_path: Path) -> None:
        project_dir, sessions, runtime = self._setup(tmp_path)
        # First 3 turns, the 3rd nudges
        for _ in range(3):
            _run_hook(PROMPT_REMINDER, project_dir=project_dir, sessions_dir=sessions, runtime_dir=runtime)

        # 4th turn within cooldown should be silent (cooldown default 300s)
        result = _run_hook(PROMPT_REMINDER, project_dir=project_dir, sessions_dir=sessions, runtime_dir=runtime)
        assert result.stdout.strip() == ""

    def test_re_nudges_after_cooldown_expires(self, tmp_path: Path) -> None:
        project_dir, sessions, runtime = self._setup(tmp_path)
        # Use a cooldown of 0s so any subsequent turn re-nudges
        overrides = {"TRACE_PROMPT_COOLDOWN_SEC": "0"}
        for _ in range(3):
            _run_hook(
                PROMPT_REMINDER,
                project_dir=project_dir,
                sessions_dir=sessions,
                runtime_dir=runtime,
                env_overrides=overrides,
            )

        result = _run_hook(
            PROMPT_REMINDER,
            project_dir=project_dir,
            sessions_dir=sessions,
            runtime_dir=runtime,
            env_overrides=overrides,
        )
        assert "TRACE:" in result.stdout

    def test_state_resets_when_session_becomes_active(self, tmp_path: Path) -> None:
        project_dir, sessions, runtime = self._setup(tmp_path)
        # Accumulate 2 turns without a session
        for _ in range(2):
            _run_hook(PROMPT_REMINDER, project_dir=project_dir, sessions_dir=sessions, runtime_dir=runtime)

        state_file = runtime / "my-proj.state.json"
        assert state_file.is_file()
        assert json.loads(state_file.read_text())["turn_count"] == 2

        # Session becomes active → next invocation resets state
        _make_session(sessions, session_id=f"trace_{_today()}_bbbbbb", project="my-proj", status="active")
        _run_hook(PROMPT_REMINDER, project_dir=project_dir, sessions_dir=sessions, runtime_dir=runtime)

        assert json.loads(state_file.read_text())["turn_count"] == 0

    def test_respects_min_turns_override(self, tmp_path: Path) -> None:
        project_dir, sessions, runtime = self._setup(tmp_path)
        # MIN_TURNS=1 means even the first turn should nudge
        result = _run_hook(
            PROMPT_REMINDER,
            project_dir=project_dir,
            sessions_dir=sessions,
            runtime_dir=runtime,
            env_overrides={"TRACE_PROMPT_MIN_TURNS": "1"},
        )
        assert "TRACE:" in result.stdout

    def test_uses_sanitized_filename_for_state(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        # Project name with characters that need sanitizing
        (project_dir / "CLAUDE.md").write_text('TRACE project name: "my/weird name"\n')
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        runtime = tmp_path / "runtime"

        _run_hook(PROMPT_REMINDER, project_dir=project_dir, sessions_dir=sessions, runtime_dir=runtime)

        # Filename should have / and space replaced
        candidates = list(runtime.glob("*.state.json"))
        assert len(candidates) == 1
        assert "/" not in candidates[0].name
        assert " " not in candidates[0].name


# ── pretool-guard.sh ──────────────────────────────────────────────────────


class TestPreToolGuard:
    def _setup(self, tmp_path: Path) -> tuple[Path, Path]:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text('TRACE project name: "my-proj"\n')
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        return project_dir, sessions

    def test_soft_mode_silent_with_active_session(self, tmp_path: Path) -> None:
        project_dir, sessions = self._setup(tmp_path)
        _make_session(sessions, session_id=f"trace_{_today()}_aaaaaa", project="my-proj", status="active")

        result = _run_hook(PRETOOL_GUARD, project_dir=project_dir, sessions_dir=sessions)
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert result.stderr.strip() == ""

    def test_soft_mode_warns_without_blocking(self, tmp_path: Path) -> None:
        project_dir, sessions = self._setup(tmp_path)

        result = _run_hook(PRETOOL_GUARD, project_dir=project_dir, sessions_dir=sessions)
        assert result.returncode == 0, "soft mode must never block"
        assert "TRACE:" in result.stdout
        assert "my-proj" in result.stdout
        assert result.stderr.strip() == ""

    def test_strict_mode_blocks_without_session(self, tmp_path: Path) -> None:
        project_dir, sessions = self._setup(tmp_path)

        result = _run_hook(
            PRETOOL_GUARD,
            project_dir=project_dir,
            sessions_dir=sessions,
            env_overrides={"TRACE_GUARD": "strict"},
        )
        assert result.returncode == 2
        assert "TRACE:" in result.stderr
        assert "my-proj" in result.stderr

    def test_strict_mode_allows_with_active_session(self, tmp_path: Path) -> None:
        project_dir, sessions = self._setup(tmp_path)
        _make_session(sessions, session_id=f"trace_{_today()}_bbbbbb", project="my-proj", status="active")

        result = _run_hook(
            PRETOOL_GUARD,
            project_dir=project_dir,
            sessions_dir=sessions,
            env_overrides={"TRACE_GUARD": "strict"},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert result.stderr.strip() == ""

    def test_off_mode_is_silent_with_no_session(self, tmp_path: Path) -> None:
        project_dir, sessions = self._setup(tmp_path)

        result = _run_hook(
            PRETOOL_GUARD,
            project_dir=project_dir,
            sessions_dir=sessions,
            env_overrides={"TRACE_GUARD": "off"},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert result.stderr.strip() == ""

    def test_default_mode_is_soft(self, tmp_path: Path) -> None:
        """No TRACE_GUARD env var → behaves like soft mode (warn, don't block)."""
        project_dir, sessions = self._setup(tmp_path)

        # env_overrides is None → inherit from test env (which has TRACE_GUARD unset
        # thanks to the autouse fixture)
        result = _run_hook(PRETOOL_GUARD, project_dir=project_dir, sessions_dir=sessions)
        assert result.returncode == 0
        assert "TRACE:" in result.stdout

    def test_ignores_stdin_but_accepts_it(self, tmp_path: Path) -> None:
        """Claude Code passes JSON on stdin; guard should not choke on it."""
        project_dir, sessions = self._setup(tmp_path)
        stdin_payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "x.py"}})

        result = _run_hook(
            PRETOOL_GUARD,
            project_dir=project_dir,
            sessions_dir=sessions,
            stdin=stdin_payload,
        )
        assert result.returncode == 0


@pytest.fixture(autouse=True)
def _no_user_env_leak(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Don't let the developer's real ~/.trace/ sneak into test runs.

    TRACE_SESSIONS_DIR is re-pointed (not deleted): deleting it would suspend
    the suite-wide conftest isolation and fall back to the real
    ~/.trace/sessions for any code path that doesn't get an explicit dir.
    """
    monkeypatch.setenv("TRACE_SESSIONS_DIR", str(tmp_path / "hook-sessions"))
    monkeypatch.delenv("TRACE_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("TRACE_PROMPT_MIN_TURNS", raising=False)
    monkeypatch.delenv("TRACE_PROMPT_COOLDOWN_SEC", raising=False)
    monkeypatch.delenv("TRACE_GUARD", raising=False)
