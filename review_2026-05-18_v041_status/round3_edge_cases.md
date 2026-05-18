# Round 3 — Edge-Case Stress of the FINAL Remediation Plan (P1–P9)

**Date:** 2026-05-18
**Role:** Independent Round-3 EDGE-CASES verifier — last verification before implementation. No prior context trusted; every behavior re-derived from source + read-only in-process probes against the project `.venv` (`PYTHONPATH=src`, in-memory storage shim, no repo file modified except this one; no `trace_*`/MCP calls; no process signalled; no directory moved).
**Target:** `review_2026-05-18_v041_status/round_FINAL_plan.md` P1–P9, stressed against the real merged code on `main` (`d20be80`).

---

## 1. Verdict

**The FINAL plan's fixes are MOSTLY edge-case-robust, but P1 and P9(b) each have a concrete under-specification that would let a real edge case through if implemented literally. Confidence: ~88%.**

- **P1 guard semantics** (≥2 actor TYPES, union of participants ∪ event-actors, fallback to event-actors): edge-case-CORRECT for every A1-mandated case I enumerated, **including the §3-critical ai→ai single-actor carve-out** — *provided the implementer scopes exactly as the FINAL plan says*. The "types not instances" choice (M3, `evt_016`) is **outcome-determining and resolved correctly**: it is the only reason three existing `test_v041_attribution_audit.py` tests survive P1. **One real GAP:** the plan's FM25 scoping is under-specified (FM25 at `decision_tools.py:104` is a single block with no ai/non-ai split; the plan says "gate the non-ai branch (FM1 ~:80-83,104; FM25)" but FM25 has no branch to scope) → ai→ai single-actor *fast* self-resolution loses its FM25 signal with **no test catching it** (existing FM25 tests pass only via an `OR "AI resolved"` clause that the ungated FM1 ai-branch still satisfies).
- **P2 absence simulation** is RELIABLE for the invariant P2 actually asserts (18 core tools with `extensions/learn` absent) — verified the meta-path block faithfully reproduces the `query_tools.py:158` break. Caveat: it does **not** reproduce real on-disk absence for `_load_extensions()` (pkgutil still lists the dir) — but that path is irrelevant to core-tool functionality, so the simulation is adequate. State the limitation so no one over-claims.
- **P3 L9.1**: all four asserted counts **VERIFIED exact** against the frozen fixture (28 events / 15 / 1 / 2). Stable, **not fixture-fragile** — the fixture is a read-only frozen JSON; a "benign schema change" cannot flip it. `attribution_warning_count=2` survives P1 because the waggle session has 2 actor types under the union (`ai`,`human`). One brittleness note below.
- **P4 ↔ P1**: **SAFE.** P4's deletion of the orphan-discovery duplicate ⚠️ (`:378-383`) does not perturb P1's `attribution_warning` render (independent own-block at `:162-167`) nor the render-order test's attribution-vs-missing-snippet ordering. No cross-fix invalidation.
- **P9(b) lockfile**: **GAP — wrong critical-section scope.** The plan cites `store.py:75-106` (`save_store` only) as the lock site. The read-modify-write **span** is in `extensions/learn/__init__.py` (`load_store` at `:204/:257/:274/:301` → mutate → `save_store` at `:232/:278/:323`). Locking only inside `save_store` does **not** prevent the lost-update — the unlocked `load_store` read already raced. Plus: lock granularity (per-project vs whole-dir) unspecified → whole-dir lock starves 15 projects; NFS (`~/.trace` on a network FS) unaddressed; `filelock` is an undeclared transitive dep. **P9(c)**: torn-`.npy` window is real but the `load_embeddings_cache` size-guard (`store.py:286`) already degrades it to a perf hit, not a correctness bug — plan should note this.

None of these change the prior verdict (not release-ready). They are plan-tightening items, mostly in P1 and P9(b).

---

## 2. Per-fix edge-case table

### P1 — actor-type counting + ai→ai carve-out

Probed current (pre-fix) behavior, then simulated the FINAL guard semantics ("≥2 actor TYPES across `metadata.participants` ∪ event-actors; fallback event-actors; gate non-ai general branch + L5.4 push only; ai-branch + ai-only `self_resolved_ids` ungated").

| Edge input | Pre-fix actual (VERIFIED probe) | FINAL guard predicted | A1 mandate | Verdict |
|---|---|---|---|---|
| `system→system`, `participants=[]`, single event-actor type | FM1 warns + FM25 warns + `attribution_warning_count==1` | types=`{system}`, multi=False → FM1 no-warn, FM25 no-warn, attr=0 | **0 warnings** (A1:45) | **ROBUST** |
| solo-human `human→human`, no participants | FM1 warns + FM25 warns + attr==1 | types=`{human}`, multi=False → no-warn, attr=0 | 0 (A1:46 wants warn *only in multi-actor*) | **ROBUST** |
| **`ai→ai` single-actor, no participants** | FM1 "AI resolved its own proposal" + FM25 + attr==1 + `self_resolution_count==1` | ai-branch UNGATED → FM1 **still warns**; `self_resolution_count==1` preserved; general `attribution_warning_count`→**0** | **MUST still warn** (A1:47, §3 verified-solid) | **ROBUST for FM1** / **GAP at session-end & FM25** (see below) |
| `human→human`, participants `[human, ai]` (true evt_025) | FM1 warns + attr==1 | types=`{human,ai}`, multi=True → FM1 warns, attr=1 | warn (A1:46) | **ROBUST** |
| `ai→ai` in 2-actor-type session | FM1 ai-msg + attr==1 + `self_resolution_count==1` | ai-branch ungated → warns; attr=1 (multi) | warn | **ROBUST** |
| actor-ID drift: participants `ai/claude` vs events `ai/ai-assistant` (waggle) | — | **types-count unaffected** (both are type `ai`); union types `{ai,human}` → multi=True | — | **ROBUST** — drift is harmless under TYPE counting (the M3 decision's whole point); confirmed against the real waggle JSON |
| one side's actor absent from participants (proposer in participants, resolver only an event-actor) | — | union includes event-actors → still counted | — | **ROBUST** (union, not participants-only) |
| `proposed_by`/`resolved_by` differ by id only, same type, single-type session | no warn (id≠) | no warn (id≠, and multi=False anyway) | no warn | **ROBUST** |

**GAP 1 (P1, MEDIUM — session-end ai→ai asymmetry, no test):** Post-P1, an `ai→ai` **single-actor** session has `self_resolution_count==1` (ai-only, ungated — correct, §3) but generalized `attribution_warning_count==0` (general L5.4 gated on multi-actor). This asymmetry is correct by design, **but the FINAL plan's P1 test list has no session-end test asserting it.** `test_v041_attribution_audit.py::test_ai_self_resolution_counts_in_both_metrics` asserts *both ==1*, but it uses the **2-participant-type** helper → both stay 1 → it does **not** exercise the single-actor ai→ai session-end case. Recommended: add to P1's test list a session-end test — *ai→ai single-actor session ⇒ `self_resolution_count==1` AND `attribution_warning_count==0`* (regression-guards the carve-out at the session-end layer, not just the decision-time layer).

**GAP 2 (P1, MEDIUM — FM25 scoping under-specified, regression invisible):** `decision_tools.py:104` (FM25) is `if elapsed < 5.0 and is_self_resolution and not suppress:` — a **single block with no ai/non-ai split**. The FINAL plan says "add the multi-actor guard ONLY to the generalized non-ai same-instance branch … (FM1 ~:80-83,104; FM25)". This names `:104` but there is no "non-ai branch" there to scope. Two implementer interpretations diverge:
- (a) gate FM25 wholesale on multi-actor → **ai→ai single-actor fast self-resolution silently loses its FM25 "self-resolved in 0.0s" warning** (a v0.3-era behavior).
- (b) add an ai/non-ai split to FM25 mirroring FM1 (does not exist today) → only the non-ai FM25 gated.

**Neither existing FM25 test catches a wrong choice**: `test_decision_guards.py:832` and `test_failure_modes_e2e.py:542` are ai→ai single-actor and assert `"self-resolved" in result OR "AI resolved" in result` — the **ungated FM1 ai-branch still emits "AI resolved its own proposal"**, so the `OR` clause passes even if FM25 is fully gated off. Recommended plan tightening: P1 must **explicitly state the FM25 disposition** (recommend interpretation (b): mirror FM1's ai/non-ai split so ai→ai keeps FM25 unconditionally, consistent with §3 and A1:48 "generalize FM25 to match"), and add a test asserting the literal `"self-resolved in"` string for ai→ai single-actor so a wrong choice fails loudly.

**A1-mandated cases in the FINAL test list — coverage check:** the FINAL plan's P1 test list = `_clean` restore (single-actor human→human no-warn), hook single-actor no-warn, single-actor `system→system` no-warn, **`ai→ai` single-actor STILL warns** (§3 regression guard), ≥2-actor-type same-non-ai-id warns. This covers A1:43–47 at the **decision-time** layer. **Omitted from the FINAL test list:** (i) the **session-end** ai→ai single-actor asymmetry (GAP 1); (ii) an explicit **FM25** ai→ai single-actor literal-string assertion (GAP 2); (iii) A1:46's *different-instance human→human in a multi-actor session ⇒ no warn* is covered by the surviving `test_human_different_instance_self_resolves_clean` (single-actor) — but that test is single-actor, so post-P1 it passes via *both* id-inequality *and* multi=False; the plan should add a **multi-actor** different-human-instance no-warn case so the id-inequality path is tested independently of the multi-actor gate (otherwise a guard bug that suppresses everything would still pass it).

### P2 — non-destructive absence simulation reliability

| Concern | Finding | Verdict |
|---|---|---|
| Does a `sys.modules`/meta-path block reliably simulate "extensions/learn absent"? | **VERIFIED**: a `MetaPathFinder` raising `ModuleNotFoundError` for `trace_mcp.extensions.learn[.*]` (after purging already-imported submodules) reproduces the `query_tools.py:158` break exactly — `project_summary` → `ModuleNotFoundError` pre-fix; `health_check` survives via its guarded `:340`. | **ROBUST for P2's stated invariant** |
| Could the test pass while real absence still breaks (false green)? | The meta-path block does **NOT** reproduce real on-disk absence for `_load_extensions()`: `pkgutil.iter_modules(ext_pkg.__path__)` **still lists `learn`** (dir on disk), so `_load_extensions` calls `import_module("…learn")`, the Blocker raises, and server.py:731's broad `except Exception` swallows it. So the *server-startup* code path exercised differs from true absence. **However**, the 18 core tools never go through `_load_extensions` (it only registers the 5 trace-learn tools); the *only* core→ext coupling is the two `query_tools` imports, both faithfully reproduced. So P2's invariant ("18 core tools function with learn absent") is correctly tested. | **ROBUST**, with a documentation caveat |
| Robust form | Purge `sys.modules['trace_mcp.extensions.learn*']` **before** installing the finder, install a `MetaPathFinder` (not just a `sys.modules[...]=None`, which only blocks already-attempted imports), and assert each of the 18 core tools. Optionally also assert `_load_extensions()` logs-and-continues (does not raise) under the block, to cover the startup path explicitly. | recommended tightening |

**No GAP** for P2's scope. Recommended plan note: the test asserts *core-tool* functionality, not *server-startup-with-no-learn-dir*; if the latter is also desired, add the `_load_extensions()` no-raise assertion (cheap).

### P3 — L9.1 asserted counts: stability

| Asserted (FINAL L9.1) | Actual (VERIFIED via `_build_attribution_audit` on the real fixture) | Stable? |
|---|---|---|
| 28 events | 28 | **Yes** — frozen read-only fixture |
| 15 missing-snippet contributions | `missing_snippet_contribution_count == 15` | **Yes** — but see brittleness note |
| 1 missing-snippet correction | `missing_snippet_correction_count == 1` | **Yes** |
| 2 same-instance self-resolutions | `attribution_warning_count == 2` (`evt_001`, `evt_025`); `self_resolution_count == 0` | **Yes, survives P1** — waggle union actor types = `{ai,human}` → multi=True → guard passes |

**Verdict: ROBUST.** The counts are not fixture-fragile in the sense the mission worried about: the fixture is a frozen JSON the plan loads read-only, so no "benign schema/format change" in the codebase can flip them. Asserting exact counts here is an **appropriate** gate, not brittle — it is a golden-file regression on a real production artifact (exactly G3's intent). **One brittleness note (LOW):** `missing_snippet_*` counts key off `conversation_snippet is None`. They are robust against code changes, but the assertion is coupled to *this specific fixture's* snippet population. P3 should assert these as **exact equality with an inline comment citing the fixture line-derivation** (so a future fixture edit fails loudly and intentionally), not as `>=`. The plan already specifies exact counts — keep it that way; do **not** soften to `>=` (Round-2-correctness §5 noted the pre-A1 worry; confirmed moot — counts are A1-invariant for this fixture). Also confirm P3 is sequenced **after** P1 (Round-2 already flagged) so it asserts post-guard behavior; verified the post-guard value is still exactly 2, so the assertion text need not change.

### P4 — duplicate-⚠️ deletion vs P1 render order

| Concern | Finding | Verdict |
|---|---|---|
| Does deleting `:378-383` (orphan-discovery dup ⚠️) affect P1's session-end detector output? | No. `attribution_warning_count` is computed independently (`attribution_warning_ids` at `:330-334`) and rendered via its **own block** (`:162-167`). The `:378-383` append only adds the *orphan-discovery* line to the trailing `self.warnings` ⚠️ block. | **SAFE** |
| Does it change render order? | Render order (`render()`): unresolved → self_resolution → **attribution_warning (own block)** → unlinked_correction → orphan_discovery (own block `:175-182`) → missing_snippet → explicit_absence → trailing `self.warnings` ⚠️ block. Deleting `:378-383` removes only the orphan entry from the **trailing** ⚠️ block; the attribution-vs-missing-snippet ordering that `test_attribution_warning_before_missing_snippet_in_render` checks is unaffected. | **SAFE** |
| Cross-fix consistency observation (not a defect) | `attribution_warning` is **also** double-rendered (own block `:162-167` + pushed to `audit_warnings` at `:367-372` → trailing ⚠️ at `:205-207`). P4 treats orphan-discovery's identical double-render as a bug to delete but leaves attribution_warning's as a feature. Defensible (the ⚠️ block is the actionable-summary), but the plan's "remove the duplicate" rationale is selectively applied — worth one sentence in P4 so the implementer doesn't also delete `:367-372` (which would regress the actionable-summary and the render-order test). | note only |

**No GAP.** Recommended: P4 should explicitly say *"delete only the orphan-discovery `audit_warnings.append` at `:378-383`; do NOT touch the attribution_warning `audit_warnings.append` at `:367-372` — its double-render is intentional and the render-order test depends on it."*

### P9(b)/(c) — lockfile + .npy atomicity

| Concern | Finding | Verdict |
|---|---|---|
| **Critical-section scope** | **GAP (HIGH).** Plan cites `store.py:75-106` = `save_store` only. The read-modify-write **span** is `extensions/learn/__init__.py`: `load_store()` (`:204/:257/:274/:301`) → in-memory mutate → `save_store()` (`:232/:278/:323`), as **separate unlocked calls**. A lock confined to `save_store` does **not** prevent lost-update — process B's `load_store` already read the stale store before any in-`save_store` lock. Classic A-reads/B-reads/A-saves/B-saves clobber. **The lock MUST wrap the entire load→mutate→save span in `__init__.py`, per call site.** | **GAP** — plan would not fix the bug as written |
| Lock granularity | **GAP (MED).** Store path is **per-project** (`~/.trace/knowledge/{sanitize(project)}.json`). Plan says "around shared `~/.trace/knowledge/`" — ambiguous. A single dir-level lock serializes all **15** projects' concurrent `trace_learn_add`/`extract` → starvation under the documented 8+-session scenario. Lock must be **per-project** (`{project}.json.lock`), so different projects never contend. | **GAP** — specify per-project |
| Stale lock after crash / dead PID | `filelock` 3.25.2 present; POSIX backend = `UnixFileLock` (fcntl/flock). OS **auto-releases flock on process death incl. SIGKILL** → no permanent stale lock on local FS. **ROBUST on local FS.** | ROBUST (local) |
| NFS / `~` on network FS | **GAP (MED).** `flock` semantics are unreliable/unsupported over NFS; `filelock` documents this. Plan says `~/.trace/knowledge/` with no NFS caveat. If `~` is on NFS (not uncommon on managed/Berkeley research machines), the lock silently provides no mutual exclusion. Plan must at least **document** the NFS limitation (and that `filelock` falls back to a best-effort lock-file). | **GAP** — add caveat |
| Cross-platform (macOS/Linux) | `UnixFileLock` covers both; `filelock` auto-selects backend. No code-level issue. | ROBUST |
| Undeclared dependency | **GAP (LOW).** `filelock` is present **transitively** (via model2vec/HF), **not** in `pyproject.toml` `dependencies`. Core (non-`[learn]`) install would not get it. Since locking lives in the **learn extension** path, declare it under the `learn`/optional extra (consistent with numpy/model2vec there), not core. | **GAP** — declare it |
| Deadlock / starvation | Single lock, acquired once around the span, no nested/ordered multi-lock acquisition → **no classic deadlock**. `filelock` is process-re-entrant. With **per-project** granularity (above), no cross-project starvation. With dir-level granularity, starvation is real (see above). | ROBUST iff per-project |
| **P9(c) `.npy` torn window vs paired JSON** | **Real but self-healing.** `save_store` does `os.replace(JSON)` (atomic) THEN `np.save(.npy)` (non-atomic, streams) at `store.py:104`. A concurrent reader can see new JSON + torn `.npy`. **BUT** `load_embeddings_cache` (`store.py:286`) rejects any matrix with `shape[0] != len(store.learnings)` → torn/short `.npy` ⇒ returns `None` ⇒ embeddings recomputed. So the failure mode is a **silent perf degradation, not a correctness bug** — *except* the narrow case where row-count is unchanged but row *contents* are partially old (possible when only existing rows mutate). Plan's temp+`os.replace` for `.npy` is still the right fix; plan should **note the size-guard already provides partial protection** so the fix is correctly prioritized (MED, not HIGH). The per-span lock from P9(b), if it wraps `save_store`, **also** serializes the `.npy` write — making P9(c) partly redundant once P9(b) is scoped correctly. | **GAP (spec)** — note interaction with P9(b) scope + the existing size-guard |

---

## 3. Cross-fix interaction risks

1. **P1 ↔ P3 (RESOLVED, no risk):** P3's `attribution_warning_count==2` depends on P1's guard *passing* the waggle session. **VERIFIED** the waggle union actor types are `{ai,human}` (2 types) under participants-only, events-only, OR union — so the type-based guard passes and the count stays exactly 2 post-P1. P3's literal assertion needs **no change**. Sequence P3 after P1 (already in plan's sequencing) but the numbers are A1-invariant for this fixture. **No invalidation.**
2. **P1 ↔ existing `test_v041_attribution_audit.py` (RESOLVED by the M3 "types" decision):** `_make_session` there builds a **2-participant-type** session. Under the FINAL "≥2 actor **types**" guard, `test_human_same_instance_self_resolution_counts` (attr==1), `test_ai_self_resolution_counts_in_both_metrics` (both==1), and `test_attribution_warning_before_missing_snippet_in_render` all **survive P1**. **Had the plan chosen "instances" instead of "types", these would still pass too** (the helper has distinct ids) — but `system→system`/single-actor cases diverge between the two definitions. The FINAL plan's resolution to **types** (M3/`evt_016`) is correct and is *the* reason no existing audit test is invalidated. **No invalidation; flagged because it is load-bearing and silent.**
3. **P1 ↔ FM25 (RISK — see GAP 2):** P1's under-specified FM25 scoping does not invalidate any existing test (masked by `OR "AI resolved"`), but a wrong implementer choice **silently regresses** the ai→ai single-actor FM25 signal with the suite still green. This is the highest cross-fix risk because it is invisible.
4. **P4 ↔ P1 (SAFE):** confirmed independent render paths; P4 does not change P1's detector output or the render-order test. Caveat: implementer must not also delete the attribution_warning `audit_warnings.append` at `:367-372` (different from the orphan one at `:378-383`).
5. **P9(b) ↔ P9(c) (efficiency note):** a correctly-scoped P9(b) per-span lock also serializes the `.npy` write inside `save_store`, partially subsuming P9(c). P9(c)'s atomic-rename is still worth doing (defends single-process interruption / signal mid-write) but the plan should note the overlap so effort is not double-counted.
6. **P1 ↔ M1 hook (consistency):** the FINAL plan's M1 mirrors the guard in `decision-audit.sh:69`. **VERIFIED** the hook currently computes `same_instance_self_resolved` with **no actor-count gate** and reports `NON_AI_SELF = SAME_INSTANCE − AI_SELF_RESOLVED`. The hook test `test_v041_decision_audit_hook.py:74` (single-actor, no participants) asserts the warning fires → **will fail post-M1** → correctly in the plan's M2 list. But the hook's Python snippet has **no participants/event-actor type set computation at all** — M1 must add the same union-of-types logic to the embedded Python (not just a one-line guard), and the hook reads only `data["events"]` + `data["metadata"]`; confirm `metadata.participants` is present in persisted JSON (it is — `create_session` writes it). Minor: the plan says "mirror the guard … at `decision-audit.sh:69`" — line 69 is the `(type,id)` equality; the guard insertion point is actually the per-event loop + a pre-pass building the type set, a slightly larger edit than "line 69".

---

## 4. A1-mandated cases the FINAL test list omits

A1 (`trace_v1_round3_amendments.md` §A1, lines 43–48) mandates these test cases. Mapping to the FINAL plan's P1 test list:

| A1-mandated case | In FINAL P1 test list? | Note |
|---|---|---|
| Restore single-actor `human→human` ⇒ no warn (`_clean`) | **Yes** | decision-time layer |
| `system→system` self-resolution ⇒ no warn | **Yes** | decision-time layer |
| Claude(id=claude) proposed, GPT(id=gpt) resolved ⇒ no warn | **Partial** | covered by surviving `test_human_different_instance_self_resolves_clean` (id≠) — but that fixture is **single-actor**, so post-P1 it passes via *both* id-inequality AND multi=False. **OMISSION:** no **multi-actor** different-instance no-warn test → the id-inequality path is not tested independently of the multi-actor gate. Add one. |
| Same human (id=human) proposed AND resolved ⇒ warn (evt_025) | **Yes** | the ≥2-actor-type same-non-ai-id case |
| `ai→ai` single-actor ⇒ STILL warns (§3 regression guard) | **Yes** (decision-time FM1) | **but OMITS** the **session-end** assertion (`self_resolution_count==1` AND `attribution_warning_count==0` for ai→ai single-actor) — GAP 1 |
| Generalize FM25 to match (A1:48) | **NOT explicitly tested** | GAP 2 — no FM25 literal-string assertion for ai→ai single-actor; existing FM25 tests masked by `OR "AI resolved"` |

**Net omissions in the FINAL test list (all P1):**
1. Session-end ai→ai single-actor asymmetry test (`self_resolution_count==1` & `attribution_warning_count==0`) — **MEDIUM**.
2. FM25 ai→ai single-actor literal `"self-resolved in"` assertion + explicit plan statement of FM25's scoping disposition — **MEDIUM** (silent-regression risk).
3. Multi-actor different-human-instance no-warn test (de-couple id-inequality from the multi-actor gate) — **LOW/MED**.

These are additive to the plan's existing M2 "exactly 2 bug-tests to rewrite" finding (which is **correct and complete** — I independently confirmed exactly two bug-encoding tests: `test_decision_guards.py:84` and `test_v041_decision_audit_hook.py:74`; the two `test_v041_attribution_audit.py` assertions use the multi-actor helper and are NOT bug-encoding).

---

## 5. Bottom line

The FINAL plan's edge-case posture is sound for the cases it enumerates. The **type-based** actor counting (M3/`evt_016`) is the correct, outcome-determining choice and the reason no existing audit test breaks. **P2/P3/P4 are edge-robust** (P2 simulation reliable for its invariant; P3 counts VERIFIED exact and A1-invariant for the frozen fixture; P4↔P1 SAFE). **P1 has two real under-specifications** (FM25 scoping with an invisible regression path; missing session-end ai→ai asymmetry + multi-actor different-instance tests). **P9(b) has a critical scope bug** (lock must wrap the `__init__.py` load→mutate→save span per-project, not `save_store` internals) plus NFS/dependency-declaration gaps; **P9(c)** is real but self-healing via the existing size-guard. Tighten P1's FM25 disposition + test list and P9(b)'s critical-section scope/granularity before implementation; everything else is plan-prose tightening.

*Provenance: all "VERIFIED" claims executed read-only — in-process probes via `.venv` with `PYTHONPATH=src` and an in-memory storage shim, targeted `uv run pytest` (13 passed, current pre-fix code, confirming the suite encodes G4), `grep`, JSON analysis of the frozen waggle fixture. No repo file modified except this one. No process signalled. No `trace_*`/MCP calls. No directory moved.*
