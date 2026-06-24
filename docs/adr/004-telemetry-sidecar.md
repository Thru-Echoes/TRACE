# ADR 004: Telemetry sidecar — carbon & token-cost as an optional, estimate-provenanced layer

**Status:** accepted · **Date:** 2026-06-18

## Context

TRACE records decision provenance at the tool-call and decision level. Two
adjacent measurements are frequently requested:

1. **Environmental footprint** — the energy/carbon attributable to a tool call
   or AI decision.
2. **Self-cost** — how much of the host's token/credit budget TRACE itself
   consumes (its always-loaded tool schemas plus the arguments/results that flow
   through each `trace_*` call), and how that compares to the rework it avoids
   via `trace-learn`.

Both are structurally identical: attach an *estimated numeric measure* to an
event and roll it up. Both also collide with hard facts about where TRACE sits:

- **An MCP server is blind to host token accounting.** Tool handlers receive
  only the tool name and arguments; the host model's total context, per-turn
  token usage, cache behaviour, and credit burn never cross the JSON-RPC
  boundary. Carbon and self-cost therefore cannot be *measured* from inside a
  tool handler — at best they are *estimated* from the bytes TRACE can see.
- **The standing schema cost is small and cache-amortized, not a per-turn tax.**
  Tool/system definitions occupy the cached prompt prefix: under prompt caching
  they are written once per session and read at a fraction of input price
  thereafter — they are not re-billed in full every turn. A naive
  `schema_chars × turns` figure overstates the real cost by roughly an order of
  magnitude and in the wrong shape.
- **Adding MCP tools is self-defeating for a cost measurement.** Every
  registered tool enlarges the cached prefix and, if the tool set varies across
  turns, invalidates that cache. A tool whose purpose is to measure context cost
  must not itself grow it.
- **`recall_count` is a surfacing signal, not a savings signal.** It increments
  when a learning is returned, not when it is used; dedup hits are transactional
  and not persistently counted. There is no measured "rework avoided".
- **The governance boundary (ADR 003) is binding.** Core is provenance-only and
  must not depend on any extension.

A bespoke "carbon feature" plus a bespoke "cost feature", each baking numbers
into core event models, would duplicate estimate-provenance logic, bloat the
audit trail, and drift TRACE toward being a metrics dashboard — the opposite of
its identity.

## Decision

1. **One optional telemetry layer, never core.** Carbon and self-cost are served
   by a single optional concern (a future `trace-telemetry` extension and the
   offline analyzer described below), not two features and not core fields.
   Removing it leaves a fully functional provenance system. Core MUST NOT import
   it (ADR 003 holds).

2. **Canonical store is a separate sidecar, not the provenance record.**
   Telemetry is derived diagnostics, not provenance, so it does not mutate event
   data. It lives in its own sidecar file written with the existing atomic
   temp-file + `os.replace` pattern (as `trace-learn` writes its knowledge
   store), independent of the session lock. An on-event namespaced
   `x_trace_telemetry` extra is reserved **only** for genuinely host-reported
   usage captured at event-creation time; it rides the existing `extra="allow"`
   and needs no wire-version bump. Any write into the canonical session record
   (e.g. a rollup under `Session.metadata.custom`) MUST route through
   `locked_disk_session` and be registered as an INV-1 writer.

3. **Every number carries estimate provenance.** A measure records its
   `value_kind` (`measured` / `host_reported` / `estimated` / `proxy_estimated`
   / `modeled`), `method_id`, a versioned + hashed factor set, per-input source,
   and an uncertainty **range** — never a bare point value. A missing input
   yields **no estimate plus a skip reason**, never a silent default. Measured
   and modeled values are never merged into one total. This makes a flagged
   estimate honest and an unflagged one a protocol violation.

4. **Self-cost is proxy by default, measured via host telemetry.** The honest
   offline estimate is the schema-surface size plus per-call argument/result
   sizes that TRACE can see, labeled as a `chars/4` proxy and reported under a
   caching regime (cold write / warm read), never multiplied by turn count. The
   authoritative number comes from the host's own telemetry: Claude Code emits a
   `claude_code.token.usage` OpenTelemetry metric attributed by `mcp_server.name`
   / `mcp_tool.name`, `type` (input/output/cacheRead/cacheCreation), `model`, and
   correlated by `session.id`. Ingesting that stream yields measured, per-tool,
   per-session cost. The offline proxy is the no-setup approximation of it.

5. **Carbon estimates require explicit inputs and only attach to sizeable
   events.** Carbon is `tokens × energy-per-token × PUE × grid-intensity`; the
   inputs (model, token counts, region, factors) are not visible to an MCP
   server and must be user-supplied, host-reported, or labeled proxy. Estimation
   runs only on `host="mcp"` events whose payload TRACE actually marshalled;
   `host="internal"`/`"external"` events are logged by reference and get a skip
   reason, never a guessed size. A decision's footprint is a derived rollup over
   its linked tool calls, never invented from the decision alone. Factor sets
   ship as cited templates, never as authoritative silent defaults.

6. **ROI is reuse-counts-with-a-flag, not measured savings.** The honest benefit
   signal is raw reuse counts carrying a data-quality flag (recall counts track
   surfacing, not use, and have known gaps). "Tokens saved" is only ever a
   `modeled` figure derived from an explicit human attestation that a recall
   prevented specific rework, with a zero floor for unconfirmed cases, labeled a
   counterfactual scenario — never a measured benefit and never auto-derived
   from `recall_count`.

7. **Delivery is offline-CLI-first; the NOW increment adds zero MCP tools.**
   Because an occasional, retrospective analysis must not enlarge the
   always-loaded tool surface it measures, the first increment is an offline
   analyzer (`trace_mcp.selfcost`, `python -m trace_mcp.selfcost`) that reads
   stored session JSON read-only and introspects the registered tool schemas via
   the FastMCP tool manager. It adds no MCP tool, no schema field, no wire
   change, and no core dependency. Any future telemetry MCP tool must keep the
   tool set static and deterministically ordered (cache-stable) and document the
   cache cost it adds.

## Implementation tiers

- **NOW** (shipped with this ADR): the offline `trace_mcp.selfcost` report —
  schema-surface estimate under cold/warm caching regimes, per-session authored
  token estimate (`host="mcp"` coverage), and flagged reuse signal — plus this
  ADR and its test (`tests/test_selfcost.py`). Additive, read-only, deletable.
- **SOON** (not built here): a `trace-telemetry` sidecar extension (ledger
  writer + derive-on-read summary); the OpenTelemetry host-usage importer (the
  measured self-cost path); an optional fail-open observe-event hook if
  auto-capture is needed; a human-attestation tool for defensible ROI. All
  optional, no schema bump.
- **NOT-NOW / NEVER**: core token/cost/energy/carbon fields; any wire/schema
  version bump for telemetry; live carbon-intensity APIs or real-time power
  metering; dashboards, offsets, or "green scores"; auto-attribution of host
  credit burn without host telemetry; auto ROI from `recall_count`; external
  ESG-disclosure positioning while compounded end-to-end uncertainty spans an
  order of magnitude (kept behind the project's compliance kill-gates); any
  publish coupling while naming/trademark is on hold.

## Consequences

- The schema is untouched and the governance boundary is preserved; carbon and
  cost can never silently become part of the provenance semantics.
- Users get an immediate, honest answer to "what does TRACE cost me" without any
  host configuration, and a documented upgrade path to a measured number.
- Honesty is enforceable rather than aspirational: a deterministic gate can
  assert that every estimate carries method + factors + uncertainty, that
  missing inputs are skipped not guessed, that measured and modeled totals stay
  separate, and that the telemetry ledger schema holds no free-text payload
  fields (a privacy guard, since size estimation reads argument/result content
  and must persist only derived scalars).
- The carbon ambition is explicitly parked behind an uncertainty check rather
  than abandoned, keeping a credible-but-gated path without overclaiming.

## References

- ADR 003 — core/extension boundary (the binding governance constraint).
- `docs/INVARIANTS.md` INV-1 — the single locked session-write path.
- `src/trace_mcp/selfcost.py` — the NOW offline analyzer.
- `docs/specification.md` §7.3 — custom-extension rule (extensions must not
  redefine the semantics of defined fields).
