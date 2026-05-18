# Round 1 — Reviewer C: Adoption Surface & Core/Extension Boundary Audit

**Date:** 2026-05-18
**Reviewer role:** Independent adversarial adoption / downstream / architecture-boundary reviewer
**Scope:** v0.4.1 PR (`7c9e474..d20be80`, merged as PR #6), core/extension boundary, Round-3 Layer 11 adoption surface, honest readiness.
**Method:** Static analysis + read-only git/grep + one definitive runtime experiment (learn/ physically moved out and restored, byte-identical). Did NOT re-run the full suite (live-session load constraint). One narrow non-session-end test class run for VERIFIED evidence.

---

## 1. Verdict

**v0.4.1 is NOT finalizable as-is. Honest readiness: ~80%** (the *plan* reached ~95% per Round 3; the *merged implementation* is materially below that). The core/extension boundary is **architecturally sound but currently VIOLATED in `main`**: `tools/query_tools.py` hard-imports `trace_mcp.extensions.learn.store` at two sites, one of them (`_compute_knowledge_metrics`) **unguarded and unconditionally invoked** by `project_summary` — so deleting `extensions/learn/` does NOT leave a fully working 18-tool core (`trace_project_summary` raises `ModuleNotFoundError`). VERIFIED by experiment. The documented boundary invariant (CONTRIBUTING.md:75) is real but is contradicted by the code it governs. Separately, the single most important test the fix plan defined ("invalid by construction" without it — L9.1 waggle regression) is **MISSING**, and the Round-3 A1 multi-actor guard amendment was **NOT implemented** (the code fires on bare instance equality with no multi-actor gate, exactly the false-positive A1 was written to prevent). The "~95% ready / do not run Round 4 / remaining 5% is implementation discovery" framing is defensible *for the plan* but has been misread as project readiness; a real session-end regression (owned by others) plus these gaps prove the implementation was never gated on a green suite. The Layer-11 scaffolding (version lines, schema-URL cascade, CLAUDE_BLOCK, decision-audit.sh bash-3.2 + FM1 generalization, namespace policy, ADR content) is genuinely well-executed — the failures are in *behavioral correctness* and *release process*, not adoption scaffolding.

---

## 2. Severity-Ranked Findings

| # | Severity | Finding | file:line | Evidence | Recommended remediation (describe only) |
|---|----------|---------|-----------|----------|------------------------------------------|
| C1 | **CRITICAL** | Core→extension boundary violated. `_compute_knowledge_metrics` hard-imports `trace_mcp.extensions.learn.store` with **no try/except** and is called **unconditionally** by `project_summary`. Deleting `extensions/learn/` breaks core tool `trace_project_summary`. | `src/trace_mcp/tools/query_tools.py:158` (import) + `:320` (unconditional call) | VERIFIED (ran: physically `mv src/trace_mcp/extensions/learn /tmp; PYTHONPATH=src python -c "query_tools._compute_knowledge_metrics('trace-mcp')"` → `ModuleNotFoundError: No module named 'trace_mcp.extensions.learn'`; learn/ restored byte-identical, `diff -rq` clean, `git status` clean) | Route knowledge metrics through `extension_hooks.py` (a `register_metrics_hook`) so core never imports the extension; OR wrap in try/except returning the empty-metrics dict on `ImportError` (matching the pattern `health_check` already uses at `:339-344`). The hook route is the correct one given CONTRIBUTING.md:75. |
| C2 | **CRITICAL** | L9.1 — the canonical waggle regression test — is MISSING. The FINAL plan calls it "the canonical regression scenario… If v0.4.1 ships without these warnings firing on the original audit subject, the fix is invalid by construction." | session JSON present at `audit_2026-05-13_waggle_session/trace_session_trace_20260513_446733.json`; **no test references it** | VERIFIED (ran: `grep -rl 446733 tests/` → 0 files; the 5 `tests/test_v041_*.py` files contain no `446733`) | Add the L9.1 regression test: load the waggle session JSON under the v0.4.1 schema; assert schema validates, and the documented audit counts (missing-snippet contribution/correction, attribution_warning ≥2 for evt_001+evt_025, orphan-discovery ≥1) fire. This is the release's own correctness contract. |
| C3 | **HIGH** | Round-3 A1 multi-actor guard NOT implemented. Both the log-time FM1 (`decision_tools.py`) and the session-end structural detector (`session_tools.py`) fire on bare `proposed_by==resolved_by` instance equality with **no ≥2-distinct-actor-types gate**. A1 explicitly required this guard "without false-firing on single-actor or multi-AI sessions" and "Same scoping applies to L5.4: identical condition." Warning text says "in multi-actor workflows" but code never checks. Will false-positive on solo-human and system→system production sessions (A1 cites real data `~/.trace/sessions/trace_20260320_205356.json`). | `src/trace_mcp/tools/decision_tools.py:80-99` (no participant/actor-count guard); `src/trace_mcp/tools/session_tools.py:330-334` (no guard) | VERIFIED (ran: `grep -nE "participant\|distinct.actor\|unique.actor\|>= 2\|multi.actor\|actor.types" src/trace_mcp/tools/decision_tools.py` → only the literal warning *string* at :97, no logic) | Add the A1-specified gate: count distinct actor types across `metadata.participants` and event actors (with the A7 fallback `{e.actor.type for e in session.events}` when participants empty); fire FM1/FM25/L5.4 only when ≥2. The implementation followed the weaker superseded FINAL L9.4, not the Round-3 A1 amendment that replaced it. |
| C4 | **HIGH** | Test suite passes for the same-actor warning but **does not cover** A1's required cases (system→system single-actor = no warning; Claude/GPT different-instance = no warning). Passing tests mask the unimplemented C3 guard. | `tests/test_decision_guards.py::TestSameActorWarning` (7 tests, all pass); A1's `system→system` "no warning" test absent | VERIFIED (ran: `uv run pytest tests/test_decision_guards.py::TestSameActorWarning -q` → `7 passed`; `grep -rn "system.*system\|test_system" tests/test_decision_guards.py tests/test_v041_attribution_audit.py` → no single-actor "no warning" test) | Add A1's missing test cases (system→system: no warning; same-human-id: warning; Claude-id vs GPT-id: no warning). These cannot pass until C3 is fixed — which is the point: they would have caught C3. |
| C5 | **HIGH** | CHANGELOG header still `## [0.4.1] — In progress`; bottom compare-links reference git tags (`v0.4.0`, `v0.3.0`, `v0.2.0`, `v0.1.0`, `releases/tag/v0.1.0`) that **do not exist** — the repo has ZERO tags ever. Every "What's new" link target is dead. | `CHANGELOG.md:10` (header), `CHANGELOG.md:172-176` (compare links) | VERIFIED (ran: `git tag -l \| wc -l` → 0; `git for-each-ref refs/tags \| wc -l` → 0) | Before finalize: change header to `## [0.4.1] — 2026-MM-DD`; create annotated git tags `v0.1.0..v0.4.1` at the corresponding commits (or rewrite compare-links to commit SHAs / a "no releases yet" note). Shipping an OSS release whose own changelog links 404 erodes the trust the audit set out to protect. |
| C6 | **MEDIUM** | ADR 002 exists and is comprehensive, but is **NOT listed in the ADR index** (`docs/adr/README.md` table still shows only 001). Discoverability of the v0.4.1 design rationale — the entire point of L11.6 — is degraded. | `docs/adr/README.md` (table: only row 001); `docs/adr/002-v041-protocol-additions.md` (exists, complete) | VERIFIED (ran: `grep -c "002" docs/adr/README.md` → 0) | Add the ADR 002 row to the index table. One-line fix; pure scaffolding miss that the L11.6 item should have caught. |
| C7 | **MEDIUM** | CONTRIBUTING.md boundary rule under-enumerates "Core". It names `server.py, schema/, storage/, tools/` but omits `exporters/` and `scratchpad.py` (both in the governance-constraint core set). The written invariant is narrower than the policy it documents. | `CONTRIBUTING.md:75` | VERIFIED (read) | Expand to "Core (`server.py`, `schema/`, `storage/`, `tools/`, `exporters/`, `scratchpad.py`)". Also promote this invariant into an ADR or spec §, not just CONTRIBUTING — see boundary deep-dive §3. |
| C8 | **MEDIUM** | No written governance statement that Tier 3 (RL feedback loop + cross-project global knowledge) MUST remain an optional extension. CONTRIBUTING.md:106-109 lists Tier 3 under "Development roadmap" with zero scope-boundary language. The user's hard policy is undocumented in the repo. | `CONTRIBUTING.md:106-109` | VERIFIED (read) | Add an explicit clause (in ADR 002, a new ADR, or spec) stating Tier 3 adaptive learning lives only in `extensions/learn/` and core must retain zero dependency on it; deleting the extension must leave a working 18-tool provenance system. Without this, C1-style creep is unchecked. |
| C9 | **LOW** | decision-audit.sh hook fires the v0.4.1 same-instance warning without the multi-actor gate (consistent with, and inheriting, C3). Hook is otherwise correct & bash-3.2-safe. | `src/trace_mcp/adapters/claude_code/assets/hooks/decision-audit.sh:69` | VERIFIED (read; `grep -cE "mapfile\|readarray"` hit is only the explanatory *comment* at line 23; actual parse uses `read -r <<<` at :93) | After C3 is fixed server-side, mirror the actor-count gate into the hook's Python block, or (cleaner) refactor the hook to read the server-persisted AttributionAudit instead of recomputing — L11.5 itself suggested this option. |
| C10 | **LOW** | Distribution is `uvx --from <local-path> --refresh-package` only. No PyPI package, no wheel artifact, no `gh release`. CHANGELOG/README present as an OSS release but there is no installable release for anyone without the local checkout. | `.mcp.json`; CHANGELOG `[0.1.0]` link → `releases/tag/v0.1.0` (nonexistent) | VERIFIED (read `.mcp.json`; tag check as C5) | For v1.0-OSS: publish to PyPI (or attach wheels to a GitHub Release), and switch the documented install path off a machine-local absolute path. Not a v0.4.1 finalize blocker, but a v1.0 blocker. |

---

## 3. Core/Extension Boundary — Deep Dive

### 3.1 The intended architecture (sound)

The decoupling mechanism is **`src/trace_mcp/extension_hooks.py`** — a zero-dependency registry. Core calls `recall_if_available` / `extract_if_available`, which return empty/no-op when no hook is registered (fail-open, lines 74-80, 109-116). The `learn` extension registers via `register(mcp, storage)` discovered by `pkgutil.iter_modules` in `server.py:_load_extensions` (`server.py:715-732`). `extension_hooks.py:7` even documents "zero imports from trace_mcp tools or extensions." This is a clean, correct inversion-of-control design. CONTRIBUTING.md:75 documents the invariant.

### 3.2 Every coupling point (grepped: `src/trace_mcp/{schema,storage,tools,exporters}/`, `server.py`, `scratchpad.py`)

| Coupling | Location | Nature | Clean? |
|---|---|---|---|
| Extension discovery | `server.py:715-732` (`_load_extensions`, `pkgutil.iter_modules`) | The *intended* single coupling — generic, names no extension | ✅ Clean |
| Hook registry | `extension_hooks.py` (whole file); called from `server.py` session start/end + `decision_tools` indirectly | IoC: core never imports extension; extension pushes callbacks in | ✅ Clean |
| `extension_hooks.py:103` "learn" | docstring/comment only | Cosmetic naming | ✅ Clean (text) |
| **`query_tools.py:158`** | `from trace_mcp.extensions.learn.store import load_store` inside `_compute_knowledge_metrics`, **no guard**, called unconditionally by `project_summary` (`:320`) | **Hard import of a named extension from core** | ❌ **VIOLATION (C1)** |
| `query_tools.py:340` | `from trace_mcp.extensions.learn.store import _get_directory` inside `health_check`, **wrapped in try/except Exception** with a fallback (`:343-344`) | Hard import of named extension, but degrades gracefully | ⚠️ Tolerated but still a named-extension import from core (should also route through a hook) |

`schema/`, `storage/`, `exporters/`, `scratchpad.py`: **no** `extensions`/`learn`/`trace_learn` imports (grep clean). `scratchpad.py:8` references "trace-learn" in a comment only. `prov_mapping.py` / `schema` learning-concept grep: only the word "knowledge" in unrelated PROV context — no weights/decay/RL leaked into core schema or spec. The v0.4.1 `discovery` annotation category was added to *core* `schema/events.py` (correct — it is a provenance concept) and *also* to `extensions/learn/models.py:LearningCategory` (correct — superset, extension-side). No learning concept leaked into core.

### 3.3 Delete-the-extension thought experiment (RUN, not reasoned)

- All core *modules* import fine with `extensions.learn` absent (the two bad imports are function-local, so import-time is unaffected). VERIFIED.
- **At runtime, `trace_project_summary` → `project_summary` → `_compute_knowledge_metrics` → `ModuleNotFoundError`.** VERIFIED by physically moving `src/trace_mcp/extensions/learn` out and calling the function (then restoring byte-identical; `diff -rq` clean; `git status` clean).
- `trace_health_check` survives (try/except fallback). VERIFIED by source inspection of `query_tools.py:339-344`.

**Conclusion:** the claim "deleting `extensions/learn/` leaves a fully functional 18-tool provenance system" is **FALSE in `main`**. 17 of 18 tools survive; `trace_project_summary` is broken. The architecture *can* satisfy the governance constraint (the hook pattern is right there), but the merged code does not. This is a governance violation by the project's own CONTRIBUTING.md:75 rule.

### 3.4 Scope-creep risk (Tier 3)

CONTRIBUTING.md:106-109 frames Tier 3 (RL-like weight boost/demote feedback loop; cross-project global knowledge store) as "future" under a generic roadmap with **no statement that it must stay extension-scoped**. The C1 violation is the canary: a *Tier-2* feature (knowledge metrics) already leaked a hard import into core `tools/`. With no written governance fence and an existing precedent of leakage, Tier 3 (a heavier, statefully-coupled feature) is at material risk of importing weight/decay state into core query/scratchpad surfaces. **The boundary documentation that exists (CONTRIBUTING.md:75) is (a) under-scoped (C7), (b) located in a contributor doc rather than an ADR/spec, and (c) already contradicted by shipped code.**

### 3.5 Missing boundary documentation — recommendation

There is **no ADR and no spec section** asserting the core/extension boundary or the Tier-3-stays-optional policy. CONTRIBUTING.md:75 is the only home and is incomplete. **Recommended:** author a dedicated ADR (e.g., `docs/adr/003-core-extension-boundary.md`) that (1) enumerates the full core set (`server.py`, `schema/`, `storage/`, `tools/`, `exporters/`, `scratchpad.py`), (2) states core MUST have zero import dependency on `extensions/`, integration only via `extension_hooks.py`, (3) states adaptive learning / Tier 3 (RL feedback, cross-project knowledge) MUST remain in `extensions/learn/`, (4) defines the acceptance test "deleting `extensions/learn/` leaves a working 18-tool system" as a CI-enforceable invariant, and (5) cites C1 as the motivating violation. Mirror a one-paragraph normative statement into `docs/specification.md`.

---

## 4. Adoption-Surface Matrix (Round-3 Layer 11)

| Item | Status | file:line | Evidence |
|---|---|---|---|
| **L11.1** README version line + schema URL | ✅ DONE | `README.md:23` (`**Version:** 0.4.1`), `:25` + `:254` (`schemas/trace-v0.4.json`) | VERIFIED (grep). Note schema *namespace* URI deliberately kept `…/v0.3` per ADR D6 (documented, consistent). |
| **L11.2** root CLAUDE.md version | ✅ DONE | `CLAUDE.md:5` (`> **Version**: 0.4.1`) | VERIFIED (grep) |
| **L11.3** CONTRIBUTING schema-regen filename | ✅ DONE | `CONTRIBUTING.md:69` (`trace-v0.4.json`) | VERIFIED (grep) |
| **L11.4** CLAUDE_BLOCK.md mirrors §3.6 / §3.7.1 / §3.4.1 | ✅ DONE | `assets/CLAUDE_BLOCK.md:21` (Proposer Identity §3.6), `:27-28` (URI-form §3.7.1), `:35-40` (snippet §3.4.1 + absence markers), `:31-32` (discovery), `:41` (subagent dispatch) | VERIFIED (grep) — all new normative rules present |
| **L11.5** decision-audit.sh generalized FM1 + bash-3.2 | ✅ DONE | `assets/hooks/decision-audit.sh:69` (full `(type,id)` equality, not ai-only), `:93` (`read -r <<<`, no mapfile), `:98` (`NON_AI_SELF` derived) | VERIFIED (read). Generalization genuine; bash-3.2-safe (mapfile only in a comment, :23). **Caveat:** no multi-actor gate (C9, inherits C3). |
| **L11.6** ADR 002 covers the 4 topics | ⚠️ **PARTIAL** | `docs/adr/002-v041-protocol-additions.md` D1 (Proposer Identity), D2 (URI corrects_event_ids), D3 (PROV split), D4 (single-source trace_version) — all present and thorough | VERIFIED (read). **Gap:** ADR is NOT in `docs/adr/README.md` index (C6) — discoverability, the item's own purpose, is broken. |
| **L11.7** PROV namespace policy decided & consistent | ✅ DONE | `prov_mapping.py:32` (`ns/v0.3#`), `docs/specification.md:8` & `:459` (`ns/v0.3#`), ADR D6 documents the keep-at-v0.3 decision; zero stray `ns/v0.4` | VERIFIED (ran: `grep -rnE "ns/v0.4" src/ docs/ README.md schemas/` → none) — fully consistent |
| **L11.8** schema `$id`+URL cascade everywhere | ✅ DONE | `scripts/generate_schema.py:19,20,28`; `scripts/validate_session.py:20`; `schemas/trace-v0.4.json:778` (`$id`), `:705`,`:708`; `schema/session.py:58` (`context` kept v0.3 per D6); `prov_mapping.py:32` (ns kept v0.3 per D6); `README.md:25,254`; `CONTRIBUTING.md:69`; `docs/specification.md:7,472`; `tests/test_specification_conformance.py:42,1011,1169` | VERIFIED (grep all 9 sites) — every reference consistent; the intentionally-retained v0.3 strings (`context`, PROV ns) are documented in ADR D6. No stale/missing/inconsistent reference found. |
| **L11.9** CHANGELOG migration callouts | ✅ DONE | `CHANGELOG.md:56-59` — (a) PROV-consumer `wasRevisionOf`→`wasInvalidatedBy`/`wasInfluencedBy`, (b) re-run `trace-mcp-init`, (c) pinned-Pydantic `extra="ignore"` — all three present | VERIFIED (read) |
| **L11.10** (optional, defer-to-v1.1) `--upgrade` flag | ⏸ DEFERRED (as designed) | not implemented | INFERRED — Round 3 explicitly marked optional/deferrable; not a finding |

**Layer-11 adoption scaffolding is ~9/9 substantively done** (L11.6 has the index-omission defect C6; L11.10 legitimately deferred). The Layer-11 work is the *strongest* part of the release. The release's weaknesses are in **behavioral correctness (C1–C4)** and **release process (C5, C10)** — i.e., the parts Round 3 explicitly punted to "implementation discovery."

---

## 5. Honest Completeness Assessment

### 5.1 Deconstructing the "~95% ready / do not run Round 4" claim

The Round-3 doc is precise and self-aware. Its 95% applies to **the plan**, with the remaining 5% explicitly labeled "implementation discovery… the implementation work itself will surface any remaining issues via failing tests." That reasoning is sound *only if a green suite actually gates the merge.* It did not:

- `audit_methodology.md:5` confirms the audit ran **concurrently with implementation** as a vs-JSONL comparison — never a green-suite gate.
- The PR (`7c9e474..d20be80`) merged with a real session-end regression (test_fm7 / `trace_end_session` hang — owned by others) that "3 rounds of verification" did not catch, **because none of the three rounds ran the suite** (they verified the *plan* by reading, not the *code* by executing).
- The release's own designated "invalid by construction" gate — L9.1 waggle regression — was **never written** (C2).
- A Round-3 amendment (A1 multi-actor guard) was **not implemented**; its tests were written to the weaker superseded spec and pass, masking the gap (C3/C4).

**The "implementation discovery" hand-wave is the exact failure surface that swallowed the regression and C1–C4.** "Do not run Round 4" was reasonable advice for *plan iteration*; it has been (mis)operationalized as "do not verify the implementation," which is how a non-functional `trace_project_summary` and an unimplemented amendment reached `main`.

### 5.2 True readiness: ~80%

- Schema/spec/scaffolding/Layer-11: **~95%** (genuinely strong; C6/C7 minor).
- Behavioral correctness: **~70%** (C1 breaks a core tool when extension absent; C3 unimplemented amendment will false-positive on real production sessions; C2 canonical regression test absent; plus the externally-owned session-end hang).
- Release process: **~50%** (zero tags, dead changelog links, "In progress" header, no PyPI).
- Weighted, for a *defensible OSS release*: **~80%**. The "95%" narrative overstates by ~15 points and, more importantly, mislocates the remaining work as "discovery" when it is concrete, enumerable, and partly already-known.

### 5.3 Genuine remaining "necessary steps"

**To finalize a defensible v0.4.1 (blockers):**
1. **C1** — remove the unguarded core→`extensions.learn` import in `query_tools.py` (route via `extension_hooks.py`; also fix the `health_check` one for consistency). Re-run the delete-the-extension experiment as proof.
2. **C2** — write the L9.1 waggle regression test (the release's own correctness contract).
3. **C3** — implement the Round-3 A1 multi-actor guard in *both* `decision_tools.py` (FM1/FM25) and `session_tools.py` (L5.4), with the A7 empty-participants fallback.
4. **C4** — add A1's missing test cases (system→system no-warning; Claude/GPT different-instance no-warning); confirm they pass only after C3.
5. **(externally owned)** resolve the test_fm7 / `trace_end_session` session-end hang.
6. **Run the full suite green** and record the result — the release-process contract the three rounds asserted but never executed.
7. **C5** — flip CHANGELOG header to a date; create the `v0.1.0..v0.4.1` git tags (or fix compare-links); then the changelog's own links resolve.
8. **C6** — add ADR 002 to the ADR index.

**Additionally toward a v1.0 OSS release:**
9. **C8** — author the core/extension-boundary + Tier-3-stays-optional ADR (§3.5); add a CI test enforcing "delete `extensions/learn/` ⇒ 18 tools still work."
10. **C7** — correct the CONTRIBUTING.md core enumeration; mirror the boundary statement into the spec.
11. **C10** — publish to PyPI / attach release wheels; switch documented install off a machine-local absolute path; cut a real `gh release` per tag.
12. Address the audit's own pre-v1.0 list still open beyond v0.4.1 scope (audit_findings §"Pre-release fixes"): the items are largely covered, but verify enforcement empirically against the waggle subject (folds into C2).

---

### Provenance of this review
- Findings marked **VERIFIED (ran: …)** were executed read-only. The one mutating experiment (move `extensions/learn` out, run, restore) was reverted to a byte-identical state (`diff -rq` clean, `git status --porcelain` clean) — no repository state changed. No `trace_*`/MCP tools called. No processes signaled. Exactly this one file written.
