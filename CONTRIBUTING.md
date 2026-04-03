# Contributing to TRACE

## Development Setup

```bash
# Clone and install with dev dependencies
git clone https://github.com/<org>/trace-mcp.git
cd trace-mcp
uv pip install -e ".[dev]"
```

## Testing

```bash
uv run pytest                     # Run all tests
uv run pytest -k llm              # Run real LLM integration tests (requires OPENAI_API_KEY)
uv run pytest tests/test_schema.py # Run a specific test file
```

All async tests use `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed.

## Code Style

- **Formatter/linter**: ruff (`uv run ruff check src/` and `uv run ruff format src/`)
- **Type checker**: pyright (`uv run pyright src/`)
- Line length: 120
- Target: Python 3.11+
- Use `from datetime import UTC` (not `timezone.utc`) per ruff UP017

## Schema Regeneration

When you modify Pydantic models in `src/trace_mcp/schema/`, regenerate the JSON Schema:

```bash
python scripts/generate_schema.py
```

This updates `schemas/trace-v0.3.json` from `Session.model_json_schema()`.

## Extension Development

Extensions live in `src/trace_mcp/extensions/<name>/` and are auto-discovered via `pkgutil.iter_modules`. Each extension must expose a `register(mcp, storage)` function in its `__init__.py`.

See `extensions/learn/` for the reference implementation.

## Pull Request Guidelines

1. All tests must pass (`uv run pytest`)
2. No new ruff or pyright errors
3. Add tests for new functionality
4. Keep PRs focused — one feature or fix per PR
5. If modifying schema models, regenerate the JSON Schema
