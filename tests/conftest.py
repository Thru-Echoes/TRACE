"""Root conftest: isolate the suite from the developer's real ~/.trace data.

Two mechanisms:

1. **Data-directory isolation** — TRACE_KNOWLEDGE_DIR, TRACE_SESSIONS_DIR, and
   TRACE_SCRATCHPAD_DIR point at a per-run temp directory, set in
   ``pytest_configure`` (NOT a fixture: ``server.py`` binds a module-level
   ``JsonFileStorage()`` at import, which happens during collection — before
   any session fixture runs). Subprocesses spawned by tests inherit the
   isolation via os.environ. Before this existed, suite runs deposited
   fm-test*/e2e-*/guard-e2e* stores into the real ~/.trace/knowledge and
   overwrote the developer's real .claude/SCRATCHPAD.md on every run.
   Guarded by tests/test_env_isolation.py.

2. **Opt-in gate for real-data tests** — any test or class marked
   ``@pytest.mark.real_data`` is skipped unless TRACE_REAL_DATA_TESTS is set
   to 1/true/yes. The marker is load-bearing (enforced here by
   ``pytest_collection_modifyitems``), so a marked class cannot forget its
   skipif. Marked tests must pass real paths explicitly — the env defaults
   point at the isolated temp dirs.

Deliberately NOT isolated: the trace-learn ``.env`` lookup
(``~/.trace/.env`` / ``./.env``). The real-LLM integration tests intentionally
read the developer's real key; config tests that need key-absence scrub all
three sources themselves (env var + _TRACE_ENV_PATH + monkeypatch.chdir).

Side effects: mutates os.environ for the pytest run (restored in
pytest_unconfigure); creates a temp directory that outlives the run.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_ISOLATED_VARS = ("TRACE_KNOWLEDGE_DIR", "TRACE_SESSIONS_DIR", "TRACE_SCRATCHPAD_DIR")
_REAL_DATA_ENV = "TRACE_REAL_DATA_TESTS"
_previous_env: dict[str, str | None] = {}


def real_data_opted_in() -> bool:
    """True when the operator explicitly opted into real-data tests."""
    return os.environ.get(_REAL_DATA_ENV, "").strip().lower() in {"1", "true", "yes"}


def pytest_configure(config: pytest.Config) -> None:
    """Point TRACE data-dir env vars at a per-run temp dir before collection.

    Side effect: mutates os.environ (restored in pytest_unconfigure).
    """
    base = Path(tempfile.mkdtemp(prefix="trace-isolated-"))
    for name, sub in zip(_ISOLATED_VARS, ("knowledge", "sessions", "scratchpads"), strict=True):
        _previous_env[name] = os.environ.get(name)
        os.environ[name] = str(base / sub)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Restore any pre-existing values of the isolated env vars."""
    for name, value in _previous_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    _previous_env.clear()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the run loudly if any test broke the isolation contract.

    A test that deletes one of the isolated vars (e.g. a bare
    ``os.environ.pop`` in a finally block instead of monkeypatch) silently
    disables isolation for every test that runs after it — that exact bug let
    e2e session-ends overwrite the developer's real .claude/SCRATCHPAD.md.
    The guard tests in test_env_isolation.py run early and cannot see a
    later deletion; this hook checks at the end of the run.
    """
    broken = [name for name in _ISOLATED_VARS if name not in os.environ]
    if broken:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        print(
            f"\nERROR: isolation env var(s) {broken} were DELETED during the run — "
            "some test used os.environ.pop/del instead of monkeypatch; tests that ran "
            "after it executed without ~/.trace isolation."
        )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Enforce the real_data marker: skip unless explicitly opted in."""
    if real_data_opted_in():
        return
    skip = pytest.mark.skip(
        reason=(
            "reads the developer's real ~/.trace data (contents drift with personal "
            "machine state) — opt in with TRACE_REAL_DATA_TESTS=1"
        )
    )
    for item in items:
        if "real_data" in item.keywords:
            item.add_marker(skip)
