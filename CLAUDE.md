# TRACE — Project Instructions

> **Full documentation**: [README.md](README.md) (architecture, tools, configuration, changelog)
> **Formal specification**: [docs/specification.md](docs/specification.md)
> **Version**: 0.3.0

---

## Development

```bash
uv pip install -e ".[dev]"          # Install with dev dependencies
uv run pytest                       # Run full test suite (322+ tests)
uv run pytest -k llm                # Run real LLM integration tests
uv run ruff check src/              # Lint
uv run pyright src/                 # Type check
python scripts/generate_schema.py   # Regenerate JSON Schema from Pydantic models
```

### Key Patterns

- **Pydantic v2** for all data models: `model_dump()`, `model_validate()`, `model_rebuild()`
- **Forward refs across files**: Import both models in `schema/__init__.py`, call `model_rebuild()` after
- All modules import `Session` from `trace_mcp.schema` (not `.schema.session`) to trigger rebuild
- `from datetime import UTC` (not `timezone.utc`) — ruff UP017
- **FastMCP** `@mcp.tool()` needs parentheses
- `asyncio_mode = "auto"` in pytest config for async tests
- **Atomic writes** (temp file + `os.replace`) for all JSON writes — no `fcntl` dependency (cross-platform)
- `server.py` imports `__version__` from `trace_mcp` for startup log
- **Line length**: 120 (ruff configured)

### Architecture Quick Reference

```
src/trace_mcp/
    server.py              # MCP server entry point (FastMCP) + extension loader
    scratchpad.py           # Session-end scratchpad generator
    hooks.py               # Hook registry for extension ↔ core integration
    schema/                # Pydantic v2 models (Session, TraceEvent, etc.)
    storage/               # Abstract interface + JSON file backend
    tools/                 # MCP tool implementations (session, logging, decision, query, export)
    extensions/learn/      # trace-learn: cross-session knowledge persistence (default)
```

Extensions auto-discovered via `pkgutil.iter_modules` in `extensions/`. Each extension
provides a `register(mcp, storage)` function.

## Project Rules

- `.claude/rules/python-quality.md` — Code style, type checking, linting
- `.claude/rules/manuscript.md` — Literature audit rubric and paper conventions (activates for `manuscript/**/*`)

## Skills

- `/lit-audit` — Code papers against the TRACE literature audit rubric
- `/trace-session` — Start a new TRACE session with standard boilerplate

## TRACE Protocol

This project logs its own development with TRACE. The full TRACE protocol
instructions are in the global `~/.claude/CLAUDE.md`. Key points:

- Start a session at the beginning of any multi-step workflow
- Log decisions BEFORE acting; log contributions AFTER the artifact exists
- Log rejected alternatives as separate decision events for significant methodology discussions
- End with a summary including what was accomplished and what is next
- The scratchpad auto-generates decisions, contributions, and corrections from session events

## Available Tools (23 total)

18 core tools + 5 trace-learn extension tools. See [README.md](README.md#available-tools-23-total) for the full table.

**Core**: `trace_start_session`, `trace_end_session`, `trace_log_tool_call`, `trace_log_annotation`, `trace_log_contribution`, `trace_log_state_change`, `trace_propose_decision`, `trace_resolve_decision`, `trace_get_session`, `trace_get_events`, `trace_get_decisions`, `trace_get_decision_chain`, `trace_search`, `trace_export`, `trace_list_sessions`, `trace_project_summary`, `trace_health_check`

**trace-learn**: `trace_learn_recall`, `trace_learn_add`, `trace_learn_list`, `trace_learn_forget`, `trace_learn_extract`
