"""Root conftest: isolate the whole suite from the developer's real ~/.trace.

TRACE_KNOWLEDGE_DIR and TRACE_SESSIONS_DIR are pointed at per-run temp
directories for every test, so a test that forgets to pass an explicit
directory cannot read from or write into real stores. (Before this existed,
the real ~/.trace/knowledge accumulated fm-test*, e2e-*, guard-e2e*, and
export-test stores from exactly that leak.) Guarded by
tests/test_env_isolation.py.

Tests that intentionally target real data must opt in explicitly and pass the
real path themselves — see TestRealDataEmbeddings (TRACE_REAL_DATA_TESTS=1).

Side effects: mutates os.environ for the pytest session (restored on exit).
Subprocesses spawned by tests inherit the isolation automatically.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

_ISOLATED_VARS = ("TRACE_KNOWLEDGE_DIR", "TRACE_SESSIONS_DIR")


@pytest.fixture(scope="session", autouse=True)
def _isolate_trace_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point TRACE data-directory env vars at per-run temp dirs (session-wide).

    Session-scoped because the built-in monkeypatch fixture is function-scoped;
    saves and restores any pre-existing values.
    """
    previous = {name: os.environ.get(name) for name in _ISOLATED_VARS}
    base = tmp_path_factory.mktemp("trace-isolated")
    os.environ["TRACE_KNOWLEDGE_DIR"] = str(base / "knowledge")
    os.environ["TRACE_SESSIONS_DIR"] = str(base / "sessions")
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
