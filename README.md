# TRACE

**Transparent Recording of AI-assisted Collaboration Experiments**

TRACE is an MCP server that provides a standardized audit trail for AI-assisted research workflows. It records tool calls, decisions, annotations, contributions, and actor attribution — who proposed what, who accepted or revised it, and why.

TRACE runs as a **sidecar** alongside your domain MCP servers. It doesn't proxy or intercept calls — the AI client explicitly logs events to TRACE, creating a complete, human-readable provenance record.

**Version:** 0.3.0 | **Schema:** `https://trace-protocol.org/v0.3` | **License:** Apache 2.0

> The schema URI is an identifier (per W3C PROV convention) and is not currently a resolvable URL. The machine-readable JSON Schema lives at [`schemas/trace-v0.3.json`](schemas/trace-v0.3.json) in this repository.

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

## Why Decision Provenance?

Current AI observability stacks (LangSmith, Langfuse, OpenTelemetry GenAI semconv) capture call-level traces — what tool an agent called, with what input, with what result. They do not capture decision-level provenance: who proposed each step, whether a human reviewed it, what alternatives were rejected. Methodological decisions in research used to be made only by humans; existing review norms reflect that. Agentic AI now proposes and resolves choices alongside researchers — a new mode of collaboration that documentation norms were not built for.

The cost is empirically visible. A structured rubric audit of recently published agentic-AI deployments in environmental science finds analytical decision provenance averaging less than half the score of basic workflow description. Examples surfaced in the audit (anonymized):

- A peer-reviewed 2026 paper benchmarks five LLMs with uniformly wrong parameter counts, including one model that does not exist as a released variant. Its citation for that model resolves to an unrelated 1993 computer-graphics paper.
- A 2025 paper repeatedly describes its system as built on one open-weights LLM and even defines a generation function in those terms; the published code uses a different proprietary model with no presence of the named open-weights model anywhere.
- A 2026 multi-agent paper reports double-digit energy savings derived from a synthetic load formula applied post-hoc to LLM outputs, not from measured energy. The paper claims experiments were conducted "in real smart-home environments."

These are not isolated lapses. They are the predictable consequence of agentic AI being deployed faster than the documentation norms surrounding it have updated.

### Regulatory landscape (a 2026 inflection)

Decision-process documentation for AI-assisted workflows is moving from academic ideal to regulatory requirement:

- **EU AI Act, Articles 12 and 19** (Regulation 2024/1689) — high-risk AI systems must enable automatic event logging over the system's lifetime; logs retained at least six months. **Applicable to high-risk systems August 2, 2026.**
- **Colorado AI Act (SB 24-205)** — deployers of high-risk AI must maintain three years of audit trails, impact assessments, incident reports, and remediation documentation. **Effective June 30, 2026.**
- **FDA PCCP final guidance** (December 2024) — marketing submissions for AI-enabled medical devices must document data lineage, performance tied to claims, bias analysis, and the human-AI workflow.
- **NIST AI Risk Management Framework** — Govern / Map / Measure / Manage functions all require organizations to document system provenance, trustworthiness characteristics, risks, and risk responses.
- **ISO/IEC 42001:2023** — 20+ mandatory documents under Clause 7.5 covering AI risk assessments, impact assessments, treatment, monitoring, and audits.

TRACE is designed to make the documentation these frameworks require a workflow byproduct rather than an after-the-fact compilation effort.

### Talks and venues

- **UC Open Summit 2026** (March 2026, Oakland CA) — TRACE presented as part of "Open Infrastructure for Collaborative Research."
- **Agentic AI Summit 2026** (Berkeley RDI, UC Berkeley, August 1–2, 2026) — talk submission in review on decision-provenance infrastructure for agentic AI.

## Preliminary Deployment Results

In the four weeks since the v0.3 release (2026-03-19 → present), TRACE has been actively used across five research workflows. These numbers are a snapshot — they will grow as the protocol matures.

| Project | Domain | Sessions | Events | Decisions | Corrections |
|---|---|---:|---:|---:|---:|
| When-Algorithms-Meet-Artists | Computational art / cultural studies | 22 | 114 | 27 | 4 |
| corp-sus-report-extractor | Corporate sustainability disclosure | 11 | 56 | 17 | 3 |
| TRACE (self-host / meta) | Protocol research | 9 | 54 | 16 | 6 |
| REAP | Environmental discourse analysis | 3 | 53 | 22 | 3 |
| green-narrative | Environmental narrative analysis | 7 | 50 | 19 | 5 |
| **Total** | | **52** | **327** | **101** | **21** |

### Decision attribution (101 decisions across 5 projects)

| Metric | Count | Share |
|---|---:|---:|
| Proposed by AI | 45 | 45% of all decisions |
| Proposed by human | 56 | 55% of all decisions |
| Accepted (of resolved) | 55 | 86% of resolved |
| Revised (of resolved) | 4 | 6% of resolved |
| Rejected (of resolved) | 5 | 8% of resolved |
| Pending (no resolution) | 37 | 37% of all decisions |

The 86% acceptance rate is not rubber-stamping. TRACE captures both genuine alignment AND active human steering: 4 revisions, 5 outright rejections, and 21 separately-logged corrections where a human caught and fixed an AI mistake.

### Contribution attribution (146 contributions across 5 projects)

| Direction → Execution | Count | Share |
|---|---:|---:|
| Human-directed → AI-executed | 106 | 73% |
| Collaborative-directed → AI-executed | 29 | 20% |
| AI-directed → AI-executed | 11 | 8% |

Direction (who had the idea) is tracked separately from execution (who did the work). Pure AI-directed-and-executed contributions are 8% of the total; the dominant pattern is human direction with AI execution — the human-in-the-loop collaboration current attribution norms cannot describe.

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
      "command": "uvx",
      "args": ["--from", "/path/to/TRACE", "--refresh-package", "trace-mcp", "trace-mcp"]
    }
  }
}
```

Using `uvx` builds the package into an isolated environment, avoiding `.venv` breakage from Python upgrades. The `--refresh-package` flag ensures source changes are picked up on next server start.

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

## Available Tools (23 total)

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
TRACE_LLM_MODEL=gpt-5.4-mini             # Model for matching/scoring
TRACE_LLM_EXTRACTION_MODEL=gpt-5.4-mini  # Model for extraction (can be different)
TRACE_LLM_ENABLED=true                   # Set false to force BM25-only
TRACE_STRICT_LLM=true                    # Fail loudly on LLM errors (default: true when key set)
```

Environment variables take precedence over `.env` file values for CI/container use.

#### Strict vs Permissive LLM Mode

**Strict mode (default when `OPENAI_API_KEY` is set)** — LLM failures raise
`LLMFallbackError` instead of silently degrading to BM25/rule-based. This
ensures you know when LLM features aren't working rather than silently
getting lower-quality results. Backend selection is logged at `INFO` level
at startup so you always know which tier is active.

**Permissive mode (`TRACE_STRICT_LLM=false`)** — LLM failures fall back to
BM25/rule-based with a `WARNING` log. Use this in environments where
degraded operation is preferable to hard failures (e.g., CI fixtures, or
environments without reliable network access).

If `OPENAI_API_KEY` is not set at all, strict mode is disabled automatically
and BM25 is used without error — there's nothing to be strict about.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TRACE_SESSIONS_DIR` | `~/.trace/sessions/` | Directory for session JSON files |
| `TRACE_KNOWLEDGE_DIR` | `~/.trace/knowledge/` | Directory for trace-learn knowledge stores |
| `TRACE_LOG_LEVEL` | `INFO` | Logging verbosity |
| `OPENAI_API_KEY` | — | OpenAI API key for LLM matching and extraction |
| `TRACE_LLM_MODEL` | `gpt-5.4-mini` | Model for LLM relevance scoring |
| `TRACE_LLM_EXTRACTION_MODEL` | `gpt-5.4-mini` | Model for LLM learning extraction |
| `TRACE_LLM_ENABLED` | `true` | Set `false` to force BM25/rule-based only |
| `TRACE_STRICT_LLM` | `true` if key set, else `false` | Fail loudly on LLM errors instead of silent BM25 fallback |
| `TRACE_BM25_K1` | `1.5` | BM25 term frequency saturation parameter |
| `TRACE_BM25_B` | `0.75` | BM25 document length normalization parameter |
| `TRACE_TAG_WEIGHT` | `0.3` | Weight given to tag overlap in scoring (0.0–1.0) |
| `TRACE_DECAY_ENABLED` | `true` | Enable time-based decay for learning scores |
| `TRACE_DECAY_HALF_LIFE_DAYS` | `365.0` | Half-life for exponential decay (days) |
| `TRACE_EVERGREEN_RECALL_THRESHOLD` | `3` | Recalls needed for evergreen floor protection |
| `TRACE_EVERGREEN_FLOOR` | `0.8` | Minimum decay multiplier for evergreen learnings |
| `TRACE_DEDUP_ENABLED` | `true` | Enable content deduplication on add |
| `TRACE_DEDUP_THRESHOLD` | `0.85` | Jaccard similarity threshold for dedup |

## Export Formats

- **JSON** — The native session file. Always available, always complete.
- **Markdown** — Human-readable summary with decision log, tool call table, annotations, and statistics.
- **PROV JSON-LD** — W3C PROV-compatible provenance graph for interoperability with other provenance systems.

## Specification

TRACE implements the **Decision Provenance for AI-Assisted Workflows** specification — a technology-agnostic standard defining what to record when humans and AI collaborate on research.

| Artifact | Location | Role |
|----------|----------|------|
| **Specification** | [`docs/specification.md`](docs/specification.md) | Authoritative definition of the data model, semantics, and conformance rules. Technology-neutral. |
| **JSON Schema** | [`schemas/trace-v0.3.json`](schemas/trace-v0.3.json) | Machine-readable formalization. Any JSON document validating against this schema is a conforming session document. |
| **Reference implementation** | This repository (`trace-mcp`) | An MCP server that produces conforming documents. One possible implementation — not the only one. |

The specification defines five event types (tool invocations, decisions, annotations, state changes, contributions), a decision lifecycle model (proposed / accepted / revised / rejected), and an actor taxonomy (human / ai / system). Any tool that produces JSON documents conforming to the schema implements the standard — no dependency on MCP, Python, or TRACE itself.

Regenerate the schema from models: `python scripts/generate_schema.py`

## Using with Claude Code

Copy the skill file to teach Claude Code to automatically use TRACE:

```bash
cp docs/claude-code-skill.md ~/.claude/skills/TRACE.md
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
```

## Test Suite

### Overview

```bash
uv run pytest                     # Run all tests
uv run pytest -k llm              # Run real LLM integration tests only
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
| **trace-learn matching** | 74 | Stemmer (13), BM25 with stemming (4), BM25 index/normalization (9), Jaccard (8), LLM scoring (mocked, 3), backend selection (4), recall integration (7), BM25 vs Jaccard (1), per-backend thresholds (5), tag overlap (5), recall tracking (4), decay (12) |
| **trace-learn dedup** | 14 | find_duplicate, add_learning_dedup, dedup in extraction, threshold configurability |
| **Knowledge metrics** | 7 | project_summary knowledge section: totals, categories, most-surfaced, never-surfaced, averages |
| **trace-learn E2E** | 12 | Full pipeline: extract → persist → recall across sessions, config loading, correction chain tracking |
| **Recall layers** | 23 | 3-layer recall architecture (session start, on-demand, decision proposal), hook registration, format functions, auto-extract on session end, cross-session persistence |
| **Installation health** | 34 | Import checks, config resolution, extension loading |
| **E2E server** | 12 | Full MCP tool invocations through the server layer |

### What the Test Suite Does NOT Cover

- **Concurrent access** — No tests for multiple simultaneous sessions writing to the same store. File locking (`fcntl.flock`) is implemented but not stress-tested.
- **Large-scale performance** — No benchmarks for stores with hundreds of learnings or sessions with hundreds of events.
- **Network failure handling** — LLM backend tests verify fallback on API errors (mocked), but no tests simulate real network timeouts, rate limits, or partial responses.
- **MCP transport layer** — Tests call tool functions directly, not through the MCP stdio transport. The `test_e2e_server.py` tests use the tool layer but not the actual MCP protocol wire format.
- **Cross-project knowledge** — No tests for sharing learnings between different projects (planned for Tier 3).
- **Feedback loops** — No tests for boosting/demoting learning weights based on decision outcomes (planned for Tier 3).

## Development

```bash
uv pip install -e ".[dev]"   # Install with dev dependencies
uv run pytest                 # Run full test suite
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

#### Tier 2: Production Hardening (completed)

- **Decay and staleness** — Exponential decay based on time since last surfaced (not creation date). Frequently-surfaced learnings are protected by an evergreen floor (default 0.8 at 3+ recalls). Configurable half-life (default 365 days).
- **Deduplication** — Jaccard similarity check during `add_learning` to skip near-duplicates before they enter the store. Integrated into both extraction backends. Default threshold 0.85.
- **Recall tracking** — `recall_count` and `last_surfaced` fields on Learning, incremented on each recall. Callers save the store after recall.
- **Knowledge store metrics** — `trace_project_summary` includes a `knowledge` section with total learnings, category breakdown, most-surfaced (top 5), never-surfaced count, and average recall count.

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

## Changelog

### v0.3.0 (2026-03-18)

- **Attribution audit**: `trace_end_session` returns a structured attribution audit summarizing contributions (direction/execution), decisions (disposition), corrections, and human interventions
- **`conversation_snippet`**: `trace_log_contribution`, `trace_log_annotation`, and `trace_propose_decision` accept a `conversation_snippet` parameter (~200 chars of relevant user message)
- **Path sanitization**: Session IDs and project names sanitized against directory traversal attacks via `sanitize_name()`
- **Schema cleanup**: Removed dead fields `verification` (TraceEvent) and `parent_event_id` (EventContext)
- **Error handling**: `resolve_decision()` raises `ValueError` for missing decisions instead of returning error strings
- **Scratchpad**: Auto-generates human-readable session summaries to `.claude/SCRATCHPAD.md` at session end
- **Embedding backend**: Cosine similarity on precomputed vectors (OpenAI `text-embedding-3-small` or model2vec `potion-base-8M` local). Sub-millisecond recall after initial embedding.
- **50 new tests**: Path traversal, corrupt JSON, conversation_snippet roundtrip, attribution audit, BM25 edge cases, decay, export edge cases

### v0.2.0 (2026-02-15)

- Contribution logging with direction/execution attribution
- Decision `suggestion_type` (proactive/requested/collaborative)
- Project summaries with aggregated metrics
- Correction annotations with `corrects_event_ids`
- Tool call retry chains via `retries_event_id`
- Human intervention metrics

### Install Extras

```bash
pip install trace-mcp              # Core only (BM25 matching)
pip install trace-mcp[llm]         # + OpenAI embeddings & LLM matching
pip install trace-mcp[embeddings]  # + model2vec local embeddings
pip install trace-mcp[all]         # Everything
```

## Known Limitations

- **Single-client server** — TRACE uses global state in `server.py` (one `active_sessions` dict). It is designed for a single AI client; concurrent clients would need separate server instances.
- **File-based storage only** — All data is stored as JSON files. There is no database backend. For large-scale deployments, a database adapter would need to be implemented against the `TraceStorage` abstract interface.
- **No concurrent write protection on Windows** — The atomic write pattern (temp file + `os.replace`) works cross-platform, but there is no file locking on Windows.
- **LLM matching is optional** — Without an OpenAI API key, knowledge recall uses BM25 (keyword-based). Semantic similarity requires LLM configuration.

## Paper Context

The paper introducing TRACE focuses on the **audit standard** and **decision provenance** for AI-assisted research workflows — not the learning system. The trace-learn extension provides supporting infrastructure for knowledge persistence, but the core contribution is the TRACE protocol itself: a standardized way to record who proposed what, who accepted or revised it, and why.
