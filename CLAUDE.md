# TRACE — Project Instructions

> **Full documentation**: [README.md](README.md) (architecture, tools, configuration, changelog)
> **Orientation**: [docs/ONBOARDING.md](docs/ONBOARDING.md) (dev + user onboarding) · [docs/WHAT-IS-TRACE.md](docs/WHAT-IS-TRACE.md) (non-technical explainer)
> **Formal specification**: [docs/specification.md](docs/specification.md)
> **Version**: 0.5.1 (package) · protocol/schema v0.5.1
> **TRACE project name**: "trace-mcp" (canonical project key: `trace-mcp`)

---

## Development

```bash
uv pip install -e ".[dev]"          # Install with dev dependencies
uv run pytest                       # Run full test suite (1240+ tests)
uv run pytest tests/test_invariants.py       # Invariant-registry guard (docs/INVARIANTS.md) — fast
uv run pytest -k llm                # Run real LLM integration tests
uv run ruff check src/              # Lint
uv run pyright src/                 # Type check
python scripts/generate_schema.py   # Regenerate JSON Schema from Pydantic models
uv build && uv run pytest tests/test_packaging_artifacts.py   # Verify the shipped wheel/sdist before tagging
trace-mcp identity check            # Report project-identity drift (non-zero exit on findings)
trace-mcp doctor [DIR] [--live]     # Report deployed-state drift for one project (non-zero exit on findings)
trace-mcp fleet-check [ROOTS...]    # Same check across every TRACE project under the given roots
```

Two console scripts ship with the package: `trace-mcp` (the MCP server) and
`trace-mcp-init` (writes `.mcp.json`, the `TRACE_PROJECT` pin, and host adapter
assets into a consumer project). `trace-mcp identity` dispatches to the
migration tooling (`snapshot`, `scan`, `apply`, `check`, `merge-stores`,
`adopt`, `bundle`) — see [`docs/adr/006-project-identity-and-isolation.md`](docs/adr/006-project-identity-and-isolation.md).

### Key Patterns

- **Pydantic v2** for all data models: `model_dump()`, `model_validate()`, `model_rebuild()`
- **Forward refs across files**: Import both models in `schema/__init__.py`, call `model_rebuild()` after
- All modules import `Session` from `trace_mcp.schema` (not `.schema.session`) to trigger rebuild
- `from datetime import UTC` (not `timezone.utc`) — ruff UP017
- **FastMCP** `@mcp.tool()` needs parentheses
- `asyncio_mode = "auto"` in pytest config for async tests
- **Atomic writes** (temp file + `os.replace`) for all JSON writes — no `fcntl` dependency (cross-platform)
- **Session writes go through `storage.locked.locked_disk_session`** — the single fail-closed, disk-truth read-modify-write path (INV-1, `docs/INVARIANTS.md`). Never hand-roll a lock block; never let a write proceed on a lock timeout.
- **Fail closed on integrity primitives**: the per-session lock raises `TimeoutError` rather than writing unlocked; stale-lock theft is gated on holder-PID liveness. A missed lock must be *visible*, not silent.
- **Read aggregates skip-and-report**: `project_summary`/`health_check` catch per-session `ValidationError`/`JSONDecodeError` and surface a `skipped_sessions` list rather than aborting the whole aggregate.
- **Schema models preserve unknown fields** (`extra="allow"` via the `TraceModel` base) for forward-compat; `Environment` is the one closed exception (legacy `trace_version` drop).
- **A project is identified by its canonical key, never by its free-text label** — `metadata.project_key` is authoritative when present; a document without one resolves through the alias registry (`~/.trace/projects.json`). `project` stays an unconstrained display label. All matching and filtering goes through `project_identity` (INV-4, INV-9); never compare labels directly.
- **Label repair is alias-table-first** — a mislabelled project is fixed by adding an alias, never by rewriting a capture record. `auto` and `shared` are reserved keys.
- **Cross-project reads and writes fail closed** when the server is pinned via `TRACE_PROJECT`; `TRACE_REQUIRE_PIN` additionally refuses both session-creation paths on an unpinned process.
- `server.py` imports `__version__` from `trace_mcp` for startup log
- **Per-project OpenAI key**: the key lives in the project's own `.env`, which
  overrides the machine-global `~/.trace/.env` (an exported env var still wins
  over both). `TRACE_LOCAL_ONLY` is the one exception — a restrict-only ratchet
  ORed across sources, so no project can switch a machine-wide kill switch off.
  Config is read once at server start; a `.env` edit needs a server restart.
- **A missing or refused key is loud**: reported in the `trace_start_session`
  banner and in the affected `trace_learn_*` responses, and a provider 401/403
  raises `ApiKeyRejectedError` regardless of `TRACE_STRICT_LLM`. A backend that
  cannot be built degrades to keyword matching *with a notice* rather than
  failing registration — an unregistered extension is indistinguishable from an
  uninstalled one.
- **Line length**: 120 (ruff configured)

### Architecture Quick Reference

```
src/trace_mcp/
    server.py              # MCP server entry point (FastMCP) + extension loader
    project_identity.py    # Canonical project keys + alias registry (~/.trace/projects.json)
    identity_cli.py        # `trace-mcp identity` migration subcommands
    identity_report.py     # Read-only drift/stray-store reporting for the CLI
    conformance/           # `trace-mcp doctor` / `fleet-check` — deployed-state checks (INV-11)
    init_project.py        # `trace-mcp-init`: .mcp.json, TRACE_PROJECT pin, adapter dispatch
    scratchpad.py           # Session-end scratchpad generator
    extension_hooks.py     # Hook registry for extension ↔ core integration
    schema/                # Pydantic v2 models (Session, TraceEvent, etc.)
    storage/               # Abstract interface + JSON file backend
    tools/                 # MCP tool implementations (session, logging, decision, query, export)
    adapters/              # Host installers (claude_code, codex) — pure, zero runtime imports
    extensions/learn/      # trace-learn: cross-session knowledge persistence (default)
```

Extensions auto-discovered via `pkgutil.iter_modules` in `extensions/`. Each extension
provides a `register(mcp, storage)` function.

## Invariants & Pre-merge

Correctness invariants are registered in [`docs/INVARIANTS.md`](docs/INVARIANTS.md)
— one entry per invariant with its exhaustive site-set and enforcing test.
Twelve are registered and enforced (INV-1 … INV-12), covering session writes,
completed-session immutability, decision validation, canonical-key project
scoping, cloud-egress attestation, project/session coherence, registry writes,
the knowledge-store lock, learn-tool label guarding, version-declaration
consistency, conformance of a freshly initialized project, and learning-id
uniqueness.
`tests/test_invariants.py` runs as a dedicated CI step and **fails when a new
site appears that is not registered** — a new `storage.update_session` caller
outside `locked_disk_session`, a new OpenAI call site that does not
`attest_egress()` first, a new registry write that bypasses `locked_registry`.
This is the durable defense against the recurring defect pattern — *an invariant
enforced in one place but not uniformly.*

**Before merging a change that touches a write/read path, packaging, or a
registered invariant:**

1. Run the invariant guard — `uv run pytest tests/test_invariants.py`.
2. If you add a session-write path, route it through `locked_disk_session` and
   register it in `docs/INVARIANTS.md` + `INV1_REGISTERED_WRITERS`. The same
   pattern applies to the other site-set registries (`INV5_EGRESS_CALL_SITES`,
   `INV7_REGISTRY_WRITE_SITES`).
3. For a release/packaging change, build and verify the *real* artifact
   (`uv build && uv run pytest tests/test_packaging_artifacts.py`) — the dev
   `uvx --from <path>` launcher builds differently than the published wheel, so a
   missing-file packaging bug can stay hidden until release.
4. For a deep pass (pre-release / before tagging / any storage or schema
   write-path change), run the saved multi-agent review — `Workflow({name: "status-review"})`.
   It *mints* findings you then convert into guards; it is not the recurring
   safety net (that's tiers 1–2: `/code-review`, the invariant guard, CI).

## Project Rules

- `.claude/rules/python-quality.md` — Code style, type checking, linting

## Skills

- `/trace-session` — Start a new TRACE session with standard boilerplate

## TRACE Protocol

This project logs its own development with TRACE. The full TRACE protocol
instructions are in the global `~/.claude/CLAUDE.md`. Key points:

- Start a session at the beginning of any multi-step workflow
- Log decisions BEFORE acting; log contributions AFTER the artifact exists
- Log rejected alternatives as separate decision events for significant methodology discussions
- End with a summary including what was accomplished and what is next
- The scratchpad auto-generates decisions, contributions, and corrections from session events
- This checkout's `.mcp.json` pins `TRACE_PROJECT=trace-mcp`, so omit the
  `project` argument to `trace_start_session` — the server resolves it, and
  cross-project reads and writes fail closed. An **unpinned** server instead
  rejects `trace_start_session` outright, which a client reasonably reports as
  "TRACE tools are unavailable"; if you see that, the pin is missing.
- Consumer projects build the server via `uvx --from <this checkout>`, which
  compiles the **currently checked-out working tree**. Leaving a half-finished
  branch checked out ships it to every consumer on their next server start.
  Keep `main` checked out unless actively testing a branch.

## Available Tools (22 total)

17 core tools + 5 trace-learn extension tools. See [README.md](README.md#available-tools-22-total) for the full table.

**Core**: `trace_start_session`, `trace_end_session`, `trace_log_tool_call`, `trace_log_annotation`, `trace_log_contribution`, `trace_log_state_change`, `trace_propose_decision`, `trace_resolve_decision`, `trace_get_session`, `trace_get_events`, `trace_get_decisions`, `trace_get_decision_chain`, `trace_search`, `trace_export`, `trace_list_sessions`, `trace_project_summary`, `trace_health_check`

**trace-learn**: `trace_learn_recall`, `trace_learn_add`, `trace_learn_list`, `trace_learn_forget`, `trace_learn_extract`

<!-- trace-mcp:claude-code -->

## TRACE Audit Protocol (v0.5.0+)

This project uses [TRACE](https://github.com/Thru-Echoes/TRACE) for transparent
documentation of AI-human collaboration. The TRACE MCP server is configured in
`.mcp.json` and enforced via `.claude/hooks/`.

**Absolute rule**: Never fabricate, falsify, or retroactively alter TRACE
data. A sparse honest record beats a dense fabricated one.

**Project identity (v0.5.0, spec §3.2 and §3.2.2)**

This project has a canonical project key, minted by `trace-mcp-init` and
recorded in `.claude/trace.project` (the hooks' highest-precedence source), in
`.mcp.json` as the `TRACE_PROJECT` env pin, and in the registry at
`~/.trace/projects.json`. The key — not the free-text display label — is what
identifies the project, so case and separator variants of the name no longer
read as separate projects.

- With the pin set, omit `project` from `trace_start_session`; the server
  resolves it. Cross-project reads and writes fail closed.
- Without a pin, pass `project="<label>"` explicitly.
- Never repair a wrong label by editing a captured session. Add an alias to
  the registry instead — capture records are not rewritten.

**Session lifecycle**

- **Start** a TRACE session at the beginning of any multi-step workflow.
- **End** with a summary when the workflow is complete. Review the
  Attribution Audit returned by `trace_end_session` before closing.

**What to log**

- **Decisions** (propose BEFORE acting, resolve when the human responds).
  - **Proposer Identity Rule (v0.4.1, spec §3.6)**: set `proposed_by` to the
    actor who authored the proposal *content* (whose words populate
    `description`), not the speaker of the resolving directive.
    Question→AI-proposal→accept means `proposed_by=ai`, `resolved_by=human`.
- **Corrections** when a participant catches a mistake.
  - If the corrected entity is not a TRACE event (subagent output, tool
    result, external claim), use a URI-form reference per spec §3.7.1:
    `external:<uri>` (universal fallback), `jsonl:<path>#L<line>`,
    `subagent:<id>`, or `tool-result:<id>`. `related_event_ids` is NOT
    for the correction relationship.
- **Discoveries (v0.4.1, `category="discovery"`)**: non-trivial findings
  from autonomous work — log AT THE MOMENT of discovery, not in a
  post-hoc summary.
- **Contributions** — one per artifact, with `direction` (who had the idea)
  and `execution` (who did the work). Always set `conversation_snippet`
  to the relevant user message (~200 chars). If no user message
  motivated the event, use the explicit absence marker
  `<autonomous-stretch>` (no user turn since the last decision) or
  `<no recent user message>` (general fallback) rather than omitting.
  Silent omission is a v0.4.1 protocol violation per spec §3.4.1.
- **Subagent dispatches** when their outcome is summarized by a
  contribution — `trace_log_tool_call(host="internal", server="claude-code",
  parent_event_id=...)` per spec §3.5. Skip routine file reads, greps,
  or TRACE's own calls.

Full protocol, including attribution rules, URI-form references, and
worked examples, lives at the [TRACE specification](https://github.com/Thru-Echoes/TRACE/blob/main/docs/specification.md).

<!-- /trace-mcp:claude-code -->
