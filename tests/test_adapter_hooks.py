"""Integration tests for the bundled hook scripts.

These tests invoke the shell scripts with controlled env vars and temp
directories, so behavior is verified end-to-end including the inline Python
logic.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
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
    project_key: str | None = None,
) -> Path:
    metadata: dict[str, object] = {"project": project}
    if project_key is not None:
        metadata["project_key"] = project_key
    data = {
        "id": session_id,
        "created": f"{datetime.now(UTC).isoformat()}",
        "status": status,
        "metadata": metadata,
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


# ── canonical project identity (shared detection block) ───────────────────


class TestCanonicalProjectIdentity:
    """Identity is a canonical key, so labels that differ only cosmetically match.

    Each test drives a real hook end to end: what is asserted is what the
    shipped asset does, not a re-implementation of it.
    """

    def test_bolded_claude_md_pin_line_is_detected(self, tmp_path: Path) -> None:
        """The verified root cause of a live drift pair.

        A plain-only regex skipped the bolded marker, so the hooks fell through
        to the git basename while a model reading the same file saw the marker's
        value — deterministically minting two labels for one repository.
        """
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        subprocess.run(["git", "init"], cwd=project_dir, capture_output=True, check=True)
        (project_dir / "CLAUDE.md").write_text('> **TRACE project name**: "pinned-name"\n')
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        _make_session(sessions, session_id=f"trace_{_today()}_aaaaaa", project="pinned-name")

        result = _run_hook(SESSION_REMINDER, project_dir=project_dir, sessions_dir=sessions)
        assert result.stdout.strip() == "", (
            "bolded pin line was not detected; the hook fell through to the git basename"
        )

    def test_hooks_prefer_pin_file_over_claude_md(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        (project_dir / ".claude").mkdir(parents=True)
        (project_dir / "CLAUDE.md").write_text('TRACE project name: "md-name"\n')
        (project_dir / ".claude" / "trace.project").write_text("pin-name\n")
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        # An active session for the CLAUDE.md name must NOT satisfy the pin.
        _make_session(sessions, session_id=f"trace_{_today()}_aaaaaa", project="md-name")

        result = _run_hook(SESSION_REMINDER, project_dir=project_dir, sessions_dir=sessions)
        assert "no active session" in result.stdout
        assert "pin-name" in result.stdout

        # ...and one for the pin name does.
        _make_session(sessions, session_id=f"trace_{_today()}_bbbbbb", project="pin-name")
        result = _run_hook(SESSION_REMINDER, project_dir=project_dir, sessions_dir=sessions)
        assert result.stdout.strip() == ""

    def test_hooks_match_project_key_sessions(self, tmp_path: Path) -> None:
        """A stamped project_key is authoritative over the display label."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text('TRACE project name: "my-proj"\n')
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        _make_session(
            sessions,
            session_id=f"trace_{_today()}_aaaaaa",
            project="Some Legacy Display Label",
            project_key="my-proj",
        )

        result = _run_hook(SESSION_REMINDER, project_dir=project_dir, sessions_dir=sessions)
        assert result.stdout.strip() == "", "session with a matching project_key was not matched"

    def test_hooks_fall_back_to_canonicalized_label_for_legacy_sessions(self, tmp_path: Path) -> None:
        """Legacy sessions carry no key, so the label is canonicalized to match."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text('TRACE project name: "my-proj"\n')
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        # Case and separator variants of the same project.
        _make_session(sessions, session_id=f"trace_{_today()}_aaaaaa", project="My_Proj")

        result = _run_hook(SESSION_REMINDER, project_dir=project_dir, sessions_dir=sessions)
        assert result.stdout.strip() == "", "a display-label variant split one project in two"

    def test_distinct_projects_still_do_not_match(self, tmp_path: Path) -> None:
        """Canonicalization must not over-merge: a different project is still different."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text('TRACE project name: "my-proj"\n')
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        _make_session(sessions, session_id=f"trace_{_today()}_aaaaaa", project="my-proj-two")

        result = _run_hook(SESSION_REMINDER, project_dir=project_dir, sessions_dir=sessions)
        assert "no active session" in result.stdout

    def test_pretool_guard_strict_mode_still_finds_sessions_post_change(self, tmp_path: Path) -> None:
        """Regression: strict mode must not hard-block on a session it should match.

        Strict mode exits 2, which blocks the edit outright — a detection change
        that stopped matching valid sessions would turn this guard into a wall.
        """
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text('> **TRACE project name**: "my-proj"\n')
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        _make_session(sessions, session_id=f"trace_{_today()}_aaaaaa", project="My-Proj")

        result = _run_hook(
            PRETOOL_GUARD,
            project_dir=project_dir,
            sessions_dir=sessions,
            env_overrides={"TRACE_GUARD": "strict"},
        )
        assert result.returncode == 0, f"strict mode blocked despite an active session: {result.stderr!r}"
        assert result.stdout.strip() == ""

    def test_prompt_reminder_state_file_is_named_by_canonical_key(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text('TRACE project name: "My Weird/Name"\n')
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        runtime = tmp_path / "runtime"

        _run_hook(PROMPT_REMINDER, project_dir=project_dir, sessions_dir=sessions, runtime_dir=runtime)

        assert [p.name for p in runtime.glob("*.state.json")] == ["my-weird-name.state.json"]

    @pytest.mark.parametrize(
        ("script", "needs_runtime"),
        [(SESSION_REMINDER, False), (PROMPT_REMINDER, True), (PRETOOL_GUARD, False)],
    )
    def test_hooks_emit_version_stamp(self, tmp_path: Path, script: Path, needs_runtime: bool) -> None:
        """A stale fleet copy self-identifies by the absence of this stamp."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text('TRACE project name: "my-proj"\n')
        sessions = tmp_path / "sessions"
        sessions.mkdir()

        result = _run_hook(
            script,
            project_dir=project_dir,
            sessions_dir=sessions,
            runtime_dir=tmp_path / "runtime" if needs_runtime else None,
            env_overrides={"TRACE_PROMPT_MIN_TURNS": "1"} if needs_runtime else None,
        )
        assert "[trace-hooks v0.6]" in result.stdout


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


# ── shadow period: the person's words and the day-30 reminder (ADR 008) ───

DIALOG_WORDS = _HOOKS / "dialog-words.sh"
_REMINDER_TAIL = (
    'Tell the user: run "trace-mcp shadow report", then "trace-mcp shadow stop" to end it. '
    "It does not stop by itself. [trace-hooks v0.6]"
)


class _ShadowEnv:
    """A temp project pinned to ``acme`` plus shadow, sessions and runtime dirs."""

    def __init__(self, tmp_path: Path) -> None:
        self.project = tmp_path / "proj"
        (self.project / ".claude").mkdir(parents=True)
        (self.project / ".claude" / "trace.project").write_text("acme\n")
        self.shadow = tmp_path / "shadow"
        self.sessions = tmp_path / "sessions"
        self.runtime = tmp_path / "runtime"
        self.words = self.shadow / "acme" / "words"

    def state(self, *, started: datetime | None = None, stopped: datetime | None = None, **extra: object) -> Path:
        self.shadow.mkdir(parents=True, exist_ok=True)
        data: dict[str, object] = {
            "version": 1,
            "started_at": (started or datetime(2026, 11, 1, tzinfo=UTC)).isoformat(),
            "stopped_at": stopped.isoformat() if stopped else None,
            "excluded": [],
        }
        data.update(extra)
        path = self.shadow / "STATE.json"
        path.write_text(json.dumps(data))
        return path

    def run(self, script: Path, stdin: str, runtime: Path | None = None) -> subprocess.CompletedProcess[str]:
        return _run_hook(
            script,
            project_dir=self.project,
            sessions_dir=self.sessions,
            runtime_dir=runtime or self.runtime,
            env_overrides={"TRACE_SHADOW_DIR": str(self.shadow)},
            stdin=stdin,
        )

    def lines(self, name: str = "host-1.jsonl") -> list[dict]:
        path = self.words / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def word_files(self) -> list[Path]:
        return sorted(self.shadow.rglob("*.jsonl")) if self.shadow.exists() else []


def _prompt_input(prompt: object = "hello there", **extra: object) -> str:
    data: dict[str, object] = {"session_id": "host-1", "transcript_path": "/tmp/t/host-1.jsonl", "prompt": prompt}
    data.update(extra)
    return json.dumps(data)


class TestPromptWordsCapture:
    def test_running_trial_records_the_prompt_byte_for_byte(self, tmp_path: Path) -> None:
        env = _ShadowEnv(tmp_path)
        env.state()
        prompt = "Use the  Paris office\nfor the launch"
        result = env.run(PROMPT_REMINDER, _prompt_input(prompt))
        assert result.returncode == 0
        lines = env.lines()
        assert len(lines) == 1
        line = lines[0]
        assert line["text"] == prompt
        assert line["kind"] == "prompt"
        assert line["host_session_id"] == "host-1"
        assert line["transcript"] == "/tmp/t/host-1.jsonl"
        assert datetime.fromisoformat(line["ts"]).tzinfo is not None
        assert (env.words / "host-1.jsonl").stat().st_mode & 0o777 == 0o600

    def test_missing_transcript_path_records_null(self, tmp_path: Path) -> None:
        env = _ShadowEnv(tmp_path)
        env.state()
        env.run(PROMPT_REMINDER, json.dumps({"session_id": "host-1", "prompt": "a prompt with no transcript"}))
        assert env.lines()[0]["transcript"] is None

    def test_capture_adds_no_output(self, tmp_path: Path) -> None:
        env = _ShadowEnv(tmp_path)
        overrides = {"TRACE_PROMPT_MIN_TURNS": "1"}
        without = _run_hook(
            PROMPT_REMINDER,
            project_dir=env.project,
            sessions_dir=env.sessions,
            runtime_dir=tmp_path / "rt-a",
            env_overrides={**overrides, "TRACE_SHADOW_DIR": str(env.shadow)},
            stdin=_prompt_input(),
        )
        env.state()
        with_capture = _run_hook(
            PROMPT_REMINDER,
            project_dir=env.project,
            sessions_dir=env.sessions,
            runtime_dir=tmp_path / "rt-b",
            env_overrides={**overrides, "TRACE_SHADOW_DIR": str(env.shadow)},
            stdin=_prompt_input(),
        )
        assert len(env.lines()) == 1
        assert with_capture.stdout == without.stdout
        assert "[trace-hooks v0.6]" in with_capture.stdout

    @pytest.mark.parametrize(
        "prompt",
        [
            "<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n</task-notification>",
            "  <system-reminder>\nnote\n</system-reminder>",
        ],
    )
    def test_host_notices_are_not_recorded(self, tmp_path: Path, prompt: str) -> None:
        env = _ShadowEnv(tmp_path)
        env.state()
        result = env.run(PROMPT_REMINDER, _prompt_input(prompt))
        assert result.returncode == 0
        assert env.word_files() == []

    @pytest.mark.parametrize(
        "prompt",
        [
            "<div> is the tag I mean",
            "/deploy staging",
            '<pasted_content id="ab12">\nthree pasted lines\n</pasted_content id="ab12">\nuse this',
        ],
    )
    def test_lookalike_prompts_are_recorded(self, tmp_path: Path, prompt: str) -> None:
        env = _ShadowEnv(tmp_path)
        env.state()
        env.run(PROMPT_REMINDER, _prompt_input(prompt))
        lines = env.lines()
        assert len(lines) == 1
        assert lines[0]["text"] == prompt

    def test_no_state_file_records_nothing(self, tmp_path: Path) -> None:
        env = _ShadowEnv(tmp_path)
        result = env.run(PROMPT_REMINDER, _prompt_input())
        assert result.returncode == 0
        assert not env.shadow.exists() or not list(env.shadow.rglob("words"))

    @pytest.mark.parametrize("stopped", [True, False])
    def test_stopped_or_excluded_records_nothing(self, tmp_path: Path, stopped: bool) -> None:
        env = _ShadowEnv(tmp_path)
        if stopped:
            env.state(stopped=datetime(2026, 11, 20, tzinfo=UTC))
        else:
            env.state(excluded=["acme"])
        env.run(PROMPT_REMINDER, _prompt_input())
        assert env.word_files() == []

    @pytest.mark.parametrize("stdin", ["not json", '{"session_id": "h"}', ""])
    def test_bad_input_exits_zero_and_records_nothing(self, tmp_path: Path, stdin: str) -> None:
        env = _ShadowEnv(tmp_path)
        env.state()
        result = env.run(PROMPT_REMINDER, stdin)
        assert result.returncode == 0
        assert env.word_files() == []

    def test_huge_prompt_is_recorded_quickly(self, tmp_path: Path) -> None:
        import time

        env = _ShadowEnv(tmp_path)
        env.state()
        prompt = "x" * 2_000_000
        began = time.monotonic()
        result = env.run(PROMPT_REMINDER, _prompt_input(prompt))
        elapsed = time.monotonic() - began
        assert result.returncode == 0
        lines = env.lines()
        assert len(lines) == 1 and len(lines[0]["text"]) == 2_000_000
        assert elapsed < 5

    def test_session_id_cannot_leave_the_words_directory(self, tmp_path: Path) -> None:
        env = _ShadowEnv(tmp_path)
        env.state()
        env.run(PROMPT_REMINDER, _prompt_input(session_id="../../evil"))
        files = env.word_files()
        assert [f.name for f in files] == ["______evil.jsonl"]
        assert files[0].resolve().parent == env.words.resolve()

    def test_corrupt_state_file_records_nothing(self, tmp_path: Path) -> None:
        env = _ShadowEnv(tmp_path)
        env.shadow.mkdir(parents=True)
        (env.shadow / "STATE.json").write_text("{not json")
        result = env.run(PROMPT_REMINDER, _prompt_input())
        assert result.returncode == 0
        assert env.word_files() == []


_DIALOG = {
    "hook_event_name": "PostToolUse",
    "tool_name": "AskUserQuestion",
    "session_id": "host-1",
    "transcript_path": "/tmp/t/host-1.jsonl",
    "tool_response": {
        "questions": [],
        "answers": {"Which cache?": "Use the local cache (Recommended)", "Anything else?": "also keep the old key"},
        "annotations": {"Which cache?": {"notes": "only for staging", "preview": "cache: local"}},
    },
}


def _dialog(**changes: object) -> str:
    data = json.loads(json.dumps(_DIALOG))
    response_changes = changes.pop("tool_response_changes", None)
    data.update(changes)
    if isinstance(response_changes, dict):
        for k, v in response_changes.items():
            if v is _ABSENT:
                data["tool_response"].pop(k, None)
            else:
                data["tool_response"][k] = v
    return json.dumps(data)


_ABSENT = object()


class TestDialogWordsCapture:
    def _assert_silent(self, result: subprocess.CompletedProcess[str]) -> None:
        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_running_trial_records_answers_then_notes(self, tmp_path: Path) -> None:
        env = _ShadowEnv(tmp_path)
        env.state()
        result = env.run(DIALOG_WORDS, _dialog())
        self._assert_silent(result)
        lines = env.lines()
        assert [(x["kind"], x["question"], x["text"]) for x in lines] == [
            ("dialog_answer", "Which cache?", "Use the local cache (Recommended)"),
            ("dialog_answer", "Anything else?", "also keep the old key"),
            ("dialog_note", "Which cache?", "only for staging"),
        ]
        assert len({x["ts"] for x in lines}) == 1
        assert all(x["host_session_id"] == "host-1" for x in lines)
        assert "cache: local" not in (env.words / "host-1.jsonl").read_text()
        assert (env.words / "host-1.jsonl").stat().st_mode & 0o777 == 0o600

    @pytest.mark.parametrize("annotations", [{}, _ABSENT])
    def test_without_notes_only_answers(self, tmp_path: Path, annotations: object) -> None:
        env = _ShadowEnv(tmp_path)
        env.state()
        self._assert_silent(env.run(DIALOG_WORDS, _dialog(tool_response_changes={"annotations": annotations})))
        assert [x["kind"] for x in env.lines()] == ["dialog_answer", "dialog_answer"]

    def test_other_tools_are_ignored(self, tmp_path: Path) -> None:
        env = _ShadowEnv(tmp_path)
        env.state()
        self._assert_silent(env.run(DIALOG_WORDS, _dialog(tool_name="Bash")))
        assert env.word_files() == []

    @pytest.mark.parametrize(
        "stdin",
        [
            _dialog(tool_response="a string"),
            json.dumps({k: v for k, v in _DIALOG.items() if k != "tool_response"}),
            _dialog(tool_response_changes={"answers": ["a", "list"]}),
        ],
    )
    def test_malformed_response_records_nothing(self, tmp_path: Path, stdin: str) -> None:
        env = _ShadowEnv(tmp_path)
        env.state()
        self._assert_silent(env.run(DIALOG_WORDS, stdin))
        assert env.word_files() == []

    def test_non_string_or_empty_answers_are_skipped(self, tmp_path: Path) -> None:
        env = _ShadowEnv(tmp_path)
        env.state()
        answers = {"Q1": 3, "Q2": "", "Q3": "kept"}
        stdin = _dialog(tool_response_changes={"answers": answers, "annotations": {}})
        self._assert_silent(env.run(DIALOG_WORDS, stdin))
        assert [(x["question"], x["text"]) for x in env.lines()] == [("Q3", "kept")]

    @pytest.mark.parametrize("case", ["no-state", "stopped", "excluded"])
    def test_trial_off_records_nothing(self, tmp_path: Path, case: str) -> None:
        env = _ShadowEnv(tmp_path)
        if case == "stopped":
            env.state(stopped=datetime(2026, 11, 20, tzinfo=UTC))
        elif case == "excluded":
            env.state(excluded=["acme"])
        self._assert_silent(env.run(DIALOG_WORDS, _dialog()))
        assert env.word_files() == []

    @pytest.mark.parametrize("stdin", ["not json", ""])
    def test_bad_input_records_nothing(self, tmp_path: Path, stdin: str) -> None:
        env = _ShadowEnv(tmp_path)
        env.state()
        self._assert_silent(env.run(DIALOG_WORDS, stdin))
        assert env.word_files() == []

    def test_session_id_cannot_leave_the_words_directory(self, tmp_path: Path) -> None:
        env = _ShadowEnv(tmp_path)
        env.state()
        self._assert_silent(env.run(DIALOG_WORDS, _dialog(session_id="../../evil")))
        files = env.word_files()
        assert [f.name for f in files] == ["______evil.jsonl"]
        assert files[0].resolve().parent == env.words.resolve()

    def test_prompt_and_dialog_share_one_file(self, tmp_path: Path) -> None:
        env = _ShadowEnv(tmp_path)
        env.state()
        env.run(PROMPT_REMINDER, _prompt_input("which cache should we use"))
        env.run(DIALOG_WORDS, _dialog())
        assert {x["kind"] for x in env.lines()} == {"prompt", "dialog_answer", "dialog_note"}


class TestShadowReminder:
    def _run(self, env: _ShadowEnv) -> subprocess.CompletedProcess[str]:
        env.sessions.mkdir(exist_ok=True)
        return _run_hook(
            SESSION_REMINDER,
            project_dir=env.project,
            sessions_dir=env.sessions,
            env_overrides={"TRACE_SHADOW_DIR": str(env.shadow)},
        )

    @staticmethod
    def _days_ago(days: int) -> datetime:
        return datetime.now(UTC) - timedelta(days=days, minutes=5)

    def test_day_31_prints_the_reminder(self, tmp_path: Path) -> None:
        env = _ShadowEnv(tmp_path)
        started = self._days_ago(31)
        env.state(started=started)
        result = self._run(env)
        assert result.returncode == 0
        expected = f"TRACE shadow trial has been running since {started.date().isoformat()} (day 31). " + _REMINDER_TAIL
        assert expected in result.stdout

    def test_at_most_once_a_day(self, tmp_path: Path) -> None:
        env = _ShadowEnv(tmp_path)
        env.state(started=self._days_ago(31))
        assert "shadow trial" in self._run(env).stdout
        assert "shadow trial" not in self._run(env).stdout

    @pytest.mark.parametrize(("days", "stopped"), [(29, False), (40, True)])
    def test_no_reminder_before_day_30_or_after_stop(self, tmp_path: Path, days: int, stopped: bool) -> None:
        env = _ShadowEnv(tmp_path)
        env.state(started=self._days_ago(days), stopped=self._days_ago(1) if stopped else None)
        assert "shadow trial" not in self._run(env).stdout

    def test_corrupt_state_prints_no_reminder(self, tmp_path: Path) -> None:
        env = _ShadowEnv(tmp_path)
        env.shadow.mkdir(parents=True)
        (env.shadow / "STATE.json").write_text("{not json")
        result = self._run(env)
        assert result.returncode == 0
        assert "shadow trial" not in result.stdout

    def test_state_file_is_never_written(self, tmp_path: Path) -> None:
        env = _ShadowEnv(tmp_path)
        path = env.state(started=self._days_ago(31))
        before = path.read_bytes()
        self._run(env)
        self._run(env)
        assert path.read_bytes() == before

    def test_reminder_printed_without_a_sessions_directory(self, tmp_path: Path) -> None:
        env = _ShadowEnv(tmp_path)
        env.state(started=self._days_ago(31))
        result = _run_hook(
            SESSION_REMINDER,
            project_dir=env.project,
            sessions_dir=tmp_path / "no-such-dir",
            env_overrides={"TRACE_SHADOW_DIR": str(env.shadow)},
        )
        assert "(day 31)" in result.stdout

    def test_pydantic_z_suffix_is_understood(self, tmp_path: Path) -> None:
        """The CLI writes STATE.json through Pydantic, which serializes UTC as a trailing Z."""
        env = _ShadowEnv(tmp_path)
        env.shadow.mkdir(parents=True)
        started = self._days_ago(31).replace(microsecond=0)
        stamp = started.strftime("%Y-%m-%dT%H:%M:%SZ")
        (env.shadow / "STATE.json").write_text(json.dumps({"started_at": stamp, "stopped_at": None, "excluded": []}))
        assert "(day 31)" in self._run(env).stdout
