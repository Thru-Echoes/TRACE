# Final Pre-PR Reliability / Edge-Case Review — ITERATION 2

**Reviewer:** Independent adversarial reliability/edge-case verifier (no prior context).
**Date:** 2026-05-18 · **Branch:** `fix/v0.4.1-criticals-p1-p3` @ `d20be80` (not switched).
**Method:** Read-only. In-process inline probes via `PYTHONPATH=src uv run --no-project python`
(no editable install present in `.venv`; `PYTHONPATH=src` used — pure read-only, no writes/moves;
absence simulated via `sys.modules`/`MetaPathFinder`, never directory moves). Targeted test
files run (not the full suite — owned by another reviewer). No processes signalled.

---

## 1. VERDICT

**GREEN — ship-ready for PR (subject to the unrelated P7 release-tagging USER decision, which is
out of this gate's scope).** Confidence: **HIGH.**

The headline defect (the lost-update an unlocked `_recall_hook` caused) is **VERIFIED-FIXED**:
re-demonstrated by (a) reproducing the original defect with the pre-fix unlocked RMW shape, and
(b) proving the now-locked path — including the **genuine registered closure** pulled from the
hook registry — prevents it. The full adversarial pass found **zero** deadlock / crash / data-loss /
false-positive defects in the remediated surfaces. Two benign, pre-existing, out-of-scope residuals
are documented (sanitize-name collision; rdflib pipe-in-actor-id serialization warning) — neither is
a regression and neither blocks the PR.

---

## 2. RE-DEMONSTRATION OF THE FIXED DEFECT (headline)

**Result: lost-update is now PREVENTED. VERIFIED-FIXED (empirically, two independent ways).**

Code-read confirmation: `src/trace_mcp/extensions/learn/__init__.py:107` — `_recall_hook` now opens
`with store.project_lock(project):` and the entire `load_store → _needs_embedding → _embed_learnings
→ matching.recall_learnings → save_store` span (lines 108-124) executes **inside** that lock. All
6 RMW sites are locked: lines 107 (`_recall_hook`), 127 (`_extract_hook`), 159 (`trace_learn_recall`),
216 (`trace_learn_add`), 287 (`trace_learn_forget`), 316 (`trace_learn_extract`).

**(a) CONTROL — original defect faithfully reproduced.** Tmp `TRACE_KNOWLEDGE_DIR`. Thread A =
pre-fix unlocked recall RMW (`load_store` → sleep 1.0s → `save_store`); Thread B = locked
store-level `add` of `ADD-CONTROL`, started 0.2s into A's window. Result: surviving learnings =
`['seed']` — **`ADD-CONTROL` clobbered**. Lost-update **reproduced** (proves the defect was real
and the test harness exercises the true race).

**(b) FIXED — equivalent locked path.** Same interleaving; A now wraps its RMW in
`store.project_lock`. Result: `['ADD-FIXED', 'seed']` — **both survive**. Lock serialized
load→…→save.

**(c) FIXED — the GENUINE registered closure.** `learn.register(mcp, storage)` called; the real
`register.<locals>._recall_hook` (module `trace_mcp.extensions.learn`) pulled from
`extension_hooks._recall_hook`; executed (it returned 2 results and saved — a real RMW)
concurrently with a locked store-level add of `ADD-DURING-REALHOOK`. Final store =
`['ADD-DURING-REALHOOK', 'seed-alpha about caching', 'seed-beta about locking']` — **the
concurrent add was NOT clobbered by the recall-hook save.** This exercises the actual production
closure body, removing all doubt. (An OpenAI call fired incidentally because a real key is in the
env; irrelevant — the RMW + save completed under the lock regardless.)

`filelock 3.25.2` is installed and is a **real declared core dependency** (`pyproject.toml:23`
`filelock>=3.12`), so the lock path (not the no-op degradation) is what shipped users exercise.

---

## 3. PER-SURFACE STRESS TABLE

| Surface | Verdict | Evidence / breaking input |
|---|---|---|
| **`_recall_hook` lost-update (headline)** | **ROBUST / VERIFIED-FIXED** | §2 (a)(b)(c). Control reproduces defect; locked path + genuine closure both prevent it. `__init__.py:107-124`. |
| **`project_lock` — per-project keying** | ROBUST | projA/projB do not contend (B enters+exits while A holds). `store.py:77` keys lock by `_store_path(project)`. |
| **`project_lock` — same-project serialization** | ROBUST | Overlapping same-project threads serialize (`E1,X1,E2,X2`), incl. weird-char project names. |
| **`project_lock` — no-filelock graceful** | ROBUST | `sys.modules["filelock"]=None` ⇒ context still yields (no-op), one-time WARNING, no crash. `store.py:62-74`. |
| **`project_lock` — timeout → single yield** | ROBUST (with noted hazard) | Nested same-project lock (non-re-entrant `FileLock`) blocks to `TRACE_LOCK_TIMEOUT` then **yields once** (`store.py:81-88`) — degrades to unlocked, NOT an infinite deadlock. **No RMW site nests `project_lock`** (verified: matching/extraction/embeddings have 0 `project_lock` refs; `recall_if_available`/`extract_if_available` call the hook holding no lock). Hazard is unreachable via current code. |
| **`project_lock` — lock/store path keying** | ROBUST | lock path == store path + `.lock`; both via `sanitize_name`; path stays inside knowledge dir (no traversal). |
| **PROV exporter — empty / agents-only / many-mixed-anchors / huge-dict / retry+parent / unicode-pipe-newline** | ROBUST | All 5 export + `rdflib.parse` succeed (5/10/56/35/15 triples). event-ID + 4 URI schemes + ambiguous `evt_1:foo` all handled; 300-key nested dict stringified via `_lit`. |
| **P1 guard — decision-resolve matrix** | ROBUST | solo `ai→ai` STILL warns (FM1 + FM25); solo `human→human` & `system→system` NO warn; multi-actor same non-ai id WARNS; actor-id drift NO warn; 0-participants `ai→ai` (event-actor fallback) STILL warns. |
| **P1 guard — session-end asymmetry (A-R3-5)** | ROBUST | solo `ai→ai`: `self_resolution_count=1`, `attribution_warning_count=0`; multi `human→human` same id: `attrib=1, self=0`; multi `ai→ai`: both=1. |
| **P1 guard — `decision-audit.sh` (bash 3.2)** | ROBUST | Clean isolated runs: solo human→human NO warn; multi-actor warn; solo ai→ai → AI self-resolution warn. No `mapfile`; `read -r <<<` is bash-3.2-safe (shebang `#!/bin/bash`). Mirrors `is_multi_actor()` via type-set pre-pass (lines 44-54). |
| **Fail-safe — `extension_status`** | ROBUST | With `extensions.learn` blocked (`find_spec` finder): degrades to "no learning extension", never raises. Lazy embedding-provider import (P9a). |
| **Fail-safe — P2 zero-sentinel** | ROBUST | `_compute_knowledge_metrics` returns exact zero sentinel under genuine `ModuleNotFoundError` (`query_tools.py:163-172`). `:353-358` also guarded (`except Exception`). 18-core-tool invariant test green. |
| **Fail-safe — PROV/lock/npy tests SKIP not ERROR** | ROBUST | `test_v041_p5_prov_roundtrip.py:20` `importorskip("rdflib")` (module-level); `test_v041_p9_lock.py:33,65` `importorskip("filelock")`; `test_v041_p9_atomic_npy.py:16` `importorskip("numpy")`. |
| **`.mcp.json`** | ROBUST (deviation noted) | Valid JSON; `uvx --from . --with openai/numpy/model2vec --refresh trace-mcp` — local repo-relative path (not machine-absolute). Differs from MEMORY's documented canonical `--from /abs/path --refresh-package` form; not a defect (repo-local dev config), noted for awareness. |
| **git tags** | ROBUST | `v0.1.0..v0.4.0` present, all **annotated**, at exactly the R3-expected commits (v0.1.0→7110528, v0.2.0→568c023, v0.3.0→50051ec, v0.4.0→0540346). **`v0.4.1` absent** (correct — P7 deferred). |
| **git status** | ROBUST | Only expected P1–P9 modifications + expected untracked (review dir, new test files, `extension_status.py`, `docs/adr/003-*`, `notes/`, screenshots). No unexpected state. |
| **Collateral — `trace_export` 3 formats** | ROBUST | json (valid), markdown (starts `#`), prov-jsonld (22 rdflib triples); unknown format → graceful error string (no crash). |
| **Collateral — server / scratchpad import** | ROBUST | Both import cleanly, incl. with extension blocked. |
| **Collateral — P9c atomic npy** | ROBUST | Success: atomic write, no `.tmp` leftover. Failure (`numpy.save` raises): exception propagates, temp cleaned, prior good `.npy` intact (no torn sidecar). |
| **Collateral — embeddings lazy-load (P9a)** | ROBUST | `StaticModel.from_pretrained` only in `_ensure_model` "on first use", not at import/`register()`; confirmed empirically (provider INFO logs only on first embed call). |
| **Broader health — ruff / pyright** | ROBUST | ruff "All checks passed" on 8 changed/critical files; pyright 0/0/0 on lock/store/extension_status/query_tools. |
| **Targeted test files** | ROBUST | `test_v041_p9_lock` (3) + `core_extension_boundary` (1) + `l9_1_waggle_regression` (2) + `extension_status` (6) + `p9_atomic_npy` (2) = 14 passed. `p5_prov_roundtrip` + `decision_guards` + `decision_audit_hook` + `attribution_audit` + `prov_ld_split` = 121 passed. L9.1 asserts exact mandated counts (28/15/1/2, evt_001+evt_025). |

---

## 4. DEADLOCK / CRASH / DATA-LOSS / FALSE-POSITIVE FINDINGS

**None that block the PR.** Specifically:

- **Data-loss (headline):** FIXED and re-verified two ways (§2). Original defect reproduced under
  CONTROL; prevented under the fix and via the genuine closure.
- **Deadlock:** None reachable. `filelock.FileLock` (installed 3.25.2) is non-re-entrant, so a
  *hypothetical* nested same-project `project_lock` would stall to `TRACE_LOCK_TIMEOUT` (15s
  default) then proceed unlocked via the `Timeout`→single-`yield` handler — bounded degradation,
  **not** an infinite deadlock. Verified by exhaustive code-read that **no** RMW site nests
  `project_lock`, helper modules (matching/extraction/embeddings) hold zero locks, and the hook
  dispatchers call the hook without holding a lock. Latent only if future code wraps a
  `trace_learn_*` call inside another `project_lock`.
- **Crash:** None. PROV exporter, fail-safe paths, export formats, npy failure path all degrade
  gracefully (no unhandled exception; unknown export format returns an error string).
- **False-positive (P1 guard):** None. The full decision-resolve matrix, session-end asymmetry,
  and the bash hook all behave correctly — solo `human/system` self-resolution is silent, solo
  `ai→ai` still warns, true multi-actor `evt_025` warns. (An apparent hook false-positive in an
  early probe was a **test-setup artifact**: two session files in the dir, `ls -t` re-read the
  newer multi-actor one; clean isolated runs are correct.)

### Documented benign residuals (pre-existing, out of scope, NOT regressions)

1. **`sanitize_name` collision** — distinct project names that sanitize to the same filename
   (`a/b` & `a:b` → `a_b`) share a store file *and* a lock. Pre-dates P9; the lock makes collided
   projects *safer* (serialized, not racing). Not a P9 regression.
2. **rdflib pipe-in-actor-id warning** — actor IDs containing `|` make rdflib emit a
   non-fatal "does not look like a valid URI" *warning*; the document **still round-trips**
   (triples produced). Real TRACE actor IDs are sanitized identifiers without pipes. Export does
   not crash; not a defect.
3. **`.mcp.json` form** differs from MEMORY's documented canonical (`--from .`/`--refresh` vs
   `--from /abs`/`--refresh-package`). Repo-local-portable; informational only.

---

## 5. SCOPE CONFIRMATION

Per the FINAL plan / round-3 amendments, the remediated surfaces (P1 guard scoping incl. FM25
ai/non-ai split, P2 boundary, P3 L9.1 gate, P5 prov round-trip, P9a/b/c) are all present, correct,
and stable under adversarial input. The headline P9(b) lock wraps the full `load_store → … →
save_store` span per A-R3-2, is per-project, and `filelock` is a real declared dependency. No
deadlock/crash/data-loss/false-positive introduced. **Reliability gate: GREEN.**
