# TRACE

**Transparent Recording of AI-assisted Collaboration Experiments**

TRACE is an MCP server that provides a standardized audit trail for AI-assisted research workflows. It records tool calls, decisions, annotations, contributions, and actor attribution — who proposed what, who accepted or revised it, and why.

TRACE runs as a **sidecar** alongside your domain MCP servers. It doesn't proxy or intercept calls — the AI client explicitly logs events to TRACE, creating a complete, human-readable provenance record.

**Version:** 0.2.0 | **Schema:** `https://trace-protocol.org/v0.2` | **License:** Apache 2.0

## Architecture

```
AI Client (Claude Code, Claude Desktop, etc.)
    |
    +-- connects to: Domain MCP Server(s)
    |                 (corpus search, NLP pipeline, data retrieval, etc.)
    |                 --> does the actual work
    |
    +-- connects to: TRACE MCP Server (this project)
                     --> records what happened to JSON files
                     --> persists learnings across sessions (trace-learn)
```

**Storage model:** One self-contained JSON file per session in `~/.trace/sessions/`. Files are human-readable (pretty-printed with `indent=2`), git-diffable, and shareable.

**Core stack:** Python 3.11+, Pydantic v2, async throughout, zero external dependencies beyond `mcp` and `pydantic` (OpenAI optional for LLM-enhanced features).

## Quick Start

### Install

```bash
uv pip install -e ".[dev]"
```

### Configure for Claude Code

Add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "trace": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/TRACE", "trace-mcp"]
    }
  }
}
```

Using `uv run` ensures a consistent virtual environment regardless of system Python changes.

### Run a First Session

Once configured, TRACE tools are available to the AI client:

```
You: "Start a TRACE session for our climate NLP analysis"

Claude: -> trace_start_session(project="climate-nlp", ...)
        "Session started: trace_20260205_a1b2c3"
        "Relevant learnings from past sessions:
          - [correction] Always use ml-dev conda env, not base (relevance: 87%)"

You: "Search for adaptation passages in the IPCC corpus"

Claude: -> [calls corpus-search-mcp/search_passages]
        -> trace_log_tool_call(server="corpus-search-mcp", ...)
        -> trace_propose_decision(description="Focus on chapters 14-17", ...)

You: "Also include chapter 6"

Claude: -> trace_resolve_decision(disposition="revised", ...)

You: "End the session"

Claude: -> trace_end_session(summary="Analyzed 47 passages...")
        (learnings auto-extracted and persisted for future sessions)
```

## Available Tools (28 total)

### Core Tools (18)

| Tool | Description |
|------|-------------|
| `trace_start_session` | Start a new audit session (auto-recalls relevant past learnings) |
| `trace_end_session` | End a session with summary (auto-extracts learnings) |
| `trace_log_tool_call` | Record a tool invocation on another MCP server |
| `trace_log_annotation` | Record a learning, gotcha, correction, observation, todo, or question |
| `trace_log_contribution` | Record a deliverable with direction (who had the idea) vs execution (who did the work) attribution |
| `trace_log_state_change` | Record an environment or configuration change |
| `trace_propose_decision` | Propose a methodological decision (with `suggestion_type`: proactive/requested/collaborative) |
| `trace_resolve_decision` | Accept, revise, or reject a proposed decision |
| `trace_get_session` | Get session metadata |
| `trace_get_events` | List events (filterable by type) |
| `trace_get_decisions` | List decisions (filterable by disposition and/or `proposed_by_type`) |
| `trace_get_decision_chain` | Walk linked decision revisions via `revises_event_id` |
| `trace_search` | Search events by text content |
| `trace_export` | Export as JSON, Markdown, or PROV JSON-LD |
| `trace_list_sessions` | List all sessions (filterable by project) |
| `trace_project_summary` | Aggregated metrics across all sessions for a project |
| `trace_health_check` | System health and event-level statistics |

### Extension: trace-learn (5) — Default for new sessions

Cross-session knowledge persistence. Learnings from one session are automatically surfaced in future sessions when relevant.

| Tool | Description |
|------|-------------|
| `trace_learn_recall` | Find relevant past learnings using text similarity and tag matching |
| `trace_learn_add` | Manually add a learning to the knowledge store |
| `trace_learn_list` | List all learnings (optionally filtered by category) |
| `trace_learn_forget` | Remove a learning by ID |
| `trace_learn_extract` | Extract learnings from session events (annotations, rejected decisions, contributions) |

**Storage:** `~/.trace/knowledge/{project}.json` (env var: `TRACE_KNOWLEDGE_DIR`)

### Extension: trace-evolve (5) — Legacy

Evolution-themed terminology for projects already using this extension. Same underlying functionality as trace-learn with different naming (mutate/express/select/extinct/fitness).

**Storage:** `~/.trace/evolution/{project}.json` (env var: `TRACE_EVOLUTION_DIR`)

## Core Concept: Decision Provenance

TRACE's differentiator is the **decision chain** — every decision has:

- An **actor** (who proposed and who resolved it)
- A **disposition** (proposed → accepted / revised / rejected)
- A **rationale** (why this choice was made)
- A **suggestion_type** (proactive, requested, or collaborative)
- An optional **revises_event_id** linking to prior decisions

This creates a provenance DAG of decisions, not just a flat log. A future reader can reconstruct: who proposed what, why it was accepted or rejected, and how the approach evolved during the session.

## Event Types

| Type | Description | Key Fields |
|------|-------------|------------|
| **tool_call** | MCP tool invocation on another server | server, name, input, output, status, `retries_event_id` |
| **decision** | Methodological decision with attribution | description, rationale, disposition, `suggestion_type`, `revises_event_id` |
| **annotation** | Learning, gotcha, correction, observation, todo, question | category, content, `corrects_event_ids` |
| **state_change** | Environment or configuration change | description, field, old_value, new_value |
| **contribution** | Work product with direction/execution attribution | description, direction, execution, artifact, `related_decision_ids` |

## Knowledge Persistence (trace-learn)

The trace-learn extension provides **cross-session memory**: corrections, gotchas, and learnings from past sessions are automatically surfaced when relevant in future sessions.

### How It Works

Knowledge flows through three layers:

1. **Session start** — When a new session starts, TRACE auto-recalls the most relevant past learnings based on the session description and tags.
2. **On-demand search** — At any time, `trace_learn_recall` can search for relevant knowledge.
3. **Decision proposal** — When a decision is proposed, related past learnings are surfaced as warnings (e.g., a past correction about using the wrong conda environment).

At session end, new learnings are automatically extracted from annotations and rejected/revised decisions.

### Matching Backends

TRACE uses a tiered matching system for finding relevant learnings:

| Backend | When Used | How It Works |
|---------|-----------|--------------|
| **LLM** (primary) | When `openai` is installed and `OPENAI_API_KEY` is configured | Sends context + candidate learnings to an OpenAI model for semantic relevance scoring. Understands synonyms, abbreviations, and conceptual similarity. Falls back to BM25 on any error. |
| **BM25** (fallback) | When no API key is available, or as pre-filter for LLM | Pure-Python BM25 ranking with stemming and tag boosting. Handles term frequency and document length normalization. Zero external dependencies. |
| **Jaccard** (legacy) | Backward compatibility only | Simple token-overlap scoring. Kept as absolute fallback. |

**Auto-selection:** LLM if the `openai` package is installed and an API key is configured, otherwise BM25.

### BM25 Stemming

BM25 includes a lightweight suffix-stripping stemmer that handles common English morphological variants without external dependencies:

- **Plurals:** decisions → decision, entries → entry, processes → process
- **Gerunds:** logging → log, implementing → implement (with doubled-consonant handling)
- **Past tense:** logged → log, implemented → implement
- **Multi-step:** learnings → learning → learn

This ensures that "decisions" in a query matches "decision" in a stored learning, and "logging" matches "log" — a class of morphological mismatches that previously caused recall failures.

### Per-Backend Thresholds

Each backend has a tuned default threshold to balance recall against false positives:

| Backend | Default Threshold | Rationale |
|---------|-------------------|-----------|
| BM25 | 0.15 | Higher than naive 0.1 to filter keyword-overlap noise |
| LLM | 0.20 | LLM scores are more semantically meaningful |
| Jaccard | 0.10 | Legacy, more permissive |

Thresholds can be overridden per-call via the `threshold` parameter on `trace_learn_recall`.

### Extraction

Learnings are extracted from session events via two backends:

| Backend | When Used | What It Does |
|---------|-----------|--------------|
| **LLM-enhanced** (primary) | When configured | Sends all session events to an OpenAI model which identifies valuable, actionable learnings and generates quality tags. Avoids duplicating existing learnings. |
| **Rule-based** (fallback) | When no API key | Processes annotations with category in {learning, correction, gotcha}, rejected/revised decisions (preserving rationale and revision notes), and collaborative contributions. |

Both backends are **idempotent** — running extraction twice on the same session produces no duplicates.

### LLM Configuration

Place your OpenAI API key in `~/.trace/.env` (shared across all TRACE projects):

```bash
OPENAI_API_KEY=sk-...
TRACE_LLM_MODEL=gpt-5-nano              # Model for matching/scoring
TRACE_LLM_EXTRACTION_MODEL=gpt-5-mini   # Model for extraction (can be different)
TRACE_LLM_ENABLED=true                  # Set false to force BM25-only
```

Environment variables take precedence over `.env` file values for CI/container use.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TRACE_SESSIONS_DIR` | `~/.trace/sessions/` | Directory for session JSON files |
| `TRACE_KNOWLEDGE_DIR` | `~/.trace/knowledge/` | Directory for trace-learn knowledge stores |
| `TRACE_EVOLUTION_DIR` | `~/.trace/evolution/` | Directory for trace-evolve genomes |
| `TRACE_LOG_LEVEL` | `INFO` | Logging verbosity |
| `OPENAI_API_KEY` | — | OpenAI API key for LLM matching and extraction |
| `TRACE_LLM_MODEL` | `gpt-5-nano` | Model for LLM relevance scoring |
| `TRACE_LLM_EXTRACTION_MODEL` | `gpt-5-mini` | Model for LLM learning extraction |
| `TRACE_LLM_ENABLED` | `true` | Set `false` to force BM25/rule-based only |
| `TRACE_BM25_K1` | `1.5` | BM25 term frequency saturation parameter |
| `TRACE_BM25_B` | `0.75` | BM25 document length normalization parameter |
| `TRACE_TAG_WEIGHT` | `0.3` | Weight given to tag overlap in scoring (0.0–1.0) |

## Export Formats

- **JSON** — The native session file. Always available, always complete.
- **Markdown** — Human-readable summary with decision log, tool call table, annotations, and statistics.
- **PROV JSON-LD** — W3C PROV-compatible provenance graph for interoperability with other provenance systems.

## Schema Reference

The formal protocol specification is a JSON Schema generated from the Pydantic models:

- [`schemas/trace-v0.2.json`](schemas/trace-v0.2.json)

Regenerate with: `python scripts/generate_schema.py`

## Using with Claude Code

Copy the skill file to teach Claude Code to automatically use TRACE:

```bash
cp skill/TRACE.md ~/.claude/skills/
```

The skill provides detailed guidance on when and how to log events, with five worked examples covering decisions, corrections, contributions, decision chains, and complex multi-event scenarios.

## File Structure

```
src/trace_mcp/
    server.py              # MCP server entry point (FastMCP) + extension loader
    hooks.py               # Hook registry for extension ↔ core integration
    schema/
        session.py         # Session, Actor, Environment, SessionMetadata
        events.py          # TraceEvent, ToolCallData, DecisionData, ContributionData, etc.
    storage/
        base.py            # Abstract storage interface
        json_file.py       # JSON file storage (one file per session)
    tools/
        session_tools.py   # start/end session
        logging_tools.py   # log tool calls, annotations, state changes, contributions
        decision_tools.py  # propose/resolve decisions
        query_tools.py     # search, retrieve, project summary
        export_tools.py    # export formatters
    extensions/
        learn/             # Cross-session knowledge persistence (default)
            __init__.py    # Registers 5 MCP tools + recall/extract hooks
            config.py      # Config from env vars and ~/.trace/.env
            models.py      # Learning, KnowledgeStore (Pydantic v2)
            store.py       # File I/O with atomic writes
            extraction.py  # Rule-based + LLM extraction backends
            matching.py    # BM25 (with stemming) + LLM + Jaccard matching backends
        evolve/            # Legacy evolution-themed extension
```

## Test Suite

### Overview

**455 tests, 0 failures** across 20 test files.

```bash
uv run pytest                                      # Run all tests
uv run pytest tests/test_trace_triggers.py -k llm   # Run real LLM tests only
```

### What the Test Suite Covers

| Area | Tests | What's Verified |
|------|-------|-----------------|
| **Schema validation** | 32 | Pydantic models, forward refs, event type validation, `model_rebuild()` |
| **Storage** | 14 | JSON file I/O, atomic writes, session CRUD, listing, filtering |
| **Core tools** | 14 | Session start/end, event logging, decision propose/resolve |
| **Exporters** | 17 | Markdown export, PROV JSON-LD export, format correctness |
| **trace-learn models** | 16 | Learning/KnowledgeStore validation, ID generation, serialization |
| **trace-learn store** | 25 | Load/save with atomic writes, add/remove/list learnings |
| **trace-learn extraction** | 27 | Rule-based extraction from annotations, decisions, contributions; LLM extraction (mocked); idempotency |
| **trace-learn matching** | 70 | Stemmer (13 tests), BM25 with stemming (4), BM25 index/normalization (9), Jaccard (8), LLM scoring (mocked, 3), backend selection (4), recall integration (7), BM25 vs Jaccard comparison (1), per-backend thresholds (5), tag overlap (5) |
| **trace-learn E2E** | 12 | Full pipeline: extract → persist → recall across sessions, config loading, correction chain tracking |
| **Recall layers** | 23 | 3-layer recall architecture (session start, on-demand, decision proposal), hook registration, format functions, auto-extract on session end, cross-session persistence |
| **Trigger behavior** | 21 | BM25 morphological recall (4), per-backend thresholds (3), recall hook triggers (4), extract hook triggers (3), multi-session E2E with stemming (3), **real LLM integration (4)** |
| **trace-evolve** | 27 | Evolution-themed extension: mutations, expressions, selections, extinction, fitness scoring |
| **Installation health** | 34 | Import checks, config resolution, extension loading |
| **E2E server** | 12 | Full MCP tool invocations through the server layer |
| **Verification (trace-vv)** | 84 | Integrity chains, snapshots, text analysis, verification engine, git reconciliation |

### What the Test Suite Does NOT Cover

- **Concurrent access** — No tests for multiple simultaneous sessions writing to the same store. File locking (`fcntl.flock`) is implemented but not stress-tested.
- **Large-scale performance** — No benchmarks for stores with hundreds of learnings or sessions with hundreds of events.
- **Network failure handling** — LLM backend tests verify fallback on API errors (mocked), but no tests simulate real network timeouts, rate limits, or partial responses.
- **MCP transport layer** — Tests call tool functions directly, not through the MCP stdio transport. The `test_e2e_server.py` tests use the tool layer but not the actual MCP protocol wire format.
- **Cross-project knowledge** — No tests for sharing learnings between different projects (planned for Tier 3).
- **Learning decay/staleness** — No tests for time-based relevance weighting (planned for Tier 2).
- **Deduplication on add** — No tests for detecting and merging near-duplicate learnings at insertion time (planned for Tier 2).
- **Feedback loops** — No tests for boosting/demoting learning weights based on decision outcomes (planned for Tier 3).

## Development

```bash
uv pip install -e ".[dev]"   # Install with dev dependencies
uv run pytest                 # Run full test suite (455 tests)
uv run pytest -k llm          # Run real LLM integration tests
uv run ruff check src/        # Lint
uv run pyright src/            # Type check
python scripts/generate_schema.py  # Regenerate JSON Schema
```

### Development Roadmap

Development is organized into three tiers, implemented sequentially.

#### Tier 1: Close the Loop (completed)

- **Stemming for BM25** — Lightweight suffix-stripping stemmer handling plurals, gerunds, and past tense. Fixes morphological mismatches like "decisions" not matching "decision".
- **Real LLM integration tests** — 4 tests that call the actual OpenAI API for scoring and extraction, verifying the full LLM pipeline works end-to-end.
- **Per-backend thresholds** — Each matching backend has a tuned default threshold (BM25: 0.15, LLM: 0.2, Jaccard: 0.1) to reduce false positives.

#### Tier 2: Production Hardening (next)

- **Decay and staleness** — Time-based relevance weighting so that 12-month-old learnings score lower than recent ones. Must protect evergreen learnings (e.g., foundational corrections that remain relevant throughout a project's lifetime).
- **Deduplication** — Similarity check during `add_learning` to merge or skip near-duplicates before they enter the store.
- **Knowledge store metrics** — `trace_project_summary` should include learning counts, recall hit rates, and which learnings have been surfaced most often.

#### Tier 3: Adaptive Learning (future)

- **Learning feedback loop** — When a surfaced learning leads to a better decision, boost its weight. When a surfaced learning is ignored and the mistake repeats, demote it. This is the RL-like mechanism that closes the gap between recording and learning.
- **Cross-project learnings** — Some corrections are universal (e.g., "always check conda env"). A global knowledge store that aggregates across projects would surface these universally.

### Design Principles

- **Pydantic v2** for all data models with strict validation
- **Async throughout** — MCP servers are async by design
- **Fail open** — Audit errors warn, never block workflows
- **Human-readable IDs** — `trace_20260205_a1b2c3`, not UUIDs
- **UTC ISO 8601 timestamps** everywhere
- **Pretty-printed JSON** — `indent=2`, openable in any editor
- **No external dependencies** beyond `mcp` and `pydantic` (OpenAI optional)
- **Atomic writes** — Temp file + rename prevents corrupt stores

## Paper Context

The paper introducing TRACE focuses on the **audit standard** and **decision provenance** for AI-assisted research workflows — not the learning system. The trace-learn extension provides supporting infrastructure for knowledge persistence, but the core contribution is the TRACE protocol itself: a standardized way to record who proposed what, who accepted or revised it, and why.
