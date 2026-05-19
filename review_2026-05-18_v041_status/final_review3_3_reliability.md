# Final Review 3.3 — Reliability / Edge-Case Verification (Iteration 3)

**Reviewer:** Independent adversarial pre-PR reliability gate · no prior context
**Date:** 2026-05-18 · **Branch:** `fix/v0.4.1-criticals-p1-p3` (not switched)
**Scope:** Re-verify the WHOLE remediation's reliability after iteration-2 fixes
(`_recall_hook` lock, CHANGELOG phrase, `.mcp.json` `--with filelock`). Emphasis:
lost-update still prevented + P9(b) lock genuinely ACTIVE under the configured
`.mcp.json` (not a silent no-op). All probes in-process / read-only; no writes,
no git state change, no process signals.

---

## 1. VERDICT

### GREEN — confidence HIGH (0.93)

Every reliability-critical surface passed under hostile probing. The two
headline mandates are independently re-demonstrated with positive controls:

- **Lost-update is PREVENTED** — re-demoed with a no-lock control that
  *does* lose an update, and a real-lock serialization probe (0.92s block).
- **P9(b) lock is genuinely ACTIVE under the configured `.mcp.json`** —
  `filelock` 3.25.2 imports under the exact `--with` set; the real-lock
  branch is reached; cross-process lost-update prevented with real
  subprocesses.

No deadlock, no crash, no data-loss, no false-positive was found. The single
latent sharp edge (`project_lock` non-re-entrancy) is **unreachable by any
code path** and degrades gracefully (bounded timeout, not a hang) even if
reached — consistent with the FINAL plan's stated expectation.

Residual (non-blocking, documentation, not reliability): CHANGELOG is
forward-dated `## [0.4.1] — 2026-05-18` with a dangling
`compare/v0.4.1...HEAD` link while the `v0.4.1` tag is intentionally not yet
cut (P7 is green-gated/LAST + a USER tag-policy decision). This is expected
on this branch and explicitly deferred by the plan — noted, not a defect.

---

## 2. Lost-update-prevented re-demo (HEADLINE)

`_recall_hook` is the closure registered via `register_recall_hook` in
`extensions/learn/__init__.py:138`; its RMW span is locked at
`__init__.py:107-124` (`with store.project_lock(project): load_store → embed
→ recall → save_store`).

**Re-demo (verified-equivalent locked RMW vs concurrent locked store-add,
same project, tmp dir, no file writes outside tmp):**

| Run | Result |
|-----|--------|
| Recall-hook RMW path (locked, 0.8s window) + concurrent locked add | FINAL=3 learnings; `concurrent_add` observed `n_before=2` ⇒ it loaded **after** the recall-hook save committed (serialized). SEED + recall-add + concurrent-add all survive. **LOST_UPDATE_PREVENTED = True** |
| **Positive control — no lock** (monkeypatched no-op) | FINAL = `['recall-add','seed']` — `concurrent-add` **LOST**. Confirms the scenario genuinely triggers a lost update absent the lock. |
| Serialization probe (real lock) | Thread B blocked **0.92s** while A held the lock 1.0s ⇒ real OS mutex, not a no-op. |
| **Cross-process** (two real subprocesses, the true P9b topology) | FINAL = `['proc-A','proc-B','seed']` — all survive across process boundaries. **CROSS_PROCESS_LOST_UPDATE_PREVENTED = True** |

**Code-read — all 6 RMW (load→mutate→save) spans locked:**

| # | RMW span | Location | Locked |
|---|----------|----------|--------|
| 1 | `_recall_hook` (the headline) | `__init__.py:107` | ✅ |
| 2 | `_extract_hook` | `__init__.py:127` | ✅ |
| 3 | `trace_learn_recall` | `__init__.py:159` | ✅ |
| 4 | `trace_learn_add` | `__init__.py:216` | ✅ |
| 5 | `trace_learn_forget` | `__init__.py:287` | ✅ |
| 6 | `trace_learn_extract` | `__init__.py:316` | ✅ |
| — | `trace_learn_list` (`:270`) | read-only, no `save_store` | correctly UNlocked |

`_recall_hook` IS included. Every `load_store` that is followed by a
`save_store` is inside `store.project_lock`.

---

## 3. P9(b) active under `.mcp.json` — confirmation

`.mcp.json` (verified valid JSON):
`args = ["--from",".","--with","openai","--with","numpy","--with","model2vec","--with","filelock","--refresh","trace-mcp"]`
→ `--from .` local, `--with filelock` **present**.

| Check | Result |
|-------|--------|
| `filelock` importable in exact configured env (`uv run --with openai --with numpy --with model2vec --with filelock`) | **filelock 3.25.2** imports; `from filelock import FileLock, Timeout` succeeds |
| `store.project_lock` reaches the REAL-lock branch (not graceful no-op) under those deps | YES — `store.py:63` import succeeds ⇒ `store.py:79` `with FileLock(...)` taken |
| Real lock serializes a 2nd `project_lock` on the same project | YES — 0.92s block while held (§2) |
| Graceful no-op still works when filelock absent (`sys.modules['filelock']=None`, no dir moves) | YES — `store.py:64-74` warns once, yields, no crash; count correct; 2nd call stable (warn-once flag) |

**P9(b) is genuinely ACTIVE in the server the configured `.mcp.json` would
launch — not a silent no-op.**

---

## 4. Per-surface stress table

| Surface | Probe | Result |
|---------|-------|--------|
| **project_lock — per-project keying** | proj-A held vs proj-B acquire | B waited **0.00s** — independent; 15 projects won't starve |
| **project_lock — timeout → single yield** | 0.3s timeout, 1.2s holder | inner block ran **exactly once** after ~0.43s (no double-yield) |
| **project_lock — no-filelock graceful** | `sys.modules['filelock']=None` | warn-once + yield, no crash |
| **project_lock — non-re-entrant, no RMW nesting** | same-thread nested `project_lock(p)` | inner blocks then **graceful-degrades after timeout** (no permanent deadlock); **NO code path nests** — hooks invoked from `server.py:108/161/219/503` at tool-entry, never inside a lock; `trace_learn_*` take the lock once at top level and call only non-locking helpers. Latent + unreachable + bounded-degrade. |
| **lock-path sanitization** | `'my/proj'`, `'../escape'` | `sanitize_name` applied to both store + `.lock`; all stay inside knowledge dir; no escape/dangerous collision |
| **PROV — empty session** | export + rdflib parse | 5 triples, parses |
| **PROV — agents-only** | participants, 0 events | 10 triples, parses |
| **PROV — many mixed anchors** | event-ID + 4 URI-form corrects, all 5 event types | 59 triples; `wasInvalidatedBy=1` (event-ID), `prov:Influence=4` (URI) — **correction split exactly correct** |
| **PROV — retry + parent** | `retries_event_id` + `parent_event_id` | `prov:wasRevisionOf` + `prov:wasInformedBy` both emitted |
| **PROV — huge dict** | 200-key×50-list tool input | 41 KB doc, 18 triples, parses |
| **PROV — unicode/pipe/ctrl** | emoji, pipe, quotes, `<xml>`, accents | parses (15 triples) — `_lit()` JSON-stringifies safely |
| **P1 FM1 — solo human (0 & 1 participant)** | human→human | **NO warn** (A1's named false-positive fixed) |
| **P1 FM1 — system→system solo** | system→system | **NO warn** |
| **P1 FM1 — ai→ai solo (0 & 1 participant)** | ai→ai | **STILL WARNS** (§3 regression guard intact) |
| **P1 FM1 — actor-id drift, same TYPE** | ≥2-type, same human id | **WARNS** (true evt_025) |
| **P1 FM1 — multi-actor different human ids** | distinct ids, ≥2-type | **NO warn** (id-inequality decoupled from gate) |
| **P1 FM25 — fast-resolution split (A-R3-1)** | ai→ai solo vs human/system solo vs multi non-ai | ai→ai solo **STILL fast-warns**; solo non-ai suppressed; multi non-ai fires — split present, §3 FM25 warning NOT silently dropped |
| **P1 session-end detector — ai→ai asymmetry (A-R3-5a)** | ai→ai / human / multi-same-human | `ai_ai_solo`: self_res=1, attr_warn=**0**; `human_solo`: 0/0; `multi_same_hum`: attr_warn=**1** — asymmetry correct |
| **P1 adopter mirror — decision-audit.sh E2E** | multi human→human / solo human→human / solo ai→ai (real subprocess) | SAME_INSTANCE=1 / **0** / 0; AI_SELF=0/0/**1** — mirrors server exactly; bash-3.2 (no `mapfile`, `bash -n` OK); type-set pre-pass present |
| **P2 — core/extension boundary** | `extensions.learn` blocked via meta_path hook (non-destructive) | `_compute_knowledge_metrics` returns exact zero-sentinel; core `trace_project_summary` survives; query_tools `:163` + `:353` both guarded |
| **P2 — CI gate (A-R3-6)** | `ci.yml:45-46` | named "Core/extension boundary invariant (governance ADR 003)" step runs `test_v041_core_extension_boundary.py` |
| **P9(c) — atomic npy success** | `save_embeddings_cache` | written to final name, **0 `.tmp` leftovers**, reloads |
| **P9(c) — atomic npy failure** | `np.save` raises mid-write | OSError propagates, temp cleaned, **no torn `.npy`**, no final file |
| **fail-safe — extension_status** | config import forced to ImportError | never raises; degrades to "no learning extension" |
| **fail-safe — test SKIP not ERROR** | grep guards | P5 `importorskip("rdflib")`, P9-lock `importorskip("filelock")` + explicit graceful test, P9-npy `importorskip("numpy")` |
| **collateral — trace_export 3 formats** | json/markdown/prov-jsonld | 1292 / 274 / 935 bytes, all OK |
| **collateral — server + scratchpad import** | import | OK; `__version__ = 0.4.1` |
| **collateral — git tags** | `git tag -l` (read-only) | exactly `v0.1.0→7110528`, `v0.2.0→568c023`, `v0.3.0→50051ec`, `v0.4.0→0540346`; **v0.4.1 absent** (correct — P7 not yet cut) |
| **collateral — git status** | `--porcelain` | clean tree; untracked = review dir / notes / pngs / ADR-003 / extension_status.py / new v041 tests — all expected |
| **collateral — ADR truth-up** | ADR-002 `:100`, ADR-003, ADR README | ADR-002 has the "Amended 2026-05-18 (A1/evt_016)" self-contradiction fix (A-R3-3); ADR-003 is the durable boundary home (A-R3-6); README indexes 002+003 |
| **lint/types** | ruff + pyright on 8 changed core files | `All checks passed!` · `0 errors, 0 warnings` |

---

## 5. Deadlock / crash / data-loss / false-positive findings

**None blocking.** Detail:

- **No deadlock.** The only non-re-entrant path (same-thread nested
  `project_lock` on the same project — distinct `FileLock` instances don't
  share filelock's per-instance re-entrancy counter) is **unreachable**: no
  code path nests two `project_lock` calls. Hooks are invoked from
  `server.py` at MCP-tool entry (never inside a lock); each `trace_learn_*`
  tool acquires the lock once and calls only non-locking `store.*` helpers.
  Even if reached, it does NOT hang — it blocks `TRACE_LOCK_TIMEOUT` (15s
  default) then takes the documented graceful `except Timeout: yield`
  degrade. Latent, unreachable, bounded — matches the FINAL plan's
  "non-re-entrant but no RMW nesting incl _recall_hook" expectation.
- **No crash.** Graceful no-op (filelock absent), extension_status, P2
  zero-sentinel, atomic-npy failure path all degrade without raising.
- **No data-loss.** Lost-update prevented in-thread AND cross-process;
  positive no-lock control confirms the lock is load-bearing; npy writes
  atomic on success and failure.
- **No false-positive.** P1 fixed A1's named production false positives
  (solo human, system→system) while preserving the §3-verified-solid ai→ai
  warnings at FM1, FM25, and the session-end detector — server and
  decision-audit.sh adopter mirror agree on every probed case.

**Non-blocking residual (documentation, deferred by the plan):**
CHANGELOG `## [0.4.1] — 2026-05-18` is forward-dated and
`[Unreleased]: …compare/v0.4.1...HEAD` references a `v0.4.1` tag that does
not yet exist on this branch. This is exactly the P7 "release process, LAST,
green-gated, USER tag-policy decision" state — expected here, not a
reliability defect. Flagged for the release step, not for this gate.

---

**Probe hygiene:** all probes were in-process (`uv run --with …`), used
tmp directories, performed no writes/moves/deletes outside tmp, issued no
state-changing git, signaled no process, and did not run the full suite.
Three probe-side schema-validation errors (invalid `status='ok'`,
`disposition='accepted'` without `resolved_by`) were probe bugs, not code
defects — corrected and re-run; they incidentally confirm the schema's own
invariants are enforced.
