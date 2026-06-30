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
    result = await session_tools.start_session(storage, active, project="boundary-test", description="boundary")
    session_id = result.split("Session: ")[1].split("\n")[0]
    await session_tools.end_session(storage, active, session_id=session_id, summary="done")

    _block_learn_extension(monkeypatch)

    # Must not raise ModuleNotFoundError; core tool stays functional.
    summary = await query_tools.project_summary(storage, "boundary-test")
    assert isinstance(summary, dict)
    assert "knowledge" in summary
    # Fail-open sentinel shape (same as the no-learnings case).
    assert summary["knowledge"]["total"] == 0


async def test_all_core_tools_run_without_learn_extension(
    storage: JsonFileStorage,
    active: dict[str, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every core tool must load AND run with the trace-learn extension absent.

    Deleting the extension must leave a fully functional provenance system
    (HARD CONSTRAINT #1 / governance evt_002). This exercises one of each core
    tool — session lifecycle, all four logging tools, both decision tools, every
    query tool, and all three export formats — with the extension blocked.
    """
    from trace_mcp.tools import decision_tools, export_tools, logging_tools

    _block_learn_extension(monkeypatch)

    # session lifecycle
    start = await session_tools.start_session(storage, active, project="boundary", description="full core sweep")
    sid = start.split("Session: ")[1].split("\n")[0]
    session = active[sid]

    # all four logging tools
    await logging_tools.log_tool_call(storage, session, server="s", tool_name="t", input={"q": 1}, status="success")
    await logging_tools.log_annotation(storage, session, category="gotcha", content="g")
    await logging_tools.log_state_change(
        storage,
        session,
        description="env change",
        field="python",
        old_value="3.12",
        new_value="3.13",
    )
    await logging_tools.log_contribution(
        storage,
        session,
        description="a model",
        direction="human",
        execution="ai",
        conversation_snippet="build the model",
    )

    # both decision tools
    d_id = await decision_tools.propose_decision(
        storage,
        session,
        description="use X",
        proposed_by_type="ai",
        proposed_by_id="claude",
    )
    await decision_tools.resolve_decision(
        storage,
        session,
        event_id=d_id,
        disposition="accepted",
        resolved_by_type="human",
        resolved_by_id="researcher",
    )

    # every query tool (sync; operate on the persisted session)
    loaded = await storage.get_session(sid)
    assert query_tools.get_events(loaded)
    assert query_tools.get_decisions(loaded)
    assert query_tools.get_decision_chain(loaded, event_id=d_id)
    assert isinstance(query_tools.search_events(loaded, query="X"), list)
    assert isinstance(await storage.list_sessions(), list)  # the trace_list_sessions path

    # all three export formats (prov-jsonld exercises is_uri_form_reference)
    assert export_tools.export_session(loaded, format="json")
    assert export_tools.export_session(loaded, format="markdown")
    assert export_tools.export_session(loaded, format="prov-jsonld")

    # async query tools that historically reached for the extension
    summary = await query_tools.project_summary(storage, "boundary")
    assert summary["knowledge"]["total"] == 0  # fail-open sentinel
    health = await query_tools.health_check(storage, project="boundary")
    assert "knowledge_dir" in health["storage"]

    # session end
    end = await session_tools.end_session(storage, active, session_id=sid, summary="done")
    assert "Session ended" in end


def test_core_install_requires_only_mcp_and_pydantic() -> None:
    """A core install must pull ONLY mcp + pydantic — no optional backend
    (openai / numpy / model2vec / filelock) may leak into the core dependency
    set. Packaging-level guarantee for the core/extension boundary: the import
    graph of a core-only install cannot transitively require the extension's deps.
    """
    import tomllib

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    core_deps = data["project"]["dependencies"]
    names = {d.split(">")[0].split("=")[0].split("[")[0].strip().lower() for d in core_deps}
    assert names == {"mcp", "pydantic"}, f"core dependency set drifted beyond mcp+pydantic: {names}"
