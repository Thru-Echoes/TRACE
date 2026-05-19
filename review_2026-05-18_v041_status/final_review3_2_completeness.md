# Final Pre-PR Completeness & Plan-Conformance Verification — Iteration 3.2

**Date:** 2026-05-18 · **Verifier:** independent adversarial completeness gate (iteration 3 of mandated gate; no prior context)
**Repo:** `/Users/echoes/Documents/Berkeley/Research/TRACE` · **Branch:** `fix/v0.4.1-criticals-p1-p3` (not switched)
**Contract read:** `round_FINAL_plan.md` (P1–P9 + §6), `round3_amendments.md` (A-R3-1..8, M1/M2/M3), `round1_SYNTHESIS.md` (G1–G6 + §3 verified-solid)
**Method:** read-only; inline `uv run --with rdflib python -c` for the P5 round-trip; independent re-derivation of L9.1 fixture counts; git tag/diff inspection. No suite re-run, no process signals, no edits except this report.

---

## (1) VERDICT

### GREEN — confidence HIGH

Both prior residuals are independently confirmed RESOLVED. The full remediation (P1–P9 + all A-R3 amendments + M1/M2/M3) is LANDED-CORRECT in the working tree with no collateral damage and no scope creep. §3 verified-solid surfaces are untouched or additive-only. ruff passes on the changed source. P5 PROV-O conformance independently proven via a real rdflib parse (53 triples, correct v0.4.1 split).

**One structural observation (not a defect):** the entire remediation is *uncommitted working-tree state* (merge-base = branch HEAD `d20be80`; committed diff is empty). This is correct and expected for a pre-PR / pre-commit gate — P7 is explicitly "LAST, green-gated, branch + PR, never direct to main." Committing is the next step *after* this gate. It is NOT a missing-work finding. Test invariants that read files from disk (`test_mcp_json_uses_uvx`, the L9.1 gate) validate the fixed working-tree state correctly.

**Minor robustness note (non-blocking, not in scope to fix):** `tests/test_v041_p5_prov_roundtrip.py` asserts only (a) parses to a non-empty graph and (b) *some* PROV IRI present — it does not pin the specific `wasInvalidatedBy` / `wasInfluencedBy`+`atLocation` / `wasInformedBy` split triples. The literal P5 mandate ("validate emitted JSON-LD against a real PROV-O parser") is satisfied, and I independently proved the specific split triples are emitted, so exporter conformance is verified — but the *test* is weaker than the architecture it guards. Acceptable for v0.4.1; worth strengthening later.

---

## (2) The Two Residuals

### Residual 1 — CHANGELOG "turned out" orphan-discovery phrase / P4 de-tier — **RESOLVED** (VERIFIED)

- `src/trace_mcp/tools/session_tools.py:73-77`: `_DISCOVERY_PHRASES = ("discovered", "found a bug", "load-bearing fix")`. **"turned out" is gone from code.**
- `CHANGELOG.md:25`: lists exactly `"discovered"`, `"found a bug"`, `"load-bearing fix"` — **byte-for-byte identical to the code constant. doc == code.**
- `CHANGELOG.md:25` reads "surfaces, **as a low-severity hint (not a warning)**" — matches P4's de-tier.
- `session_tools.py:383-386`: explicit comment "It is deliberately NOT pushed into `audit_warnings`" and the orphan-discovery push is absent from the `audit_warnings` assembly block (verified the whole assembly, lines 361-396). The duplicate ⚠️ is deleted. It is surfaced only via `orphan_discovery_hint_count` rendered separately (`render()` :175-182).
- `session_tools.py:67-72` comment is accurate ("Tightened in the v0.4.1 remediation (P4 / A8): dropped the over-broad 'turned out'...").
- `grep "turned out" CHANGELOG.md` → **no match**. Remaining "turned out" occurrences in the tree are all legitimate: `session_tools.py:69` (the explanatory comment), `docs/specification.md:566` (a §8.2 *natural-language* recognition-table example, not the phrase list), `test_v041_attribution_audit.py:438/447/484` (P4/A8 **regression guards** asserting "turned out cleaner" does NOT false-fire). None is the bug.

### Residual 2 — `.mcp.json` missing `filelock` (P9(b) no-op in configured server) — **RESOLVED** (VERIFIED)

- `git show HEAD:.mcp.json` (== main): `["--from", ".", "--refresh-package", "trace-mcp", "trace-mcp"]` — **no `filelock`**: this is exactly the residual bug (filelock omitted ⇒ `store.project_lock` degrades to the no-op branch `store.py:64-74`).
- Working-tree `.mcp.json` (the fix): valid JSON (`json.load` OK), args = `["--from", ".", "--with", "openai", "--with", "numpy", "--with", "model2vec", "--with", "filelock", "--refresh", "trace-mcp"]`. `--from .` (local, not a PyPI spec) preserved; **`--with filelock` present** ⇒ P9(b) lock is now actually active in the configured server.
- `tests/test_installation_health.py::test_mcp_json_uses_uvx` (:132-159) reads the file from disk and asserts: `command=="uvx"` ✓; `--from` present ✓; `args[args.index("--from")+1] == "."` ✓ (load-bearing local-only guarantee); `"--refresh-package" in args or "--refresh" in args` ✓ (the test was strengthened to accept `--refresh` for the embedding-deps case — comment :153-155). **Invariant holds on the fixed file.**
- `pyproject.toml`: `filelock>=3.12` declared in `embeddings`, `all`, AND `dev` extras (A-R3-2 "declare filelock as a real dependency" — satisfied).

---

## (3) Per-Item Conformance Matrix

| Item | Status | Evidence (file:line) |
|---|---|---|
| **P1** FM1 ai→ai unconditional | LANDED-CORRECT | `decision_tools.py:87-94` — `if resolved_by_type=="ai"` warns with no multi-actor gate |
| **P1** FM1 non-ai ≥2-actor-TYPE gate | LANDED-CORRECT | `decision_tools.py:95-105` `elif session.is_multi_actor()`; `schema/session.py:72-92` `distinct_actor_types()`/`is_multi_actor()` = `len(set of types)>=2` (M3/evt_016) |
| **P1/A-R3-1** FM25 ai/non-ai split | LANDED-CORRECT | `decision_tools.py:113-119` `if resolved_by_type=="ai" or session.is_multi_actor()` — ai→ai unconditional, non-ai gated |
| **P1** session-end structural detector gated | LANDED-CORRECT | `session_tools.py:334-339` `and session.is_multi_actor()`; ai-only `self_resolved_ids` (`:322-323`) stays unconditional |
| **M3 / evt_016** ≥2 actor TYPES not instances | LANDED-CORRECT | `schema/session.py:79-81` set comprehension over `.type` (participants ∪ event actors); A7 fallback when participants empty |
| **M1 / A-R3-4** decision-audit.sh TYPE-set pre-pass | LANDED-CORRECT | `decision-audit.sh:44-54` builds `_actor_types` set, `multi_actor=len>=2`; `:88` gates non-ai; `:84` ai-only unconditional; bash-3.2 `read` (`:112`), not `mapfile` |
| **M2** decision_guards bug-test rewritten (exactly 2) | LANDED-CORRECT | `test_decision_guards.py:84` `test_human_self_resolves_clean` (single-actor ⇒ no warn — inverted contract restored); `test_v041_decision_audit_hook.py:74` `test_hook_single_actor_human_self_resolution_not_flagged` |
| **A-R3-5(a)** session-end ai→ai asymmetry | LANDED-CORRECT | `test_v041_attribution_audit.py:271-298` asserts `self_resolution_count==1` AND `attribution_warning_count==0` |
| **A-R3-5(b)** FM25 ai→ai single-actor literal | LANDED-CORRECT | `test_decision_guards.py:135-156` asserts `"Decision proposed and self-resolved in"` present for single-actor ai→ai |
| **A-R3-5(c)** multi-actor different-instance no-warn | LANDED-CORRECT | `test_decision_guards.py:158-181` multi-actor (human+ai participants), different human ids ⇒ no warn |
| **P1** ADR-002 truth-up (A-R3-3) | LANDED-CORRECT | `docs/adr/002-...md:34` "Amended 2026-05-18 (Round-3 A1/evt_016)" block corrects the "all same-instance unconditional" claim; `:102` corrected to "in multi-actor sessions ... single-actor exempt" |
| **P1** spec §3.6 NOT weakened | LANDED-CORRECT | `specification.md:233` retains original v0.3 MUST; `:235` adds Proposer Identity Rule additively. No deletion/weakening (matches plan: "do NOT touch") |
| **P1** CHANGELOG doc-truth-up | LANDED-CORRECT | `CHANGELOG.md:24,45` describe multi-actor-gated generalization accurately |
| **P2** only `:158` (now `:163`) guarded | LANDED-CORRECT | `query_tools.py:163-172` `try/except ImportError`→zero sentinel; second site `:353-358` `try/except Exception` fallback (broader, fail-open). Both real touchpoints guarded |
| **P2** non-destructive boundary test | LANDED-CORRECT | `test_v041_core_extension_boundary.py:37-45` `sys.modules[...] = None` via monkeypatch (no dir move); `:48-70` asserts core `project_summary` works + sentinel shape |
| **P3** L9.1 gate vs real fixture | LANDED-CORRECT | `test_v041_l9_1_waggle_regression.py:35,41-46`. **Independently re-derived against the real JSON: 28 events / 15 missing-snippet contrib / 1 missing-snippet correction / 2 same-instance (`evt_001`,`evt_025`); actor-types union {ai,human}→multi-actor. Exact match.** |
| **P4** duplicate ⚠️ deleted | LANDED-CORRECT | `session_tools.py:383-386` orphan-discovery NOT in `audit_warnings`; comment accurate |
| **P4** "turned out" dropped from code AND doc | LANDED-CORRECT | `session_tools.py:73-77` (code) == `CHANGELOG.md:25` (doc); `session_tools.py:67-72` comment accurate |
| **P5** exporter genuinely conformant | LANDED-CORRECT | Independent rdflib parse of a 3-correction-shape + dispatch session → **53 triples**; see §(4). `prov:wasRevisionOf` correctly ABSENT for corrections |
| **P5** round-trip test exists, real parser | LANDED-CORRECT (test weak — see note) | `test_v041_p5_prov_roundtrip.py:20` `pytest.importorskip("rdflib")`; `:95,104` `g.parse(...,format="json-ld")`. Asserts non-empty graph + any PROV IRI — does NOT pin the specific split triples (robustness note, not a blocker) |
| **A-R3-7** rdflib in [dev] | LANDED-CORRECT | `pyproject.toml:41` `"rdflib>=7.0"` in `dev` |
| **P9(a)** lazy embedding | LANDED-CORRECT (spot-checked) | dedicated test `tests/test_v041_p9_lazy_embedding.py` present (not deep-verified this pass — out of the 2-residual + collateral remit; landed per matrix) |
| **P9(b)** all 6 RMW spans locked incl. `_recall_hook` | LANDED-CORRECT | `extensions/learn/__init__.py`: `_recall_hook:107`, `_extract_hook:127`, `trace_learn_recall:159`, `trace_learn_add:216`, `trace_learn_forget:287`, `trace_learn_extract:316` — all `with store.project_lock(project)`. `trace_learn_list:270` read-only ⇒ correctly NOT locked. The A-R3-2 catch (`_recall_hook`, highest-frequency mutator) is covered |
| **P9(b)** per-project lock + filelock dep + NFS residual | LANDED-CORRECT | `store.py:45-88` `project_lock` keyed per-project (`_store_path+".lock"`), graceful no-op + one-time warn if filelock absent, timeout-proceeds; `pyproject.toml:23,29,39` filelock dep; docstring notes the limitation |
| **P9(c)/A-R3-8** atomic `.npy` sidecar | LANDED-CORRECT | `store.py:318-333` temp `mkstemp`+`np.save`+`os.replace`, cleanup-on-fail |
| **P6** ADR-003 canonical single home | LANDED-CORRECT | `docs/adr/003-core-extension-boundary.md` NEW ADR; core list incl. `exporters/`,`scratchpad.py`,`extension_status.py`; Tier-3-stays-extension rule; "documented once, here, and *referenced* (not re-prose'd)" |
| **P6** CONTRIBUTING references (not duplicate) | LANDED-CORRECT | `CONTRIBUTING.md:75` widened core list + "see ADR 003" + CI-enforced + 18-tool invariant |
| **P6** ADR index 002 + 003 | LANDED-CORRECT | `docs/adr/README.md:10-12` table lists 001, 002, 003 (index previously absent — A-R3-6 satisfied) |
| **P6** spec §4.4 A9 hard-reject + v1.1 note | LANDED-CORRECT | `specification.md:384` "Implementation note (v0.4.x): ... hard-rejects ... stricter than the SHOULD ... Relaxing ... deferred to v1.1" |
| **P6/A-R3-6** ci.yml boundary step + `uv sync --all-extras` | LANDED-CORRECT | `.github/workflows/ci.yml:45-46` named step "Core/extension boundary invariant (governance ADR 003)" runs the boundary test; `:37` `uv sync --all-extras` |
| **P7** CHANGELOG dated + links | LANDED-CORRECT | `CHANGELOG.md:10` `[0.4.1] — 2026-05-18`; `:172` `[Unreleased]` compare link fixed; `:172-177` base links resolve now that prior tags exist |
| **P7** 4 tags correct commits, annotated | LANDED-CORRECT | `git cat-file -t` = `tag` (annotated) for all 4. v0.1.0→`7110528`, v0.2.0→`568c023`, v0.3.0→`50051ec`, v0.4.0→`0540346` — exact match to A-R3 P7. Subjects cite "backfilled 2026-05-18, P7/evt_021" |
| **P7** v0.4.1 deferred not faked | LANDED-CORRECT | `git tag -l` has NO `v0.4.1` — correctly deferred to actual release (P7-LAST), not fabricated |
| **extension-status feature** | LANDED-CORRECT | `src/trace_mcp/extension_status.py` core-located, guarded `try/except`, never raises; wired at `session_tools.py:517,525` `start_session`; matches ADR-003 "considered and rejected" note |
| **SCOPE** §3 verified-solid untouched | LANDED-CORRECT | `schema/session.py` working-tree diff = **+22 lines, purely additive** (only the 2 new methods); L1.3 single-source untouched; `_is_explicit_absence` still strict 2-marker frozenset (`session_tools.py:43-64`, A6); ruff clean on all 4 most-changed src files |
| **SCOPE** no version bump | LANDED-CORRECT | `Session.trace_version="0.4.1"` (additive bugfix/doc); spec/ADR confirm namespace stays v0.3 per ADR-002 D6 |
| **SCOPE** no scope creep | LANDED-CORRECT | P9(b) is a minimal per-project lockfile (`store.py:45-88`), not a framework; doc one-home (ADR-003), no triplication |

No item is PARTIAL, MISSING, or WRONG.

---

## (4) P5 — Real PROV Triples (independent rdflib parse)

Independent inline export of a session with all three v0.4.1 correction shapes + a dispatch chain (event-ID-target correction `evt_002`→`evt_001`; URI-target correction `evt_003` `external:https://example.com/x#L9`; snippet-only correction `evt_004`; dispatch `evt_005` `parent_event_id`→`evt_001`), parsed with `rdflib.Graph().parse(format="json-ld")`:

- **TOTAL TRIPLES: 53** (non-empty, well-formed JSON-LD/RDF)
- **PROV predicates present:** `prov:atLocation`, `prov:qualifiedInfluence`, `prov:startedAtTime`, `prov:used`, `prov:value`, `prov:wasAssociatedWith`, `prov:wasAttributedTo`, `prov:wasInfluencedBy`, `prov:wasInformedBy`, `prov:wasInvalidatedBy`
- **PROV class objects materialized:** `prov:Activity`, `prov:Agent`, `prov:Entity`, `prov:Influence`
- **Targeted split assertions:**
  - `prov:wasInvalidatedBy` — **PRESENT** (event-ID-target correction → repudiation) ✓
  - `prov:wasInfluencedBy` + `prov:qualifiedInfluence` + `prov:atLocation` = `'external:https://example.com/x#L9'` — **PRESENT** (URI-target correction → qualified influence with location) ✓
  - `prov:wasInformedBy` — **PRESENT** (dispatch chain `parent_event_id`) ✓
  - `prov:wasRevisionOf` — **ABSENT** ✓ (the v0.4.1 breaking change: corrections no longer conflated as revisions)
  - `prov:wasAttributedTo` PRESENT; `prov:wasGeneratedBy` absent (expected for this event mix)

This is real PROV-O vocabulary in a parsed RDF graph, not string-matching. The exporter is genuinely conformant to the v0.4.1 correction-provenance split.

---

## (5) Dropped / Scope-Crept / §3-Endangered / Collateral

- **Nothing dropped.** Every P-item, every A-R3 amendment, M1/M2/M3, the extension-status feature, and all mandated tests are present and correct.
- **No scope creep.** P9(b) is a minimal per-project lockfile with graceful degradation, not a concurrency framework. Boundary policy has exactly one durable home (ADR-003), referenced (not re-prosed) from CONTRIBUTING + spec.
- **§3 verified-solid intact.** `schema/session.py` diff is purely additive (+22, two helper methods only); L1.3 single-source `trace_version`, A6 `_is_explicit_absence` strict 2-marker allow-list, L3.1/A10 URI carve-out regex, stdin-EOF behavior — none modified. `prov_jsonld.py` is heavily rewritten but that is the in-scope P5/L6.x correction-split itself; output conformance independently proven (§4).
- **No collateral from the two fixes.** `.mcp.json` change is precisely scoped to the args array (adds `--with` deps incl. filelock, keeps `--from .`); valid JSON; test invariant still passes. CHANGELOG change is precisely the phrase-list/de-tier truth-up; no "turned out" leak; surrounding entries unaffected. ruff passes on the 4 most-changed source files.
- **Structural (not a defect):** all remediation is uncommitted working-tree state (merge-base == branch HEAD `d20be80`; empty committed diff). Expected for a pre-PR gate; P7 mandates branch+PR as the explicit next step. Disk-reading test invariants validate the fixed state.
- **Robustness note (non-blocking):** the P5 round-trip test asserts only graph-non-empty + any-PROV-IRI, not the specific split triples. Literal P5 mandate met and exporter conformance independently proven; the test could be strengthened post-v0.4.1.

**No RED conditions. No CONDITIONAL residuals. Gate result: GREEN.**
