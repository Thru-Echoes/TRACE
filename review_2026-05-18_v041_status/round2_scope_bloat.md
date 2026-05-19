# Round 2 — Scope-Bloat / Discipline Verification of the Proposed Remediation Plan

**Date:** 2026-05-18
**Reviewer:** Independent Round-2 scope-bloat verifier (read-only, mostly static analysis)
**Artifact under review:** `review_2026-05-18_v041_status/round1_SYNTHESIS.md` §5 (plan P1–P9) + §6 (scope discipline)
**Cross-checked against:** `trace_v1_FINAL_fix_plan.md` (Layer-10 exclusion list), `trace_v1_round3_amendments.md` (v1.1 deferrals + "survive R3 unchanged"), the 3 Round-1 reviews, and the merged code on `main`.
**Method:** Targeted reads of `decision_tools.py`, `session_tools.py`, `query_tools.py`, `extension_hooks.py`, `docs/specification.md`, `CHANGELOG.md`; read-only `git tag -l`; `grep` for the waggle-session reference in `tests/`. VERIFIED = ran the check. INFERRED = reasoned from read text.

---

## 1. Verdict

**The plan is appropriately scoped and disciplined. It does NOT over-reach.** Every P-item is the minimal response to a Round-1 finding; none bundles a "while we're here" refactor; none pulls Layer-10-excluded or Round-3-v1.1-deferred work into v0.4.1; none requires a schema/protocol version bump. The plan's own §6 scope-discipline is self-consistent with §5.

The one place the plan flirts with **under-scope, not bloat**, is P9(b) (shared-knowledge-store concurrency): it is correctly scoped as *verify-then-minimal-fix-or-document*, but it is the only P-item whose worst-case outcome ("add locking") could grow if the verification finds a real lost-update window. The plan handles this correctly by gating the fix behind an explicit Round-2 confirm/refute and offering "document the risk" as an acceptable terminal state — that is discipline, not bloat. Flagged below as the single watch-item.

Two minor trims recommended (P1 docstring/spec wording, P6 ADR-vs-spec duplication) — both are tightening, not removal of necessary work.

The plan is, if anything, slightly *leaner* than the FINAL plan + Round-3 amendments would license: it correctly declines to re-do Layer-7's 12 spec edits (already shipped, verified §3-clean by Reviewer A/B), re-doing L11.x adoption (verified ~9/9 by Reviewer C), or re-touching L1.3/L3.1/L6.x/A6 (verified solid). It only fixes what Round 1 proved broken or missing.

---

## 2. Per-P-item scope table

| P | Finding(s) | Scope verdict | Evidence | Recommended trim / expansion |
|---|------------|---------------|----------|------------------------------|
| **P1** | G1+G4+G5 | **MINIMAL** | The fix is exactly Round-3 **A1** (the binding amendment that *replaced* FINAL L3.2/L5.4 — `trace_v1_round3_amendments.md:37-49`). Code today (`decision_tools.py:80-83`, `session_tools.py:330-333`) ships full `(type,id)` equality but **zero** multi-actor gate; `git log -S participants` empty (Reviewer A F1, B F1, C C3 — all VERIFIED). P1 adds *one conjunct* (≥2 distinct actors) at the 3 sites A1 names (FM1, FM25, L5.4 detector), restores the deleted `test_human_self_resolves_clean`, and truths-up docs. **Does NOT expand the detector surface** — it *narrows* an over-firing detector back to A1's intended scope. §6 explicitly forbids "expand the AttributionAudit detector surface beyond restoring A1's intended scoping"; P1 obeys. | **Minor trim:** the new warning text and §3.6 wording must not be *added* — spec §3.6:233 *already* says "when the workflow involves multiple actors" (VERIFIED). So the doc work in P1 is purely making CHANGELOG (24,45) + docstrings (`decision_tools.py:97`, `session_tools.py:166,371`) match the *already-correct* spec — confirm P1 does not also rewrite §3.6 (it is already right; rewriting it would be needless churn). |
| **P2** | G2 | **MINIMAL** | `query_tools.py:158` unguarded `from …extensions.learn.store import load_store`, called unconditionally by `project_summary` (`:320`); Reviewer C VERIFIED `ModuleNotFoundError` by physically moving `learn/` out. P2 = guard `:158` (+ audit/fix the already-try/excepted `:340`) and add the delete-the-extension invariant test. **The plan offers the *minimal* form** (`try/except ImportError: omit metrics`) and explicitly notes the byte-identical fail-open precedent already exists at `health_check :339-344`. | **No trim.** Optional **scope note (not expansion):** the *cleaner* fix is routing through `extension_hooks.py` (the existing `recall_if_available`/`extract_if_available` fail-open IoC pattern — VERIFIED `extension_hooks.py:63-117`). P2's text allows either; the try/except form is the minimal one and is sufficient for v0.4.1. Do **not** let P2 grow into "build a metrics hook + refactor the registry" — that is C8/Reviewer-C §3.5 territory and belongs in P6's ADR as *policy*, not in P2 as *code*. As written P2 is correctly minimal; flag only so implementation doesn't gold-plate it. |
| **P3** | G3 | **MINIMAL** | The FINAL plan **itself** declared L9.1 "the canonical regression scenario… invalid by construction" without it (`trace_v1_FINAL_fix_plan.md:145`). VERIFIED: zero test files reference `446733`. P3 writes exactly that one gate test — load the waggle JSON, assert the documented counts. This is fulfilling a mandate the plan already owned, not new scope. | None. |
| **P4** | A8 (PARTIAL) | **MINIMAL** | A8's intent (`trace_v1_round3_amendments.md:120`) = rename `_warning_count`→`_hint_count` AND render lower-severity. Rename shipped; the hint is still `audit_warnings.append(...)` at ⚠️ severity (`session_tools.py:378-383`; Reviewer B F5 VERIFIED 3/3 false positives on innocuous prose). P4 = move the line to a lower tier + tighten the phrase list. **Does NOT rewrite the orphan detector** — the `_DISCOVERY_PHRASES` tuple and the O(K·N) scan stay; only the *severity placement* and *phrase list* change. This is de-noising, exactly A8's unmet half — not a redesign. | **No trim.** Watch that "tighten phrase list" stays a constant-edit (drop/keep entries in `_DISCOVERY_PHRASES`), not a switch to a new structural-signal engine — the FINAL plan already decided phrase-lists are "examples, not normative" and L5.5 already tightened to 4 phrases; further structural-signal work was *not* mandated and would be bloat. P4 text says "tighten phrase list" (constant edit) — correct. |
| **P5** | A4 (PARTIAL) | **MINIMAL** | A4 part 1 (qualified PROV-O exporter) is VERIFIED correctly shipped (Reviewer A claimed-vs-actual, Reviewer B item 5: genuine `prov:Influence`/`qualifiedInfluence`/`atLocation`, deterministic blank nodes — *not* a `wasRevisionOf` shortcut). Only A4 part 2 (the parser/ontology round-trip test) is absent (`TestRoundTrip` = `json.loads` + key-presence only). P5 = add *only* that test. **Explicitly does NOT touch the exporter** ("architecture already correct; only the test is missing"). Correctly leaves L6.x verified-solid (§3) untouched. | None. |
| **P6** | A9 + C6/C7/C8 | **MINIMAL (doc-only), with one de-dup trim** | All four sub-parts are pure documentation truth-up of *already-shipped* behavior: spec §4.4 note that cross-session refs hard-reject (impl VERIFIED hard-rejects at `append_event`; spec §4.4:381-382 silent on the tightening — Reviewer B F6); add ADR 002 to `docs/adr/README.md` index (Reviewer C C6); widen CONTRIBUTING core list to include `exporters/`+`scratchpad.py` (C7); add a durable ADR/spec home for the core/extension boundary + Tier-3-extension-only policy (C8). No code, no schema. | **Trim:** C8's "**Add an ADR (or spec section)**" risks producing *both* a new ADR (Reviewer C suggested `003-core-extension-boundary.md`) *and* a spec paragraph *and* an ADR-002 amendment — overlapping homes. Recommend P6 pick **one** durable home (a single new ADR is cleanest per C §3.5) + a one-line spec pointer; do not triplicate the same normative statement across ADR-002, ADR-003, and the spec. This is the only place the plan could accrete doc-bloat. |
| **P7** | G6 | **MINIMAL & correctly sequenced LAST** | Zero git tags ever (VERIFIED `git tag -l` = 0); CHANGELOG `[0.4.1] — In progress` (`:10`); `[Unreleased]` → dead `v0.4.0...HEAD` (`:172`, VERIFIED). P7 = flip header to a date, fix the compare link, decide tag policy (and **flags the tag-vs-commit-range choice for the user** rather than unilaterally backfilling history — correct restraint), branch+PR. It is explicitly gated on "P1–P3 land AND full green `uv run pytest`." | None. The user-flag on tag policy is exactly right — backfilling `v0.1.0..v0.4.0` annotated tags onto historical commits is a judgement call, not a mechanical fix; the plan correctly does not assume it. |
| **P8** | FM7 (low) | **CORRECTLY SCOPED — NOT a code fix** | The plan's §0 + Reviewer A §3 (in-process repro 0.001s, FM7 file untouched by v0.4.1, 17/17 pass) + Reviewer C corroboration establish FM7 is an E2E read-timeout flake under 8+-session load, not a `trace_end_session` regression. P8 says **"Do not modify `trace_end_session`"** and scopes itself to optional harness ergonomics (configurable timeout / serial-or-skip-under-load mark / document as known load-sensitive). This is the disciplined call: it neither "fixes" a non-bug (which would be the bloat trap the controller's seed hypothesis set) nor ignores the real harness fragility. **P8 is the model of correct scope restraint in this plan.** | None. Keep P8 strictly optional/parallel as written; do not let it become a `_send_and_receive` rewrite. |
| **P9** | concurrency (user-raised) | **(a) MINIMAL; (b) WATCH — correctly scoped as verify-then-minimal, low under-scope risk** | **(a)** Lazy-load `model2vec`/embedding provider: Reviewer A F5 VERIFIED the eager `register()`-time import + `StaticModel.from_pretrained` (cold-cache HF download *before* `initialize`) — a real pre-existing (v0.3) startup-latency + N×RAM cost across concurrent sessions. Making it lazy is a *minimal, well-targeted* efficiency fix that also removes the most plausible FM7 cold-start contributor. Not a rewrite — move one import/instantiation from `register()` to first-recall/extract. **(b)** Shared `~/.trace/knowledge/` concurrent-write safety: scoped as *Round-2 must confirm/refute, then either confirm locking exists OR document last-writer-wins OR add locking* — explicitly bounded, with "document the risk" an acceptable terminal state. Per-session files already correctly noted safe (atomic temp+`os.replace`). | **Watch (under-scope, not bloat):** P9(b)'s fix-size is contingent on the verification. If a real lost-update window exists, "add locking" must stay a *minimal* cross-process lock (e.g., a lockfile around the knowledge-store read-modify-write), **not** a speculative concurrency framework / queue / WAL — that would be the over-engineering §6 implicitly guards against. The plan's phrasing ("confirm cross-process locking, or document … and add locking") is correctly minimal. Recommend the implementation explicitly prefer *document the risk* unless a concrete corrupting interleaving is demonstrated, to keep P9(b) from growing. P9(a) and P9(b) are both pre-existing (v0.3) extension concerns — correctly *not* gated as v0.4.1 release blockers (P9 is not in the P1–P3 critical band). |

**Summary:** 7 MINIMAL · 2 with minor trims (P1 doc-wording de-dup, P6 ADR/spec de-dup) · 1 watch-item (P9b must stay minimal-or-document) · **0 BLOATED · 0 MIS-SCOPED · 0 (genuinely) UNDER-SCOPED**.

---

## 3. Verified-solid (§3) collision risks

Synthesis §3 verified-solid set: L1.3, L3.1/A10, L6.1–L6.3 PROV split, A6 `_is_explicit_absence`, A2/A3/A7, L11.x adoption (~9/9), stdin-EOF self-exit, ruff/pyright clean.

| §3 item | P-item that comes near it | Collision risk? |
|---|---|---|
| **L6.1–L6.3 PROV split** | P5 (PROV-O round-trip test) | **NONE.** P5 adds a *test only*; explicitly "architecture already correct; only the test is missing." It reads the exporter output, does not modify `prov_jsonld.py`/`prov_mapping.py`. No regression surface. |
| **A6 `_is_explicit_absence`** | P1, P4 (both touch `session_tools.py` audit loop) | **NONE.** P1 adds a multi-actor conjunct to the *self-resolution* branch; P4 moves the *orphan-discovery* line's severity. Neither touches `_is_explicit_absence` (`session_tools.py:43-64`) or its call sites (`:284,:346`). The function is independent of both edited regions. |
| **L3.1/A10 URI carve-out** | P6 (spec §4.4 / §3.7.1 doc note) | **NONE.** P6 *documents* the existing hard-reject + URI behavior; it does not alter `_URI_SCHEME_RE` (`session_tools.py:581`) or `_check_referential_integrity`. Doc-only. The §3.7.1 normative clause (`docs/specification.md:281`) stays. |
| **A2 (L5.7 removed) / A3 (dispatch hint)** | P4 (same audit-build region) | **LOW, manageable.** P4 edits the orphan-discovery append (`session_tools.py:378-383`); the A3 dispatch-hint append (`:400-411`) and A2's absence-of-summary-regex are *adjacent but distinct* branches. Risk = an imprecise edit collaterally moving the A3 hint. **Mitigation (advice, not a plan defect):** P4 must touch only the `if orphan_discovery_ids:` block and the `_DISCOVERY_PHRASES` constant, leaving the A3 `[hint]` block byte-identical. The plan's text ("Move the hint out of `audit_warnings`") is correctly narrow; just ensure the *orphan* hint, not the *dispatch* hint, is moved. |
| **L1.3 single-source `trace_version`** | P7 (CHANGELOG/release) | **NONE.** P7 edits CHANGELOG header/links + tag policy; does not touch `schema/session.py` or `_auto_environment`. |
| **L11.x adoption (~9/9)** | P6 (CONTRIBUTING widen, ADR index) | **NONE — complementary.** Reviewer C found L11.6's *only* defect was ADR-002 missing from the index (C6) and CONTRIBUTING under-scoped (C7). P6 fixes exactly those two residual misses; it does not re-touch the verified-solid L11.1–L11.5/L11.7–L11.9 cascade. P6 *completes* L11.x, does not regress it. |
| **stdin-EOF self-exit** | (none) | **NONE.** No P-item touches `server.py main()`/stdio. The plan correctly leaves Reviewer B F8 / synthesis §3's "pathological pipe-retention edge" as the explicit non-issue it is — no hardening item smuggled in. Good restraint. |

**No P-item endangers a §3 verified-solid item.** The two adjacency cases (P4↔A2/A3, P1/P4↔A6) are implementation-precision notes, not plan-scoping defects. The plan's §6 lead clause "Do not touch §3 verified-solid items" is honored by every P-item.

---

## 4. Sequencing & version assessment

### 4.1 Sequencing — sound, with one ordering hazard the plan *already* defuses

Declared order (§5/§6): **P1–P3 (criticals, TDD) → P4–P6 → P7 (release, green-gated) ; P8 optional/parallel.** P9 unsequenced (user-raised, pre-existing — correctly not in the critical band).

- **P1–P3 first:** correct. These are the three CRITICAL Round-1 findings (G1/G4/G5, G2, G3). TDD ("write the failing/contract test first") is the right discipline given the meta-finding that no green suite ever gated the merge.
- **P7 last, green-gated:** correct and load-bearing. P7 explicitly requires "P1–P3 land AND full green `uv run pytest`."
- **Ordering hazard the prompt asked about — "P6 doc truth-up before P1 lands would document not-yet-true behavior":** The plan **already avoids this**. P6's doc work is *§4.4 hard-reject*, *ADR index*, *CONTRIBUTING core list*, *core/extension boundary policy* — **none of which is the P1 behavior**. P1 owns its *own* doc truth-up (CHANGELOG/§3.6/docstrings) *as part of P1*, sequenced with the P1 code. So there is no window where P6 documents P1's not-yet-landed multi-actor scoping. The doc-truth-up responsibilities are correctly partitioned: P1 documents P1's behavior; P6 documents the *other, already-shipped* behaviors. **No ordering hazard.** (Had P6 owned the §3.6/CHANGELOG multi-actor truth-up, that would be a hazard — it does not.)
- **P5/P6 after P1–P3:** fine — P5 is an independent test; P6 is doc-only; neither depends on or blocks the criticals. P4 (de-noise) is independent of P1's detector-narrowing (different branch). No inter-P4/P6 dependency hazard.
- **P9 placement:** correctly *not* in the P1–P3 critical band. P9(a)/(b) are pre-existing v0.3 extension concerns (Reviewer A F5: "Pre-existing (v0.3); NOT introduced by v0.4.1"). Treating them as release-gating would be **over-scope**; the plan correctly does not. P9 connects to FM7 only as *root-cause hardening*, explicitly not as a v0.4.1 blocker. Disciplined.
- **P8 optional/parallel:** correct — it is not gating and must not block P7.

**One soft note:** P7's tag-policy decision is flagged for the user. That correctly *blocks P7 on a human decision*, not on engineering — the plan does not pretend tag-backfill is mechanical. This is correct sequencing of a judgement call, not a hazard.

### 4.2 v0.4.1-vs-v1.1 placement — correct; nothing excluded/deferred is pulled in, nothing real is pushed out

Cross-checked every P-item against `trace_v1_FINAL_fix_plan.md` Layer-10 ("explicitly NOT included in v0.4.1") and `trace_v1_round3_amendments.md` v1.1 deferrals (A3→L5.6 deferred to v1.1; L11.10 `--upgrade` deferred; A9 cross-session-relaxation deferred):

- **No P-item revives a Layer-10 exclusion.** P1–P9 contain no `trace_log_discovery` wrapper, no `subagent_dispatch`/`subagent_claim` event type, no auto-snippet-extraction, no hard-required snippet, no `dispatch_kind`/`prompt_summary`/`result_summary` fields, no idle-gap/dispatch hooks, no `scripts/audit_coverage.py`, no `0.5.0` bump, no retroactive injection. Verified by reading §5 against Layer-10's list — zero overlap.
- **P9 ≠ the deferred dispatch/auto-capture work.** P9 is *lazy-load an existing import* + *verify an existing shared-store's concurrency*. It is **not** L5.6's deferred dispatch-visibility detector, **not** auto-capture hooks, **not** L11.10's `--upgrade`. It is a pre-existing-robustness fix, not deferred-feature pull-in. Correctly placed.
- **A9 cross-session relaxation:** P6 only *documents the current hard-reject + the v1.1-deferral note* (exactly A9's intent: "Defer the relaxation to v1.1, but document the current behavior"). P6 does **not** implement the relaxation in v0.4.1. Correct — the plan respects the v1.1 deferral and only ships the doc note A9 mandated for v0.4.1.
- **L5.6 dispatch detector:** Round-3 A3 deferred the *real warning/counter* to v1.1 and shipped only an advisory hint for v0.4.1. The merged advisory hint is VERIFIED present (`session_tools.py:400-411`); no P-item tries to upgrade it to a counted warning in v0.4.1. Reviewer A F6 noted the hint is in `audit_warnings` rather than a separate list — **the plan correctly does NOT add a P-item for this** (it is cosmetic, A3-sanctioned, and "upgrade to real warning" is explicitly v1.1). Declining to fix F6 is correct scope discipline, not under-scope.
- **Nothing real pushed OUT:** the three CRITICALs (G1/G4/G5, G2, G3) and the canonical L9.1 gate are all *in* v0.4.1 (P1/P2/P3). No release blocker is deferred. The only items left to v1.1 (cross-session relaxation, dispatch auto-capture, `--upgrade`) are the ones Round-3 *already* designated v1.1. No under-scope.

### 4.3 Version-bump correctness — the "do NOT bump protocol/schema version" stance is correct

§6: "Do not bump the protocol/schema version (all P-items are bugfix/doc, additive)." Verified per-item:

- **P1:** narrows an over-firing runtime warning (adds a guard conjunct). Pure behavior bugfix — *removes* false positives; emits no new field, no schema change. No bump.
- **P2:** wraps an import in try/except. No schema, no protocol surface. No bump.
- **P3:** adds a test. No bump.
- **P4:** moves a warning's render tier + edits a phrase constant. No schema/field change. No bump.
- **P5:** adds a test. No bump.
- **P6:** spec/ADR/CONTRIBUTING *documentation* of already-shipped behavior. Critically — the §4.4 note documents an *existing* hard-reject (a tightening that *already shipped* in v0.4.1); documenting it is not a new normative change requiring a version bump. Spec Appendix B is already at `0.4.1` (VERIFIED `docs/specification.md:726`). No bump.
- **P7:** CHANGELOG/tags/release process. The package is *already* `0.4.1` (VERIFIED). P7 finalizes the *existing* 0.4.1, it does not advance to 0.4.2/0.5.0. Correct — and consistent with the FINAL plan's own "all changes additive 0.4.0 → 0.4.1; protocol stays 0.x" (`trace_v1_FINAL_fix_plan.md:206`) and ADR-002-D6 (spec URL/PROV ns deliberately retained at v0.3).
- **P8:** harness ergonomics / docs. No protocol surface. No bump.
- **P9:** (a) import laziness — internal perf, no schema; (b) concurrency verify/lockfile/doc — no schema, no protocol field. No bump.

**Every P-item is genuinely additive-bugfix-or-doc. None requires a schema or protocol version change.** The stance is correct. (Note: P1 actually makes the *shipped* code finally match the *already-0.4.1* spec §3.6 — it closes a code-vs-spec gap *without* needing any version movement, which is the strongest evidence the no-bump stance is right.)

### 4.4 §6-vs-§5 self-consistency

§6's five directives vs §5's nine P-items:

1. "Do not touch §3 verified-solid" ↔ §3 collision analysis above: **consistent** (no P-item regresses a §3 item).
2. "Do not 'fix' FM7 as a code regression" ↔ P8 ("Do not modify `trace_end_session`") + §0: **consistent**.
3. "Do not bump protocol/schema version" ↔ §4.3 per-item: **consistent** (all additive/doc).
4. "Do not expand the AttributionAudit detector surface beyond restoring A1's intended scoping" ↔ P1 *narrows* the detector; P4 only *re-tiers* an existing hint; no P-item adds a new detector: **consistent**. (P9 adds no detector — it is import-laziness + store-locking, not audit logic.)
5. "Sequence P1–P3 → P4–P6 → P7 (green-gated); P8 optional/parallel" ↔ §5's stated priority order: **consistent verbatim**.

**No P-item contradicts §6.** The plan's scope-discipline section is internally consistent with its remediation section. (The only nuance: §6 enumerates P1–P3/P4–P6/P7/P8 but is silent on P9 in the sequence line — P9 is user-raised and pre-existing, correctly *outside* the release-critical sequence; this is an omission-by-design, not a §5/§6 contradiction.)

---

## 5. Bottom line

**The plan is disciplined and appropriately scoped — recommend proceeding as written.** It is the minimal closure of Round-1's findings: it fixes only what Round 1 proved broken (G1–G6) or partial (A4/A8/A9, C6/C7/C8), explicitly protects §3 verified-solid, refuses the FM7 non-bug, declines cosmetic non-issues (F6 dispatch-hint placement, stdin-EOF hardening), and pulls in zero Layer-10-excluded or Round-3-v1.1-deferred work. No schema/protocol bump is needed or implied.

**Two minor trims (tightening, not scope cuts):**
- **P1:** spec §3.6 is *already* correctly worded ("when the workflow involves multiple actors"); P1's doc work is only CHANGELOG + docstring truth-up — do not also rewrite §3.6.
- **P6:** pick *one* durable home for the core/extension-boundary policy (single new ADR + a one-line spec pointer); do not triplicate it across ADR-002, a new ADR, and the spec.

**One watch-item (under-scope risk, not bloat):** P9(b) — keep any concurrency fix to a minimal cross-process lockfile *or* a documented last-writer-wins risk; do not let it grow into a speculative concurrency framework. The plan already scopes it as verify-then-minimal — hold that line.

**No sequencing hazard.** The P6-documents-not-yet-true-behavior risk the prompt asked about does **not** exist: P1 owns its own doc truth-up (sequenced with P1 code); P6 documents only *other, already-shipped* behaviors. P7 is correctly last and green-gated. P9 is correctly outside the release-critical band (pre-existing v0.3 concern). The no-version-bump stance is correct for all nine items.
