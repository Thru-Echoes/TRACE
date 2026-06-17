# ADR 003: Core/extension boundary & Tier-3 scope

**Status:** accepted · **Date:** 2026-05-18

## Context

TRACE's identity is **decision provenance**. The `trace-learn` extension
(cross-session knowledge: recall, dedup, decay, and the planned Tier-3
RL-style feedback loop + cross-project knowledge) is valuable and likely
popular, but it is *not* the core thesis. A multi-round quality review
found this boundary was asserted only in a single
under-scoped CONTRIBUTING line and — worse — was **violated in `main`**:
`tools/query_tools.py` hard-imported `trace_mcp.extensions.learn`
unguarded, so deleting the extension broke the core tool
`trace_project_summary`. The user set an explicit governance
constraint — the core/extension boundary decision — that adaptive
learning must remain a strict optional extension with no scope creep
into core.

## Decision

1. **Core is provenance-only and self-contained.** Core =
   `server.py`, `schema/`, `storage/`, `tools/`, `exporters/`,
   `scratchpad.py`, `extension_status.py`. Core MUST NOT import from
   `extensions/`. Any optional-extension touchpoint in core MUST be a
   guarded, fail-open probe (try/except ImportError → degrade), mirroring
   `extension_hooks.py` / `_compute_knowledge_metrics`.

2. **The delete-the-extension invariant is binding and CI-enforced.**
   Deleting `extensions/learn/` MUST leave a fully functional
   17-core-tool provenance system. Enforced by
   `tests/test_v041_core_extension_boundary.py`, run as a named CI step.

3. **Tier-3 stays extension-scoped.** The Tier-3 roadmap (RL-style
   learning-weight feedback; cross-project global knowledge) MUST be
   implemented entirely within `extensions/learn/` (or a new extension).
   No Tier-3 concept (weights, decay, feedback, cross-project state) may
   enter core schema, core tools, or the spec's normative provenance
   model. If a Tier-3 feature appears to need a core change, that is a
   signal to redesign the feature, not to widen core.

## Consequences

- The boundary is now documented once, here, and *referenced* (not
  re-prose'd) from `CONTRIBUTING.md` and the spec — single source of truth.
- A reviewer or contributor can mechanically check the invariant by
  running the boundary test; CI fails the build if core regresses.
- Tier-3 design is constrained up front, preventing the scope creep the
  user explicitly called out.

**Considered and rejected:** putting `extension_status.py` (the
user-facing "which learning mode" banner) inside `extensions/` — it would
make a core, always-present session-start string depend on an optional
package. It lives in core and *probes* the extension defensively instead.
