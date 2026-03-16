# TRACE Protocol v0.2

> **TRACE**: Transparent Recording of AI-assisted Collaboration Experiments
> **Version**: 0.2.0
> **Schema**: `https://trace-protocol.org/v0.2`
> **Last Updated**: 2026-03-16

---

## Overview

TRACE is an MCP server that provides a standardized audit trail for AI-assisted research workflows. It records tool calls, decisions, annotations, contributions, and actor attribution — who proposed what, who accepted or revised it, and why.

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
    server.py              # MCP server entry point (FastMCP) + extension loader
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
        logging_tools.py    # log tool calls, annotations, state changes, contributions
        decision_tools.py   # propose/resolve decisions
        query_tools.py      # search & retrieve, project summary
        export_tools.py     # export formatters
    extensions/
        __init__.py         # Package marker
        learn/              # trace-learn: cross-session knowledge persistence (default)
            __init__.py     # register(mcp, storage) — registers 5 MCP tools + hooks
            config.py       # Config from env vars and ~/.trace/.env
            models.py       # Learning, KnowledgeStore
            store.py        # File I/O for ~/.trace/knowledge/{project}.json
            extraction.py   # Rule-based + LLM extraction backends
            matching.py     # BM25 (with stemming) + LLM + Jaccard matching backends
        evolve/             # trace-evolve: evolution-themed adaptive persistence (legacy)
            __init__.py     # register(mcp, storage) — registers 5 MCP tools
            models.py       # Adaptation, Genome
            store.py        # File I/O for ~/.trace/evolution/{project}.json
            selection.py    # Natural selection: extract adaptations from session events
            fitness.py      # Fitness scoring: Jaccard similarity + tag-boosted recall
    exporters/
        markdown_export.py  # Human-readable Markdown
        prov_jsonld.py      # W3C PROV JSON-LD
schemas/
    trace-v0.2.json        # JSON Schema (generated from Pydantic models)
skill/
    TRACE.md               # Claude Code skill file
tests/
    test_schema.py
    test_storage.py
    test_tools.py
    test_exporters.py
    test_learn.py
    test_evolve.py
scripts/
    generate_schema.py     # Regenerate JSON Schema from models
```

## Available Tools (28 total)

### Core Tools (18)

| Tool | Description |
|------|-------------|
| `trace_start_session` | Start a new audit session |
| `trace_end_session` | End a session with summary |
| `trace_log_tool_call` | Record a tool invocation (with optional `retries_event_id` for retry chains) |
| `trace_log_annotation` | Record a learning, gotcha, correction, observation, todo, or question (with optional `corrects_event_ids`) |
| `trace_log_contribution` | Record a contribution with direction/execution attribution |
| `trace_log_state_change` | Record an environment/config change |
| `trace_propose_decision` | Propose a methodological decision (with optional `suggestion_type`: proactive/requested/collaborative) |
| `trace_resolve_decision` | Accept, revise, or reject a decision |
| `trace_get_session` | Get session metadata |
| `trace_get_events` | List events (filterable by type) |
| `trace_get_decisions` | List decisions (filterable by disposition and/or `proposed_by_type`) |
| `trace_get_decision_chain` | Walk linked decision revisions |
| `trace_search` | Search events by text |
| `trace_export` | Export as JSON, Markdown, or PROV JSON-LD |
| `trace_list_sessions` | List all sessions |
| `trace_project_summary` | Aggregated metrics across all sessions for a project |
| `trace_health_check` | System health info and event-level statistics (version, storage paths, event breakdown) |

### Extension: trace-learn (5) — DEFAULT for new sessions

| Tool | Description |
|------|-------------|
| `trace_learn_recall` | Find relevant past learnings for a context (LLM or BM25 with stemming + tag matching) |
| `trace_learn_add` | Manually add a learning to the knowledge store |
| `trace_learn_list` | List all learnings (optionally filtered by category) |
| `trace_learn_forget` | Remove a learning by ID |
| `trace_learn_extract` | Extract learnings from session annotations/decisions (idempotent) |

**Storage**: `~/.trace/knowledge/{project}.json` (env var: `TRACE_KNOWLEDGE_DIR`)

### Extension: trace-evolve (5) — legacy, for existing projects

Uses evolution-themed terminology: genomes contain adaptations that are mutated, expressed, selected, and go extinct.

| Tool | Evolution Concept | Description |
|------|-------------------|-------------|
| `trace_evolve_express` | Gene expression | Find relevant past adaptations for a context (fitness scoring) |
| `trace_evolve_mutate` | Mutation | Add a new adaptation to the project's genome |
| `trace_evolve_list` | — | List all adaptations in a genome (optionally filtered by category) |
| `trace_evolve_extinct` | Extinction | Remove an adaptation from the genome |
| `trace_evolve_select` | Natural selection | Extract adaptations from session annotations/decisions (idempotent) |

**Storage**: `~/.trace/evolution/{project}.json` (env var: `TRACE_EVOLUTION_DIR`)

## Core Concept: Decision Provenance

TRACE's differentiator is the **decision chain** — every decision has:
- An **actor** (who proposed/resolved it)
- A **disposition** (proposed, accepted, revised, rejected)
- A **rationale** (why)
- An optional **revises_event_id** (linking to prior decisions)
- An optional **suggestion_type** (proactive, requested, collaborative)

This creates a provenance DAG of decisions, not just a flat log.

## Event Types

1. **tool_call** — Records an MCP tool invocation on another server
2. **decision** — Records a methodological decision with attribution
3. **annotation** — Free-form observations (learning, gotcha, correction, observation, todo, question)
4. **state_change** — Records changes in environment or configuration
5. **contribution** — Records work products with direction (who had the idea) vs execution (who did the work) attribution

## What's New in v0.2

- **Contribution logging** (`trace_log_contribution`): Records who directed vs who executed a contribution, with optional artifact and decision links
- **Decision suggestion_type**: Tracks whether decisions were `proactive` (AI volunteered), `requested` (human asked), or `collaborative` (emerged from discussion)
- **Project summaries** (`trace_project_summary`): Aggregated metrics across all sessions for paper-ready statistics
- **Enhanced decision filtering**: `trace_get_decisions` now supports filtering by `proposed_by_type` (human/ai)
- **ContributionData** schema: New Pydantic model with direction, execution, artifact, related_decision_ids, tags
- **Correction annotations**: New `correction` category for `trace_log_annotation` with `corrects_event_ids` field linking to the events being corrected — captures human-catches-AI-mistake patterns
- **Tool call retry chains**: `retries_event_id` on `trace_log_tool_call` links repeated failed attempts of the same action
- **Human intervention metrics**: `trace_project_summary` now includes `human_interventions` block with correction count, retry chains, decision rejection/revision counts, and intervention rate

## Knowledge Matching and Extraction

### Matching Backends (recall)

| Backend | When Used | Default Threshold |
|---------|-----------|-------------------|
| **LLM** (primary) | `openai` installed + API key configured | 0.20 |
| **BM25** (fallback) | No API key or LLM fails | 0.15 |
| **Jaccard** (legacy) | Backward compatibility | 0.10 |

BM25 includes a lightweight suffix-stripping stemmer: decisions→decision, logging→log, entries→entry.

### Extraction Backends

| Backend | When Used |
|---------|-----------|
| **LLM-enhanced** | API key configured — sends events to OpenAI for intelligent extraction |
| **Rule-based** | No API key — processes annotations, rejected/revised decisions, collaborative contributions |

Both are idempotent. Configure via `~/.trace/.env`:

```
OPENAI_API_KEY=sk-...
TRACE_LLM_MODEL=gpt-5-nano
TRACE_LLM_EXTRACTION_MODEL=gpt-5-mini
TRACE_LLM_ENABLED=true
```

## Development

```bash
uv pip install -e ".[dev]"
uv run pytest                     # Run all 455 tests
uv run pytest -k llm              # Run real LLM integration tests
python scripts/generate_schema.py # Regenerate JSON Schema
```

## Design Principles

- **Pydantic v2** for all data models with strict validation
- **Async throughout** — MCP servers are async
- **Fail open** — audit errors warn, never block workflows
- **Human-readable IDs** — `trace_20260205_a1b2c3`, not UUIDs
- **UTC ISO 8601 timestamps**
- **Pretty-printed JSON** — `indent=2`, openable in any editor
- **No external dependencies** beyond `mcp` and `pydantic` (OpenAI optional)
- **Atomic writes** — temp file + rename prevents corrupt stores
