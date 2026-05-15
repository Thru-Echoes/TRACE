# Round 3 Verification — Amendments to the FINAL Fix Plan

**Date:** 2026-05-14
**Source:** Three independent verification subagents (engineering reality, edge cases, adoption/downstream impact)
**Status:** The FINAL plan in `trace_v1_FINAL_fix_plan.md` requires these amendments before implementation can begin.

---

## Honest verdict from Round 3

Three rounds in, the plan is **~85% ready**. Round 3 found:

- **3 real bugs** in the FINAL plan (would cause implementation to fail or be confused)
- **2 fundamental scoping errors** in detectors L3.2 / L5.4 that would produce false positives on real production sessions
- **1 wrong diagnosis** (L5.7 — fixes a problem in the wrong file)
- **1 too-sensitive trigger** (L5.6) that would fire on every session in `~/.trace/sessions/`
- **~15 test fixtures** invalidated, not in the plan's effort estimate
- **~10 adoption-surface gaps** (README, project CLAUDE.md, CONTRIBUTING.md, ADR, installer assets, hook scripts, schema URLs, PROV namespace)

The plan as written would ship a v0.4.1 with three quietly-broken surfaces: the `decision-audit.sh` hook would under-report; PROV-LD consumers would silently lose corrections; README/CLAUDE.md would say `0.3.0` / `0.4.0` while the package is `0.4.1`. None of these are crashes, but cumulatively they break trust on an OSS release.

Below are the required amendments. After applying these, the plan is genuinely ready.

---

## Critical bug fixes (must amend before implementation)

### A1. L3.2 + L5.4 — scope to same-instance, preserve None-check, add multi-actor guard

**The bug:** Round 3a found that `tests/test_decision_guards.py:84-99` is `test_human_self_resolves_clean` with docstring *"Human proposes + human resolves -> no warning (humans can change their mind)."* The plan's L3.2 (generalize FM1 to any same-type) directly inverts this test's stated contract.

Round 3b independently found that the type-only check fires false positives on:
- Single-actor `system→system` workflows (real production data exists at `~/.trace/sessions/trace_20260320_205356.json`)
- Multi-AI workflows (Claude + GPT, both `type="ai"` but different `id`)
- Solo human running TRACE manually

**The fix — replace L3.2 with this scoped version:**

> Generalize FM1 in `decision_tools.py:~76`: fire when `proposed_by == resolved_by` (full Actor equality — both `type` AND `id` match), AND the session involves multiple distinct actors (≥2 unique actor types across `metadata.participants` and event actors). Preserve the `if resolved_by is not None` short-circuit. This catches the evt_025 instance (same human resolving their own proposal) without false-firing on single-actor or multi-AI sessions. Update warning text: *"Same actor instance proposed and resolved this decision. Per spec §3.6, in multi-actor workflows the proposer should differ from the resolver."*

**Same scoping applies to L5.4** (session-end structural detector): identical condition.

**Also amend L9.4 (test):** explicitly *replace* `test_human_self_resolves_clean` — the new test should assert that **same-instance** self-resolution emits the warning, and that **different-instance** human-to-human (e.g., one human reviewer accepts another human's proposal) does not. Add tests for:
- `system→system` self-resolution: no warning (only 1 actor type in session)
- Claude (id=claude) proposed, GPT (id=gpt) resolved: no warning (different instances)
- Same human (id=human) proposed AND resolved: warning fires (the evt_025 case)

**Also generalize FM25** (`decision_tools.py:~84-94`) to match — it has the same `ai`-only guard and would diverge confusingly if left alone.

### A2. L5.7 — wrong diagnosis, redirect or remove

**The bug:** Round 3a found that `scratchpad.py:182-186` already auto-derives the event count from `len(session.events)`. The audited "27 vs 28" off-by-one is in `session.summary` (the human/LLM-authored free-text summary at JSON line 44), NOT in scratchpad's auto-emitted line.

**The fix — replace L5.7 with this:**

> Detect summary-text / event-count discrepancy in the session-end audit. If `session.summary` contains digit-count strings (e.g., regex `(\d+)\s+(?:TRACE\s+)?events?\s+logged`) AND the captured number doesn't match `len(session.events)`, surface a warning in the AttributionAudit: *"Session summary text claims N events but session has M events. Update summary text or accept the discrepancy as known."* Soft warning only — do not modify the user-authored summary text.

Alternatively, **remove L5.7 entirely** and accept that human-authored summaries are freeform prose. This is defensible — the structural count is already right, and policing free text is out of scope for a provenance system. **Recommended: remove L5.7.** The cost of an off-by-one in human summary text is small; the cost of a false-positive warning every time a controller writes a summary that mentions counts is larger.

### A3. L5.6 — dispatch-visibility detector trigger too sensitive

**The bug:** Round 3b ran `grep -c '"type": "tool_call"'` on ~20 recent sessions in `~/.trace/sessions/` — **all returned 0**. The proposed trigger (`≥5 contributions AND 0 tool_calls`) fires on every contribution-rich session in current production. The warning would immediately become noise and lose all signal.

**The fix — replace L5.6 with this:**

> Defer L5.6 to v1.1 (paired with the auto-capture hooks). For v0.4.1, instead add a one-line note to the session-end audit when `contributions ≥ 10 AND tool_calls == 0 AND client == "Claude Code"`: render as an **advisory hint** (separate from `audit_warnings`) reading: *"This session logged N contributions and 0 tool_call events. Subagent dispatches may have gone uncaptured; see spec §3.5 and CLAUDE.md USUALLY tier for guidance on manual dispatch logging."* Do not increment any warning counter; this is purely informational until hooks land.

This converts a noise-generator into a one-time educational nudge. Field data can determine whether to upgrade it to a real warning in v1.1.

### A4. L6.2 — qualified PROV-O influence needs exporter architecture work

**The bug:** Round 3a found that `exporters/prov_jsonld.py:150-155` currently emits shorthand `wasRevisionOf` for corrections. No qualified-influence pattern exists anywhere in the exporter — implementing one requires new bundle sections (`bundle["influence"]`, `bundle["wasInfluencedBy"]`) and JSON-LD shape work.

**The fix — amend the effort estimate and add a sub-item:**

> L6.2 is exporter-architecture work, not a 1-2 hour change. Realistic effort: 3-5 hours including round-trip JSON-LD validation against a PROV-O parser. The plan's "Layer 6 ~2 hours" booking should be raised to ~5 hours. Add explicit sub-item: write a parser-roundtrip test that validates the emitted JSON-LD against a third-party PROV parser (or at minimum the W3C PROV-O ontology constraints).

### A5. L9 — fixture migration is not in the budget

**The bug:** Round 3a enumerated ~15 test fixtures that reference fields the plan changes (`trace_version="0.3.0"` kwargs in 7 fixtures, schema URI literal in 4 sites, `test_human_self_resolves_clean` contract inversion, warning-text checks in `test_failure_modes_e2e.py`).

**The fix — add new item L9.0:**

> **L9.0 (NEW):** Fixture migration audit. Before implementing the schema changes, sweep `tests/` for:
> - `trace_version="0.3.0"` kwarg passed to `Environment(...)` constructor → delete the kwarg (per L1.3)
> - `Session.trace_version == "0.3.0"` assertions → bump to `"0.4.1"`
> - `"trace-v0.3.json"` literal strings in test paths → update per L1.5
> - `"wasRevisionOf"` assertions in PROV export tests → split per L6.1
> - Warning-text assertions in `test_failure_modes_e2e.py` and `test_decision_guards.py` → update per L4.x text changes
> - `test_human_self_resolves_clean` → rewrite per A1
> Effort: ~3-4 hours. Must precede L9.1 (waggle regression) so the existing tests still pass before adding new ones.

---

## New Layer 11 — Adoption surface (must add)

The FINAL plan covers the package internals but does not cover external-facing artifacts. Round 3c found ~10 gaps. Add this whole new layer:

| ID | Change | File:line | Issue |
|---|---|---|---|
| L11.1 | Update README version line and schema URL | `README.md:23,25,252` | scaffolding |
| L11.2 | Update project-checked-in CLAUDE.md version line | `CLAUDE.md:5` (repo root, not user's global) | scaffolding |
| L11.3 | Update CONTRIBUTING.md schema regeneration filename | `CONTRIBUTING.md:69` | scaffolding |
| L11.4 | **Update installer asset** `CLAUDE_BLOCK.md` to mirror L7.1, L7.3, L7.5 normative changes. This is what `trace-mcp-init` installs into consumer projects' CLAUDE.md — it MUST reflect the new rules or new users get stale guidance. | `src/trace_mcp/adapters/claude_code/assets/CLAUDE_BLOCK.md:18-24` | issue 1, 2, 4 |
| L11.5 | **Update `decision-audit.sh` hook script.** Currently hard-codes `proposed_by.type == resolved_by.type == 'ai'` check (the narrow FM1). After L3.2 generalizes server-side, this hook will under-report. Either inline the new generalized check, or refactor to shell out to the session's persisted audit block. Add hook integration test to L9. | `src/trace_mcp/adapters/claude_code/assets/hooks/decision-audit.sh:14-40` | issue 2 |
| L11.6 | **Create new ADR.** `docs/adr/002-v041-protocol-additions.md` documenting: Proposer Identity Rule rationale, URI-form `corrects_event_ids` rationale, single-source-of-truth `trace_version` decision, PROV-LD correction predicate split. Without an ADR, the v0.4.1 design rationale lives only in the audit folder and CHANGELOG — weak homes for "why." | `docs/adr/002-v041-protocol-additions.md` (new) | scaffolding |
| L11.7 | **Decide PROV namespace URI policy.** Either keep `https://trace-protocol.org/ns/v0.3#` (additive extensions OK — recommended) or bump to `ns/v0.4#`. Update `prov_mapping.py:24`, spec §6, README consistently. Document the decision in L11.6 ADR. | `prov_mapping.py:24`, spec §6, README | adoption |
| L11.8 | **Schema $id and URL cascade.** Update all the references the FINAL plan missed: `scripts/generate_schema.py:19,23-24,27` ($id literal, descriptions), `scripts/validate_session.py:20` (hard-coded filename), `schemas/trace-v0.3.json:760` ($id inside the schema), `src/trace_mcp/schema/session.py:52` (default `context` URL), `prov_mapping.py:24` (PROV namespace, contingent on L11.7). | various | scaffolding |
| L11.9 | **CHANGELOG migration callouts.** Add explicit sections in the `0.4.1` CHANGELOG entry: (a) "Breaking-for-PROV-consumers: corrections now emit `wasInvalidatedBy` (event target) or qualified `wasInfluencedBy` (URI target) instead of `wasRevisionOf`. Update SPARQL/jq queries accordingly." (b) "Re-run `trace-mcp-init` in consumer projects to refresh installed hooks — server-side FM1 generalization (L3.2) is otherwise invisible to consumers running the old hook." (c) "Pinned-version Pydantic consumers should set `model_config = ConfigDict(extra='ignore')` if they parse v0.4.1-written sessions through old schemas." | `CHANGELOG.md` | adoption |
| L11.10 | (Optional, can defer to v1.1) Add `--upgrade` flag to `trace-mcp-init` that re-installs hook scripts idempotently and stamps a version into the hook header so future plans can detect outdated copies. | `src/trace_mcp/adapters/claude_code/init_project.py` | adoption |

---

## Other edge-case mitigations (must amend to Layer 5 / Layer 4)

| ID | Change | Issue |
|---|---|---|
| A6 | L5.2 `_is_explicit_absence` — add `.strip()` before set lookup, so `" <autonomous-stretch>"` (leading whitespace) still counts as explicit absence. One-line change. | edge case 7 |
| A7 | L5.3 / L5.5 / L5.6 — guard against `session.metadata.environment is None` and `session.metadata.participants == []`. Real production sessions have both empty. Use fallback: infer actor types from `{e.actor.type for e in session.events}` when participants empty. | edge case A, B |
| A8 | L5.5 — rename `orphan_discovery_warning_count` → `orphan_discovery_hint_count` and render in a lower-severity tier than the warnings. The detector is heuristic and will produce some false positives; calling them "warnings" overweights the signal. | edge case 8 |
| A9 | Spec §4.4 / `_check_referential_integrity` — note explicitly that cross-session references (`revises_event_id` pointing to a different session) currently hard-reject at `append_event:417`. Spec §4.4 says only SHOULD-validity within session, so this is a tightening beyond what the spec licenses. Defer the relaxation to v1.1, but document the current behavior in the spec to avoid confusing consumers. | edge case 9 |
| A10 | L7.5 — when adding the new §3.7.1 subsection on URI-form `corrects_event_ids`, clarify the prefix-discrimination heuristic: scheme matches `[a-z][a-z0-9-]+:`. Event IDs match `evt_NNN`. The two are unambiguously distinguishable. | implementation clarity |

---

## Updated effort estimate (after amendments)

Original plan: ~3 days. After Round 3 amendments:

| Layer | Original | Revised | Delta |
|---|---|---|---|
| Layer 1 (scaffolding) | ~1 hr | ~3 hr | +2 hr (L11.8 cascade) |
| Layer 2 (schema) | ~30 min | ~30 min | — |
| Layer 3 (validators) | ~2 hr | ~3 hr | +1 hr (multi-actor guard, ID-equality, FM25 sync) |
| Layer 4 (logging tools) | ~3 hr | ~3 hr | — |
| Layer 5 (AttributionAudit) | ~half day | ~half day | — (some items easier, L5.6 deferred) |
| Layer 6 (PROV) | ~2 hr | ~5 hr | +3 hr (qualified-influence is architecture work) |
| Layer 7 (spec) | ~half day | ~half day | — |
| Layer 8 (CLAUDE.md) | ~1 hr | ~1 hr | — |
| Layer 9 (tests) | ~1 day | ~1.5 day | +half day (L9.0 fixture migration, additional edge case tests) |
| **Layer 11 (NEW: adoption)** | — | ~half day | +half day |

**Total revised effort: ~5 days of focused work** (up from ~3 in the original FINAL plan).

---

## Items that survive Round 3 unchanged

These were verified clean by all three Round 3 subagents — implement as written in the FINAL plan:

- **L1.1, L1.2, L1.4** — version + changelog scaffolding
- **L1.3** — drop `trace_version` from Environment (with fixture migration noted in L9.0)
- **L2.1, L2.2, L2.3** — schema additions (`discovery` category, `host` field, `parent_event_id` field). All clean additive.
- **L3.1** — URI carve-out in `_check_referential_integrity` (Round 3a confirmed `:` heuristic is unambiguously safe — event IDs are strictly `evt_NNN`)
- **L4.1, L4.2, L4.3, L4.4, L4.5, L4.6, L4.7, L4.8** — all logging tool refinements clean
- **L5.1, L5.2 (with `.strip()` from A6), L5.3, L5.4 (with scoping from A1), L5.5, L5.8** — clean after amendments
- **L6.1, L6.3** — PROV mapping split is conceptually correct; only L6.2's exporter implementation needs more time
- **L7.1 through L7.12** — all 12 spec edits clean
- **L8.1 through L8.7** — all CLAUDE.md / adapter doc updates clean

---

## What this means for the release timeline

Three rounds of independent verification have surfaced ~33 substantive issues across the original plan (10 in Round 1's audit findings, 13 in Round 2's verification of the proposed plan, ~10 more in Round 3's verification of the FINAL plan). The diminishing-returns curve is real — Round 4 would likely find ~3-5 more minor issues, most cosmetic.

**Recommendation:** Apply Round 3 amendments above, then implement. Do not run Round 4 — the marginal value is below the cost. The implementation work itself will surface any remaining issues via failing tests.

**What confidence-buys-what:**
- After Round 1: plan was ~40% ready (correctness skeleton).
- After Round 2: plan was ~70% ready (technical accuracy + scope discipline).
- After Round 3: plan was ~85% ready (engineering reality + edge cases + adoption surface).
- After Round 3 amendments: plan would be ~95% ready (the remaining 5% is implementation discovery).

The audit-verify-amend cycle has been productive but has reached the point where one more iteration costs more than it would save.
