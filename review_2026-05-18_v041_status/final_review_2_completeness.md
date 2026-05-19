# Final Review 2 — Completeness & Plan-Conformance (independent, adversarial)

**Date:** 2026-05-18 · **Branch:** `fix/v0.4.1-criticals-p1-p3` · **Reviewer:** independent pre-PR completeness/conformance verifier (no prior context).
**Method:** every claim re-derived from the working tree (`git diff d20be80` — changes are UNCOMMITTED but present), targeted file reads, inline `uv run --with rdflib python -c` PROV round-trip, fixture re-derivation, targeted test runs. No full-suite run (owned by another reviewer). Read-only; no files modified except this report; no processes signalled; no git state changed.

---

## 1. VERDICT: **GREEN** · confidence **~93%**

Every P-item, every A-R3 amendment, and M1/M2/M3 **landed correctly and completely** in the working tree. The two central regression risks the contract flagged — (a) the §3-verified-solid `ai→ai` single-actor warning being suppressed by the new gate, (b) FM25 having no ai/non-ai split to gate — are both **correctly handled** in code AND guarded by tests. P5 is **genuinely PROV-O conformant** (51 real RDF triples materialized via rdflib, not a weak-test game). No §3 verified-solid item was regressed; `server.py` and `trace_end_session` were not touched. Two minor, internally-consistent out-of-plan dev-config additions noted (non-blocking).

GREEN is conditional only on: the **other reviewer's full green `uv run pytest`** (P7 gate, not in my scope), commit + branch + PR (work is currently uncommitted), and the deferred `v0.4.1` tag being applied post-merge (correctly NOT faked now).

---

## 2. Conformance matrix

| Item | Status | Evidence (file:line) |
|------|--------|----------------------|
| **P1 — FM1 non-ai gate by ≥2 actor TYPES** | LANDED-CORRECT | `decision_tools.py:86-105`: ai→ai branch (`:87-94`) UNCONDITIONAL; non-ai branch (`:95-105`) gated by `session.is_multi_actor()`. `schema/session.py:72-92`: `distinct_actor_types()` = `{p.type for participants} ∪ {e.actor.type for events}` (TYPE set, A7 empty-participants fallback), `is_multi_actor()` = `len>=2`. NOT instances. VERIFIED. |
| **P1 — ai→ai FM1 UNCONDITIONAL (§3 no-regress)** | LANDED-CORRECT | `decision_tools.py:87-94` fires without any multi-actor guard. Guarded by unchanged `test_decision_guards.py::test_ai_self_resolves_gets_warning` (:47-64) + `test_fm1_ai_self_resolves_detected` (:725-739), both on single-actor `_make_session` — **10/10 TestSameActorWarning pass**. §3 PRESERVED. |
| **P1 — FM25 ai/non-ai split (A-R3-1)** | LANDED-CORRECT | `decision_tools.py:114-119`: `if resolved_by_type == "ai" or session.is_multi_actor():` — FM25 had NO branch originally; the split now exists, ai→ai unconditional. Guarded by `test_ai_self_resolves_fast_still_warns_fm25` (single-actor ai→ai asserts `"Decision proposed and self-resolved in"`). |
| **P1 — session-end detector gated** | LANDED-CORRECT | `session_tools.py:333-340`: same-instance push now `and session.is_multi_actor()`. The ai-only `self_resolved_ids` count (`:321-322` region) is NOT gated — preserved unconditionally. |
| **P1 — decision-audit.sh TYPE-set pre-pass (A-R3-4)** | LANDED-CORRECT | `adapters/.../hooks/decision-audit.sh:38-54` (new, ~18-line pre-pass, *larger than "line 69"*): builds `_actor_types` from participants ∪ event actor types → `multi_actor`. Gate applied at the same-instance counter; `ai_self_resolved` counter NOT gated. **12/12 hook tests pass.** |
| **M2 — exactly 2 bug-tests rewritten** | LANDED-CORRECT | (1) `test_decision_guards.py`: `test_human_self_resolves_warns` → `test_human_self_resolves_clean` (single-actor ⇒ no warn). (2) `test_v041_decision_audit_hook.py`: `test_hook_handles_session_with_human_self_resolution` → `test_hook_single_actor_human_self_resolution_not_flagged`. Exactly these two. |
| **A-R3-5 — 3 added P1 tests** | LANDED-CORRECT | (5a) `test_v041_attribution_audit.py::test_single_actor_ai_self_resolution_asymmetry` (`self_resolution_count==1`, `attribution_warning_count==0`). (5b) `test_decision_guards.py::test_ai_self_resolves_fast_still_warns_fm25` (FM25 ai→ai literal). (5c) `test_decision_guards.py::test_multi_actor_different_human_instances_clean` (decouples id-inequality from multi-actor gate). Plus `test_single_actor_system_self_resolves_clean`. |
| **M3 / evt_016 — ≥2 actor TYPES not instances** | LANDED-CORRECT | `schema/session.py:79-81` operates on `.type` only. Confirmed by waggle re-derivation: `distinct_actor_types={ai,human}` → multi-actor → count stays 2. |
| **A-R3-3 — ADR-002 D1/adapter truth-up** | LANDED-CORRECT | `docs/adr/002...md:32` "FM1 generalized to all same-instance pairs" → "FM1/FM25" + new **"Amended 2026-05-18 (Round-3 A1 / evt_016)"** para (non-ai gated, ai→ai unconditional, "Spec §3.6 ... was already correct"). `:100` adapter line now says "non-`ai` ... **in multi-actor sessions** ... single-actor exempt". |
| **P1 — CHANGELOG/docstrings match real behavior; spec §3.6 NOT weakened** | LANDED-CORRECT | `CHANGELOG.md:24,45` already stated "in multi-actor sessions"/"in a multi-actor session" — code was raised to meet them (round2 §4.1 direction), now truthful. `decision_tools.py:76-105` / `session_tools.py:325-332` docstrings accurate. `docs/specification.md` diff = **1 line only** (the §4.4 note); §3.6 (~:233) untouched/not weakened. VERIFIED. |
| **P2 — only query_tools.py:158 guarded, :340 not double-touched** | LANDED-CORRECT | `query_tools.py:163-172`: `_compute_knowledge_metrics` import wrapped `try/except ImportError` → zero sentinel (`{"total":0,...}`), shape matches the empty-store sentinel `:176-182`. `:353-357` (`health_check`/`_get_directory`) was ALREADY `try/except Exception` — NOT in the diff, not double-touched. Fail-open shape correct. |
| **P2 — delete-extension invariant test, non-destructive** | LANDED-CORRECT (minor partial) | `tests/test_v041_core_extension_boundary.py`: `monkeypatch.setitem(sys.modules, name, None)` — non-destructive, no dir moved. **1/1 pass.** *Minor:* exercises `project_summary` (the actual G2 break-point) only, not all 18 tools enumerated — pins the real regression; literal "all 18 tools function" is asserted in prose/ADR-003 + CI step, not exhaustively in-test. |
| **P3 — L9.1 test exists; counts match real fixture** | LANDED-CORRECT | `tests/test_v041_l9_1_waggle_regression.py`. **Independently re-derived from `audit_2026-05-13_waggle_session/trace_session_trace_20260513_446733.json`:** events=**28**, missing_snippet_contribution=**15**, missing_snippet_correction=**1**, same-instance self-res=**[evt_001,evt_025]**, attribution_warning_count=**2**, distinct_actor_types={ai,human}→multi-actor (P1↔P3 safe). All match assertions + FINAL L9.1 spec exactly. **2/2 pass.** |
| **P4 — DELETED duplicate ⚠️ (not "added a tier")** | LANDED-CORRECT | `session_tools.py:382-385`: the `audit_warnings.append(...orphan_discovery...)` push is **deleted** (replaced by explanatory comment). Low-severity tier already existed and is PRESERVED at `:174-181` (`orphan_discovery_hint_count` non-⚠️ render). "turned out" removed from `_DISCOVERY_PHRASES` (`:72-76`; was 4 entries, now 3); the 3 TP technical-discovery phrases kept. |
| **P5 — exporter genuinely PROV-O conformant** | LANDED-CORRECT | See §3 deep-dive. `exporters/prov_jsonld.py` rewritten to flat `@graph` node objects; rdflib parses to **51 real triples** with correct `prov:wasInvalidatedBy` / `prov:qualifiedInfluence`+`prov:atLocation` / `prov:wasInformedBy` / `prov:wasRevisionOf` + real `rdf:type prov:*` classes. 17-test rewrite asserts specific triples (not tautologies). **13/13 pass** (split + roundtrip). |
| **A-R3-7 — rdflib in [dev]** | LANDED-CORRECT | `pyproject.toml`: `rdflib>=7.0` added to `[project.optional-dependencies] dev`. |
| **P9a — lazy-load defers from_pretrained** | LANDED-CORRECT | `embeddings.py:103-128`: `__init__` no longer calls `StaticModel.from_pretrained`; `self._model=None`; new `_get_model()` defers import+`from_pretrained` to first use; `dimensions` lazy `@property`; `embed_texts` via `_get_model()`. Module-level `_HAS_MODEL2VEC` feature-probe (`:41-45`) PRESERVED (round2 §P9 caution honored). `test_construction_does_not_load_model` asserts `call_count==0`. **2/2 pass.** |
| **P9b — lock wraps FULL span, all 5 sites; per-project; filelock declared** | LANDED-CORRECT | `__init__.py`: `with store.project_lock(project):` wraps full `load→…→save` in **all 5**: `_extract_hook` (:122), `recall` (:152), `add` (:208), `forget`/remove (:282), `extract` (:310). `store.py:43-90`: `project_lock` is **per-project** (`_store_path(project)+".lock"`), graceful no-op + 1-time warn if filelock absent, timeout-proceeds. `filelock>=3.12` in `embeddings`/`all`/`dev`. `test_concurrent_adds_do_not_lose_updates` proves no lost update; `test_project_lock_is_per_project` proves keying. **3/3 pass.** Matches A-R3-2 exactly. |
| **P9c — .npy atomic** | LANDED-CORRECT | `store.py:316-332` (`save_embeddings_cache`): bare `np.save(str(path),...)` → `mkstemp(dir=path.parent)` + `np.save(fh,...)` + `os.replace` + cleanup-on-`BaseException`. **2/2 pass.** |
| **A-R3-8 — .npy severity reclass** | LANDED-CORRECT (doc) | Implemented anyway (P9c). |
| **P6 — ADR-003 single canonical home** | LANDED-CORRECT | `docs/adr/003-core-extension-boundary.md` (new, accepted): core list, delete-extension invariant CI-enforced, Tier-3-stays-extension, explicitly "documented once here, *referenced* not re-prose'd", documents `extension_status.py` placement. |
| **P6 — CONTRIBUTING+spec REFERENCE (no duplicate prose)** | LANDED-CORRECT | `CONTRIBUTING.md:75`: core list widened (`exporters/`,`scratchpad.py`,`extension_status.py`) + "see [ADR 003] ... CI-enforced" — references, no re-prose. `docs/specification.md:384`: §4.4 hard-reject + v1.1 deferral note (A9). |
| **P6 — ADR index has 002+003** | LANDED-CORRECT | `docs/adr/README.md`: rows for `[002]` AND `[003]` added (was 001-only). A-R3-6. |
| **P6 — ci.yml boundary step** | LANDED-CORRECT | `.github/workflows/ci.yml:45-46`: named step "Core/extension boundary invariant (governance ADR 003)" runs the boundary test, before main Tests step. A-R3-6 (un-CI'd gate now wired). |
| **P6 — ci.yml `uv sync --all-extras` ⇒ rdflib/filelock in CI** | LANDED-CORRECT | `ci.yml:37` `uv sync --all-extras` (pre-existing). `pyproject.toml:16` confirms `dev`/`embeddings`/`all` are `[project.optional-dependencies]` extras → `--all-extras` installs all → rdflib (dev) + filelock present in CI ⇒ PROV/lock tests do NOT silently skip. VERIFIED. |
| **P7 — CHANGELOG [0.4.1] dated + compare-links** | LANDED-CORRECT | `CHANGELOG.md:10` `In progress`→`2026-05-18`. `:172-173` `[Unreleased]`→`v0.4.1...HEAD`; new `[0.4.1]: v0.4.0...v0.4.1`. |
| **P7 — 4 local tags at correct commits** | LANDED-CORRECT | Annotated tags: v0.1.0→`7110528`, v0.2.0→`568c023`, v0.3.0→`50051ec`, v0.4.0→`0540346` — exactly the R3-specified commits; all four verified present in `git log`. |
| **P7 — v0.4.1 tag correctly DEFERRED (not faked on d20be80)** | LANDED-CORRECT | `git tag -l` = {v0.1.0,v0.2.0,v0.3.0,v0.4.0}. **No `v0.4.1` tag.** Not faked onto d20be80. Correct per FINAL P7 (defer until post-green/PR). |
| **M1 — re-run trace-mcp-init CHANGELOG callout** | LANDED-CORRECT | `CHANGELOG.md:58`: "Consumer projects with installed hooks should re-run `trace-mcp-init` to refresh `decision-audit.sh`." (`--upgrade` correctly NOT needed per R3.) |
| **extension-status — 4 modes + fail-safe, boundary-safe** | LANDED-CORRECT | `src/trace_mcp/extension_status.py` (core, NOT under extensions/): exactly 4 modes (none / OpenAI / model2vec / keyword-only); two `try/except Exception` → never raises; guarded extension import (ADR-003 pattern). Wired into `session_tools.start_session` (`:514-524`) function-locally. **6/6 pass.** |
| **P8 — FM7 NOT a code fix** | LANDED-CORRECT | `server.py` diff EMPTY; `trace_end_session`/`end_session` NOT in any diff. §3/scope honored. |
| **Scope discipline — §3 verified-solid untouched** | LANDED-CORRECT | L1.3, A6, L3.1/A10, server.py, stdin-EOF, ai→ai warnings: untouched/preserved & test-guarded. L6.x PROV: *format* fixed (the P5 mandate); architecture (deterministic blank nodes, repudiatory vs influence split) preserved & strengthened. |

---

## 3. P5 genuinely-conformant deep-dive

Constructed a session with all 3 v0.4.1 correction shapes + revision/dispatch (event-ID-target correction, URI-form correction `subagent:abc-123`, decision `revises_event_id`, tool_call `parent_event_id`+`retries_event_id`), exported via `export_prov_jsonld`, parsed with **rdflib** (`uv run --with rdflib`). Result: **51 RDF triples** (10-node `@graph`), NOT a "non-empty graph" game. Real PROV-O triples materialized:

- **`prov:wasInvalidatedBy`** ×1 — `evt_001 → evt_003_annotation` (event-ID-target → repudiatory invalidation). REAL.
- **`prov:qualifiedInfluence`** ×1 — `evt_004_annotation → _:infl_evt_004_0` (deterministic blank node, not `hash()`). REAL.
- **`prov:atLocation`** ×1 — `_:infl_evt_004_0 → "subagent:abc-123"` (URI on the Influence node). REAL.
- **`prov:wasInformedBy`** ×1 — `evt_005 → evt_002` (`parent_event_id` dispatch chain). REAL.
- **`prov:wasRevisionOf`** ×2 — `evt_002 → evt_001` (decision revision), `evt_005 → evt_001` (tool retry). REAL.
- Plus `prov:wasAssociatedWith` ×3, `prov:wasAttributedTo` ×2, `prov:used` ×1, `prov:value` ×3, `prov:startedAtTime` ×4 (xsd:dateTime).
- **`rdf:type`**: 4 `prov:Activity`, 4 `prov:Entity`, 2 `prov:Agent`, 1 `prov:Influence` — genuine PROV-O class typing.

Rewritten `test_v041_prov_ld_split.py` (218+/366−) asserts **specific triples** (`assert (TRACE.evt_001, PROV.wasInvalidatedBy, TRACE.evt_002_annotation) in g`), negative cases (corrections ≠ wasRevisionOf), and Influence-node structure — not tautologies. `test_v041_p5_prov_roundtrip.py` adds the dedicated A4 rdflib round-trip. `test_specification_conformance.py` / `test_exporters.py` PROV assertions were migrated `bundle`→`@graph` (necessary, semantics-preserving P5 collateral — not weakened, not scope creep). The exporter docstring honestly records the prior PROV-JSON (zero-triple) defect this fixes.

---

## 4. Dropped / scope-crept / §3-endangered

**Nothing dropped.** Every G1–G6, A8/A4/A9, M1/M2/M3, and all A-R3-1..8 map to a landed change. P8 correctly = no-op on code.

**§3 NOT endangered.** The single highest-risk failure class (gating suppressing the §3 `ai→ai` single-actor warning) is correctly avoided in FM1, FM25, the session-end detector, AND the bash hook; all four ai→ai paths stay unconditional and are test-guarded (`test_ai_self_resolves_gets_warning`, `test_fm1_ai_self_resolves_detected`, `test_ai_self_resolves_fast_still_warns_fm25`, `test_single_actor_ai_self_resolution_asymmetry`). `server.py`/`trace_end_session` untouched.

**Minor out-of-plan additions (non-blocking, internally consistent):**
1. `.mcp.json` — local dev MCP server invocation switched to `--with openai/numpy/model2vec --refresh` (was `--from . --refresh-package`). Out-of-plan but dev-only config, low-risk; arguably needed so the lazy-load/lock changes are exercised in this repo's live sessions. Its conformance test (`test_installation_health.py`) was correctly updated to accept both refresh forms AND made *stricter* (asserts `--from` == `.` local-only guarantee). Self-consistent, not accidental.
2. ADR-003 / extension_status.py core-list additions (`exporters/`, `scratchpad.py`, `extension_status.py`) extend beyond round1's literal "server.py, schema/, storage/, tools/" — but this is a *correct* tightening of the boundary definition (those modules are genuinely core), consistent across ADR-003 + CONTRIBUTING. Not creep.

**Minor doc nit (non-blocking):** `session_tools.py:67-71` docstring says the phrase list "dropped 'all along' and 'as it turns out'" — those were never in the `d20be80` list (only `"turned out"` was). Behavior is correct; only the docstring's historical description is imprecise.

**Targeted test runs (all green):** TestSameActorWarning 10/10; decision_audit_hook 12/12; core_extension_boundary 1/1; L9.1 2/2; prov split+roundtrip 13/13; P9 lazy/lock/atomic 7/7; extension_status 6/6; exporters+spec_conformance+attribution_audit+installation_health 137/1-skip. No full-suite run (out of scope — owned by another reviewer; the P7 green gate remains that reviewer's call).
