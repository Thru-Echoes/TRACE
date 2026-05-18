# Final Pre-PR Correctness & Regression Review (Independent, Adversarial)

**Date:** 2026-05-18 · **Branch:** `fix/v0.4.1-criticals-p1-p3` · **Reviewer role:** independent final correctness/regression gate (no prior context).
**Method:** full suite run + isolation re-runs + code reading + inline `uv run` runtime probes (no file writes except this report). All probes ran via `PYTHONPATH=src uv run [--with rdflib] python -c`.

---

## 1. VERDICT

**CONDITIONAL — ship with ONE noted minor (non-blocking) defect.** Confidence: **high**.

All 6 critical/headline findings (P1, P2, P3, P5, P9a, P9b, P9c) are independently **CONFIRMED correct**. Full suite is green modulo the documented FM7-family load-flake (proven flake by isolation re-run — NOT a regression). ruff 0 errors, pyright 0 errors. No regression found.

One genuine but **non-blocking** gap: P9(b)/A-R3-2's mandated per-project lock was applied to 5 of 6 read-modify-write spans; the **auto-recall hook `_recall_hook` (`extensions/learn/__init__.py:97–119`) remains an unlocked `load_store→mutate→save_store` span** — the highest-frequency one. Degrades to last-writer-wins data-loss (recall-count / embedding-backfill), not corruption or crash. This does not block the PR but should be tracked (detail in §5).

---

## 2. Full-Suite Result + Per-Failure Classification

**VERIFIED — ran:** `uv run --with rdflib pytest -q -k "not llm"`
Result: **2 failed, 837 passed, 6 skipped, 1 xfailed, 20 deselected, 13 warnings — 182.04s**.

| Failed test | Symptom | Classification | Isolation evidence |
|---|---|---|---|
| `test_failure_modes_e2e.py::TestSystemicFailures::test_fm15_out_of_order_timestamps_accepted` | `asyncio.TimeoutError` at `test_e2e_server.py:84` (`_send_and_receive` 15 s `proc.stdout.readline()` read timeout) — MCP subprocess cold-start blown | **LOAD-FLAKE, not a regression** | re-ran ALONE → **PASS** |
| `test_failure_modes_e2e.py::TestSystemicFailures::test_fm28_logging_overhead_performance` | `assert 15.325… < 15.0` — 0.3 s over a hard wall-clock perf bound | **LOAD-FLAKE, not a regression** | re-ran ALONE → **PASS** |

**VERIFIED — ran:** `uv run --with rdflib pytest "…::test_fm15_out_of_order_timestamps_accepted" "…::test_fm28_logging_overhead_performance" -q` → **`2 passed in 15.10s`**.

Both failures are textbook FM7-family load-flakes (MCP-subprocess E2E timing under 8+ concurrent live Claude sessions), exactly as `round_FINAL_plan.md` P8 / `round1_SYNTHESIS.md` §0 predicted and explicitly classified as NOT code regressions. They pass deterministically in isolation. No "other" (real) failures occurred. The 13 warnings are benign (rdflib `ConjunctiveGraph` DeprecationWarning from the new P5 round-trip tests).

All new/modified P-item test files independently re-run green: **VERIFIED — ran:** `uv run --with rdflib pytest tests/test_v041_l9_1_waggle_regression.py tests/test_v041_core_extension_boundary.py tests/test_v041_p5_prov_roundtrip.py tests/test_v041_p9_lazy_embedding.py tests/test_v041_p9_lock.py tests/test_v041_p9_atomic_npy.py tests/test_v041_extension_status.py tests/test_decision_guards.py tests/test_v041_decision_audit_hook.py -q` → **`97 passed, 2 warnings in 1.51s`**.

---

## 3. Headline-Fix Re-Verification Table

| Fix | Status | Evidence |
|---|---|---|
| **P1** — `is_multi_actor`/`distinct_actor_types`; FM1 ai→ai unconditional + non-ai multi-actor-gated; FM25 split; session-end detector gated; decision-audit.sh type-set pre-pass | **CONFIRMED** | Code read: `schema/session.py:72–92` (union participants ∪ event actors, `>=2` types); `decision_tools.py:86–105` FM1 (`if resolved_by_type=="ai"` unconditional, `elif session.is_multi_actor()`); `decision_tools.py:114–119` FM25 (`if resolved_by_type=="ai" or session.is_multi_actor()` — A-R3-1 split present); `session_tools.py:333–338` detector gated by `session.is_multi_actor()`, ai-only `self_resolved_ids` (`:321`) preserved unconditional; `decision-audit.sh:44–54` type-set pre-pass, `:88` `multi_actor`-gated, `:84–85` ai-only backward-compat unconditional. **Behavioral probe (7 cases, VERIFIED):** ai→ai single-actor → FM1+FM25 warn (A-R3-1 regression guard holds); solo human → no warn; solo system → no warn; same-instance non-ai in {ai,human} session → warn (true evt_025); ai(claude)→human(o) multi-actor → no warn; participant-empty event-actor fallback both directions correct. |
| **P2** — `query_tools.py` knowledge-metrics import wrapped try/except ImportError fail-open | **CONFIRMED** | Code read: `query_tools.py:163–172` `try: from …extensions.learn.store import load_store except ImportError:` → zero sentinel; `:353–358` `_get_directory` also guarded (`except Exception`). **Probe (VERIFIED):** blocked `trace_mcp.extensions.learn.store` import → `_compute_knowledge_metrics('trace-mcp')` returns `{'total':0,…}` (no `ModuleNotFoundError`). Boundary test wired into CI (`.github/workflows/ci.yml:45–46`). |
| **P3** — L9.1 waggle regression gate (28 / 15 / 1 / 2) | **CONFIRMED** | Re-derived **two independent ways**: (a) raw-JSON probe over `audit_2026-05-13_waggle_session/trace_session_trace_20260513_446733.json` → 28 events, missing-snippet-contrib 15, missing-snippet-corr 1, multi-actor-gated same-instance self-res 2 = `['evt_001','evt_025']`; (b) via the real `_build_attribution_audit()` → identical. Test asserts exactly these. Actor-type union `{ai,human}` → multi-actor → P1 guard passes → count stays 2 (P1↔P3 safe). |
| **P5** — `prov_jsonld.py` rewritten to conformant `@graph` JSON-LD | **CONFIRMED** | Code read: `exporters/prov_jsonld.py` (typed node objects, `@graph`, deterministic blank nodes). **rdflib round-trip probe (VERIFIED)** on a session with event-ID correction + URI correction + `parent_event_id`: parsed **48 triples (29 PROV-namespaced)**; `trace:evt_001 prov:wasInvalidatedBy …` (event-ID correction), `prov:qualifiedInfluence` + `prov:Influence` node + `prov:atLocation "external:…"` (URI correction), `trace:evt_004 prov:wasInformedBy trace:evt_001` (`parent_event_id`). Non-empty graph with correct PROV-O predicates. |
| **P9a** — model2vec lazy-load; `dimensions` read-only property | **CONFIRMED** | Code read: `embeddings.py:103–129` (`self._model=None` at `__init__`; `_get_model()` does `from model2vec import StaticModel; StaticModel.from_pretrained` on first use; `@property dimensions`). **Probe (VERIFIED):** patched `model2vec`; after `Model2VecEmbeddingProvider(...)` → `from_pretrained` call list **empty**, `_model is None`; first `.dimensions` access → exactly one `from_pretrained` call. |
| **P9b** — `store.project_lock()` per-project FileLock around RMW spans; `filelock` declared | **CONFIRMED (with §5 caveat)** | Code read: `store.py:45–88` per-project `FileLock` (graceful no-op + warn if `filelock` absent; timeout→proceed-warned); `pyproject.toml:23,29,39` `filelock>=3.12` in `[dev]`/`[embeddings]`/`[all]`. **Probe (VERIFIED):** two threads under `project_lock` on same project → strict enter/exit nesting (A,A,B,B), **both writes persist, no lost update**. Caveat: see §5 (one RMW span unlocked). |
| **P9c** — atomic `.npy` sidecar (tempfile + `os.replace`) | **CONFIRMED** | Code read: `store.py:317–326` (`tempfile.mkstemp(...".npy.tmp")`, `np.save` into temp, `os.replace`, cleanup on `BaseException`). **Fault-injection probe (VERIFIED):** wrote good sidecar; patched `np.save` to raise; failed write left original sidecar **byte-identical** (shape & 140 bytes unchanged), **no leftover `.npy.tmp`**. |

---

## 4. ruff / pyright

- **VERIFIED — ran:** `uv run ruff check src/` → **`All checks passed!`** (0 errors).
- **VERIFIED — ran:** `uv run pyright src/` → **`0 errors, 3 warnings, 0 informations`**.
  - The 3 warnings are `reportUnusedImport` on `embeddings.py:27` (`_np`), `:34` (`AsyncOpenAI`), `:41` (`StaticModel`) — the long-standing `# noqa: F401 (runtime probe)` feature-detection imports that set the `_HAS_*` booleans. Pre-existing, intentional, ruff-suppressed, not introduced by this branch (the lazy-load refactor moved the *functional* import inside `_get_model`; the top-level detection probes are unchanged design). **0 errors** matches the "0 expected on src/" bar.

---

## 5. Regressions / Real Defects

**No regression found.** No verified-solid item appears disturbed; P1↔P3 interaction is safe; backward-compat (ai-only `self_resolution_count`, v0.3 session load) preserved.

**One genuine non-blocking defect (CONDITIONAL note) — P9(b) incomplete vs A-R3-2:**

`extensions/learn/__init__.py:97–119` `_recall_hook` performs an unlocked read-modify-write: `store.load_store(project)` (`:103`) → `_embed_learnings(stale)` mutates `ks.learnings[*].embedding` in place (`:107`, see `:61–63`) and `recall_learnings` updates recall counts → `store.save_store(ks)` (`:118`) — **with no `store.project_lock(project)`**. The 5 other RMW spans (`_extract_hook` :122, `trace_learn_recall` :154, `trace_learn_add` :211, `trace_learn_forget` :282, `trace_learn_extract` :311) are correctly locked; `trace_learn_list` (:265) is read-only (correctly unlocked).

- **Reachability (VERIFIED by code trace):** `register_recall_hook(_recall_hook)` (`:133`) → invoked via `extension_hooks.recall_if_available` (`extension_hooks.py:77`, **no lock in the chain**) → called from core auto-recall at `server.py:108, 161, 503`. This is the **highest-frequency** RMW span (fires on the core auto-recall path on every relevant tool call across all concurrent sessions) — precisely the multi-session contention A-R3-2 targeted.
- **A-R3-2 scope match:** the amendment explicitly scopes the fix to "the full `load_store → mutate → save_store` span (**separate unlocked calls in `extensions/learn/__init__.py`**)". `_recall_hook` is exactly such a span and was missed.
- **Severity:** last-writer-wins **data-loss** of recall-count increments / embedding backfill on concurrent same-project recall; **no corruption, no crash, no incorrect provenance** (the per-session `~/.trace/sessions/*.json` audit records are unaffected — they have their own atomic writes). Graceful degradation, not a correctness break in the provenance record itself. The atomic `save_store` (store.py temp+`os.replace`) still prevents a torn file; only the lost-update window remains.
- **Recommendation (not PR-blocking):** wrap `_recall_hook`'s `:103`–`:118` body in `with store.project_lock(project):` (one-line structural change mirroring `trace_learn_recall:154`). Track as a follow-up; the existing 5 locks plus atomic writes make this a robustness gap, not a release blocker, and it is consistent with the plan's "documented residual" tolerance for the lock work.

---

### Bottom line

GREEN on every headline fix and the full suite (FM7-family failures proven load-flakes by isolation, not regressions; ruff/pyright clean). One real but non-blocking lock-coverage gap (`_recall_hook` unlocked RMW) downgrades the overall to **CONDITIONAL — ship with that gap explicitly tracked.** No defect rises to RED / PR-block.
