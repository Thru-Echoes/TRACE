# Contributing to TRACE

## Development setup

```bash
git clone https://github.com/Thru-Echoes/TRACE.git
cd TRACE
uv pip install -e ".[dev]"
```

## Testing

```bash
uv run pytest                        # Run all tests
uv run pytest -k llm                 # Run real LLM integration tests (requires OPENAI_API_KEY)
uv run pytest tests/test_schema.py   # Run a specific test file
```

All async tests use `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed.

### What the test suite covers

The authoritative count is `uv run pytest` (**889 tests** — 887 passing + 2
environment-gated skips as of v0.4.2). The table below highlights the main
areas; it is representative, not an exhaustive per-file tally.

| Area | Tests | What's verified |
|------|------:|-----------------|
| **Schema validation** | 32 | Pydantic models, forward refs, event type validation, `model_rebuild()` |
| **Storage** | 14 | JSON file I/O, atomic writes, session CRUD, listing, filtering |
| **Core tools** | 14 | Session start/end, event logging, decision propose/resolve |
| **Exporters** | 17 | Markdown export, PROV JSON-LD export, format correctness |
| **trace-learn models** | 16 | Learning/KnowledgeStore validation, ID generation, serialization |
| **trace-learn store** | 25 | Load/save with atomic writes, add/remove/list learnings |
| **trace-learn extraction** | 27 | Rule-based extraction from annotations, decisions, contributions; LLM extraction (mocked); idempotency |
| **trace-learn matching** | 74 | Stemmer (13), BM25 with stemming (4), BM25 index/normalization (9), Jaccard (8), LLM scoring mocked (3), backend selection (4), recall integration (7), per-backend thresholds (5), tag overlap (5), recall tracking (4), decay (12) |
| **trace-learn dedup** | 14 | `find_duplicate`, `add_learning_dedup`, dedup in extraction, threshold configurability |
| **Knowledge metrics** | 7 | `project_summary` knowledge section: totals, categories, most-surfaced, never-surfaced, averages |
| **trace-learn E2E** | 12 | Full pipeline: extract → persist → recall across sessions, config loading, correction chain tracking |
| **Recall layers** | 23 | 3-layer recall (session start, on-demand, decision proposal), hook registration, format functions, auto-extract on session end |
| **Adapters** | 47 | Host-adapter base + Claude Code installer + Codex placeholder + hook scripts (session reminder, prompt reminder, pretool guard, decision audit) |
| **Installation health** | 34 | Import checks, config resolution, extension loading |
| **E2E server** | 12 | Full MCP tool invocations through the server layer |
| **Protocol additions (v0.4.1)** | 101 | Attribution audit, URI-form `corrects_event_ids`, PROV-LD correction split, decision-audit hook, `tool_call` wrapper, core/extension boundary, extension status |
| **Hardening (v0.4.2)** | 20 | Storage lost-update + **cross-process** concurrency lock, query payload caps, cheap bootstrap, recall-count accounting |
| **Spec conformance + guards** | 225 | Specification conformance, hardening E2E, decision guard-rails, failure-mode detectors |

### What the test suite does NOT cover

- **Large-scale performance** — No benchmarks for stores with hundreds of learnings or sessions with hundreds of events.
- **Network failure handling** — LLM backend tests verify fallback on API errors (mocked), but no tests simulate real network timeouts, rate limits, or partial responses.
- **MCP transport layer** — Tests call tool functions directly. The `test_e2e_server.py` tests use the tool layer but not the actual MCP wire format.
- **Cross-project knowledge** — No tests for sharing learnings between different projects (planned for Tier 3).
- **Feedback loops** — No tests for boosting/demoting learning weights based on decision outcomes (planned for Tier 3).

## Code style

- **Formatter / linter**: ruff — `uv run ruff check src/` and `uv run ruff format src/`
- **Type checker**: pyright — `uv run pyright src/`
- **Line length**: 120
- **Target**: Python 3.11+
- Use `from datetime import UTC` (not `timezone.utc`) per ruff UP017
- FastMCP `@mcp.tool()` needs parentheses
- Prefer Pydantic v2 BaseModel over raw dicts for anything with a known shape
- Atomic writes (temp file + `os.replace`) for any new JSON storage path

## Schema regeneration

When you modify Pydantic models in `src/trace_mcp/schema/`, regenerate the JSON Schema:

```bash
python scripts/generate_schema.py
```

This updates `schemas/trace-v0.4.json` from `Session.model_json_schema()`.

## Extension development

Extensions live in `src/trace_mcp/extensions/<name>/` and are auto-discovered via `pkgutil.iter_modules`. Each extension must expose a `register(mcp, storage)` function in its `__init__.py`. See `extensions/learn/` for the reference implementation.

Core (`server.py`, `schema/`, `storage/`, `tools/`, `exporters/`, `scratchpad.py`, `extension_status.py`) must not import from `extensions/` — extensions integrate via the hook registry in `extension_hooks.py`. This boundary is **normative**; see [ADR 003](docs/adr/003-core-extension-boundary.md) for the rationale and the Tier-3 scope rule. It is CI-enforced by `tests/test_v041_core_extension_boundary.py` — deleting `extensions/learn/` must leave all 17 core tools functional.

## Adapter development

Host adapters (`src/trace_mcp/adapters/<host>/`) install hook scripts and config files into a consumer project. They are **pure installers**: they run only at `trace-mcp-init` time and are never imported by the MCP server runtime. Core has zero imports from `adapters/`. See `adapters/claude_code/` for the reference implementation and `adapters/codex/README.md` for the placeholder spec.

## Pull request guidelines

1. All tests must pass (`uv run pytest`)
2. No new ruff or pyright errors on `src/`
3. Add tests for new functionality
4. Keep PRs focused — one feature or fix per PR
5. If modifying schema models, regenerate the JSON Schema

## Development roadmap

Development is organized into three tiers, implemented sequentially.

### Tier 1: close the loop (completed)

- **Stemming for BM25** — Lightweight suffix-stripping stemmer handling plurals, gerunds, and past tense.
- **Real LLM integration tests** — Tests that call the actual OpenAI API for scoring and extraction.
- **Per-backend thresholds** — Each matching backend has a tuned default threshold (BM25: 0.15, LLM: 0.2, Jaccard: 0.1).

### Tier 2: production hardening (completed)

- **Decay and staleness** — Exponential decay based on time since last surfaced. Frequently-surfaced learnings are protected by an evergreen floor (default 0.8 at 3+ recalls). Configurable half-life (default 365 days).
- **Deduplication** — Jaccard similarity check during `add_learning` to skip near-duplicates. Default threshold 0.85.
- **Recall tracking** — `recall_count` and `last_surfaced` fields on Learning, incremented on each recall.
- **Knowledge store metrics** — `trace_project_summary` includes a `knowledge` section with total learnings, category breakdown, most-surfaced (top 5), never-surfaced count, and average recall count.

### Tier 3: adaptive learning (future)

- **Learning feedback loop** — When a surfaced learning leads to a better decision, boost its weight. When a surfaced learning is ignored and the mistake repeats, demote it. RL-like mechanism that closes the gap between recording and learning.
- **Cross-project learnings** — Some corrections are universal (e.g., "always check conda env"). A global knowledge store that aggregates across projects would surface these universally.

## Design principles

- **Pydantic v2** for all data models with strict validation
- **Async throughout** — MCP servers are async by design
- **Fail open** — Audit errors warn, never block workflows
- **Human-readable IDs** — `trace_20260205_a1b2c3`, not UUIDs
- **UTC ISO 8601 timestamps** everywhere
- **Pretty-printed JSON** — `indent=2`, openable in any editor
- **No external dependencies** beyond `mcp` and `pydantic` (OpenAI optional)
- **Atomic writes** — Temp file + `os.replace` prevents corrupt stores

## Install extras

TRACE is not yet on PyPI — the published distribution name is being finalized.
For now, install from a local clone (`uvx --from <path-to-clone> trace-mcp`, see
the [README](README.md#install)) or `uv pip install -e ".[dev]"` for
development. Once published, the extras will be:

```bash
# After PyPI publication (distribution name pending):
pip install <trace-dist>              # Core only (BM25 matching)
pip install <trace-dist>[llm]         # + OpenAI embeddings & LLM matching
pip install <trace-dist>[embeddings]  # + model2vec local embeddings
pip install <trace-dist>[all]         # Everything
```
