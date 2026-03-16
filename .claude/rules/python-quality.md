---
paths:
  - "src/**/*.py"
  - "tests/**/*.py"
  - "scripts/**/*.py"
---

# Python Code Quality — TRACE Project

- Pydantic v2 patterns: `model_dump()`, `model_validate()`, `model_rebuild()`
- `from datetime import UTC` (not `timezone.utc`) — ruff UP017
- `asyncio_mode = "auto"` in pytest config for async tests
- FastMCP `@mcp.tool()` needs parentheses
- Forward refs across files: import both models in `__init__.py`, call `model_rebuild()` after
- All modules import `Session` from `trace_mcp.schema` (not `.schema.session`) to trigger rebuild
- File locking with `fcntl.flock` for JSON writes
- Line length: 120 (ruff configured)
- Run `ruff check --select E,F,W,B,I,UP` on changed files
