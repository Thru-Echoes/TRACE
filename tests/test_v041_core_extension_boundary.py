"""Core/extension boundary invariant (governance: TRACE decision evt_002).

The trace-learn extension is OPTIONAL. Core (schema/storage/tools/server)
must function with `trace_mcp.extensions.learn` absent — deleting the
extension must leave a fully functional provenance system. This pins the
P2 fix for the Round-1/2/3 finding G2 (unguarded core→extension import in
query_tools._compute_knowledge_metrics breaking the core tool
trace_project_summary).

Absence is simulated NON-destructively via sys.modules (no directory is
moved/deleted — there are concurrent live sessions and the real package
must be left intact).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trace_mcp.schema import Session
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import query_tools, session_tools


@pytest.fixture
def storage(tmp_path: Path) -> JsonFileStorage:
    return JsonFileStorage(directory=str(tmp_path))


@pytest.fixture
def active() -> dict[str, Session]:
    return {}


def _block_learn_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `trace_mcp.extensions.learn.*` unimportable (ImportError) without
    touching the filesystem. Setting a sys.modules entry to None makes
    `import` of that dotted path raise ImportError; monkeypatch reverts it."""
    for name in (
        "trace_mcp.extensions.learn.store",
        "trace_mcp.extensions.learn",
    ):
        monkeypatch.setitem(sys.modules, name, None)


async def test_project_summary_works_without_learn_extension(
    storage: JsonFileStorage,
    active: dict[str, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trace_project_summary is a CORE tool — it must succeed when the
    trace-learn extension is absent (governance evt_002)."""
    result = await session_tools.start_session(
        storage, active, project="boundary-test", description="boundary"
    )
    session_id = result.split("Session: ")[1].split("\n")[0]
    await session_tools.end_session(
        storage, active, session_id=session_id, summary="done"
    )

    _block_learn_extension(monkeypatch)

    # Must not raise ModuleNotFoundError; core tool stays functional.
    summary = await query_tools.project_summary(storage, "boundary-test")
    assert isinstance(summary, dict)
    assert "knowledge" in summary
    # Fail-open sentinel shape (same as the no-learnings case).
    assert summary["knowledge"]["total"] == 0
