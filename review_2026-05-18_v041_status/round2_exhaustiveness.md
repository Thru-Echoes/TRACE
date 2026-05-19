# Round 2 — Exhaustiveness Verification of the v0.4.1 Status Synthesis + Plan

**Date:** 2026-05-18
**Reviewer:** Independent Round-2 exhaustiveness verifier (no prior context; adversarial; READ-ONLY)
**Target audited:** `review_2026-05-18_v041_status/round1_SYNTHESIS.md` §1–§6 (plan P1–P9), checked for coverage gaps vs `round1_A/B/C`, and for unaddressed code-level consequences/interactions of P1/P2/P3/P9.
**Method:** Read all 5 source docs in full. VERIFIED via read-only `git`, `grep`, targeted `uv run pytest`, inline `python3 -c` (waggle-JSON analysis). No file written except this one. No process signalled. No `trace_*`/MCP calls. No directory moved.

---

## 1. Verdict

**The synthesis correctly captures every CRITICAL/HIGH finding from A/B/C — no Round-1 finding was outright DROPPED.** All G1–G6 + the §2 partials (A4/A8/A9/C6/C7/C8) + §3 verified-solid set faithfully aggregate the three reports. The plan P1–P9 is **substantially exhaustive at the finding level**.

**But the plan is NOT exhaustive at the *consequence/interaction* level. The single biggest gap: P1's remediation scope is under-specified and omits three concrete downstream surfaces that A1 itself binds, all of which are *named in Round-1* but lost in the P-item compression:**

1. **`decision-audit.sh:69` mirrors the un-guarded same-instance check server-side and is NOT in P1's edit list.** Round-1 C9 + Round-3 L11.5/A1 explicitly require the hook to carry the same guard (or be refactored to read the persisted audit). P1 names only `decision_tools.py` + `session_tools.py`. If P1 lands without touching the hook, consumers running `decision-audit.sh` keep the exact false-positive A1 was written to kill — re-creating G5's trust problem on the *adoption* surface. **(Round-1 had this; synthesis DILUTED it to "docstrings".)**
2. **A *third* bug-encoding test is unenumerated.** Synthesis G4/F2 names only `test_decision_guards.py::test_human_self_resolves_warns`. `tests/test_v041_decision_audit_hook.py::test_hook_handles_session_with_human_self_resolution` (lines 74-103) asserts the warning fires on a **single-actor, zero-participant** session (`{"project":"smoke"}`, one `human/researcher` actor) — identical bug-encoding shape. P1's test scope ("restore `test_human_self_resolves_clean`; add system→system / claude→gpt / same-human-in-≥2-actor") does **not** mention reworking this hook test, which **will fail** once the guard lands (or worse, will be left asserting the bug).
3. **The "≥2 distinct actor *types*" vs "≥2 distinct actor *instances*" ambiguity is unresolved and outcome-determining.** P1 prose says "≥2 distinct actor **instances**"; A1/round3_amendments.md:39 says "≥2 unique actor **types**"; round1_A/B/C variously say "actor types/instances". These are NOT equivalent (a solo human + their AI assistant = 2 types but the evt_025 case is *within* one instance). The waggle session (P3's L9.1 gate) has participants `[human/human, ai/claude]` but event actors `{(human,human),(ai,ai-assistant)}` — **actor-ID drift** (FINAL-plan issue #5). Under type-based counting it has 2 types (fires, L9.1 passes); under instance-based counting it has 3 instances (fires, L9.1 passes) — *coincidentally* both pass here, but the `system→system`/multi-AI P1 test cases and any real session resolve **differently** under the two definitions. The plan must pick one and state it; the synthesis flags neither the ambiguity nor the actor-ID-drift interaction.

Everything else in the plan's scope is sound. Honest assessment of the *plan's* exhaustiveness: **~85%** — the finding inventory is complete, but P1/P3/P4/P9 each lose a Round-1-known consequence in compression, and one plan-mandated test family (L9.0/L9.2–L9.7 status) is asserted-but-not-verified by the synthesis.

---

## 2. Coverage matrix — every Round-1 finding → P-item

Legend: **COVERED** (a P-item addresses it) · **DILUTED** (addressed but a sub-aspect lost) · **DROPPED** (no P-item).

### round1_A (Engineering Reality)

| A-finding | Synthesis | P-item | Status | Evidence |
|---|---|---|---|---|
| **F1** A1 multi-actor guard missing (decision_tools + session_tools) | G1 | **P1** | COVERED | `decision_tools.py:80-83` instance-equality only; grep for `participant/distinct/multi.actor` → text-only at `:97`, `session_tools.py:370-371` (VERIFIED) |
| **F2** test suite rewritten to assert the bug (`test_human_self_resolves_warns`, `_make_session` no participants) | G4 | **P1** | **DILUTED** | P1 names only `test_human_self_resolves_clean` restore; misses the **3rd** bug-test `test_v041_decision_audit_hook.py:74-103` (single-actor, asserts warning) — VERIFIED, see §4 |
| **F3** ADR 002 + spec internally inconsistent w/ shipped behavior | G5 | **P1** | **DILUTED** | P1 says "correct CHANGELOG/spec §3.6/docstrings" but does **not** name **ADR 002 D1 line 32** ("enforced at log time … AND at session-end") which is the specific overstatement A-F3 cited — VERIFIED `docs/adr/002-…:32` |
| **F4** FM7 not a deterministic defect (E2E flake under load) | §0 + §4 | **P8** | COVERED | "Do not modify trace_end_session"; P8 hardens harness |
| **F5** server eagerly imports model2vec + instantiates provider at startup | (in §0/§4 narrative) | **P9(a)** | COVERED | `learn/__init__.py:36-39` → `get_embedding_provider` → `embeddings.py:162` → `Model2VecEmbeddingProvider.__init__:102` `StaticModel.from_pretrained` (VERIFIED eager) |
| **F6** A3 dispatch hint in `audit_warnings` not "separate" | (folded into A8 partial framing) | — | **DROPPED (Low)** | `session_tools.py:406` `audit_warnings.append("[hint]…")`. A-F6 is a *distinct* Low item from A8/F5; synthesis §2 collapses only A8. No P-item touches the **dispatch** hint placement. Cosmetic but un-tracked. |
| **F7** A2/L5.7 absent — correctly so | §3 (verified-solid) | n/a | COVERED | informational; no action needed |

### round1_B (Edge-Case & Robustness)

| B-finding | Synthesis | P-item | Status | Evidence |
|---|---|---|---|---|
| **F1/F2** A1 guard absent (decision-time + session-end) | G1 | **P1** | COVERED | VERIFIED (same as A-F1) |
| **F3** CHANGELOG/code text falsely claim multi-actor scoping | G5 | **P1** | COVERED | CHANGELOG:24,45 ("in multi-actor sessions/session") VERIFIED false |
| **F4** FM25 inherits missing guard | G1 (implicitly) | **P1** | COVERED | P1 names "FM1 + FM25" explicitly |
| **F5** A8 half-applied (hint still ⚠️ in audit_warnings) | §2 (A8 PARTIAL) | **P4** | **DILUTED** | The hint is **double-rendered**: a correct non-⚠️ structured line at `session_tools.py:175-182` **AND** a duplicate ⚠️ at `:378-383`+`:205-207`. P4 says "move … into a lower-severity tier" — but the tier already exists; the fix is to **delete the duplicate append**, not "move". "Move" risks a third path. (VERIFIED both render sites.) |
| **F6** A9 spec §4.4 gap (no hard-reject/v1.1 note) | §2 (A9 PARTIAL) | **P6** | COVERED | P6: "Spec §4.4: document the cross-session hard-reject + v1.1 deferral" |
| **F7** A4 PROV-O round-trip test only `json.loads` | §2 (A4 PARTIAL) | **P5** | COVERED | P5 explicit |
| **F8** orphaned-server lifecycle delegated, no watchdog | §3 (verified-solid, "non-issue except pathological") | — | COVERED (deferred) | Synthesis correctly classifies as non-blocking |
| **F9** L9.0 fixture migration complete (residuals intentional) | (implied done) | — | **PARTIALLY VERIFIED** | residual `0.3.0` in `tests/`: `test_v041_decision_audit_hook.py` (backward-compat fixture, intentional), `test_specification_conformance.py:1202` (ADR-D6, intentional), `test_v041_tool_call_wrapper.py:13` (**docstring comment**, benign). VERIFIED no half-done migration. **But synthesis never states L9.0 was verified — see §5.** |

### round1_C (Adoption & Boundary)

| C-finding | Synthesis | P-item | Status | Evidence |
|---|---|---|---|---|
| **C1** core→ext boundary violated (`query_tools.py:158` unguarded, `:320` unconditional) | G2 | **P2** | COVERED | VERIFIED `:158` no try/except, `:320` `_compute_knowledge_metrics(project)` unconditional in `project_summary` |
| **C2** L9.1 waggle regression test MISSING | G3 | **P3** | COVERED | VERIFIED `grep -rl 446733 tests/` → 0 |
| **C3** A1 guard not implemented | G1 | **P1** | COVERED | VERIFIED |
| **C4** test suite passes but misses A1 cases | G4 | **P1** | **DILUTED** | P1 lists the new cases but not the 3rd bug-test (see §4) |
| **C5** zero git tags; "In progress" header; dead compare-links | G6 | **P7** | COVERED | VERIFIED `git tag -l`=0; CHANGELOG:10,172-176 |
| **C6** ADR 002 not in `docs/adr/README.md` index | §2 (C6) | **P6** | COVERED | VERIFIED README.md has only row 001 |
| **C7** CONTRIBUTING core list omits `exporters/`, `scratchpad.py` | §2 (C7) | **P6** | COVERED | VERIFIED `CONTRIBUTING.md:75` = `server.py, schema/, storage/, tools/` only |
| **C8** no ADR/spec asserting Tier-3-stays-extension | §2 (C8) | **P6** | COVERED | P6: "Add an ADR (or spec section)…" |
| **C9** decision-audit.sh fires w/o multi-actor gate | (mentioned in G1 corroboration only) | **P1?** | **DROPPED from P1's edit list** | `decision-audit.sh:69` `pb.type==rb.type and pb.id==rb.id`, no actor-count gate (VERIFIED). P1 text enumerates only the two .py files. See §4. |
| **C10** uvx-local-path only, no PyPI/wheel/gh-release | (not in synthesis tables) | — | **DROPPED (v1.0 item)** | C10 is explicitly a v1.0 (not v0.4.1) blocker; defensibly out of scope, but the synthesis omits it entirely — no "deferred to v1.0" acknowledgement. Minor. |

**Net coverage:** 0 findings DROPPED at CRITICAL/HIGH. DROPPED-at-Low/info: A-F6 (dispatch-hint placement), C10 (no v1.0 deferral note). DILUTED (Round-1 sub-aspect lost in P compression): A-F2/C4 (3rd bug-test), A-F3 (ADR 002 D1:32 as a named edit target), B-F5/P4 ("move" vs "delete duplicate"), C9 (hook in P1 scope).

---

## 3. Missing items (new findings / consequences not in the synthesis), severity-ranked

| # | Sev | Item | file:line | Why the plan misses it |
|---|-----|------|-----------|------------------------|
| **M1** | **HIGH** | **`decision-audit.sh:69` needs the A1 guard mirrored (or refactor to read persisted audit) — not in P1's edit list.** P1 fixes the server but the installed hook keeps the unconditional `(type,id)` check, so every consumer who ran `trace-mcp-init` still emits the A1 false-positive. CHANGELOG:58 already tells consumers to re-run `trace-mcp-init` for the *generalization* — the *guard* must ride the same channel or it ships the inverse bug to adopters. | `src/trace_mcp/adapters/claude_code/assets/hooks/decision-audit.sh:69`; P1 in `round1_SYNTHESIS.md:41` | C9 was logged but synthesis P1 compressed scope to "decision_tools.py + session_tools.py" |
| **M2** | **HIGH** | **3rd bug-encoding test unaddressed:** `test_v041_decision_audit_hook.py::test_hook_handles_session_with_human_self_resolution` asserts a warning on a **single-actor, no-participants** session. It will FAIL after the guard lands and is not in P1's "tests to add/restore" list. (Note: `test_v041_attribution_audit.py::TestAttributionWarningDetector` does NOT encode the bug — its `_make_session` has 2 participants — so the suite-rework surface is exactly these two tests, not three; but the synthesis names only one.) | `tests/test_v041_decision_audit_hook.py:74-103` (VERIFIED single-actor session at :79-96) | F2/G4/C4 cite only `test_decision_guards.py` |
| **M3** | **HIGH** | **Type-vs-instance definition ambiguity in P1 is outcome-determining and interacts with actor-ID drift.** P1 says "≥2 distinct actor **instances**"; A1 says "≥2 unique actor **types**". The waggle fixture has 2 actor *types* but 3 actor *(type,id)* tuples (participants say `ai/claude`, events say `ai/ai-assistant` — FINAL-plan issue #5). L9.1 passes under either, masking the ambiguity; the `system→system` and multi-AI P1 test cases do NOT resolve identically under the two definitions. Plan must pick one + state how participants-vs-event-actor drift is reconciled. | `round1_SYNTHESIS.md:41` ("distinct actor instances") vs `trace_v1_round3_amendments.md:39` ("unique actor types"); waggle JSON VERIFIED `participants=[human/human,ai/claude]`, event actors `{(human,human),(ai,ai-assistant)}` | Neither synthesis nor any P-item flags the type/instance split or the drift interaction |
| **M4** | **MEDIUM** | **P1's multi-actor computation needs a session-level actor set the existing shared loop does not build.** `_build_attribution_audit` (`session_tools.py:263-347`) is a single pass; the L5.4 detector at `:330-334` has no access to a precomputed distinct-actor set. Adding the guard requires either a new pre-pass over `e.actor` (alongside the existing `discovery_anchors` pre-pass at `:252-259`) or threading `metadata.participants`. P1 says "across `metadata.participants` ∪ event actors" but does not note this is new accumulation interacting with the orphan-discovery pre-pass / loop structure (refactor risk; the render-order `audit_warnings` build at `:356-393` must keep severity order). | `session_tools.py:252-259, 263-347, 356-393` | P1 states the predicate, not its placement vs the existing shared loop |
| **M5** | **MEDIUM** | **P9 enumerates only 2 shared-state surfaces (embedding model + `~/.trace/knowledge/*.json`). There are 3 MORE cross-session shared-mutable surfaces, none mentioned:** (a) **`.claude/SCRATCHPAD.md`** — *per-project, NOT per-session*; `write_scratchpad` "Replaces any previous content" → every session-end of every concurrent same-project session clobbers it (atomic write prevents corruption, **not lost-update** — the exact risk P9 raised for the knowledge store, un-tracked here). (b) **`*.embeddings.npy` sidecar** — `save_embeddings_cache` does a bare `np.save(path)` (`store.py:267`), **NOT** atomic temp+replace (unlike every JSON write) → torn/interleaved file under concurrent knowledge writes; the size-mismatch guard only catches *stale*, not *corrupt*. (c) **`~/.trace/scratchpads/SCRATCHPAD.md`** — when not in a project dir (`scratchpad.py:44-47`), ALL projects/sessions collapse to one global file → cross-project last-writer-wins. | `scratchpad.py:216-238` ("Replaces any previous content") + `:44-47`; `store.py:243-268` (non-atomic `np.save`) | P9 scoped to "knowledge store" + embeddings *RAM*; missed scratchpad lost-update and the non-atomic .npy sidecar entirely |
| **M6** | **MEDIUM** | **`load_store`/`save_store` have NO cross-process lock — P9(b)'s concern is confirmed real, AND it extends to `save_embeddings_cache` riding inside `save_store`.** `save_store` (`store.py:75-106`) is atomic for the JSON but unconditionally calls non-atomic `save_embeddings_cache` at `:104`. Concurrent `trace_learn_add`/`extract` from multiple live sessions on the same project = lost-update on the JSON (read-modify-write, no lock) **plus** a torn `.npy`. P9(b) asks Round-2 to "confirm/refute" — **CONFIRMED: no `fcntl`/`flock`/`Lock` anywhere in `store.py`** (VERIFIED grep). The plan should state the .npy non-atomicity as part of the same fix, not just "add locking to the JSON". | `store.py` (no lock — VERIFIED); `:104` calls `:243` non-atomic | P9 frames it as "knowledge store concurrency"; the embedded non-atomic sidecar write is a distinct sub-defect |
| **M7** | **LOW** | **A-F6 (A3 dispatch-visibility hint in `audit_warnings` not "separate") has no P-item.** Distinct from A8/P4 (that is the *orphan-discovery* hint). The *dispatch* `[hint]` at `session_tools.py:406` is `audit_warnings.append`-ed; A3 (round3:66) said "separate from `audit_warnings`". Cosmetic, semantics correct (no counter, correct gate) — but it is a Round-1 finding with zero plan coverage. | `session_tools.py:395-411` | Synthesis §2 collapsed only A8; A-F6 is a sibling Low finding |
| **M8** | **LOW** | **L9.7 ships PARTIAL — synthesis treats render-order as untouched/fine.** L9.7 mandated a render test "with **all five** new counts non-zero". The shipped `TestRenderOrdering::test_attribution_warning_before_missing_snippet_in_render` (`test_v041_attribution_audit.py:563`) exercises only **2** signals. Not a defect (P-items don't break render order) but a plan-mandated test that landed weaker than specified — and P1 *changes* the attribution count semantics, so the render-order test should be hardened alongside P1, which the plan does not say. | `tests/test_v041_attribution_audit.py:563-595` (2 signals only) | Synthesis §3 lists L9.x render order under "verified-solid"; the partial L9.7 was not noticed |

---

## 4. Interaction / downstream risks for P1/P2/P3/P9 the plan does not mention

### P1 (re-scope A1 multi-actor guard)
- **`decision-audit.sh` (M1):** mirrors FM1 server-side at `decision-audit.sh:69` with the same un-guarded `(type,id)` check. P1's edit list (synthesis :41) names only `decision_tools.py`+`session_tools.py`. The hook is shipped to every adopter via `trace-mcp-init`; without the guard there (or the L11.5-suggested "read persisted audit" refactor) P1 *server-side* and the *hook* diverge — adopters keep the false-positive. **Not mentioned in P1.** (Round-1 C9 raised it; lost in compression.)
- **ADR 002 D1 (M-A-F3 dilution):** `docs/adr/002-…:32` asserts the rule "is enforced at log time (FM1 generalized to all same-instance pairs) AND at session-end" — an unconditional claim that, post-P1, becomes "enforced **in multi-actor sessions**". P1 says "correct … docstrings" but does **not** name ADR 002 D1 as an edit target. The ADR is the durable "why" home (L11.6's whole point); leaving D1:32 stale re-creates G5 in the ADR.
- **Shared session-end loop (M4):** the L5.4 detector lives in the single `for e in session.events` pass; the multi-actor set is not available there. P1's predicate requires new actor-set accumulation that interleaves with the `discovery_anchors` pre-pass and must not perturb the severity-ordered `audit_warnings` build at `:356-393`. Refactor surface unstated.
- **`_is_explicit_absence` / orphan-discovery / missing-snippet detectors (no conflict):** VERIFIED these share the loop but operate on **contributions/corrections**, not the decision branch P1 edits — adding the guard to the `elif e.type == "decision"` branch at `:302-334` does **not** disturb them. (This is the one place the plan's silence is *safe* — worth stating so the implementer doesn't over-scope.)
- **Other tests asserting old behavior (M2):** beyond `test_decision_guards.py`, `test_v041_decision_audit_hook.py:74-103` encodes the bug on a single-actor session (VERIFIED). `test_v041_attribution_audit.py::TestAttributionWarningDetector` does **not** (multi-actor `_make_session`). `test_failure_modes_e2e.py` FM1/FM25 are `ai→ai` (backward-compat branch P1 keeps) — survive, but run via the F4/P8 flaky harness.
- **Type-vs-instance (M3):** outcome-determining ambiguity unresolved; interacts with the documented actor-ID drift (participants `claude` vs events `ai-assistant`).

### P2 (restore core/extension boundary)
- **`query_tools.py:158` is NOT the only core→extension import — `:340` is the second** (VERIFIED). `:340` IS guarded (`try/except Exception` → fallback at `:343-344`), so it does not break `health_check`, but it is still a *named-extension import from core* (Round-1 C-§3.2 "Tolerated but still… should also route through a hook"). P2 says "Guard `query_tools.py:158` (and audit `:340`)" — **COVERED**, the synthesis did catch both. **Grep-confirmed these are the ONLY two:** `schema/`, `storage/`, `exporters/`, `scratchpad.py` have **zero** `extensions`/`learn` imports; `server.py` only the IoC `_load_extensions` (`:715-732`, generic) + `extension_hooks` (`:19`). `scratchpad.py:8` and `extension_hooks.py:103` are comment-only. **No third coupling point.** P2's code scope is complete.
- **Doc interaction (C6/C7/C8) lands in P6, not P2** — fine, but P2's "add a test that … all 18 core tools function with `extensions/learn/` absent" is the CI-enforceable invariant C8/§3.5 wants; the plan keeps the *invariant test* (P2) and the *ADR/CONTRIBUTING text* (P6) in separate items with no cross-reference. Minor: ensure P2's test is the one P6's ADR cites as the acceptance gate.

### P3 (L9.1 gate)
- **L9.1's exact spec'd assertions ARE achievable (VERIFIED against the JSON):** 28 events, 17 contributions / **15** missing snippet, **1** correction / 1 missing snippet, **2** same-instance self-resolutions (evt_001, evt_025). FINAL-plan L9.1 demanded exactly `missing_snippet_contribution_count=15`, `missing_snippet_correction_count=1`, `attribution_warning_count≥2`, `orphan_discovery≥1`. The numbers line up — P3 is writable as the plan states.
- **P3 ↔ P1 coupling unstated:** L9.1's `attribution_warning_count≥2` depends on P1's multi-actor guard *passing* the waggle session. It does (2 actor types / 3 instances — both ≥2), **but only because** the session is genuinely multi-actor. The plan does not note that **P3 must be written/run AFTER P1** or it will assert against pre-guard behavior (the same sequencing mistake §5 of round1_C flagged for the whole release). Synthesis §6 says "P1–P3 (criticals, TDD)" but does not order P1→P3 *internally*.
- **OTHER plan-mandated tests beyond L9.1 — status (grep-VERIFIED):** L9.0 fixture migration **DONE** (residuals intentional, B-F9). L9.2 `_is_explicit_absence` **PRESENT** (`test_v041_attribution_audit.py::TestExplicitAbsenceHelper`, 7 cases). L9.3 URI carve-out **PRESENT** (`test_v041_uri_corrects_event_ids.py`, strong). L9.4 FM1 generalization **PARTIAL** — `test_human_self_resolves_warns` exists but the A1-required `system→system`/`claude→gpt`/restored-`_clean` cases are **ABSENT** (this is exactly P1's add-list). L9.5 backward-compat **PRESENT** (verified-solid per A/B). L9.6 PROV split **PRESENT** (`test_v041_prov_ld_split.py`) but A4's parser-roundtrip depth **ABSENT** (= P5). L9.7 render-order **PARTIAL** (2 of 5 signals — M8). **So only L9.1 is fully missing; L9.4 and L9.7 are partial and L9.6-depth maps to P5.** The synthesis asserts L9.0/L9.2/L9.3/L9.5 fine (correct) but never states L9.7 is partial.

### P9 (concurrency)
- **(a) eager embedding load — CONFIRMED.** `learn/__init__.py:36-39` `register()` calls `get_embedding_provider(_config)` at startup; default `auto`+model2vec → `embeddings.py:162` `Model2VecEmbeddingProvider`, whose `__init__` (`:102`) calls `StaticModel.from_pretrained` — eager, per subprocess. P9(a) is correct; lazy-load is the right fix.
- **(b) shared knowledge-store concurrency — CONFIRMED no locking** (`store.py` has no `fcntl`/`flock`/`Lock` — VERIFIED). But P9 **under-enumerates the shared-state surface (M5/M6):** (i) `.claude/SCRATCHPAD.md` is a *per-project* file that `write_scratchpad` *replaces wholesale* every session-end → concurrent same-project sessions lost-update it (atomic ≠ no-lost-update); (ii) `*.embeddings.npy` is written **non-atomically** by `save_embeddings_cache` (`store.py:267` bare `np.save`) *inside* the otherwise-atomic `save_store` → torn sidecar under concurrent writes; (iii) the global `~/.trace/scratchpads/SCRATCHPAD.md` fallback collapses all projects. **The plan's P9(b) scope ("`~/.trace/knowledge/`… cross-process locking") must be widened to: (1) knowledge JSON lock, (2) make `.npy` sidecar write atomic, (3) document/lock the SCRATCHPAD wholesale-replace lost-update.** Per-session `~/.trace/sessions/*.json` are correctly identified as safe (distinct files, atomic).

---

## 5. Other plan-mandated-but-missing tests / docs

| Mandate | Source | Status | In a P-item? |
|---|---|---|---|
| **L9.1 waggle regression** | FINAL L9.1 | **MISSING** (0 refs to 446733) | ✅ P3 |
| **L9.4 A1 cases** (`system→system` no-warn; `claude→gpt` no-warn; restored single-actor `_clean`) | FINAL L9.4 + A1 | **MISSING** (only `_warns`/`_different_instance` exist) | ✅ P1 (add-list) — but P1 omits the **hook** test rework (M2) |
| **L9.7 render-order, all 5 counts non-zero** | FINAL L9.7 | **PARTIAL** (2 signals) | ❌ no P-item; should ride P1 (M8) |
| **A4 PROV-O parser round-trip** | A4 / L9.6 | **MISSING** (`TestRoundTrip` = `json.loads`+key only) | ✅ P5 |
| **ADR 002 D1 line 32 reconcile** ("enforced at log time AND session-end" → "in multi-actor sessions") | A-F3 | **STALE, un-targeted** | ❌ P1 says "docstrings" — does not name ADR 002 D1 (M1/§4) |
| **ADR 002 → `docs/adr/README.md` index** | C6 | MISSING (index has only 001) | ✅ P6 |
| **CONTRIBUTING.md:75 widen to add `exporters/`,`scratchpad.py`** | C7 | NARROW | ✅ P6 |
| **ADR/spec asserting core/ext boundary + Tier-3-extension-only** | C8 / §3.5 | ABSENT | ✅ P6 (but un-cross-referenced to P2's invariant test) |
| **Spec §4.4 cross-session hard-reject + v1.1 deferral note** | A9 / F6 | ABSENT (`spec :379-381` SHOULD-only) | ✅ P6 |
| **`decision-audit.sh` guard mirror / persisted-audit refactor + hook integration test (L9 add)** | C9 / L11.5 | ABSENT (`decision-audit.sh:69` un-guarded) | ❌ **DROPPED from P1's edit list (M1)** |
| **CHANGELOG:24,45 false "multi-actor" claims** | F3/G5 | FALSE | ✅ P1 |
| **CHANGELOG header / git tags / compare-links** | C5/G6 | broken | ✅ P7 |
| **L9.0 fixture migration** | A5 | **DONE** (residuals intentional) | n/a — but synthesis never *states* it verified this; asserted-by-omission |
| **C10 PyPI/wheel/gh-release** | C10 | absent | ❌ no v1.0-deferral note in synthesis (Low) |

---

## Bottom line

**No CRITICAL/HIGH Round-1 finding was dropped — the plan's finding inventory is complete and faithfully synthesized.** The exhaustiveness failures are all at the *consequence* layer and all trace to over-compression of P1, P4, P9:

1. **P1 must explicitly add `decision-audit.sh:69`, ADR 002 D1:32, and `test_v041_decision_audit_hook.py::test_hook_handles_session_with_human_self_resolution` to its edit list, and must resolve the actor-*type*-vs-*instance* definition (with the participants-vs-event-actor drift).** As written P1 ships the inverse bug to every hook-running adopter and leaves a 2nd green-but-wrong test plus a stale ADR. (M1, M2, M3, §4)
2. **P9 must widen from "knowledge store" to also cover the non-atomic `.npy` sidecar and the wholesale-replace `.claude/SCRATCHPAD.md` lost-update** (the latter is the *same* concurrency hazard P9(b) raised, on a surface P9 never lists). (M5, M6)
3. **P4 is "delete the duplicate ⚠️ append", not "move"** — the low-severity hint line already exists; "move" risks a third render path. (B-F5)
4. Minor un-tracked: A-F6 dispatch-hint placement (no P-item), L9.7 partial render test (should ride P1), C10 (no v1.0-deferral note).

P2, P3, P5, P6, P7, P8 are sound and complete as scoped (P2's two-import scope grep-confirmed exhaustive; P3's L9.1 numbers JSON-verified achievable). The plan's *sequencing* should additionally state P1→P3 internal order (L9.1 depends on the P1 guard passing the genuinely-multi-actor waggle session).

*Provenance: all "VERIFIED" claims executed read-only (`git`, `grep`, `uv run pytest` on 3 targeted classes = 12 passed, `python3 -c` JSON analysis). No file written except this one. No process signalled. No `trace_*`/MCP calls. `git status` shows only the pre-existing untracked `notes/`, `docs/*.png`, `review_2026-05-18_v041_status/`, and a pre-existing ` M .mcp.json` — unchanged by this review.*
