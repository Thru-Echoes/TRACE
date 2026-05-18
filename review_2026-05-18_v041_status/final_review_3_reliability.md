# Final Review 3 — Reliability / Edge-Case Adversarial Verification

**Reviewer:** Independent adversarial pre-PR reliability/edge-case verifier (no prior context).
**Date:** 2026-05-18 · **Branch:** `fix/v0.4.1-criticals-p1-p3` (not switched).
**Method:** read-only code reading + inline `uv run [--with rdflib/filelock/numpy] python -c` probes. No file writes/moves, no git state changes, no process signals, no MCP/trace tools.

---

## 1. VERDICT

**CONDITIONAL — confidence HIGH.**

One **real, reachable data-loss hole** was found that the remediation was specifically chartered to close and missed one span. Everything else probed is **ROBUST**. The hole is a single, localized, low-risk fix (add one `with store.project_lock(project):` to `_recall_hook`), not an architectural problem. It is **CONDITIONAL, not RED**, because: (a) the explicit mutating tools (`add`/`forget`/`extract`) ARE correctly locked; (b) the unlocked path only writes on a narrower trigger (`results or embedded`); (c) it does not crash or corrupt — it silently loses one update under same-project concurrency. But it directly defeats the stated intent of P9(b)/Round-3 A-R3-2 ("wrap the **full** load→mutate→save span"), so it should block the PR until fixed or explicitly deferred-with-rationale by the user.

---

## 2. Per-surface stress table

| Surface | Result | Detail |
|---|---|---|
| **Lock — P9(b)** | **CONDITIONAL** | `_recall_hook` (`extensions/learn/__init__.py:97-119`) does a `load_store → _embed_learnings → save_store` RMW **with NO `project_lock`**, unlike its tool twin `trace_learn_recall` (locked, :154) and `_extract_hook` (locked, :122). Lost-update **demonstrated** (see §3). Re-entrancy, multi-project, timeout, no-filelock all ROBUST (below). |
| **Exporter — PROV** | **ROBUST** | Empty session, only-agents, many mixed event-ID+URI anchors, 200-key nested dicts, retries+parent on one tool_call, unicode/pipe/newline/backslash, weird actor-id with spaces/slashes → all export with no exception and rdflib-parse to a valid graph. `_lit` safely stringifies `set`/custom-obj/`bytes` via `default=str`. One benign non-reachable edge (§4). |
| **Guard — P1 multi-actor** | **ROBUST** | All 8 server-side scenarios + 4 hook scenarios behave exactly per spec. ai→ai single-actor still warns FM1 **and** FM25 (highest-risk A-R3-1 catch holds); session-end ai-solo asymmetry holds (`self_resolution_count==1`, `attribution_warning_count==0`); solo-human / system→system / actor-id-drift → no false warn; ≥2-actor-type same non-ai id → warns (true evt_025); ≥2-actor diff human ids → no warn; suppress env honored. |
| **Fail-safe** | **ROBUST** | `extension_status` returns a string when `get_embedding_provider` raises AND when `extensions.learn` is absent — never raises. P2 `_compute_knowledge_metrics`/`project_summary` return zero-sentinel + full dict with extension import blocked. PROV/lock tests use `pytest.importorskip` → **SKIP not ERROR** when rdflib/filelock absent (verified by code + the lock test's non-destructive `monkeypatch.setitem(sys.modules,"filelock",None)`). |
| **.mcp.json + tags** | **ROBUST** | `.mcp.json` valid JSON, `uvx --from .` local (no PyPI ref / no `==` / no index). Exactly 4 **annotated** tags v0.1.0→`7110528`, v0.2.0→`568c023`, v0.3.0→`50051ec`, v0.4.0→`0540346` — match the Round-3-amendment target commits. **v0.4.1 NOT present** (correctly deferred per P7). `git status` clean except expected untracked (review dir, notes/, docs PNGs, new test files, ADR-003, extension_status.py) + expected modified P1–P9 files. |
| **Collateral** | **ROBUST** | `trace_export` works on all 3 formats; prov-jsonld still returns `str` and rdflib-parses (export_tools shape unchanged). `server.py` imports clean (FastMCP). `extension_status` import in `session_tools.py` is **lazy** (inside `start_session`, not module-top) — boundary-safe. `scratchpad.py` imports clean. `decision-audit.sh` passes `bash -n` and its guard mirrors `is_multi_actor()` exactly. P9(c) atomic npy: clean on success and on mid-write failure (temp cleaned, original intact). Legacy empty session (env=None, 0 participants/events) audit does not crash. `ruff` clean on all modified key files; all 7 new test modules collect (18 tests, no import errors). |

---

## 3. Deadlock / crash / data-loss / false-positive findings

### FINDING R3-1 (DATA-LOSS · severity HIGH · CONDITIONAL-blocking)

**Lost update via the unlocked `_recall_hook` save.**

`extensions/learn/__init__.py:97-119` — the auto-recall hook:

```python
async def _recall_hook(project, context, tags, limit):
    ks = store.load_store(project)          # NO project_lock
    ...
    if results or embedded:
        store.save_store(ks)                # writes the SHARED store, UNLOCKED
    return results
```

This hook is invoked by `server.py` on **auto-session start (:108)**, **decision propose (:161)**, and **contribution (:503)** — high-frequency operations across all concurrent sessions. The sibling tool `trace_learn_recall` (:154) and `_extract_hook` (:122) DO take `store.project_lock`; `_recall_hook` does not. Round-3 amendment A-R3-2 explicitly mandated locking *"the full `load_store → mutate → save_store` span"* — this span was missed.

**Demonstrated** (deterministic in-process interleave): a locked `trace_learn_add` that commits "writer learning B" while `_recall_hook` holds a stale view → `_recall_hook`'s unlocked `save_store` clobbers B; final store contains only the seed learning. **"writer learning B" is permanently lost.**

Trigger window: `_recall_hook` only saves when `results or embedded`; `embedded=True` on the first recall after new learnings exist (cold embedding backfill) — common in practice. Requires two sessions on the **same project** (15+ projects share per-project stores; 8+ concurrent sessions). Real and reachable, narrower than a fully-unlocked store.

**Fix (one line, low-risk):** wrap the `_recall_hook` body in `with store.project_lock(project):` exactly as `trace_learn_recall` already does. No deadlock risk — `_recall_hook` does not nest `project_lock` (verified: no RMW call site nests; hooks and tools are disjoint entry points).

### No deadlock found

`project_lock` is **NOT re-entrant** (`py-filelock` 3.25.2; each call constructs a fresh `FileLock(path)`, no `is_singleton`). A *hypothetical* nested same-project acquisition does **not deadlock** — it blocks for the full timeout (default 15s, `TRACE_LOCK_TIMEOUT` override verified at 1s/2s) then proceeds unlocked, yielding **exactly once** (verified). **However, no actual RMW call site nests `project_lock` for the same project** (all 5 sites — `add`/`_extract_hook`/`recall`/`forget`/`extract` — are flat single `with` blocks; early `return`s inside the `with` correctly release; hooks vs tools are disjoint server entry points). So the non-re-entrancy is latent, not triggered. Worth a one-line code comment noting `project_lock` is non-re-entrant so a future caller does not nest it, but not blocking.

### No crash / no false-positive found

- No-filelock path: yields exactly once per call, one-time `_warned_no_filelock` warning, RMW still functions, no crash.
- Timeout→proceed branch: single yield, no double-yield, no exception.
- Two projects independently lockable while one held: confirmed (per-project keying works).
- P1 guard: zero false positives across solo-human / system→system / actor-id-drift; zero false negatives on true evt_025 / ai→ai single-actor (the §3-verified-solid warnings still fire). The one apparent "mismatch" in probing (multi-actor ai→ai counted in both `self_resolution_count` and `attribution_warning_count`) is **documented intended behavior** per `session_tools.py:157-166` ("May overlap … kept separate so v0.3 consumers … don't see a behavior change"; `attribution_warning_count` is explicitly "(any actor type)") — **not a bug**.

---

## 4. Broader-health notes

- **PROV non-reachable edge (informational, not a finding):** a session id containing a colon/space (e.g. `"weird:id with space"`) yields a CURIE that rdflib silently drops → 0-triple graph (no exception). Not reachable: session IDs are always machine-generated `trace_YYYYMMDD_hex` (`_generate_session_id`). Actor ids with spaces/slashes (which *are* user-supplied) still parse correctly (12 triples). No action required; optionally note the session-id-shape assumption in the exporter docstring.
- **Dependency placement is correct:** `filelock>=3.12` is in `embeddings`/`all`/`dev` extras (not base) — matches "optional, degrade gracefully." `rdflib>=7.0` is `[dev]`-only (test-only, zero runtime risk) per A-R3-7.
- **Referential integrity now covers `parent_event_id`** (`session_tools.py:630-631`) — prevents dangling `prov:wasInformedBy` edges. Good hardening, no regression.
- **`decision-audit.sh`** is bash-3.2 safe (no `mapfile`; `read` parse), fail-open on unparseable JSON, and its `EXPLICIT_ABSENCE` set + multi-actor type-set pre-pass exactly mirror the server. Adopter asset is consistent with core.
- **Boundary integrity intact:** `extension_status` lives in core, probes the extension via guarded imports, and its import in `session_tools` is lazy — deleting `extensions/learn/` does not break session start or any of the 18 core tools (P2 verified).

---

## 5. Bottom line for the PR decision

Ship-blocking item: **exactly one** — R3-1 (unlocked `_recall_hook` save → lost update). It is a one-line fix that the remediation's own mandate (A-R3-2) requires. Everything else across lock / exporter / guard / fail-safe / mcp+tags / collateral is **ROBUST**. Recommend: apply the one-line `project_lock` wrap to `_recall_hook` (and ideally a non-re-entrancy comment on `project_lock`), then this surface is GREEN. Absent that fix, **CONDITIONAL** — do not open the PR claiming P9(b) closes the lost-update class, because it leaves the highest-frequency RMW path (auto-recall) unprotected.
