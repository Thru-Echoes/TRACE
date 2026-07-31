[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21711455.svg)](https://doi.org/10.5281/zenodo.21711455) [![CI](https://github.com/Thru-Echoes/TRACE/actions/workflows/ci.yml/badge.svg)](https://github.com/Thru-Echoes/TRACE/actions/workflows/ci.yml) [![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/Thru-Echoes/TRACE/blob/main/LICENSE) [![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://github.com/Thru-Echoes/TRACE/blob/main/pyproject.toml)

# Why TRACE? 

In the age of AI, how do we know *who* proposed *what* in a scientific or coding dev workflow? Was the idea for that methodological decision made by *AI* or by a *human*? And when decisions are proposed by AI, are they being accepted, rejected, or iterated on? 

What does the solution to this look like? 

<p align="center">
  <img src="https://raw.githubusercontent.com/Thru-Echoes/TRACE/main/docs/provenance.svg" width="900" alt="Timeline of a real captured TRACE session: two decisions (one proposed by AI and accepted by a human, one still awaiting resolution), a discovery, two contributions attributed direction-human/execution-AI, a to-do, and a correction that retracts one of the contributions.">
</p>

Every row above is a real event from session `trace_20260730_32c108`, captured as
the work happened. Note what a diff could not have told you: one decision is
still **awaiting resolution** because the AI is not permitted to resolve its own
proposal, and the last event **retracts** an earlier contribution that turned out
to be wrong. Regenerate it from any session with
`python3 scripts/make_provenance_animation.py <session.json>`.

**One sentence from you, fully-scoped session from Claude:**

<p align="center">
  <img src="https://raw.githubusercontent.com/Thru-Echoes/TRACE/main/docs/trace-use-case-3.png" width="650" alt="Claude Code: a single prompt — 'start a TRACE session and review the current manuscript for submission readiness' — produces auto-recalled learnings and a five-item task plan.">
</p>

1. From inside `TRACE/`, ask Claude to start a session and review the manuscript (which is inside sibling dir, `TRACE-research/`).
2. Past-session memory makes Claude pivot to that sibling repo before logging anything.
3. `trace_start_session` runs there, learnings auto-recall, and a five-item task plan emerges.

## **TRACE: Transparent Recording of AI-assisted Collaboration Experiments**

TRACE is an MCP server that provides a standardized audit trail for AI-assisted research workflows. It records tool calls, decisions, annotations, contributions, and actor attribution — who proposed what, who accepted or revised it, and why.

TRACE runs as a **sidecar** alongside your domain MCP servers. It doesn't proxy or intercept calls — the AI client explicitly logs events to TRACE, creating a complete, human-readable provenance record.

**Version:** 0.5.0 | **Spec:** v0.5.0 | **Schema:** `https://trace-protocol.org/v0.3` | **License:** Apache 2.0

> The schema URI is an identifier (per W3C PROV convention) and is not currently a resolvable URL. The machine-readable JSON Schema lives at [`schemas/trace-v0.5.json`](https://github.com/Thru-Echoes/TRACE/blob/main/schemas/trace-v0.5.json) in this repository.

**New in 0.5.0 — canonical project identity.** Additive; every v0.3.x and v0.4.x session loads unchanged. A project is identified by a stable canonical key rather than a free-text label, so case and separator variants of one name stop reading as separate projects, and two genuinely different projects can no longer be merged by a case-insensitive filesystem. A `TRACE_PROJECT` pin binds one server process to one project, and cross-project reads and writes fail closed. No capture record is ever rewritten — a mislabelled project is repaired by adding an alias.

See [CHANGELOG.md](https://github.com/Thru-Echoes/TRACE/blob/main/CHANGELOG.md) for the full entry and for 0.4.x, which added the Proposer Identity Rule, the `discovery` annotation category, URI-form corrections, and `host` / `parent_event_id` on tool calls. Design rationale lives in [the ADRs](https://github.com/Thru-Echoes/TRACE/tree/main/docs/adr); worked examples in [docs/examples.md](https://github.com/Thru-Echoes/TRACE/blob/main/docs/examples.md).

## Why decision provenance?

Existing AI observability stacks (LangSmith, Langfuse, OpenTelemetry GenAI semconv) capture **call-level** traces — what tool an agent called, with what inputs, and what came back. They do not capture **decision-level provenance** — who proposed each step, whether a human reviewed it, what alternatives were rejected. The cost is visible in practice: in a preliminary rubric audit of agentic-AI deployments in environmental science, *analytical* decision provenance scored markedly lower than basic workflow description, and several recently-published papers showed discrepancies such as model details that did not match the cited models, or analyses that could not be reproduced from the reported description.

The need is also moving from norm to regulation. The **EU AI Act** (Articles 12, 19; applicable to high-risk systems August 2, 2026), **California SB 942 (Transparency AI Act)** (applicable August 2, 2026), **Colorado SB 24-205** (effective June 30, 2026), the **FDA PCCP final guidance** (December 2024), the **NIST AI Risk Management Framework**, and **ISO/IEC 42001:2023** all require some form of decision-process documentation. TRACE is designed so that documentation is a workflow byproduct, not an after-the-fact compilation.

## Core concept: the decision chain

Every TRACE decision carries an **actor** (who proposed, who resolved), a **disposition** (proposed → accepted / revised / rejected), a **rationale**, a **suggestion_type** (proactive / requested / collaborative), and an optional `revises_event_id` linking to a prior decision. Decisions form a provenance DAG, not a flat log — a future reader can reconstruct who proposed what, why it landed where it did, and how the approach evolved during the session.

<p align="center">
  <img src="https://raw.githubusercontent.com/Thru-Echoes/TRACE/main/docs/trace-example-corp-sus-extractor-2.png" width="560"
       alt="Three TRACE events from a corp-sus-report-extractor session: a human-accepted decision, an AI-proposed alternative logged after rejection, and a correction annotation linking back to the rejection.">
</p>

Three events from a real `corp-sus-report-extractor` session: a human-proposed scope decision (`evt_002`, accepted), an AI-proposed alternative kept for provenance after rejection (`evt_003`), and a correction annotation linked to the rejection via `corrects_event_ids` (`evt_004`). Rejected alternatives and corrections are first-class events — they don't get discarded.

## Preliminary deployment results

Between 2026-03-18 and 2026-07-30, TRACE was used across **7 sustained research and development projects**:

| Project | Domain | Sessions | Decisions | Contributions | Corrections |
|---|---|---:|---:|---:|---:|
| trace-mcp (self-host / meta) | Protocol research | 89 | 132 | 206 | 26 |
| corp-sus-report-extractor | Corporate sustainability disclosure | 54 | 91 | 132 | 18 |
| REAP | Environmental discourse analysis | 38 | 65 | 112 | 18 |
| When-Algorithms-Meet-Artists | Computational art / cultural studies | 31 | 56 | 95 | 5 |
| trace-research | Manuscript / literature synthesis | 30 | 48 | 76 | 18 |
| waggle | Applied agentic tooling | 24 | 31 | 76 | 6 |
| green-narrative | Environmental narrative analysis | 23 | 38 | 40 | 8 |
| **Total** | | **289** | **461** | **737** | **99** |

Decisions: **68% AI-proposed / 32% human-proposed**. Of the 344 *resolved* decisions, 89% accepted, 7% revised, 4% rejected; a further 117 remain in the `proposed` state, because an AI may not resolve its own proposal and not every proposal gets answered. The acceptance rate is not rubber-stamping — the 23 revisions, 15 rejections, and 99 separately-logged corrections are the active human steering this protocol exists to surface, and each one is an alternative that a commit history would have discarded.

Contributions: **68% human-directed, 24% collaborative, 8% AI-directed**; 65% are human-directed *and* AI-executed. Pure AI-directed-and-executed work is a small minority. The dominant pattern is human direction with AI execution — which existing authorship and attribution norms cannot describe.

> **What these counts include.** Figures were taken on 2026-07-30 from per-session logs in `~/.trace/sessions/`, which are not committed here (they contain project-internal content), and cover the seven named projects only. The store also holds meeting-transcription namespaces, throwaway test keys, and short exploratory sessions; those are excluded, since a transcript namespace accumulates thousands of machine-written annotation events and no decisions, and counting them would inflate a raw event total by more than an order of magnitude without adding a single act of provenance. Counts here are the deliberately logged event types. Sessions recorded before v0.5 under the display label `TRACE` are counted under `trace-mcp`, which is the same project under its canonical key. Reproducible from those logs via `trace_project_summary`; an aggregated, de-identified export can be provided on request.

## Architecture

```
AI Client (any MCP-aware client: Claude Code, Cursor, ChatGPT, Codex, ...)
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

### Configure your MCP client

Add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "trace": {
      "command": "uvx",
      "args": [
        "--from", "/path/to/TRACE",
        "--with", "openai", "--with", "numpy", "--with", "model2vec",
        "--refresh-package", "trace-mcp", "trace-mcp"
      ],
      "env": { "TRACE_PROJECT": "your-project-key" }
    }
  }
}
```

Using `uvx` builds the package into an isolated environment, avoiding `.venv` breakage from Python upgrades. `--refresh-package` rebuilds TRACE from the source tree on the next server start, without re-resolving the whole dependency set each time.

Two parts of that config are easy to omit and worth keeping:

- **The three `--with` packages are what make this a 22-tool server.** They are the optional dependencies of the trace-learn extension. Without them the server still starts and still records provenance, but the extension does not load and you get the 17 core tools with no error to tell you why.
- **`TRACE_PROJECT` pins the process to one project.** With it set you omit `project` from `trace_start_session`, and cross-project reads and writes fail closed. Without it, pass `project="..."` explicitly on every session start — an unpinned server rejects the call rather than guessing.

### Install hooks

`trace-mcp-init` installs the host-side enforcement: hook scripts under `.claude/hooks/`, registrations merged into `.claude/settings.json`, and a marker block appended to `CLAUDE.md`.

```bash
trace-mcp-init                          # auto-detect host (default)
trace-mcp-init --client claude-code     # explicit
trace-mcp-init --dry-run                # preview, no writes
```

The Claude Code adapter installs four hooks:

| Hook | Event | Purpose |
|------|-------|---------|
| `session-reminder.sh` | `SessionStart` | Reminds you to start a TRACE session if one isn't active for the current project. Project detection: `CLAUDE.md` → git repo basename → cwd basename. |
| `prompt-reminder.sh` | `UserPromptSubmit` | Periodic nudge after several prompts without a session. Per-project rate-limited. |
| `pretool-guard.sh` | `PreToolUse` (`Edit\|Write`) | Warns (or blocks) edits when no TRACE session is active. |
| `decision-audit.sh` | `PostToolUse` (`trace_end_session`) | Echoes the session-end attribution audit into the conversation. |

Project detection uses, in order: a `TRACE project name: "..."` line in `CLAUDE.md`, the git repository basename, then the current working directory basename. Add the explicit marker if your repo name differs from the project name you want logged.

**Codex** support is scaffolded as a placeholder; see [`src/trace_mcp/adapters/codex/README.md`](https://github.com/Thru-Echoes/TRACE/blob/main/src/trace_mcp/adapters/codex/README.md) for the hook primitives a Codex adapter would need.

Worked examples for logging decisions, corrections, contributions, and decision chains live in [`docs/examples.md`](https://github.com/Thru-Echoes/TRACE/blob/main/docs/examples.md).

#### Hook environment variables

| Variable | Default | Effect |
|---|---|---|
| `TRACE_GUARD` | `soft` | `pretool-guard.sh` mode: `off` (no-op), `soft` (warn-only), `strict` (exit 2 to block when no session is active). |
| `TRACE_PROMPT_MIN_TURNS` | `3` | Minimum prompt turns before `prompt-reminder.sh` will nudge. |
| `TRACE_PROMPT_COOLDOWN_SEC` | `300` | Wall-clock cooldown between nudges from `prompt-reminder.sh`. |
| `TRACE_RUNTIME_DIR` | `~/.trace/runtime` | Per-project nudge state (`<project>.state.json`). Safe to delete to reset. |
| `TRACE_SOURCE_PATH` | _unset_ | Override what `trace-mcp-init` writes into `.mcp.json` as `uvx --from <X>`. Set to a local TRACE clone path. **Required when running init from an installed wheel** — with no override, init fails closed rather than writing the PyPI name `trace-mcp`, which belongs to an unrelated package (dependency confusion). |

### Run a first session

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

## Available tools (22 total)

### Core tools (17)

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

### Extension: trace-learn (5) — default for new sessions

| Tool | Description |
|------|-------------|
| `trace_learn_recall` | Find relevant past learnings via text similarity and tag matching |
| `trace_learn_add` | Manually add a learning to the knowledge store |
| `trace_learn_list` | List all learnings (optionally filtered by category) |
| `trace_learn_forget` | Remove a learning by ID |
| `trace_learn_extract` | Extract learnings from session events (annotations, rejected decisions, contributions) |

## Event types

| Type | Description | Key Fields |
|------|-------------|------------|
| **tool_call** | Invocation of an MCP server, host-internal helper, or external tool | server, name, input, output, status, `retries_event_id`, **`host`** (v0.4.1: `mcp`/`internal`/`external`), **`parent_event_id`** (v0.4.1: links dispatched child to controller) |
| **decision** | Methodological decision with attribution | description, rationale, disposition, `suggestion_type`, `revises_event_id` |
| **annotation** | Learning, gotcha, correction, observation, todo, question, **discovery** (v0.4.1) | category, content, `corrects_event_ids` (v0.4.1: MAY use URI-form schemes `external:`, `jsonl:`, `subagent:`, `tool-result:`) |
| **state_change** | Environment or configuration change | description, field, old_value, new_value |
| **contribution** | Work product with direction/execution attribution | description, direction, execution, artifact, `related_decision_ids` |

## Knowledge persistence (trace-learn)

The default `trace-learn` extension surfaces relevant past learnings at session start, on-demand via `trace_learn_recall`, and when decisions are proposed — and auto-extracts new learnings at session end. Matching uses cloud LLM scoring only when explicitly opted in (`TRACE_LLM_ENABLED=true` plus an `OPENAI_API_KEY`), with BM25 fallback otherwise. Storage: `~/.trace/knowledge/{project_key}.json`, named by the canonical project key rather than the display label (env: `TRACE_KNOWLEDGE_DIR`).

See [`docs/extensions/trace-learn.md`](https://github.com/Thru-Echoes/TRACE/blob/main/docs/extensions/trace-learn.md) for matching backends, BM25 stemming, per-backend thresholds, extraction details, and LLM configuration.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TRACE_PROJECT` | _unset_ | Pins the server process to one project key. With it set, `project` is optional on `trace_start_session` and cross-project reads and writes fail closed. Unset, the call requires an explicit `project`. |
| `TRACE_REQUIRE_PIN` | `false` | Set `1` to refuse **both** session-creation paths on an unpinned process, including the implicit auto-create a logging call would otherwise perform. |
| `TRACE_ALLOW_CROSS_PROJECT_READS` | `false` | Set `1` to let a pinned server read other projects. Off by default; writes stay refused regardless. |
| `TRACE_DEFAULT_PROJECT` | `auto` | Quarantine key used when a session is auto-created with no project available. |
| `TRACE_REGISTRY_PATH` | `~/.trace/projects.json` | Canonical-key alias registry. |
| `TRACE_LOCK_TIMEOUT` | `15` | Seconds to wait for the knowledge-store lock before failing closed. |
| `TRACE_SESSIONS_DIR` | `~/.trace/sessions/` | Directory for session JSON files |
| `TRACE_KNOWLEDGE_DIR` | `~/.trace/knowledge/` | Directory for trace-learn knowledge stores |
| `TRACE_EGRESS_LOG` | `~/.trace/egress.jsonl` | Cloud-egress ledger: one JSONL line per cloud call trace-learn makes (the fact of the call — provider, endpoint, model, purpose, item count — never the content) |
| `TRACE_LOG_LEVEL` | `INFO` | Logging verbosity |
| `OPENAI_API_KEY` | — | OpenAI API key for LLM matching and extraction |
| `TRACE_LLM_MODEL` | `gpt-5.4-mini` | Model for LLM relevance scoring |
| `TRACE_LLM_EXTRACTION_MODEL` | `gpt-5.4-mini` | Model for LLM learning extraction |
| `TRACE_LLM_ENABLED` | `false` | Cloud LLM matching/extraction is opt-in: set `true` (with `OPENAI_API_KEY`) to enable |
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

## Export formats

- **JSON** — The native session file. Always available, always complete.
- **Markdown** — Human-readable summary with decision log, tool call table, annotations, and statistics.
- **PROV JSON-LD** — W3C PROV-compatible provenance graph for interoperability with other provenance systems.

## Specification

TRACE implements the **Decision Provenance for AI-Assisted Workflows** specification — a technology-agnostic standard defining what to record when humans and AI collaborate on research.

| Artifact | Location | Role |
|----------|----------|------|
| **Specification** | [`docs/specification.md`](https://github.com/Thru-Echoes/TRACE/blob/main/docs/specification.md) | Authoritative definition of the data model, semantics, and conformance rules. Technology-neutral. |
| **JSON Schema** | [`schemas/trace-v0.5.json`](https://github.com/Thru-Echoes/TRACE/blob/main/schemas/trace-v0.5.json) | Machine-readable formalization. Any JSON document validating against this schema is a conforming session document. |
| **Reference implementation** | This repository (`trace-mcp`) | An MCP server that produces conforming documents. One possible implementation — not the only one. |

The specification defines five event types (tool invocations, decisions, annotations, state changes, contributions), a decision lifecycle model (proposed / accepted / revised / rejected), and an actor taxonomy (human / ai / system). Any tool that produces JSON documents conforming to the schema implements the standard — no dependency on MCP, Python, or TRACE itself.

Validate session documents against the schema (the schema ships inside the package; `jsonschema` is provided by the `[validate]` or `[all]` extra):

```bash
trace-mcp validate ~/.trace/sessions/trace_*.json
```

Regenerate the schema from models: `python scripts/generate_schema.py` (writes the top-level spec artifact and the byte-identical packaged copy).

## File structure

```
src/trace_mcp/
    server.py              # MCP server entry point (FastMCP) + extension loader
    project_identity.py    # Canonical project keys + alias registry
    identity_cli.py        # `trace-mcp identity` migration subcommands
    identity_report.py     # Read-only drift and stray-store reporting
    init_project.py        # `trace-mcp-init`: .mcp.json, pin, adapter dispatch
    validate.py            # `trace-mcp validate` schema conformance CLI
    scratchpad.py          # Session-end scratchpad generator
    extension_hooks.py     # Hook registry for extension ↔ core integration
    schema/                # Pydantic v2 models (Session, TraceEvent, etc.)
    schemas/               # Packaged JSON Schema copy
    storage/               # Abstract interface, JSON file backend, locked writes
    tools/                 # MCP tool implementations
    exporters/             # Markdown and PROV JSON-LD exporters
    extensions/learn/      # trace-learn (default extension)
    adapters/              # Host adapters (claude_code, codex)
        claude_code/       # Hook scripts, settings template, CLAUDE_BLOCK
        codex/             # Placeholder spec for a Codex adapter
docs/
    specification.md       # Authoritative protocol spec
    examples.md            # Worked logging examples
    INVARIANTS.md          # Correctness invariants, site sets, enforcing tests
    adr/                   # Architecture Decision Records
    extensions/            # Extension documentation
```

The host-adapter layer is a pure installer; core has zero imports from `adapters/`. Adapters run only at `trace-mcp-init` time.

## Known limitations

- **One in-memory session cache per process** — `server.py` holds a single `active_sessions` dict, so a server process is designed for one AI client. Concurrent clients want separate server instances. This is a caching limit, not a durability one: every session write goes through a per-session lock and re-reads authoritative disk state first, so concurrent processes do not lose each other's updates.
- **File-based storage only** — All data is stored as JSON files. There is no database backend. Large-scale deployments would need a database adapter against the `TraceStorage` abstract interface.
- **LLM matching is optional** — Without an OpenAI API key, knowledge recall uses BM25 (keyword-based). Semantic similarity requires LLM configuration.

## Citation

Archived on Zenodo. Cite the **concept DOI** unless you need to pin a specific archive — it always resolves to the most recent version:

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21711455.svg)](https://doi.org/10.5281/zenodo.21711455)

```bibtex
@software{muellerklein_trace,
  author    = {Muellerklein, Oliver},
  title     = {{TRACE: Transparent Recording of AI-assisted Collaboration Experiments}},
  year      = {2026},
  publisher = {Zenodo},
  doi       = {10.5281/zenodo.21711455},
  url       = {https://doi.org/10.5281/zenodo.21711455}
}
```

The v0.5.0 archive specifically is [`10.5281/zenodo.21711456`](https://doi.org/10.5281/zenodo.21711456). [`CITATION.cff`](https://github.com/Thru-Echoes/TRACE/blob/main/CITATION.cff) carries both, so GitHub's "Cite this repository" button and any CFF-aware tool stay in sync with this section.

## Contributing

See [CONTRIBUTING.md](https://github.com/Thru-Echoes/TRACE/blob/main/CONTRIBUTING.md) for development setup, the test suite layout, code style requirements, schema regeneration, and the development roadmap.

## Changelog

See [CHANGELOG.md](https://github.com/Thru-Echoes/TRACE/blob/main/CHANGELOG.md).
