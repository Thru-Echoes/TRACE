# ADR 002: v0.4.1 Protocol Additions

**Status:** Accepted
**Date:** 2026-05-14
**Context:** Quality audit of session `trace_20260513_446733` (waggle project) surfaced five issues in TRACE's audit fidelity. Fix plan (`audit_2026-05-13_waggle_session/trace_v1_FINAL_fix_plan.md`) proposed ~65 items across 11 layers. This ADR documents the v0.4.1 release decisions; deferred items are tracked for later releases.

---

## Summary

v0.4.1 is an **additive, backwards-compatible** release introducing:

1. The **Proposer Identity Rule** (§3.6) — disambiguates `proposed_by` attribution in question→AI-proposal→acceptance flows.
2. The **`discovery` annotation category** (§3.7) — for non-trivial findings from autonomous work.
3. **URI-form `corrects_event_ids`** (§3.7.1) — corrections can anchor to externally-located artifacts via prefix-discriminated schemes (`external:`, `jsonl:`, `subagent:`, `tool-result:`).
4. **`host` and `parent_event_id` on `ToolCallData`** (§3.5) — covers external MCP, external non-MCP, and host-internal subagent dispatchers; `parent_event_id` enables dispatch-graph reconstruction.
5. **Normative MUST on `conversation_snippet`** (§3.4.1) — for contributions and correction-category annotations, with explicit-absence markers (`<autonomous-stretch>`, `<no recent user message>`).
6. **PROV-LD correction mapping split** (§6) — event-ID targets emit `prov:wasInvalidatedBy` (repudiatory); URI-form targets emit qualified `prov:wasInfluencedBy` with `prov:atLocation`; `parent_event_id` emits `prov:wasInformedBy`.
7. **`AttributionAudit` extensions** — five new server-side audit counts surface the silent-warning failures the audit identified.
8. **`Environment.trace_version` removed** — single source of truth on `Session.trace_version`.

The protocol stays in the 0.4.x family. Pre-v0.4.1 session files load unchanged (Pydantic v2 default `extra="ignore"` silently drops the removed `Environment.trace_version` field). The PROV-LD correction predicate change is a documented migration step for downstream consumers.

## Decisions

### D1: Proposer Identity Rule disambiguates the v0.3 spec gap

The v0.3 spec at §3.6 line 220 says "the actor who proposes a decision MUST NOT be the same instance that resolves it, when the workflow involves multiple actors." But the field semantics for `proposed_by` were never precise enough: when a human asks a question, an AI proposes a course of action, and the human accepts ("proceed"), is the proposer the human (who initiated the conversation) or the AI (who authored the proposal content)?

The waggle audit's `evt_025` made this concrete: TRACE logged `proposed_by=human` even though the AI's words populated the entire `description` field. Three rounds of independent verification all concluded this was the dominant attribution failure mode.

**Decision:** `proposed_by` MUST identify the actor who authored the CONTENT of the proposal (whose words populate `description`), regardless of who spoke last. A 4-row disambiguation table was added to spec §3.6 covering the canonical patterns. The rule is enforced at log time (FM1/FM25) AND at session-end (`attribution_warning_count` in `AttributionAudit`).

**Amended 2026-05-18 (Round-3 A1 / decision `evt_016`):** the original v0.4.1 implementation generalized FM1 to *all* same-instance pairs unconditionally, which false-fired on legitimate single-actor sessions (solo human, `system→system`) — the exact false positive the waggle audit identified with production data. The generalized **non-`ai`** same-instance check now fires only when the session involves **≥2 distinct actor *types*** (union of `metadata.participants` and event actors; mirrored in `Session.is_multi_actor()` and the `decision-audit.sh` hook). The pre-existing v0.3 **`ai→ai`** self-resolution warning remains **unconditional** (AI must not resolve its own proposal regardless of actor count). Spec §3.6 ("when the workflow involves multiple actors") was already correct; only the implementation and this record were brought into line with it.

**Considered and rejected:** adding a new field like `proposal_authored_by` separate from `proposed_by`. This would just shift the bug to a new location.

### D2: URI-form `corrects_event_ids` rather than new event types

The audit's `evt_003` had `corrects_event_ids: []` because the corrected entity (a subagent's false claim) was not a TRACE event. The existing field semantics required event-ID references and `_check_referential_integrity` would reject anything else.

**Decision:** widen `corrects_event_ids` to accept URI-form references prefix-discriminated by `[a-z][a-z0-9-]+:`. Define `external:<uri>` as the normative universal fallback; `jsonl:`, `subagent:`, `tool-result:` as non-normative implementer examples. Carve out URI-form entries in `_check_referential_integrity` so they bypass in-session existence checking.

**Considered and rejected:** introducing a `subagent_claim` event type for the original false statement, so the correction could link to a real event. Rejected because (a) it bloats event taxonomy for what is just an utterance, (b) it pressures controllers to log every subagent assertion in case it later turns out wrong, and (c) the URI reference is more honest — the corrected statement exists outside the TRACE event log and `corrects_event_ids` can point at it directly without fabricating an event.

### D3: PROV-LD correction split — `wasInvalidatedBy` vs qualified `wasInfluencedBy`

The v0.3 PROV mapping emitted `prov:wasRevisionOf` for all corrections. But repudiatory corrections (where a prior claim is invalidated) are semantically distinct from evolutionary revisions (where an artifact is refined). And URI-form corrections (the new §3.7.1 case) target externally-located artifacts that can't be PROV `Entity` objects within the bundle.

**Decision:** event-ID corrections → `prov:wasInvalidatedBy`. URI-form corrections → `prov:wasInfluencedBy` reified through `prov:qualifiedInfluence` to a `prov:Influence` blank node bearing `prov:atLocation` with the URI. The qualified-influence pattern is exactly what W3C PROV-O specifies for annotated influences.

**Breaking note for PROV consumers:** SPARQL/jq queries matching `?correction prov:wasRevisionOf ?event` will return zero results for v0.4.1+ corrections. Migration callout is in the CHANGELOG. `prov:wasRevisionOf` continues to be emitted for decision revisions (`revises_event_id`) and tool-call retries (`retries_event_id`) — those ARE evolutionary refinements and the existing mapping is correct.

**Considered and rejected:** keeping the unified `prov:wasRevisionOf` mapping for v0.4.1 to avoid breaking consumers. Rejected because the spec change says one thing and the exporter delivers another — the verifier rounds flagged this as a credibility-eroding gap. The PROV-O semantics are clear; ship the right mapping with explicit migration guidance.

### D4: Drop `Environment.trace_version`; single source of truth on `Session`

The audited session had `Session.trace_version="0.3.0"` and `Session.metadata.environment.trace_version="0.4.0"`. A reader could not tell which version the session conformed to. The duplication served no purpose.

**Decision:** `trace_version` lives only on `Session`. `Environment` is solely about the execution context (client, OS, Python version, MCP servers). Pydantic v2 default `extra="ignore"` ensures pre-v0.4.1 session files load cleanly with the redundant field silently dropped.

**Considered and rejected:** keeping both fields but adding a model_validator asserting equality. Rejected because the invariant adds runtime cost for no semantic gain — one canonical value is simpler than two synced values.

### D5: `discovery` annotation category, not a new event type

The audit identified that the v3 Pydantic crash (a load-bearing mid-session discovery) was folded into a post-hoc contribution description rather than logged at the moment. `gotcha` (surprising-but-nobody-was-wrong) and `correction` (something-prior-was-wrong) didn't fit; the v3 bug was new information from autonomous work.

**Decision:** add `discovery` as a new annotation category. Document the criteria distinguishing it from `gotcha` and `correction`. Add spec §8.1 guidance that discoveries SHOULD be logged at the moment, not in post-hoc summaries.

**Considered and rejected:** introducing a `discovery` top-level event type alongside `tool_call`/`decision`/`annotation`/`state_change`/`contribution`. Rejected because it bloats the canonical 5-type taxonomy and requires changes to validator, exporters, scratchpad, PROV mapping, JSON Schema regen, and every consumer. An annotation category is a forward-compatible additive change.

**Considered and rejected:** adding a `trace_log_discovery` convenience wrapper tool. Rejected as bloat — `trace_log_annotation(category="discovery")` is two more characters; tool surface area should be earned by distinct semantics.

### D6: PROV namespace URI stays at `v0.3#`

The PROV namespace URI `https://trace-protocol.org/ns/v0.3#` is referenced from `prov_mapping.py:24` and spec §6 line ~454. v0.4.1 adds new properties under this namespace (`trace:host`, `trace:dispatchKind` would be in scope) but does not redefine any existing semantics.

**Decision:** keep the namespace URI at `v0.3#`. PROV namespaces are conventionally treated as stable identifiers; additive extensions are valid within the same namespace. Bumping to `v0.4#` would force every downstream consumer to update their JSON-LD `@context` for no semantic benefit.

**Considered and rejected:** renaming to `v0.4#` for consistency with the spec version. Rejected because the namespace URI is an identifier, not a versioned package — its role is to disambiguate `trace:` properties from PROV core properties, not to track protocol revisions.

### D7: Defer host-internal hook auto-capture to a future minor release

The audit's Issue 5 (42 uncaptured Agent dispatches) could be partially closed by adding `dispatch-start.sh` / `dispatch-end.sh` Claude Code hooks that auto-call `trace_log_tool_call(host="internal", ...)`. This requires careful fail-open coordination, threshold tuning, and integration with the host's tool-call event lifecycle.

**Decision:** for v0.4.1, ship the SCHEMA and protocol surface (`host` field, `parent_event_id`, spec §3.5 generalization, CLAUDE.md USUALLY-tier guidance) so manual logging is supported. Defer the auto-capture hooks to a future minor release with telemetry-driven threshold tuning — tracked in the v0.4.2+ "Deferred" list below. Add an advisory hint to `AttributionAudit` that fires when contribution density is high and tool_call count is zero, hinting at the gap without imposing on production sessions.

### D8: Three-round verification gates the release

The fix plan was developed over multiple rounds of independent subagent verification. Each round identified gaps the previous round missed — Round 1 produced 10 findings, Round 2 produced 13 additional corrections, Round 3 produced 10 more amendments, and a Final Verifier round identified 7 more spec/test/installer fixes.

**Decision:** establish three rounds of independent verification as the gate for the release. Document deferred items explicitly (in CHANGELOG, in HTML checklist, in this ADR). The discipline of "match implementation to CHANGELOG before push" is itself a release-quality contract that follows from the protocol's own "Never fabricate" rule.

## Consequences

**Backwards compatibility:** Pre-v0.4.1 session files load cleanly (Pydantic `extra="ignore"`). New optional fields default to v0.3 semantics. The annotation category enum was extended (additive). The PROV mapping change is the only documented breaking step for downstream consumers; mitigated by explicit CHANGELOG migration callout.

**Test coverage:** 76 new E2E tests in `tests/test_v041_uri_corrects_event_ids.py` (23), `tests/test_v041_attribution_audit.py` (25), `tests/test_v041_prov_ld_split.py` (11), `tests/test_v041_decision_audit_hook.py` (11), and `tests/test_v041_tool_call_wrapper.py` (6) verify v0.4.1 behavior with real storage, real PROV-LD export, a real `/bin/bash` subprocess for the hook, and a real MCP-server subprocess for the wrapper passthrough — no mocks. Pre-existing 764 tests continue to pass. Total suite: 840 tests.

**Adapter assets updated:**
- `CLAUDE_BLOCK.md` (the block `trace-mcp-init` installs into consumer projects) now documents Proposer Identity Rule, URI-form references, discovery category, snippet absence markers, and subagent dispatch logging.
- `decision-audit.sh` hook script generalizes the `ai`-only self-resolution check to non-`ai` same-instance pairs **in multi-actor sessions** (Round-3 A1 / `evt_016`; mirrors the server-side `Session.is_multi_actor()` guard — single-actor sessions are exempt), and surfaces the new audit fields (missing snippets, attribution warnings).

**Delivered in this batch (originally listed as deferred):**
- Schema file rename `schemas/trace-v0.3.json` → `schemas/trace-v0.4.json` with the `$id` cascade applied to `scripts/generate_schema.py`, `scripts/validate_session.py`, README, CONTRIBUTING, spec, and conformance tests. The PROV namespace URI and `Session.context` URL remain at v0.3 per D6.
- Appendix A worked example (`evt_006`) demonstrating the question → AI-proposal → accept flow with `suggestion_type="requested"`, plus a "Reading evt_006 against §3.6" paragraph contrasting all three Proposer-Identity patterns (proactive AI, requested AI, human directive).
- `host` and `parent_event_id` parameters exposed on the `trace_log_tool_call` MCP wrapper so the v0.4.1 schema additions are reachable through the public interface (not just the internal `logging_tools.log_tool_call` function).

**Deferred for v0.4.2+:**
- Auto-capture hooks for Claude Code subagent dispatches (`dispatch-start.sh`, `dispatch-end.sh`). v0.4.1 documents the manual logging pattern; auto-capture remains a host-side investment.

## References

- Source audit: `audit_2026-05-13_waggle_session/trace_audit_findings.md`
- Remediation plan: `audit_2026-05-13_waggle_session/trace_v1_FINAL_fix_plan.md`
- Round 3 amendments: `audit_2026-05-13_waggle_session/trace_v1_round3_amendments.md`
- Spec changes: `docs/specification.md` (sections 3.4.1, 3.5, 3.6, 3.7, 3.7.1, 4.4, 5.2, 6, 8.1, 8.2, Appendix B)
- W3C PROV-O qualified influence pattern: https://www.w3.org/TR/prov-o/#qualifiedInfluence
