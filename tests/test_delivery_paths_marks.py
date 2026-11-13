"""Every path by which TRACE text reaches a later session shows marks, or is the record itself (INV-18).

A later session reads TRACE text through the MCP tools' responses and through
the scratchpad file written at session end. ``DELIVERY_PATHS`` classifies every
registered tool:

- ``marks``: shows learning or event text with its marks; each is checked below;
- ``record``: returns the record itself, which holds every field a mark is
  computed from (disposition, resolver, actor);
- ``none``: returns no learning or event text written by someone else.

A tool added without a classification fails ``test_every_tool_is_classified``,
so a new path cannot reach sessions unmarked unnoticed.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest
from _mcp_helpers import call_tool_json
from _shadow_helpers import last_server, learn_env, make_session

from trace_mcp import extension_hooks, server
from trace_mcp.exporters.markdown_export import export_markdown
from trace_mcp.exporters.prov_jsonld import export_prov_jsonld
from trace_mcp.extensions.learn import store
from trace_mcp.scratchpad import _build_session_section

DELIVERY_PATHS: dict[str, str] = {
    # marks
    "trace_start_session": "marks",  # banner: the recalled learnings
    "trace_propose_decision": "marks",  # the related-learnings block
    "trace_learn_recall": "marks",
    "trace_learn_list": "marks",
    "trace_export": "marks",  # Markdown and PROV; the JSON export is the record itself
    "trace_end_session": "marks",  # writes the scratchpad; the response names new learning ids only
    # the record itself
    "trace_get_session": "record",
    "trace_get_events": "record",
    "trace_get_decisions": "record",
    "trace_get_decision_chain": "record",
    "trace_search": "record",
    "trace_jev_assess_candidate": "record",  # an advisory about one event the caller names by id
    # no learning or event text from anyone else
    "trace_log_tool_call": "none",
    "trace_log_annotation": "none",
    "trace_log_contribution": "none",
    "trace_log_state_change": "none",
    "trace_resolve_decision": "none",
    "trace_list_sessions": "none",  # session metadata
    "trace_project_summary": "none",  # counts
    "trace_health_check": "none",  # counts and problems
    "trace_learn_add": "none",  # echoes the caller's own new learning
    "trace_learn_forget": "none",
    "trace_learn_extract": "none",  # new learning ids and counts
}


def test_every_tool_is_classified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    learn_env(tmp_path, monkeypatch, llm=False)
    core = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    learn = {tool.name for tool in asyncio.run(last_server().list_tools())}
    assert core | learn | {"trace_jev_assess_candidate"} == set(DELIVERY_PATHS)
    assert set(DELIVERY_PATHS.values()) == {"marks", "record", "none"}


def test_the_session_banner_and_the_decision_block_are_built_by_the_marked_formatters() -> None:
    assert "format_recalled_learnings(" in inspect.getsource(server.trace_start_session)
    assert "recall_for_decision(" in inspect.getsource(server.trace_propose_decision)
    assert "format_decision_warnings(" in inspect.getsource(extension_hooks.recall_for_decision)


async def test_each_marked_path_shows_the_source_mark(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = learn_env(tmp_path, monkeypatch, llm=False)
    session = await make_session(storage)
    ks = store.load_store("collab-a")
    store.add_learning(
        ks, content="upload retries back off exponentially", source_session=session.id, source_event="evt_003"
    )
    store.save_store(ks)
    tool_server = last_server()

    recalled = await call_tool_json(
        tool_server, "trace_learn_recall", {"project": "collab-a", "context": "upload retries"}
    )
    assert recalled["results"][0]["source_marks"] == ["[accepted]"]

    listed = await call_tool_json(tool_server, "trace_learn_list", {"project": "collab-a"})
    assert listed["learnings"][0]["source_marks"] == ["[accepted]"]
    assert "[accepted]" in listed["source_marks_legend"]

    banner = extension_hooks.format_recalled_learnings(
        list(await extension_hooks.recall_if_available("collab-a", "upload retries", None, 5))
    )
    assert f"source [accepted] {session.id}#evt_003" in banner
    block = await extension_hooks.recall_for_decision("collab-a", "upload retries", None)
    assert f"source [accepted] {session.id}#evt_003" in block

    assert "- **Marks**: [accepted]" in export_markdown(session)
    prov = json.loads(export_prov_jsonld(session))
    assert any("[accepted]" in node.get("trace:provenanceMark", []) for node in prov["@graph"])

    assert "**[accepted]** Use exponential backoff. (evt_003;" in _build_session_section(session)
