"""Suite-wide isolation from the developer's real ~/.trace data.

The root conftest must point TRACE_KNOWLEDGE_DIR and TRACE_SESSIONS_DIR at
per-run temp directories so that any test which forgets to pass an explicit
directory cannot read from or write into the developer's real stores. (The
real ~/.trace/knowledge had accumulated fm-test*, e2e-*, guard-e2e*, and
export-test stores from exactly this class of leak.)

Tests that *intentionally* target real data must opt in explicitly — see
TestRealDataEmbeddings (gated behind TRACE_REAL_DATA_TESTS=1).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from trace_mcp.extensions.learn.store import load_store, save_store
from trace_mcp.storage.json_file import JsonFileStorage

REAL_TRACE_DIR = Path.home() / ".trace"


class TestSuiteEnvIsolation:
    def test_knowledge_dir_points_at_tmp(self) -> None:
        """TRACE_KNOWLEDGE_DIR must be set and must not be the real store."""
        value = os.environ.get("TRACE_KNOWLEDGE_DIR")
        assert value, "conftest must set TRACE_KNOWLEDGE_DIR for the whole suite"
        resolved = Path(value).resolve()
        assert not resolved.is_relative_to(REAL_TRACE_DIR), (
            f"TRACE_KNOWLEDGE_DIR points inside the real ~/.trace: {resolved}"
        )

    def test_sessions_dir_points_at_tmp(self) -> None:
        """TRACE_SESSIONS_DIR must be set and must not be the real store."""
        value = os.environ.get("TRACE_SESSIONS_DIR")
        assert value, "conftest must set TRACE_SESSIONS_DIR for the whole suite"
        resolved = Path(value).resolve()
        assert not resolved.is_relative_to(REAL_TRACE_DIR), (
            f"TRACE_SESSIONS_DIR points inside the real ~/.trace: {resolved}"
        )

    def test_default_knowledge_store_writes_land_in_tmp(self) -> None:
        """A store saved WITHOUT an explicit directory (the leak pattern that
        polluted the real ~/.trace/knowledge) must land in the isolated dir.

        The probe name is unique per run so a historic leak (e.g. from running
        this test against a tree without the conftest) can't shadow the result.
        """
        name = f"isolation-probe-{uuid.uuid4().hex[:8]}"
        ks = load_store(name)
        save_store(ks)
        isolated = Path(os.environ["TRACE_KNOWLEDGE_DIR"]) / f"{name}.json"
        assert isolated.exists(), f"expected probe store at {isolated}"
        real = REAL_TRACE_DIR / "knowledge" / f"{name}.json"
        assert not real.exists(), f"probe store leaked into the real store: {real}"

    def test_default_session_storage_writes_land_in_tmp(self) -> None:
        """JsonFileStorage() with no directory must resolve inside the
        isolated sessions dir, not the real ~/.trace/sessions."""
        storage = JsonFileStorage()
        resolved = Path(str(storage._dir)).resolve()
        assert resolved.is_relative_to(Path(os.environ["TRACE_SESSIONS_DIR"]).resolve()), (
            f"default JsonFileStorage dir is {resolved}, expected the isolated sessions dir"
        )
