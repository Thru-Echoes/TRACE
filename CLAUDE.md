# TRACE Protocol v0.1

> **TRACE**: Transparent Recording of AI-assisted Collaboration Experiments
> **Version**: 0.1.0 (MVP)
> **Schema**: `https://trace-protocol.org/v0.1`
> **Last Updated**: 2026-02-05

---

## Overview

TRACE is an MCP server that provides a standardized audit trail for AI-assisted research workflows. It records tool calls, decisions, annotations, and actor attribution — who proposed what, who accepted or revised it, and why.

TRACE runs as a **sidecar** alongside domain MCP servers. It does NOT proxy or intercept MCP calls. The AI client explicitly calls TRACE tools to log events.

## Architecture

```
AI Client (Claude Code, Claude Desktop, etc.)
    |
    +-- connects to: Domain MCP Server(s)
    |                 (corpus search, NLP pipeline, data retrieval)
    |                 --> does the actual work
    |
    +-- connects to: TRACE MCP Server (this project)
                     --> records what happened to JSON files
```

**Storage**: One JSON file per session in `~/.trace/sessions/`. Each file is a self-contained, valid TRACE document.

## File Structure

```
src/trace_mcp/
    __init__.py
    server.py              # MCP server entry point (FastMCP)
    schema/
        __init__.py         # Re-exports + model_rebuild()
        session.py          # Session, Actor, Environment, SessionMetadata
        events.py           # TraceEvent, ToolCallData, DecisionData, etc.
        prov_mapping.py     # W3C PROV concept mapping
    storage/
        base.py             # Abstract storage interface
        json_file.py        # JSON file storage (one file per session)
    tools/
        session_tools.py    # start/end session
        logging_tools.py    # log tool calls, annotations, state changes
        decision_tools.py   # propose/resolve decisions
        query_tools.py      # search & retrieve
        export_tools.py     # export formatters
    exporters/
        markdown_export.py  # Human-readable Markdown
        prov_jsonld.py      # W3C PROV JSON-LD
schemas/
    trace-v0.1.json        # JSON Schema (generated from Pydantic models)
skill/
    TRACE.md               # Claude Code skill file
tests/
    test_schema.py
    test_storage.py
    test_tools.py
    test_exporters.py
scripts/
    generate_schema.py     # Regenerate JSON Schema from models
```

## Available Tools

| Tool | Description |
|------|-------------|
| `trace_start_session` | Start a new audit session |
| `trace_end_session` | End a session with summary |
| `trace_log_tool_call` | Record a tool invocation |
| `trace_log_annotation` | Record a learning, gotcha, observation, or todo |
| `trace_log_state_change` | Record an environment/config change |
| `trace_propose_decision` | Propose a methodological decision |
| `trace_resolve_decision` | Accept, revise, or reject a decision |
| `trace_get_session` | Get session metadata |
| `trace_get_events` | List events (filterable by type) |
| `trace_get_decisions` | List decisions (filterable by disposition) |
| `trace_get_decision_chain` | Walk linked decision revisions |
| `trace_search` | Search events by text |
| `trace_export` | Export as JSON, Markdown, or PROV JSON-LD |
| `trace_list_sessions` | List all sessions |

## Core Concept: Decision Provenance

TRACE's differentiator is the **decision chain** — every decision has:
- An **actor** (who proposed/resolved it)
- A **disposition** (proposed, accepted, revised, rejected)
- A **rationale** (why)
- An optional **revises_event_id** (linking to prior decisions)

This creates a provenance DAG of decisions, not just a flat log.

## Event Types

1. **tool_call** — Records an MCP tool invocation on another server
2. **decision** — Records a methodological decision with attribution
3. **annotation** — Free-form observations (learning, gotcha, observation, todo, question)
4. **state_change** — Records changes in environment or configuration

## Development

```bash
pip install -e ".[dev]"
pytest                    # Run tests (50 tests)
python scripts/generate_schema.py  # Regenerate JSON Schema
```

## Design Principles

- **Pydantic v2** for all data models
- **Async throughout** — MCP servers are async
- **Fail open** — audit errors warn, never block workflows
- **Human-readable IDs** — `trace_20260205_a1b2c3`, not UUIDs
- **UTC ISO 8601 timestamps**
- **Pretty-printed JSON** — `indent=2`, openable in any editor
- **No external dependencies** beyond `mcp` and `pydantic`
