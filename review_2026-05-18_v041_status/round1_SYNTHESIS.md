# Round 1 Synthesis + Proposed Remediation Plan — v0.4.1 status review

**Date:** 2026-05-18 · **Charter:** TRACE session `trace_20260518_5dd958`, decision `evt_005` (review-first, 3-round audit-verify, whole-system).
**Inputs:** `round1_A_engineering_reality.md`, `round1_B_edge_cases.md`, `round1_C_adoption_boundary.md` (3 independent read-only reviewers).
**This document is the artifact Round 2 verifies (correctness / exhaustiveness / scope-bloat).**

## 0. Corrected framing — the FM7 "regression" was a misdiagnosis

The controller's seed hypothesis (a reproducible v0.4.1 regression in `trace_end_session`) is **refuted** by independent evidence (Reviewer A in-process repro: 0.001s, correct; FM7 passes deterministically; Reviewer C corroborated). The FM7 failure is the E2E harness's 15s `_send_and_receive` read timeout (`test_e2e_server.py:62,84`) blown by MCP-subprocess cold-start **under the load of 8+ concurrent live Claude sessions** + eager `model2vec` import at server startup. **Not a code regression. Do NOT "fix" `trace_end_session`.** Logged as TRACE correction `evt_009` (corrects `evt_004`).

## 1. Convergent critical findings (independently corroborated)

| ID | Severity | Finding | Corroboration | Evidence |
|----|----------|---------|---------------|----------|
| **G1** | CRITICAL | Round-3 amendment **A1's multi-actor guard was never implemented**. Same-instance self-resolution warning fires unconditionally on `(type,id)` equality at FM1 (`decision_tools.py:78-99`), FM25, and the session-end structural detector (`session_tools.py:325-334`). False-fires on solo-human & `system→system` sessions A1 explicitly cited with named production data. | A (F1), B (big finding), C (C3) — **all 3** | `git log -S participants -- decision_tools.py` empty; probe-verified false positive |
| **G2** | CRITICAL | **Core→extension boundary VIOLATED.** `tools/query_tools.py:158` has an unguarded `import trace_mcp.extensions.learn.store` inside `_compute_knowledge_metrics`, called unconditionally by `trace_project_summary`. Deleting `extensions/learn/` breaks a **core** tool (`ModuleNotFoundError`). Directly violates governance `evt_002` / `project_adaptive_learning_boundary.md`. | C (C1), verified by runtime experiment (extension moved out, restored byte-identical) | `query_tools.py:158`; 17/18 core tools survive without extension |
| **G3** | CRITICAL | The FINAL plan's **own mandated L9.1 waggle-regression gate test was never written** ("invalid by construction" without it). Session JSON present; zero tests reference it. | C (C2); A notes suite never gated implementation | no test references `trace_session_trace_20260513_446733.json` |
| **G4** | CRITICAL | `test_human_self_resolves_clean` was **deleted and replaced with `test_human_self_resolves_warns`** asserting the buggy behavior on a 1-participant session. A1 line 30 requires that contract be *preserved*. 123 green v0.4.1 tests encode the rejected design and mask G1. | A (F2), B, C (C4) | the green suite is actively misleading on this point |
| **G5** | HIGH | CHANGELOG (24,45), `decision_tools.py:97`, `session_tools.py:166,371`, spec §3.6 **falsely claim** the multi-actor scoping exists. Docs describe behavior the code lacks. | A (F3), B, C | trust problem on an OSS release |
| **G6** | HIGH | Release-process: **zero git tags ever**; CHANGELOG header still `[0.4.1] — In progress`; bottom compare-links 404 (`v0.4.0`…). | A, C (C5), controller (turn 1) | `git tag -l` empty |

## 2. Partial Round-3 landings (HIGH/MED)

- **A8 (PARTIAL):** field renamed `orphan_discovery_warning_count`→`_hint_count`, but the hint is still pushed into `audit_warnings` at ⚠️ warning severity; innocuous prose ("turned out cleaner", "discovered files reorganized") → 3/3 false positives. A8's intent (de-emphasize) unmet.
- **A4 (PARTIAL):** qualified PROV-O exporter is **correctly implemented** (genuine `prov:Influence`/`qualifiedInfluence`/`atLocation`, deterministic blank nodes — not a `wasRevisionOf` shortcut). But the **A4-mandated PROV-O parser/ontology round-trip test is absent** (`TestRoundTrip` only does `json.loads` + key-presence).
- **A9 (PARTIAL):** cross-session/dangling `revises_event_id` hard-rejects at `append_event` (verified), but spec §4.4 still says only "SHOULD … within the same session" with no note of the hard-reject tightening or the v1.1-relaxation deferral A9 required.
- **C6/C7/C8 (MED):** ADR 002 missing from `docs/adr` index; CONTRIBUTING core-list under-scoped (omits `exporters/`, `scratchpad.py`); **no ADR/spec doc asserts the core/extension boundary or that Tier 3 must stay extension-scoped** (only an under-scoped CONTRIBUTING line).

## 3. Verified-solid (do NOT touch — reviewers confirmed correct)

ruff 0 / pyright 0; **L1.3** single-source `trace_version` (+ v0.3.0 backwards-compat: old sessions load, version preserved, redundant fields dropped via `extra="ignore"`); **L3.1/A10** URI carve-out (regex unambiguous vs `evt_NNN`, even `evt_1:foo`); **L6.1–L6.3** PROV split architecture; **A6** `_is_explicit_absence` (strict 2-marker allow-list + `.strip()`); **A2/A3/A7** (L5.7 removed, dispatch-hint advisory, None-guards present); **L11.x adoption surface ~9/9** (version + schema-URL cascade across all 9 sites, CLAUDE_BLOCK normative mirroring, `decision-audit.sh` FM1 generalization + bash-3.2 safety, PROV namespace policy consistent per ADR D6, CHANGELOG migration callouts); **stdin-EOF orphan self-exit is correct** (upstream `mcp.server.stdio` ends on stdin EOF — the controller's "orthogonal hardening" item is a non-issue except a pathological pipe-retention edge).

## 4. Honest readiness

All three reviewers independently converge: **~75–80%, NOT release-ready** (vs the Round-3 doc's claimed ~95%). Meta-finding (unanimous): the 3 prior rounds verified the *plan*; **no green suite ever gated the *implementation* merge**, and "do not run Round 4" was misoperationalized as "do not verify the implementation."

## 5. PROPOSED REMEDIATION PLAN (Round 2 verifies this)

Priority order. TDD: write the failing/contract test first, then the fix.

- **P1 (G1+G4+G5) — Implement A1 properly.** In `decision_tools.py` (FM1 + FM25) and `session_tools.py` (session-end structural detector): fire the same-instance self-resolution warning only when `proposed_by == resolved_by` (type AND id) **AND** the session has ≥2 distinct actor instances (across `metadata.participants` ∪ event actors; fallback to event actors when participants empty, per A7). **Restore `test_human_self_resolves_clean`** (single-actor human→human ⇒ NO warning); add tests: `system→system` single-actor ⇒ no warn; Claude(id=claude)→GPT(id=gpt) ⇒ no warn; same human id in a ≥2-actor session ⇒ warn (the true evt_025 case). Then correct CHANGELOG/spec §3.6/docstrings to match real behavior.
- **P2 (G2) — Restore the core/extension boundary.** Guard `query_tools.py:158` (and audit `:340`): `try: import …extensions.learn.store except ImportError: return None`/omit knowledge metrics so `trace_project_summary` works as a pure core tool. Add a test that imports/runs the core with `extensions/learn/` absent and asserts **all 18 core tools function** (the delete-the-extension invariant). Satisfies `evt_002`.
- **P3 (G3) — Write the mandated L9.1 gate.** Load `audit_2026-05-13_waggle_session/trace_session_trace_20260513_446733.json` under v0.4.1; assert the AttributionAudit counts the FINAL plan specified. This is the release gate the plan declared mandatory.
- **P4 (A8) — De-noise orphan-discovery.** Move the hint out of `audit_warnings` into a lower-severity advisory tier; tighten phrase list. Per A8 intent.
- **P5 (A4) — Add the PROV-O round-trip test.** Validate emitted JSON-LD against a real PROV-O parser/ontology constraints (architecture already correct; only the test is missing).
- **P6 (A9 + C6/C7/C8) — Doc truth-up.** Spec §4.4: document the cross-session hard-reject + v1.1 deferral. Add ADR 002 to the adr index; widen CONTRIBUTING core list. **Add an ADR (or spec section) that explicitly states the core/extension boundary and that Tier 3 / adaptive learning MUST stay extension-scoped** — the durable home for governance `evt_002`.
- **P7 (G6) — Release process, LAST.** After P1–P3 land and a **full green `uv run pytest` gates it**: decide tag policy (create `v0.4.1` + backfill prior tags, OR switch compare-links to commit ranges — flag for user), flip CHANGELOG `In progress`→`2026-05-18`, update `[Unreleased]` compare link. Branch + PR (never direct to main).
- **P8 (FM7, low) — E2E load-fragility, NOT a code fix.** Optionally make the E2E `_send_and_receive` timeout configurable / mark MCP-subprocess E2E tests serial-or-skip-under-load, and document FM7 as a known load-sensitive harness test. **Do not modify `trace_end_session`.**
- **P9 (concurrency safety — USER-RAISED) — lazy-load the embedding model + verify shared-knowledge-store concurrency.** (a) Make the trace-learn embedding/`model2vec` import **lazy** (first knowledge use, not server startup): this is the FM7 cold-start root cause *and* an N×RAM multiplier across concurrent live sessions. (b) Verify whether `~/.trace/knowledge/` (shared across ALL sessions/projects) is safe under concurrent read-modify-write from multiple live sessions — confirm cross-process locking, or document the last-writer-wins lost-update risk and add locking. Per-session `~/.trace/sessions/*.json` are already safe (atomic temp+`os.replace`, distinct files). Connects the FM7 flake to a real product-side efficiency/robustness fix. Round 2 must independently confirm/refute (a) and (b).

## 6. Scope discipline (what NOT to do)

Do not touch §3 verified-solid items. Do not "fix" FM7 as a code regression. Do not bump the protocol/schema version (all P-items are bugfix/doc, additive). Do not expand the AttributionAudit detector surface beyond restoring A1's intended scoping. Sequence: P1–P3 (criticals, TDD) → P4–P6 → P7 (release, green-gated) ; P8 optional/parallel.
