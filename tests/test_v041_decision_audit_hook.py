"""E2E tests for the v0.4.1 decision-audit.sh hook script.

Critical test: the hook must run on macOS bash 3.2 (the default
/bin/bash on macOS; Apple's GPLv3 transition froze bash at 3.2.57).
The v0.4.1 rewrite originally used `mapfile` (bash 4+) which silently
crashes on macOS. This test executes the actual hook against synthetic
session JSONs to ensure it produces correct output AND runs on the
minimum-supported bash.

Real-data, real-subprocess, fail-loudly. No mocks.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

HOOK_PATH = (
    Path(__file__).parent.parent
    / "src"
    / "trace_mcp"
    / "adapters"
    / "claude_code"
    / "assets"
    / "hooks"
    / "decision-audit.sh"
)


def _write_session(dir_path: Path, name: str, session: dict) -> Path:
    """Write a session JSON to the sessions dir and return its path."""
    path = dir_path / f"{name}.json"
    path.write_text(json.dumps(session, indent=2))
    return path


def _run_hook(sessions_dir: Path, bash_path: str = "/bin/bash") -> subprocess.CompletedProcess:
    """Invoke the hook against the given sessions dir using a specific bash.

    Defaults to /bin/bash, which on macOS is bash 3.2 — the minimum
    bash version we support. If the test passes on /bin/bash, it
    passes on every Linux bash 4+ trivially.
    """
    return subprocess.run(
        [bash_path, str(HOOK_PATH)],
        env={"TRACE_SESSIONS_DIR": str(sessions_dir), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=15,
    )


class TestHookExecutesUnderMacOSBash:
    """Regression guards against bash-version-specific syntax slipping in.

    These tests caught a real regression: the v0.4.1 rewrite of
    decision-audit.sh originally used `mapfile -t METRICS < <(python3 ...)`,
    a bash 4+ construct. On macOS (bash 3.2), the parser failed and the
    hook silently emitted no output — defeating the entire v0.4.1
    client-side audit visibility goal.
    """

    def test_hook_runs_on_default_bin_bash(self, tmp_path: Path) -> None:
        """Smoke test: hook executes without 'bad substitution' errors on /bin/bash."""
        # Empty sessions dir — hook should silently exit 0
        result = _run_hook(tmp_path)
        assert result.returncode == 0
        # Critical: stderr must not contain shell parse errors
        assert "bad substitution" not in result.stderr.lower()
        assert "syntax error" not in result.stderr.lower()
        assert "command not found" not in result.stderr.lower()

    def test_hook_handles_session_with_human_self_resolution(self, tmp_path: Path) -> None:
        """v0.4.1: same-instance human→human self-resolution must be flagged.

        Previously this was silently allowed (v0.3 FM1 was ai→ai only).
        """
        session = {
            "id": "trace_smoke",
            "trace_version": "0.4.1",
            "metadata": {"project": "smoke"},
            "events": [
                {
                    "id": "evt_001",
                    "type": "decision",
                    "actor": {"type": "human", "id": "researcher"},
                    "decision": {
                        "description": "lower threshold",
                        "proposed_by": {"type": "human", "id": "researcher"},
                        "disposition": "accepted",
                        "resolved_by": {"type": "human", "id": "researcher"},
                    },
                },
            ],
        }
        _write_session(tmp_path, "trace_human_self", session)

        result = _run_hook(tmp_path)
        assert result.returncode == 0
        assert "same-instance self-resolution" in result.stdout.lower(), (
            f"expected same-instance warning, got: {result.stdout!r}"
        )

    def test_hook_handles_missing_snippet_on_contribution(self, tmp_path: Path) -> None:
        """v0.4.1 §3.4.1: contributions missing conversation_snippet must be flagged."""
        session = {
            "id": "trace_smoke",
            "trace_version": "0.4.1",
            "metadata": {"project": "smoke"},
            "events": [
                {
                    "id": "evt_001",
                    "type": "contribution",
                    "actor": {"type": "ai", "id": "claude"},
                    "contribution": {
                        "description": "produced an artifact",
                        "direction": "human",
                        "execution": "ai",
                    },
                    # NO context with conversation_snippet
                },
            ],
        }
        _write_session(tmp_path, "trace_missing_snip", session)

        result = _run_hook(tmp_path)
        assert result.returncode == 0
        assert "missing conversation_snippet" in result.stdout.lower(), (
            f"expected missing-snippet warning, got: {result.stdout!r}"
        )

    def test_hook_handles_orphan_correction(self, tmp_path: Path) -> None:
        """v0.3 + v0.4.1: corrections without corrects_event_ids must be flagged."""
        session = {
            "id": "trace_smoke",
            "trace_version": "0.4.1",
            "metadata": {"project": "smoke"},
            "events": [
                {
                    "id": "evt_001",
                    "type": "annotation",
                    "actor": {"type": "human", "id": "r"},
                    "annotation": {
                        "category": "correction",
                        "content": "x was wrong",
                        "corrects_event_ids": [],
                    },
                },
            ],
        }
        _write_session(tmp_path, "trace_orphan", session)

        result = _run_hook(tmp_path)
        assert result.returncode == 0
        assert "orphaned correction" in result.stdout.lower()

    def test_hook_handles_unresolved_decision(self, tmp_path: Path) -> None:
        """v0.3 + v0.4.1: decisions stuck in 'proposed' state must be flagged."""
        session = {
            "id": "trace_smoke",
            "trace_version": "0.4.1",
            "metadata": {"project": "smoke"},
            "events": [
                {
                    "id": "evt_001",
                    "type": "decision",
                    "actor": {"type": "human", "id": "r"},
                    "decision": {
                        "description": "x",
                        "proposed_by": {"type": "human", "id": "r"},
                        "disposition": "proposed",
                    },
                },
            ],
        }
        _write_session(tmp_path, "trace_unresolved", session)

        result = _run_hook(tmp_path)
        assert result.returncode == 0
        assert "unresolved decision" in result.stdout.lower()

    def test_hook_handles_explicit_absence_marker_correctly(self, tmp_path: Path) -> None:
        """v0.4.1: <autonomous-stretch> marker must NOT count as missing."""
        session = {
            "id": "trace_smoke",
            "trace_version": "0.4.1",
            "metadata": {"project": "smoke"},
            "events": [
                {
                    "id": "evt_001",
                    "type": "contribution",
                    "actor": {"type": "ai", "id": "c"},
                    "contribution": {
                        "description": "x",
                        "direction": "human",
                        "execution": "ai",
                    },
                    "context": {"conversation_snippet": "<autonomous-stretch>"},
                },
            ],
        }
        _write_session(tmp_path, "trace_marker", session)

        result = _run_hook(tmp_path)
        assert result.returncode == 0
        # Explicit absence marker → no missing-snippet warning
        assert "missing conversation_snippet" not in result.stdout.lower()

    def test_hook_handles_empty_string_snippet_as_missing(self, tmp_path: Path) -> None:
        """v0.4.1 amendment: empty/whitespace-only snippet is treated as missing.

        Closes a silent-bypass path: a producer setting `conversation_snippet=""`
        previously passed the MUST check because the existing code only
        guarded against `snippet is None`.
        """
        session = {
            "id": "trace_smoke",
            "trace_version": "0.4.1",
            "metadata": {"project": "smoke"},
            "events": [
                {
                    "id": "evt_001",
                    "type": "contribution",
                    "actor": {"type": "ai", "id": "c"},
                    "contribution": {
                        "description": "x",
                        "direction": "human",
                        "execution": "ai",
                    },
                    "context": {"conversation_snippet": "   "},
                },
            ],
        }
        _write_session(tmp_path, "trace_empty_snip", session)

        result = _run_hook(tmp_path)
        assert result.returncode == 0
        # Whitespace-only is no better than missing
        assert "missing conversation_snippet" in result.stdout.lower()

    def test_hook_handles_pre_v041_session_cleanly(self, tmp_path: Path) -> None:
        """Backward compat: v0.3-format session (with environment.trace_version)
        must load and produce v0.3-appropriate warnings."""
        session = {
            "id": "trace_v3",
            "trace_version": "0.3.0",
            "metadata": {
                "project": "old",
                "environment": {"trace_version": "0.3.0", "mcp_servers": []},
            },
            "events": [
                {
                    "id": "evt_001",
                    "type": "decision",
                    "actor": {"type": "ai", "id": "claude"},
                    "decision": {
                        "description": "x",
                        "proposed_by": {"type": "ai", "id": "claude"},
                        "disposition": "accepted",
                        "resolved_by": {"type": "ai", "id": "claude"},
                    },
                },
            ],
        }
        _write_session(tmp_path, "trace_v3", session)

        result = _run_hook(tmp_path)
        assert result.returncode == 0
        # ai→ai self-resolution under both v0.3 (ai-only check) and v0.4.1
        # (same-instance check) — backward-compat message preserves the v0.3 text
        assert "AI self-resolution" in result.stdout

    def test_hook_silent_on_clean_session(self, tmp_path: Path) -> None:
        """No warnings → no output (silent success)."""
        session = {
            "id": "trace_clean",
            "trace_version": "0.4.1",
            "metadata": {"project": "clean"},
            "events": [
                {
                    "id": "evt_001",
                    "type": "decision",
                    "actor": {"type": "ai", "id": "claude"},
                    "decision": {
                        "description": "x",
                        "proposed_by": {"type": "ai", "id": "claude"},
                        "disposition": "accepted",
                        "resolved_by": {"type": "human", "id": "researcher"},
                    },
                    "context": {"conversation_snippet": "do that"},
                },
            ],
        }
        _write_session(tmp_path, "trace_clean", session)

        result = _run_hook(tmp_path)
        assert result.returncode == 0
        # No TRACE Decision Audit prefix → no warnings to report
        assert "TRACE Decision Audit" not in result.stdout

    def test_hook_silent_on_empty_sessions_dir(self, tmp_path: Path) -> None:
        """No session files → silent exit, no errors."""
        result = _run_hook(tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""
        assert "bad substitution" not in result.stderr.lower()

    def test_hook_silent_on_malformed_session_json(self, tmp_path: Path) -> None:
        """Malformed JSON → fail-open (no crash, no false warnings)."""
        (tmp_path / "trace_bad.json").write_text("{not valid json")

        result = _run_hook(tmp_path)
        assert result.returncode == 0
        # Failed parse → zero metrics → no audit output
        assert "TRACE Decision Audit" not in result.stdout
