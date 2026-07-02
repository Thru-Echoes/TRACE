"""Suite-wide isolation from the developer's real ~/.trace data.

The root conftest must point TRACE_KNOWLEDGE_DIR, TRACE_SESSIONS_DIR, and
TRACE_SCRATCHPAD_DIR at per-run temp directories so that any test which
forgets to pass an explicit directory cannot read from or write into the
developer's real stores. (The real ~/.trace/knowledge had accumulated
fm-test*, e2e-*, guard-e2e*, and export-test stores, and suite runs were
overwriting the developer's real .claude/SCRATCHPAD.md, from exactly this
class of leak.)

Tests that *intentionally* target real data must be marked
``@pytest.mark.real_data`` (load-bearing — the conftest skips them unless
TRACE_REAL_DATA_TESTS=1) and pass real paths explicitly.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from trace_mcp.extensions.learn.store import load_store, save_store
from trace_mcp.schema import Session, SessionMetadata
from trace_mcp.scratchpad import _scratchpad_path
from trace_mcp.storage.json_file import JsonFileStorage

# Resolved so the containment checks are symlink-stable.
REAL_TRACE_DIR = (Path.home() / ".trace").resolve()


def _assert_isolated(var: str) -> Path:
    """Assert env var `var` is set and points outside the real ~/.trace.

    Returns the resolved isolated path. Used as a pre-write safety check by
    the probe tests so they cannot themselves write into real stores when the
    isolation is broken.
    """
    value = os.environ.get(var)
    assert value, f"conftest must set {var} for the whole suite"
    resolved = Path(value).resolve()
    assert not resolved.is_relative_to(REAL_TRACE_DIR), f"{var} points inside the real ~/.trace: {resolved}"
    return resolved


class TestSuiteEnvIsolation:
    def test_knowledge_dir_points_at_tmp(self) -> None:
        _assert_isolated("TRACE_KNOWLEDGE_DIR")

    def test_sessions_dir_points_at_tmp(self) -> None:
        _assert_isolated("TRACE_SESSIONS_DIR")

    def test_scratchpad_dir_points_at_tmp(self) -> None:
        """trace_end_session writes SCRATCHPAD.md by default; unisolated it
        overwrites cwd/.claude/SCRATCHPAD.md (the developer's real
        context-restoration file) or falls back to ~/.trace/scratchpads."""
        isolated = _assert_isolated("TRACE_SCRATCHPAD_DIR")
        resolved = _scratchpad_path().resolve()
        assert resolved.is_relative_to(isolated), f"scratchpad resolves to {resolved}, expected inside {isolated}"
        assert not resolved.is_relative_to((Path.cwd() / ".claude").resolve()), (
            "scratchpad would overwrite the developer's real .claude/SCRATCHPAD.md"
        )

    def test_egress_log_points_at_tmp(self) -> None:
        """Every mocked LLM/embedding test triggers a pre-call egress
        attestation; unisolated, suite runs would append hundreds of
        test-fixture lines to the developer's real ~/.trace/egress.jsonl."""
        isolated = _assert_isolated("TRACE_EGRESS_LOG")
        from trace_mcp.extensions.learn.egress import egress_log_path

        resolved = egress_log_path().resolve()
        assert resolved == isolated, f"egress ledger resolves to {resolved}, expected {isolated}"

    def test_default_knowledge_store_writes_land_in_tmp(self) -> None:
        """A store saved WITHOUT an explicit directory (the leak pattern that
        polluted the real ~/.trace/knowledge) must land in the isolated dir.

        The probe name is unique per run so a historic leak can't shadow the
        result, and the env is checked BEFORE writing so this test can't
        itself pollute the real store when isolation is broken.
        """
        isolated = _assert_isolated("TRACE_KNOWLEDGE_DIR")
        name = f"isolation-probe-{uuid.uuid4().hex[:8]}"
        ks = load_store(name)
        save_store(ks)
        assert (isolated / f"{name}.json").exists(), f"expected probe store under {isolated}"
        real = REAL_TRACE_DIR / "knowledge" / f"{name}.json"
        assert not real.exists(), f"probe store leaked into the real store: {real}"

    async def test_default_session_storage_writes_land_in_tmp(self) -> None:
        """A session created WITHOUT an explicit directory must be written
        inside the isolated sessions dir, not the real ~/.trace/sessions."""
        isolated = _assert_isolated("TRACE_SESSIONS_DIR")
        storage = JsonFileStorage()
        assert Path(storage.location()).resolve().is_relative_to(isolated), (
            f"default JsonFileStorage dir is {storage.location()}, expected inside {isolated}"
        )
        session_id = f"trace_20260610_probe{uuid.uuid4().hex[:6]}"
        await storage.create_session(Session(id=session_id, metadata=SessionMetadata(project="isolation-probe")))
        assert (isolated / f"{session_id}.json").exists()
        assert not (REAL_TRACE_DIR / "sessions" / f"{session_id}.json").exists(), (
            "probe session leaked into the real ~/.trace/sessions"
        )
