# Final Pre-PR Correctness & Regression Re-Verification (Iteration 2)

**Reviewer role:** Independent adversarial final gate, no prior context.
**Repo:** `/Users/echoes/Documents/Berkeley/Research/TRACE`
**Branch:** `fix/v0.4.1-criticals-p1-p3` (not switched)
**Date:** 2026-05-18

---

## 1. VERDICT

### 🟢 GREEN — SHIP (clears the correctness/regression gate for PR)

**Confidence: HIGH (~93%).**

- The defect under re-test (P9b/A-R3-2 — the unlocked `_recall_hook` RMW span) is **independently confirmed fixed** by code-read AND a cross-process probe.
- ALL knowledge-store RMW spans are locked; the one read-only span is correctly *not* locked; no re-entrant nesting ⇒ no deadlock.
- Full suite: **839 passed, 6 skipped, 1 xfailed, 0 failed, 0 errors**. No load-flake even triggered.
- All headline fixes (P1, P2, P3, P5, P9a/b/c) re-verified by VERIFIED probes.
- ruff `src/` = 0 errors; pyright `src/` = 0 errors.

The remaining ~7% is residual implementation/release risk the plan itself defers to USER (git tag policy — P7) and documented residuals (NFS lock limitation, `filelock` placed in extras not core deps). **None is a correctness defect; none blocks the PR.** See §6.

---

## 2. The `_recall_hook` + all-spans-locked verification (the fix under re-test)

File: `src/trace_mcp/extensions/learn/__init__.py`. Lock impl: `src/trace_mcp/extensions/learn/store.py:45-89` (`project_lock`, per-project `filelock.FileLock`, graceful no-op if `filelock` absent, proceed-on-timeout).

### (a) `_recall_hook` now wraps its full load→embed→save span — VERIFIED

`__init__.py:97-124`:
```
107  with store.project_lock(project):
108      ks = store.load_store(project)
...      stale = _needs_embedding(ks); embedded = await _embed_learnings(stale) ...
113      results = await matching.recall_learnings(...)
122      if results or embedded:
123          store.save_store(ks)
```
The `project_lock(project)` (107) encloses `load_store` (108) … `save_store` (123) — the entire read-modify-write span. **Confirmed fixed.**

### (b) ALL knowledge-store RMW spans locked — VERIFIED (grep + span trace)

`grep` of every `store.load_store(` / `store.save_store(` / `project_lock(` in `__init__.py`:

| Span | Function | `project_lock` | load | save | RMW? | Locked? |
|---|---|---|---|---|---|---|
| `_recall_hook` | hook (core auto-recall) | :107 | :108 | :123 | yes | ✅ **(the fix)** |
| `_extract_hook` | hook (session-end) | :127 | :128 | :135 | yes | ✅ |
| `trace_learn_recall` | MCP tool | :159 | :160 | :179 | yes | ✅ |
| `trace_learn_add` | MCP tool | :216 | :217 | :245 | yes | ✅ |
| `trace_learn_list` | MCP tool | — | :270 | (none) | **read-only** | correctly unlocked |
| `trace_learn_forget` | MCP tool | :287 | :288 | :292 | yes | ✅ |
| `trace_learn_extract` | MCP tool | :316 | :317 | :339 | yes | ✅ |

`trace_learn_list` (:270) does `load_store` then `list_learnings` with **no `save_store`** — it is not a mutating span, so the absence of a lock is correct (a read of a file written atomically via temp+`os.replace` is consistent). Every mutating span (add, `_extract_hook`, `_recall_hook`, `trace_learn_recall`, `trace_learn_forget`, `trace_learn_extract`) is inside a `project_lock`. **All RMW spans locked.**

No other module calls `load_store`/`save_store`/`project_lock` for a mutation: only `tools/query_tools.py:174` calls `load_store` (read-only, in the P2-guarded path) — not an RMW.

### (c) No deadlock — VERIFIED

`filelock.FileLock` is not re-entrant. Checked nesting: `extraction.extract_from_session_auto` and `matching.recall_learnings` were greped — **no `project_lock` / `recall_if_available` / `extract_if_available` / `trace_learn_*` re-entry** (grep exit 1 = no matches). Hooks are invoked from `server.py` via `recall_if_available`/`extract_if_available`; none of those wrappers hold a lock. No RMW span nests another ⇒ no self-deadlock.

### Probes (VERIFIED, no writes to real store — all used a temp `TRACE_KNOWLEDGE_DIR`)

- **Generic RMW** (2 threads, lock→load→sleep(0.4)→add→save): final count **2** (no lost-update), elapsed **0.85s** (serialized; interleaved would be ~0.4s). PASS.
- **`_recall_hook` cross-process** (real concurrency model — `filelock` is a cross-process primitive): a `recall` subprocess held the per-project lock via the `_recall_hook`→`project_lock` span ~2s; a concurrent `add` subprocess **waited 1.74 s** for the lock then committed; `final learnings == ['critical-add-must-survive']` — **the add survived (no lost-update)**. This is the exact lost-update the prior reviewer caught; it is fixed. PASS.
  - *Probe note:* an earlier single-event-loop `asyncio.gather` variant showed `max_depth=2` + "proceeding without the lock" timeouts. This is a **probe artifact, not a code defect**: `FileLock.acquire` blocks the thread synchronously, so 7 coroutines on one event loop with a lock-holder that `await`s inside the lock deadlock the loop until the documented proceed-on-timeout fallback (`store.py:81-88`) fires. Real concurrency is across OS processes (separate MCP subprocesses), which the cross-process probe exercises correctly and which PASSES. The add surviving even in the artifact run still proves `_recall_hook` acquires the lock (7 acquires observed).

---

## 3. Full-suite result + per-failure isolation

**Command (VERIFIED, ran):** `uv run --with rdflib pytest -q -k "not llm"`

```
866 collected / 20 deselected / 846 selected
= 839 passed, 6 skipped, 20 deselected, 1 xfailed, 13 warnings in 139.73s =
```

- **0 FAILED, 0 ERROR** (grep of full output: no `FAILED`/`ERROR` lines anywhere).
- The known load-flake family (`test_failure_modes_e2e.py`, `test_e2e_server`-driven) **all passed in-suite** — no isolation re-run needed.
- 6 skipped + 1 xfailed are pre-existing optional-dep / xfail markers (e.g. `test_learn_embeddings_e2e.py x.s`, `test_learn_embeddings_integration.py .........sss`), not regressions.
- 13 warnings = rdflib `ConjunctiveGraph deprecated` (test-only, in `test_v041_p5_prov_roundtrip.py` / `test_v041_prov_ld_split.py`) — cosmetic, not a defect.
- All P-item gate tests green: `test_v041_l9_1_waggle_regression .. ` (P3), `test_v041_core_extension_boundary .` (P2), `test_v041_p5_prov_roundtrip ..` (P5), `test_v041_p9_atomic_npy ..` (P9c), `test_v041_p9_lazy_embedding ..` (P9a), `test_v041_p9_lock ...` (P9b), `test_v041_attribution_audit` 29, `test_decision_guards` all dots.

**No failures ⇒ no isolation classification required.**

---

## 4. Headline-fix re-verification table

| Item | Method | Result |
|---|---|---|
| **P1 FM1** (`decision_tools.py:80-105`) | code-read | ✅ ai→ai warns **unconditionally** (`:87-94`); non-ai gated by `elif session.is_multi_actor()` (`:95-105`). |
| **P1 FM25** (`decision_tools.py:107-119`) | code-read | ✅ A-R3-1 split present: `if resolved_by_type=="ai" or session.is_multi_actor()` (`:115`) — ai→ai single-actor still warns. |
| **P1 `is_multi_actor`** (`schema/session.py:72-92`) | code-read | ✅ ≥2 **distinct actor TYPES** over `participants ∪ event actors`; fallback to event actors when participants empty (M3/evt_016, A7). |
| **P1 session-end detector** (`session_tools.py:334-339`) | code-read | ✅ same-(type,id) **AND** `session.is_multi_actor()`; ai-only `self_resolved_ids` (`:322`) stays unconditional + separate. |
| **P1 test contract (G4)** | grep + suite | ✅ `test_human_self_resolves_clean` **restored** (`test_decision_guards.py:84`); buggy `_warns` gone; `system→system` single-actor clean (`:115`); `test_ai_self_resolves_fast_still_warns_fm25` (`:135`, A-R3-1 literal-string guard); `test_multi_actor_different_human_instances_clean` (`:158`, A-R3-5c). All pass. |
| **P2 boundary** (`query_tools.py:163-172`) | code-read + probe | ✅ `from …extensions.learn.store import load_store` in `try/except ImportError` → zero sentinel. Second site `:353-357` guarded by broader `except Exception`. **Probe:** with `extensions.learn` import blocked (no dir move), `_compute_knowledge_metrics` returned the zero sentinel, **no `ModuleNotFoundError`**. PASS. |
| **P3 L9.1 gate** | probe (raw JSON + real `_build_attribution_audit`) | ✅ Re-derived from `audit_2026-05-13_waggle_session/trace_session_trace_20260513_446733.json`: **28 events**; **15** missing-snippet contributions; **1** missing-snippet correction; **2** same-instance self-resolutions (`evt_001`,`evt_025`). Via real audit code: `attribution_warning_count=2 ['evt_001','evt_025']`, `self_resolution_count=0` (no ai→ai), missing-snippet warning "15 contribution(s), 1 correction(s)". Actor-type union {ai,human}=2 ⇒ `is_multi_actor`=True ⇒ P1↔P3 safe (count stays 2). |
| **P5 PROV round-trip** | probe (rdflib parse) | ✅ Built session with event-ID correction + URI correction + `parent_event_id`; `export_prov_jsonld` → rdflib parsed **36 triples (non-empty)**: `prov:wasInvalidatedBy` ×1, `prov:wasInformedBy` ×1, `prov:qualifiedInfluence` ×1 + `prov:Influence` node + `prov:atLocation`=`external:https://example.org/doc#L5`. |
| **P9a lazy embedding** (`embeddings.py:100-129`) | code-read + probe | ✅ `__init__` sets only `model_name` + `_model=None`; `from_pretrained` only in `_get_model` (`:119`). **Probe:** spying `StaticModel.from_pretrained` to raise — `Model2VecEmbeddingProvider()` constructed with **zero** `from_pretrained` calls, `_model is None`. Git diff confirms eager construction-time load was removed. |
| **P9b lock** | code-read + probes | ✅ See §2. Generic 2-thread PASS (count 2, serialized); `_recall_hook` cross-process PASS (add waited 1.74 s, survived). |
| **P9c atomic npy** (`store.py:294-333`) | code-read + fault-inject probe | ✅ `np.save` into `tempfile.mkstemp` + `os.replace`; `except BaseException` unlinks temp. **Probe:** injected `np.save` failure → original `.npy` byte-identical (`array_equal` True), **no `.npy.tmp` leak**. |

---

## 5. ruff / pyright (VERIFIED, ran)

- `uv run ruff check src/` → **`All checks passed!`** — **0 errors**.
- `uv run pyright src/` → **`0 errors, 3 warnings, 0 informations`**.
  - The 3 warnings are `reportUnusedImport` on `embeddings.py:27/34/41` (`_np`, `AsyncOpenAI`, `StaticModel`) — module-level `try/except` **availability probes** carrying `# noqa: F401` for ruff. Git diff confirms these are **pre-existing** (the P9a lazy-load change neither added nor removed them; it moved the *functional* `StaticModel` use into `_get_model`). Warnings ≠ errors; the plan's bar is "ruff 0 / pyright 0 [errors]". **Met.**

---

## 6. Regressions / defects / residuals

**No regressions. No correctness defects. No PR blocker.**

Informational (non-blocking; consistent with the plan's documented residuals / open USER items):

1. **`filelock` placement (INFORMATIONAL).** `pyproject.toml`: `filelock>=3.12` is in the `embeddings`, `all`, `dev` extras but **not** in core `dependencies` (lines 11-14: only `mcp`+`pydantic`). A-R3-2 said "declare `filelock` as a real dependency." This is an acceptable architectural judgment, **not a defect**: `project_lock` degrades to a documented graceful no-op + one-time warning when `filelock` is absent (`store.py:62-74`), and its only consumer is the trace-learn extension, which already requires the `embeddings` extra (numpy/model2vec) to function — `filelock` lives correctly alongside that dependency surface. uv.lock pins `filelock 3.25.2`; it imports fine in the dev/test env. The missing-lock fallback is the explicitly intended residual. Surface to USER as a documentation/positioning note only.
2. **NFS / `~`-on-network-FS lock limitation** — documented residual per A-R3-2; `project_lock` docstring notes proceed-on-timeout. Not in scope to fix here.
3. **P7 git tag policy** — open USER decision per plan (`round3_amendments.md` recommends real annotated tags `v0.1.0..v0.4.1`). CHANGELOG truth-up has landed: header `## [0.4.1] — 2026-05-18` (no longer "In progress"), `[Unreleased]` compare-link = `compare/v0.4.1...HEAD`. The remaining tag action is process, not code; PR-creation gate is unaffected.
4. **ADR-002 doc (A-R3-3)** — lines 32/100 now read "enforced at log time (FM1/FM25) AND at session-end (`attribution_warning_count`)", which is accurate post-P1 (both layers still enforce the rule; multi-actor gating is a scoping refinement, not a removal). The flagged self-contradiction is resolved.

**Bottom line:** the prior gate's single defect is fixed, the fix is complete and correctly scoped (all RMW spans locked, no deadlock), the whole remediation re-verifies clean, the full suite is green with zero failures, and static analysis is clean. **GREEN — clears the correctness/regression gate; safe to proceed toward a PR** (tag policy is a separate USER decision, not a blocker).
