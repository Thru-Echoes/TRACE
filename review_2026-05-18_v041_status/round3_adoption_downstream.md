# Round 3 — Adoption & Downstream Verification of the FINAL Remediation Plan

**Date:** 2026-05-18
**Reviewer:** Independent Round-3 adoption-downstream verifier (LAST verification before implementation; no prior context; adversarial; READ-ONLY).
**Target:** `review_2026-05-18_v041_status/round_FINAL_plan.md` (P1–P9 + "Open items for USER"), cross-checked against `round1_C_adoption_boundary.md`, `round2_exhaustiveness.md`, and live `main` (`d20be80`).
**Method:** Static read + read-only `git log/tag -l`, `grep`, `sed`-view, `find`. One `find` + `cat` of installer assets. No file written except this one. No process signalled. No `trace_*`/MCP call. No directory moved. No state-changing git.

---

## 1. Verdict

**The FINAL plan, IF implemented exactly as written, yields a *substantially* defensible v0.4.1 — but it carries ONE adopter-propagation gap that is documentation/sequencing-shaped (not an `--upgrade` requirement) and ONE doc-target omission (ADR-002 D1:32) inherited verbatim from Round-2 M1/A-F3 that the FINAL plan did NOT fold in.** Neither is fatal; both are cheap; both must be added to P1's edit list or the release ships a stale "enforced … AND at session-end" claim in its own canonical "why" document.

**Confidence: 88%** that P1–P9 as written produce a defensible release *once the two additions below are made*. Confidence that the plan **as literally written** (without those additions) is fully defensible: **~70%** — it leaves ADR-002 D1:32 stale and does not state the P1→P3 internal order or the type-vs-instance resolution in the body (it is parked in "Open items for USER", which is acceptable only if the USER actually answers it before P1 is coded).

All three Round-1/Round-2 CRITICALs were independently **re-confirmed still live in `main`** (not yet fixed — this plan precedes implementation): C1 `query_tools.py:158` bare import (no `try/except`; `:340` is the 2nd, guarded); C2 zero test refs to `446733`; C3 no multi-actor gate in `decision_tools.py`/`session_tools.py`/`decision-audit.sh:69`; C5 zero git tags, 5 dead CHANGELOG links + dead `[Unreleased]`. The plan targets real, present gaps.

---

## 2. Adopter-Propagation Assessment (M1) — **NO `--upgrade` path required; M1's fix IS sufficient mechanically, but needs one sequencing correction**

### 2.1 Does `trace-mcp-init` re-run overwrite the stale hook? **YES — unconditionally, no flag.** VERIFIED.

Trace path (read, file:line):
- Entry point `pyproject.toml:42` → `trace_mcp.init_project:main`.
- `init_project.py:141 main()` → `init_project()` → `init_project.py:119 adapter.install()` → `claude_code/__init__.py:35 _install_hooks()`.
- `_install_hooks` (`claude_code/__init__.py:70-87`): for each `*.sh`, **byte-compares** `dst.read_bytes() != src.read_bytes()` (`:78`). If different → disposition `"updated"` → `:82-84` `shutil.copy2(src, dst)` **unconditionally overwrites** the file and re-chmods `0o755`. If byte-identical → `"skipped"`.

**Conclusion:** a plain `trace-mcp-init` re-run (exactly what `CHANGELOG.md:58` already instructs: *"Consumer projects with installed hooks should re-run `trace-mcp-init` to refresh `decision-audit.sh`"*) **WILL** replace the stale `decision-audit.sh` with the new bytes. There is **no skip-if-exists** behaviour and **no `--upgrade`/`--force` flag is needed**. The base-class contract (`base.py:38-44`) only promises *idempotent re-run produces `skipped` for unchanged files* — it does **not** mean "don't touch existing files"; changed content is always rewritten. **Round-3 L11.10's `--upgrade` deferral is therefore correct and not release-blocking.** M1's mechanism (mirror the guard into `decision-audit.sh:69` + a CHANGELOG re-run callout) **is mechanically sufficient** to propagate the fix to the ~15 consumer projects: re-running `trace-mcp-init` (already a refreshed-hooks habit per MEMORY: all 15 `.mcp.json` refreshed to v0.4.1 hooks on 2026-05-15) byte-replaces the hook.

### 2.2 Is there any OTHER adopter-shipped asset encoding the un-guarded FM1? **NO.** VERIFIED.

`find` over `adapters/claude_code/assets/` = 7 files. Grep for self-resolution / `proposed_by` / `same.instance` / FM1 logic:
- **`decision-audit.sh`** — the ONLY asset with the FM1 *heuristic*. `:69` `pb.get("type")==rb.get("type") and pb.get("id")==rb.get("id")` with **no actor-count gate** (VERIFIED still un-gated in the shipped asset). This is the single propagation surface.
- **`CLAUDE_BLOCK.md`** — grep hit is only the §3.6 *normative rule* prose (`:21-24`, "Question→AI-proposal→accept means `proposed_by=ai`…"). That rule is **correct and unchanged by P1** (the plan explicitly does NOT touch spec §3.6). CLAUDE_BLOCK encodes the *attribution rule*, **not** the false-positive-prone *detector heuristic*. No bug here.
- `settings_template.json` — only registers hook commands (`PostToolUse:trace_end_session → decision-audit.sh`). No logic.
- `pretool-guard.sh` / `prompt-reminder.sh` / `session-reminder.sh` — no FM1/attribution logic (grep clean).

**So adopters do NOT silently keep the false-positive *if* (a) the guard is mirrored into `decision-audit.sh:69` per M1 AND (b) they re-run `trace-mcp-init`.** Both are already plan/CHANGELOG-covered. The residual risk is purely behavioural: an adopter who never re-runs `trace-mcp-init` keeps the old un-gated hook — but that adopter is *already* running the v0.4.0 hook today (per MEMORY most were refreshed 2026-05-15) and the CHANGELOG re-run callout is the standard propagation channel TRACE uses for every hook change (v0.4.0 used the same). This is acceptable for an additive release.

### 2.3 The one correction the FINAL plan still needs (Round-2 M1, only half-folded)

`round2_exhaustiveness.md` M1 raised **two** sub-items against the un-scoped P1:
1. **`decision-audit.sh:69` guard mirror** — ✅ the FINAL plan **DID** fold this in (`round_FINAL_plan.md:15`: *"M1: mirror the guard in …decision-audit.sh:69 + add a CHANGELOG callout"*). **Closed.**
2. **`test_v041_decision_audit_hook.py::test_hook_handles_session_with_human_self_resolution`** (single-actor, zero-participant session asserting the warning fires — `:74-103`) — ✅ the FINAL plan **DID** fold this in (`round_FINAL_plan.md:16`: *"`test_v041_decision_audit_hook.py::test_hook_handles_session_with_human_self_resolution` (single-actor ⇒ no warn)"*). **Closed.**

**BUT `round2` M1/§4 also named a *third* target the FINAL plan did NOT fold in: `docs/adr/002-…:32`.** ADR-002 D1 line 32 asserts (VERIFIED, read): *"The rule is enforced at log time (FM1 generalized to all same-instance pairs) AND at session-end…"* — an **unconditional** claim. After P1, the truthful statement is "enforced **in ≥2-actor-type sessions**". `round_FINAL_plan.md:17` says only *"fix `CHANGELOG.md:24,45` + `decision_tools.py`/`session_tools.py` docstrings. Do NOT touch spec §3.6"* — it **does not name ADR-002 D1:32**. ADR-002 is the *durable "why" home* (L11.6's whole purpose); leaving D1:32 stale re-creates the exact G5/A-F3 self-contradiction the audit exists to kill, in the most authoritative doc. **This is a real, un-closed dilution.** It is one line, trivially fixed, but it is NOT in any P-item's edit list.

> **Required addition to P1's "Doc truth-up" bullet:** add `docs/adr/002-v041-protocol-additions.md:32` to the edit list alongside CHANGELOG:24,45 and the docstrings. Reword "enforced at log time … AND at session-end" → conditioned on multi-actor (≥2 actor-type) sessions, consistent with the post-P1 behaviour.

---

## 3. Boundary-Documentation Structure Verdict — **"one ADR + references" is the right structure; CONFIRMED it is NOT yet satisfied; ADR-002 is NOT that home**

### 3.1 Current state (VERIFIED)

- The **only** core/extension boundary statement in the repo is `CONTRIBUTING.md:75`: *"Core (`server.py`, `schema/`, `storage/`, `tools/`) must not import from `extensions/` — extensions integrate via the hook registry in `extension_hooks.py`."* It is **under-scoped** (omits `exporters/`, `scratchpad.py` — C7) and **contradicted by shipped code** (C1: `query_tools.py:158`).
- **`docs/specification.md` has NO core/extension architectural boundary statement and NO Tier-3-stays-extension policy.** Grep for `extension|boundary|Tier 3|cross-session` → only schema-*extension-point* language (`:488-490`) and PROV-namespace text (`:459`). Confirms C8 and round1_C §3.5.
- **`docs/adr/002-…` does NOT contain a boundary/Tier-3 statement.** It is purely the eight v0.4.1 *protocol* decisions (D1–D8). So P6's "ONE new ADR = canonical home for core/extension + Tier-3-stays-extension" is a **NEW ADR (e.g. 003)**, distinct from ADR-002.
- **ADR-002 is real, complete, but absent from the index.** `docs/adr/README.md` table ends at row 001 (file is 11 lines; only the 001 row) — C6 confirmed. This is the L11.6 discoverability defect.
- Tier-3 appears only at `CONTRIBUTING.md:106-109` under "Development roadmap" with **zero scope-fence language** — C8 confirmed.

### 3.2 Is "one ADR + references from CONTRIBUTING + spec" the right durable structure? **YES.**

Rationale: ADRs are point-in-time, the project's own `docs/adr/README.md:1-6` says they are NOT updated when code changes and supersession is by new ADR. A boundary *policy* (an invariant that must hold forever) is exactly the kind of normative decision an ADR is for, and a single normative home with thin references avoids the triplication the FINAL plan's scope-discipline (`:57`) explicitly forbids. The CONTRIBUTING line stays (contributor-facing) but should *reference* the ADR rather than re-state policy; the spec gets a one-paragraph normative pointer. **This structure does satisfy governance `evt_002`** *provided the new ADR* (a) enumerates the full core set including `exporters/` + `scratchpad.py`, (b) states zero-import-from-`extensions/` + integration only via `extension_hooks.py`, (c) states Tier-3 (RL feedback + cross-project knowledge) MUST stay in `extensions/learn/`, (d) names the "delete `extensions/learn/` ⇒ 18 tools still work" acceptance test (= P2's CI-enforceable invariant test) as the binding gate, and (e) cites C1 as the motivating violation. P6 + P2 together produce exactly this **only if P6's ADR explicitly cites P2's invariant test** as the acceptance gate — `round2` §4 (P2 note) flagged that P2 and P6 are currently un-cross-referenced. **Add to P6: the new boundary ADR must name P2's "extensions/learn/ absent ⇒ 18 core tools work" test as the enforceable acceptance criterion.** With that one cross-reference, the structure is durable and `evt_002`-satisfying.

**Verdict: structurally correct; not yet satisfied in `main`; the FINAL plan's P6 closes it *iff* it (i) creates a NEW ADR (not edits ADR-002), (ii) adds ADR-002 to the index separately (C6), (iii) cross-references P2's invariant test.**

---

## 4. P7: Tag-vs-Commit-Range — **Recommendation: (a) create real annotated tags `v0.1.0..v0.4.1` (history is fully reconstructable). It yields the strictly more defensible CHANGELOG.**

### 4.1 Evidence

- `git tag -l | wc -l` = **0**; `git for-each-ref refs/tags | wc -l` = **0**. Zero tags ever. (C5/G6 confirmed.)
- `CHANGELOG.md:172-176` + `:172` `[Unreleased]`: **all 5 compare/release links + the `[Unreleased]` link are dead** (`compare/v0.4.0...HEAD`, `compare/v0.3.0...v0.4.0`, `compare/v0.2.0...v0.3.0`, `compare/v0.1.0...v0.2.0`, `releases/tag/v0.1.0`). Every "What's new" target 404s.

### 4.2 Is option (a) reconstructable from git history? **YES — unambiguously.** VERIFIED via `git log`:

| Tag | Commit | Evidence (commit subject) |
|---|---|---|
| `v0.4.1` | `d20be80` (PR #6 merge) or `064f077` | `064f077 upgrade to v0.4.1: …`; `d20be80 Merge pull request #6 …feat/v0.4.1-fix-plan` |
| `v0.4.0` | `0540346` | `0540346 PR9: v0.4.0 cut — bump version, finalize CHANGELOG section` |
| `v0.3.0` | `50051ec` | `50051ec TRACE v0.3 release: attribution audit, scratchpad, embeddings, decision guards` |
| `v0.2.0` | `568c023` | `568c023 v0.2 trace-mcp complete, including: …` |
| `v0.1.0` | `7110528` (or `1dfddb1`) | `7110528 [HUMAN-EDIT] Initialized TRACE repo`; `1dfddb1 Added knowledge persistence, behavioral checks, checkpoints…` (the `[0.1.0]` CHANGELOG bullets = "knowledge persistence, behavioral checks, checkpoints"). The initial-repo region is the v0.1.0 anchor. |

Every release has a clearly self-identifying "release/bump" commit. Reconstruction is **not** guesswork — it is a 5-tag mechanical mapping. (Choose the *merge* commit for v0.4.0/v0.4.1 since those went through PRs, and the release commit for v0.1–0.3.)

### 4.3 Does GitHub support commit-range compare URLs (option b)? **YES — `github.com/<owner>/<repo>/compare/<base>...<head>` accepts full commit SHAs, branches, OR tags** (long-standing GitHub behaviour; the three-dot/two-dot compare endpoint is ref-agnostic). So option (b) is *technically valid* for the four `compare/…` entries. **However:** the `[0.1.0]` line is `releases/tag/v0.1.0` (a *release page*, not a compare) — a commit SHA cannot substitute there; it would have to become a `tree/<sha>` or a "first commit, no prior release" note. So option (b) still requires a special-case for `[0.1.0]` and produces opaque 40-hex URLs that no reader can interpret as "v0.3.0→v0.4.0".

### 4.4 Which is more defensible? **(a) tags.**

- (a) makes **every** CHANGELOG link resolve to a human-meaningful `v0.3.0...v0.4.0` diff, enables `gh release` per tag (the C10 v1.0 path), and matches the CHANGELOG's own declared *"adheres to Semantic Versioning"* (`:6`) — a project that claims SemVer but has zero tags is itself a credibility gap for an OSS+commercial release whose entire thesis is provenance integrity.
- (b) is a lower-effort patch that leaves the repo tag-less (still no `gh release` surface, still SemVer-claim-vs-no-tags dissonance) and yields unreadable links.
- The plan's framing ("USER decision: tag policy") is fine, **but the verifier recommendation is unambiguous: (a)**. The only argument for (b) — "history might not be reconstructable" — is **refuted** by §4.2. Recommend P7 default to (a); (b) only as a fallback if the USER refuses to mutate refs.

### 4.5 Is "green full suite gates P7" reliably enforceable given FM7 is load-flaky? **PARTIALLY — needs an explicit serialization clause, which P8/P9(a) supply.**

`round_FINAL_plan.md:4` + `:38-39` mandate a full green `uv run pytest` *"run without session load contention"* as the P7 gate, and classifies FM7 as a load-flake (NOT a code regression — do not fix `trace_end_session`). The risk: on a loaded machine (8+ live Claude sessions per the environment) the MCP-subprocess E2E tests (FM7 family) can hang/timeout, so "green" is **not deterministically reproducible** as a gate. **This is real but the plan already mitigates it twice:** P8 (`:42`) makes the E2E `_send_and_receive` timeout configurable / marks MCP-subprocess E2E serial-or-skip-under-load; P9(a) (`:45`) removes the eager `model2vec` cold-start that is FM7's root cause (≥0.5 s/subprocess + N×RAM). **Sequencing matters:** P7 is LAST and P9(a) precedes it (`:50`), so by the time the P7 gate runs the cold-start multiplier is gone. **Recommendation:** make P7's gate clause explicit — "full suite green **with FM7-family E2E run serially (P8) on a quiesced machine, AFTER P9(a) lands**"; otherwise "green gate" is theatre on a loaded box. As written the plan *implies* this ordering but does not state the gate is conditional on P8's serialization — tighten the P7 wording. With that, the gate is enforceable.

---

## 5. Honest Post-Plan Readiness + Release-Critical Gaps NO P-item Covers

### 5.1 Assuming P1–P9 implemented exactly as the FINAL plan says (with §2.3 + §3.2 + §4.5 additions folded in):

| Target | Post-plan honest readiness | Basis |
|---|---|---|
| **Defensible v0.4.1 finalize** | **~93%** | P1 closes C3/C4 + the FM1/FM25/hook/session-end surfaces + the green-gate; P2 closes C1 (the only core-tool break); P3 closes C2 (the release's own correctness contract); P4/P5/P6 close the HIGH/MED doc+test gaps; P7 makes the CHANGELOG resolvable; P9(a)(b)(c) closes the concurrency/cold-start. The residual ~7%: (i) ADR-002 D1:32 only closes if §2.3 addition is honoured (not in the plan as written → currently a known stale-doc risk); (ii) the type-vs-instance definition is parked in "Open items for USER" — **outcome-determining and unresolved**; if the USER does not answer before P1 is coded, P1's test matrix (`system→system`, `claude→gpt`) is undefined and the implementation can silently pick the wrong definition (Round-2 M3); (iii) the green-gate's reproducibility depends on the §4.5 serialization wording being added. None of these is a code-discovery risk — all are enumerable and cheap. |
| **v1.0-OSS** | **~70%** | v0.4.1-finalize work + the v1.0 items the plan **explicitly scopes out**: C10 (no PyPI/wheel/`gh release`; install path is a machine-local absolute `uvx --from /Users/echoes/…`); the boundary ADR's CI-enforced "delete extension ⇒ 18 tools" gate must actually be wired into CI (P2 writes the *test*; P6 writes the *ADR*; **no P-item says "add it to the CI workflow"** — see §5.2). v1.0 also implies a stable public install story off the local path and real release tags (P7(a)). The plan is correctly v0.4.1-scoped; it does not pretend to deliver v1.0. |

### 5.2 Release-critical steps NO P-item covers (beyond the manuscript/talk artifacts, which are correctly out of scope)

1. **CI enforcement of the boundary invariant.** P2 writes the "extensions/learn/ absent ⇒ 18 core tools work" test; P6 writes the ADR naming it the binding gate — but **neither P-item adds that test to the CI workflow** (`f5f4279 PR10: Add CI workflow` exists in history; the plan never says "register the new invariant test in CI"). Without CI wiring, the governance fence is documentation, not enforcement — exactly the C1-recurrence risk round1_C §3.4 warned about. **This is release-critical for the *durable* boundary claim and is uncovered.** (Low-effort; one CI-job line. Arguably v1.0-scope, but the ADR will *assert* "CI-enforceable invariant" — asserting it without wiring it re-creates the spec-says-X-code-does-Y gap the audit exists to kill.)
2. **Type-vs-instance definition (Round-2 M3 / FINAL "Open items for USER" #1).** Not a *missing* P-item — it is explicitly a USER decision — but it is **release-critical and unresolved**, and it gates P1's test matrix and the L9.1 (P3) assertions. Flagging here because "Open items for USER" being unanswered at implementation start is itself a release risk the plan does not time-box. (CLAUDE.md global instructions resolve this to "≥2 actor TYPES" per `evt_016`/A1; the plan body should adopt that as the default rather than leaving it fully open.)
3. **No P-item re-states the P1→P3 internal ordering** (L9.1's `attribution_warning_count≥2` only holds *after* P1's guard passes the genuinely-multi-actor waggle session). `round_FINAL_plan.md:50` says "P1, P2, P3 (criticals, TDD)" but does not order P1→P3 *within* the criticals. Round-2 §4(P3) flagged this. Low-effort wording fix; if P3 is written/run before P1 it asserts pre-guard behaviour. **Uncovered as an explicit sequencing statement.**
4. **`[Unreleased]` compare link** — `CHANGELOG.md:172` `compare/v0.4.0...HEAD` is dead today and stays dead under P7(b) unless special-cased; P7 mentions "fix `[Unreleased]` link" (`:39`) so this **is** covered — noting it only to confirm P7's `[Unreleased]` clause is necessary and present.

Everything else release-critical is covered by some P-item. No CRITICAL/HIGH adoption finding from round1_C or round2 was **dropped** by the FINAL plan (re-cross-checked the full L11.x matrix + the C1–C10 / M1–M8 inventories): C1→P2, C2→P3, C3/C4→P1, C5→P7, C6/C7/C8→P6, C9→P1(M1, folded), C10→explicit v1.0 deferral (now acknowledged), M1-hook/M2-test→P1 (folded), M1-ADR-D1:32→**NOT folded (§2.3)**, M3-type/instance→"Open items" (parked), M4→P1 placement (implicit), M5/M6→P9(b)(c) (folded), M7→A-F6 (Low, still untracked — cosmetic, acceptable), M8→L9.7 partial (rides P1, plan does not state it — Low). The only substantive un-closed dilution is **ADR-002 D1:32 (§2.3)**; the rest are Low/cosmetic or parked-by-design.

### 5.3 Is "no version bump" defensible to downstream PROV-LD / schema consumers? **YES — and it is the *correct* call.**

The PROV-LD correction-predicate change (`wasRevisionOf` → `wasInvalidatedBy`/qualified `wasInfluencedBy`) IS breaking for SPARQL/jq consumers, but: (a) it is a **0.4.0→0.4.1 patch within a 0.x line** where SemVer permits breaking changes in any release (CHANGELOG `:6` claims SemVer; pre-1.0 the API is explicitly unstable); (b) the schema `$id` already cascaded to `trace-v0.4.json` while the PROV namespace + `Session.context` URL deliberately stay at `v0.3#` per **ADR-002 D6** (additive-within-namespace — a documented, defensible W3C-conventional decision); (c) the breaking step is called out **three times** (CHANGELOG `:40`,`:57`; ADR-002 D3; migration notes). A downstream PROV consumer gets an explicit, documented migration callout — that is the honest contract. Bumping to 0.5.0 or 1.0 purely for this would over-signal (the wire format is backward-compatible; only one PROV predicate changed). **The "no version bump beyond 0.4.1" stance is defensible *because* the breaking surface is namespaced, documented, and pre-1.0.** The one thing that would make it *indefensible* — a CHANGELOG whose own links 404 so a consumer cannot even diff v0.4.0→v0.4.1 to see the change — is exactly what P7 fixes. **P7 is therefore not cosmetic; it is what makes "no version bump" honest to downstream consumers.**

---

## 6. Bottom Line

**The FINAL plan yields a defensible v0.4.1 (~93% post-plan) and is the correct scope — IF three cheap additions are made before implementation:**

1. **P1 doc-truth-up MUST add `docs/adr/002-…:32`** (the "enforced at log time AND at session-end" → condition on multi-actor). Round-2 M1 named it; the FINAL plan folded the hook + the 2nd test but **not** the ADR line. Without this the release ships a self-contradiction in its own canonical "why" doc — the precise failure the audit exists to eliminate.
2. **P6's new boundary ADR MUST cite P2's "delete extensions/learn/ ⇒ 18 tools" test as the binding acceptance gate, and a P-item (or P2/P6) MUST wire that test into CI.** Otherwise the governance fence is prose, not enforcement (C1-recurrence risk). The "one ADR + references" structure is correct; ADR-002 is NOT that home (it is index-missing per C6 — fix separately); a NEW ADR is.
3. **P7 should default to option (a) real annotated tags `v0.1.0..v0.4.1`** — history is fully reconstructable (§4.2 mapping is unambiguous); (b) commit-range URLs are GitHub-valid but opaque, leave the repo tag-less, special-case `[0.1.0]`, and keep the SemVer-claim-vs-zero-tags credibility gap. The green-gate clause must explicitly require FM7-family E2E run serially (P8) AFTER P9(a), on a quiesced machine, or "green" is non-reproducible on the loaded host.

**Adopter-propagation is NOT broken and does NOT require an `--upgrade` path:** `trace-mcp-init` byte-compares and unconditionally overwrites changed hooks (`claude_code/__init__.py:78-84`); the CHANGELOG re-run callout (`:58`) is the standard, sufficient channel; `decision-audit.sh` is the *only* asset encoding the un-guarded FM1; CLAUDE_BLOCK carries only the (correct, unchanged) §3.6 rule. M1's mechanical fix is sufficient.

**Honest post-plan readiness: v0.4.1 finalize ≈ 93% (≈ 88% if §2.3/§3.2/§4.5 additions are NOT folded — stale ADR + undefined P1 test matrix + non-reproducible gate); v1.0-OSS ≈ 70%** (gated on C10 PyPI/release + the boundary CI wiring + real tags). No CRITICAL/HIGH adoption finding was dropped; the one substantive un-closed item is ADR-002 D1:32. "No version bump" IS defensible to downstream PROV/schema consumers — *because* the break is namespaced + triply-documented and P7 makes the diff actually reachable.

---

### Provenance of this review
All "VERIFIED" claims executed read-only: `git log/tag -l/for-each-ref` (no state-changing git), `grep`, `sed -n` view, `find`, `cat` of installer assets. No file written except this one. No directory moved/deleted. No process signalled (`kill`/`pkill` never invoked). No `trace_*`/MCP tool called. Nothing outside the repo touched. `git status` working tree unchanged by this review (only the pre-existing untracked `notes/`, `docs/*.png`, `review_2026-05-18_v041_status/`).
