"""Server identity polish (ADR-006 S3 deferred items + the require-pin hole).

Four behaviors: the honest bounded session brief (window-exhausted wording
instead of a false "no prior sessions"), session_brief as part of the storage
contract, the per-key scratchpad fallback, and TRACE_REQUIRE_PIN gating the
auto-create path it previously missed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from trace_mcp import project_identity as pident
from trace_mcp import server
from trace_mcp.schema import Session, SessionMetadata
from trace_mcp.storage.base import TraceStorage
from trace_mcp.storage.json_file import JsonFileStorage


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRACE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    monkeypatch.delenv("TRACE_PROJECT", raising=False)
    monkeypatch.delenv("TRACE_REQUIRE_PIN", raising=False)
    pident._reset_registry_cache()
    # server.storage is constructed at import time — env changes alone do not
    # move it, so the module global is swapped for a tmp-backed store.
    monkeypatch.setattr(server, "storage", JsonFileStorage(str(tmp_path / "sessions")))
    monkeypatch.setattr(server, "_current_session_id", None)
    return tmp_path


def _write_session_file(sessions: Path, session_id: str, project: str) -> None:
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{session_id}.json").write_text(
        json.dumps(
            {
                "id": session_id,
                "created": "2026-07-23T00:00:00Z",
                "status": "completed",
                "metadata": {"project": project},
                "events": [],
            }
        )
    )


# ── honest bounded session_brief ──────────────────────────────────────────


class TestHonestSessionBrief:
    async def test_match_beyond_the_old_25_file_window_is_found(self, _isolated: Path) -> None:
        """The defect: a project 26 files deep in a busy store read as absent."""
        sessions = _isolated / "sessions"
        # 30 newer sessions from OTHER projects bury ours.
        _write_session_file(sessions, "trace_20260101_ours00", "my-proj")
        for i in range(30):
            _write_session_file(sessions, f"trace_20260log2_{i:04d}", "noisy-neighbor")

        brief = await JsonFileStorage(str(sessions)).session_brief("my-proj")
        assert brief["matched"] == 1, "a session inside the read ceiling was missed"
        assert brief["most_recent"]["id"] == "trace_20260101_ours00"
        assert brief["window_exhausted"] is False

    async def test_window_exhaustion_is_reported_not_asserted_absent(self, _isolated: Path) -> None:
        sessions = _isolated / "sessions"
        _write_session_file(sessions, "trace_20260101_ours00", "my-proj")  # oldest
        for i in range(10):
            _write_session_file(sessions, f"trace_20260log2_{i:04d}", "noisy-neighbor")

        brief = await JsonFileStorage(str(sessions)).session_brief("my-proj", read_ceiling=5)
        assert brief["matched"] == 0
        assert brief["window_exhausted"] is True, "hitting the ceiling with files beyond it must be reported"
        assert brief["read_ceiling"] == 5

    async def test_scan_stops_at_the_match_cap(self, _isolated: Path) -> None:
        """Bounded in both directions: enough matches ends the scan early."""
        sessions = _isolated / "sessions"
        for i in range(40):
            _write_session_file(sessions, f"trace_20260log2_{i:04d}", "my-proj")

        brief = await JsonFileStorage(str(sessions)).session_brief("my-proj", scan_cap=3)
        assert brief["matched"] == 3
        assert brief["scanned"] < 40, "the scan kept reading after the match cap"
        assert brief["capped"] is True

    async def test_bootstrap_wording_for_an_exhausted_window(self, _isolated: Path) -> None:
        """End-to-end: the orientation line must never claim 'none exist' when
        the scan simply ran out of window."""
        sessions = _isolated / "sessions"
        for i in range(210):  # exceed the default 200-file read ceiling
            _write_session_file(sessions, f"trace_20260log2_{i:04d}", "noisy-neighbor")

        out = await server.trace_start_session(project="brand-new-proj", description="d")
        assert "No prior TRACE sessions" not in out, "asserted an absolute the scan cannot know"
        assert "older history not scanned" in out

    async def test_bootstrap_wording_for_a_truly_empty_store(self, _isolated: Path) -> None:
        """The absolute claim is still correct when the whole store was seen."""
        out = await server.trace_start_session(project="first-ever", description="d")
        assert "No prior TRACE sessions" in out

    async def test_brief_is_part_of_the_storage_contract(self) -> None:
        """A minimal non-file backend gets an honest default, not an AttributeError."""

        class _MinimalBackend(TraceStorage):
            async def create_session(self, session: Session) -> str:
                return session.id

            async def update_session(self, session: Session) -> None: ...

            async def get_session(self, session_id: str) -> Session:
                raise FileNotFoundError(session_id)

            async def list_sessions(self, project: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
                return [{"id": "trace_x", "project": project or "p"}]

            async def delete_session(self, session_id: str) -> None: ...

        brief = await _MinimalBackend().session_brief("p")
        assert brief["matched"] == 1
        assert brief["most_recent"]["id"] == "trace_x"
        assert "window_exhausted" in brief and "capped" in brief


# ── scratchpad per-key fallback ───────────────────────────────────────────


class TestScratchpadPerKeyFallback:
    def _session(self, project: str, project_key: str | None = None) -> Session:
        return Session(
            id="trace_20260723_pad001",
            metadata=SessionMetadata(project=project, project_key=project_key),
        )

    def test_global_fallback_is_keyed_per_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The shared fallback dir must not let project B clobber project A's buffer."""
        from trace_mcp import scratchpad

        monkeypatch.delenv("TRACE_SCRATCHPAD_DIR", raising=False)
        monkeypatch.chdir(tmp_path)  # no .claude/ here → global fallback path
        fallback = tmp_path / "trace-home" / "scratchpads"
        monkeypatch.setattr("os.path.expanduser", lambda p: str(fallback) if "scratchpads" in p else p)

        scratchpad.write_scratchpad(self._session("Alpha Project"))
        scratchpad.write_scratchpad(self._session("beta", project_key="beta"))

        names = sorted(p.name for p in fallback.glob("*.md"))
        assert names == ["alpha-project.md", "beta.md"], (
            f"projects share one fallback file (last writer clobbers): {names}"
        )

    def test_project_local_claude_dir_keeps_the_stable_filename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A checkout's .claude/ is per-project by construction — name unchanged."""
        from trace_mcp import scratchpad

        monkeypatch.delenv("TRACE_SCRATCHPAD_DIR", raising=False)
        (tmp_path / ".claude").mkdir()
        monkeypatch.chdir(tmp_path)

        path = scratchpad.write_scratchpad(self._session("Alpha Project"))
        assert path == tmp_path / ".claude" / "SCRATCHPAD.md"

    def test_env_override_keeps_the_stable_filename(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trace_mcp import scratchpad

        monkeypatch.setenv("TRACE_SCRATCHPAD_DIR", str(tmp_path / "override"))
        path = scratchpad.write_scratchpad(self._session("Alpha Project"))
        assert path.name == "SCRATCHPAD.md"


# ── TRACE_REQUIRE_PIN closes the auto-create path ─────────────────────────


class TestRequirePinGatesAutoCreate:
    async def test_unpinned_auto_create_fails_closed_under_require_pin(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The hole: require-pin previously gated only trace_start_session, so a
        stray unpinned process still auto-created sessions on its first logging
        call — the exact capture the operator opted to fail closed on."""
        monkeypatch.setenv("TRACE_REQUIRE_PIN", "1")

        result = await server.trace_log_annotation(category="observation", content="x")
        assert "Error" in result and "TRACE_REQUIRE_PIN" in result
        assert server._current_session_id is None
        assert not list((_isolated / "sessions").glob("trace_*.json")), "a session file was created anyway"

    async def test_pinned_auto_create_still_works_under_require_pin(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pident.locked_registry() as registry:
            registry.projects["waggle"] = pident.ProjectEntry(key="waggle", display_label="waggle")
        pident._reset_registry_cache()
        monkeypatch.setenv("TRACE_PROJECT", "waggle")
        monkeypatch.setenv("TRACE_REQUIRE_PIN", "1")

        result = await server.trace_log_annotation(category="observation", content="x")
        assert "Error" not in result
        assert server._current_session_id is not None
        session = await server.storage.get_session(server._current_session_id)
        assert session.metadata.project_key == "waggle"

    async def test_default_off_keeps_capture_over_attribution(self, _isolated: Path) -> None:
        """Without the flag, unpinned auto-create still works — the flag is an
        explicit opt-out from the default capture-first posture, not a new default."""
        result = await server.trace_log_annotation(category="observation", content="x")
        assert "Error" not in result
        assert server._current_session_id is not None

    async def test_explicit_session_still_usable_under_require_pin(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The gate closes CREATION, not use: logging into an existing session by
        explicit id must keep working on an unpinned require-pin process."""
        result = await server.trace_start_session(project="waggle", description="d")
        assert "Error" not in result
        session_id = result.split("Session: ")[1].split("\n")[0].strip()
        server._current_session_id = None

        monkeypatch.setenv("TRACE_REQUIRE_PIN", "1")
        out = await server.trace_log_annotation(category="observation", content="x", session_id=session_id)
        assert "Error" not in out
