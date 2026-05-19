# Final Pre-PR Completeness & Plan-Conformance Review — Iteration 2

**Reviewer:** Independent adversarial completeness/plan-conformance verifier (no prior context).
**Date:** 2026-05-18 · **Branch:** `fix/v0.4.1-criticals-p1-p3` (not switched).
**Method:** Opened every changed file on this branch; ran the targeted P1/P3/P5/P9/boundary
test files (NOT the full suite — owned by another reviewer); independent inline
`uv run --with rdflib` PROV export+parse probe; independent recomputation of the waggle
fixture counts; `ruff`/`pyright`; read-only `git` only. No file edited except this report.
No process signalled. No MCP/trace tools.

---

## 1. VERDICT

**CONDITIONAL — confidence ~93%.**

The remediation is **substantively complete and correct**. The recent fix (`_recall_hook`
lock + `session_tools.py` comment) is **correct and caused zero collateral** — the other 5
RMW spans and their semantics are intact. P9(b) is now genuinely complete (all 6 spans
locked). Every other P-item, A-R3 amendment, and M1/M2/M3 landed correctly in the working
tree. P5 is **genuinely conformant** (verified by independent rdflib parse → 34 real RDF
triples, all v0.4.1 split predicates present; rewritten tests assert real triple
membership, not tautologies). §3 verified-solid items are untouched. Static analysis clean
(ruff 0, pyright 0 errors / 3 pre-existing probe-import warnings).

**Why CONDITIONAL not GREEN — two non-blocking items the user/PR author should resolve:**

1. **DOC DEFECT (real, low-severity, trust-class): `CHANGELOG.md:25`** still documents the
   orphan-discovery hint as detecting `"turned out"` — but P4 deliberately dropped that
   phrase from `_DISCOVERY_PHRASES` (`session_tools.py:73-77`). The CHANGELOG now describes
   a behavior the code no longer has — the exact G5-class "docs claim behavior code lacks"
   problem this remediation set out to eliminate, merely inverted. **One-line fix**: drop
   `"turned out"` from the CHANGELOG.md:25 phrase enumeration. (`specification.md:566` also
   contains "X turned out to be Y" but that is §8.2 *recognition-table guidance* for human
   speakers, not a claim about the code constant — acceptable, lower concern.)

2. **SCOPE / RUNTIME-ACTIVATION GAP (not a code defect): `.mcp.json`** was modified
   (outside the explicit P1–P9 plan) to add `--with openai/numpy/model2vec` but **omits
   `--with filelock`**. The lock code is correct and degrades gracefully, but the TRACE
   MCP server *this very repo configures* (and 15 sibling projects via `uvx --from .`)
   would run **without `filelock`**, so `project_lock` is a warned no-op there — the P9(b)
   fix is implemented but not *active* in the configured runtime. Mitigated by the
   intentional graceful-degradation design; arguably P7/deployment territory. Recommend
   adding `--with filelock` to `.mcp.json` for parity with the P9(b) intent.

Neither blocks the PR on correctness grounds; both are 1-line follow-ups. No RED findings.

> **State note (not a defect):** the entire remediation is in the **working tree
> (uncommitted)** — `HEAD == main == merge-base == d20be80`; the branch has zero commits.
> This is the expected pre-PR gate state (P7 — commit/tag/PR — is explicitly LAST and
> green-gated). Files ARE the proof of completeness per the mission ("plan text is NOT
> proof it shipped"); commits are P7's concern. Flagged for awareness only.

---

## 2. P9(b) — ALL 6 RMW SPANS LOCKED (confirmation)

Every shared-knowledge-store read-modify-write span in
`src/trace_mcp/extensions/learn/__init__.py` wraps its full `load → … → save` in
`store.project_lock(project)`:

| # | Span | Lock site | Span covered | Status |
|---|------|-----------|--------------|--------|
| 1 | `trace_learn_add` | `:216` | load `:217` → mutate `:219-243` → embed `:244` → save `:245` | LOCKED |
| 2 | `_extract_hook` | `:127` | load `:128` → extract `:130` → embed `:134` → save `:135` | LOCKED |
| 3 | `_recall_hook` **(the fix)** | `:107` | load `:108` → embed `:112` → recall `:113` → save `:123` | LOCKED |
| 4 | `trace_learn_recall` | `:159` | load `:160` → embed `:168` → recall `:169` → save `:179` | LOCKED |
| 5 | `trace_learn_forget` | `:287` | load `:288` → remove `:289` → save `:292` | LOCKED |
| 6 | `trace_learn_extract` | `:316` | load `:317` → extract `:320-332` → embed `:338` → save `:339` | LOCKED |

`trace_learn_list` (`:270`) is read-only (no `save_store`) and correctly **not** locked.

`store.project_lock` (`store.py:45-89`): **per-project** (`_store_path(project)+".lock"`
`:77`, not whole-dir); **graceful-no-filelock** (try-import `:62-74`, one-time warn, `yield`
no-op); **timeout-proceed** (`:76` `TRACE_LOCK_TIMEOUT` default 15s; `:81-88` catch
`Timeout`, warn, `yield`). `filelock>=3.12` declared in `pyproject.toml` `[embeddings]`
(`:23`), `[all]` (`:29`), `[dev]` (`:39`).

**Collateral check (the fix touched __init__.py + one comment):** the 5 non-`_recall_hook`
spans are byte-identical in lock structure/semantics to what the plan mandated — verified
by reading each. `session_tools.py` change was comment-only on the docstring side (the
`_DISCOVERY_PHRASES` comment now matches the tuple — old comment was self-contradictory).
**No collateral.** P9(b) lock tests 3/3 pass; the concurrency test uses 2 real threads +
a widened race window and asserts no lost update.

---

## 3. PER-ITEM CONFORMANCE MATRIX

| Item | Status | Evidence (file:line) |
|------|--------|----------------------|
| **P1 FM1 ai→ai unconditional** | LANDED-CORRECT | `decision_tools.py:86-94` — `if resolved_by_type=="ai":` fires inside `is_self_resolution and not suppress`, NO multi-actor gate |
| **P1 FM1 non-ai ≥2-actor-TYPES gate (M3/evt_016)** | LANDED-CORRECT | `decision_tools.py:95-105` `elif session.is_multi_actor():`; `session.py:83-92` `is_multi_actor` = `len(distinct_actor_types())>=2`; `:72-81` union of `participants.type` ∪ event `actor.type` (TYPES, not ids; empty-participants fallback automatic) |
| **P1 FM25 split (A-R3-1, highest-risk)** | LANDED-CORRECT | `decision_tools.py:114-119` `if resolved_by_type=="ai" or session.is_multi_actor():` — ai→ai FM25 fires unconditionally, non-ai gated. The §3-drop risk is closed; A-R3-5 literal-string test guards it |
| **P1 session-end structural detector** | LANDED-CORRECT | `session_tools.py:334-339` adds `and session.is_multi_actor()`; ai→ai still surfaced unconditionally via `self_resolved_ids` `:320-323` |
| **P1 decision-audit.sh TYPE-set pre-pass (A-R3-4)** | LANDED-CORRECT | `decision-audit.sh:38-54` builds `_actor_types` from participants ∪ event actor types → `multi_actor`; `:83-88` gates generalized check `if multi_actor and …`; ai-only metric ungated; no `mapfile` (bash-3.2 safe) |
| **P1 spec §3.6 NOT weakened** | LANDED-CORRECT | `specification.md:233` unchanged ("MUST NOT … when the workflow involves multiple actors … In single-actor workflows …"); `:235` Proposer Identity Rule intact. Code rose to the spec, not the reverse |
| **P1 ADR-002 truth-up (A-R3-3)** | LANDED-CORRECT | `002-…additions.md:34` "Amended 2026-05-18 (Round-3 A1 / evt_016)" reconciles the `:32` "at log time AND session-end" / "all same-instance" self-contradiction; `:102` hook described as multi-actor-gated |
| **M2 — exactly 2 bug-tests rewritten** | LANDED-CORRECT | `test_decision_guards.py`: `test_human_self_resolves_warns`→`test_human_self_resolves_clean`, assertion inverted (single-actor ⇒ NO warn). `test_v041_decision_audit_hook.py`: `…_human_self_resolution`→`…_single_actor_human_self_resolution_not_flagged`, inverted |
| **M2 + A-R3-5 — added tests** | LANDED-CORRECT | `test_decision_guards.py`: `test_single_actor_system_self_resolves_clean`, `test_ai_self_resolves_fast_still_warns_fm25` (FM25 §3 guard), `test_multi_actor_different_human_instances_clean`. `test_v041_attribution_audit.py`: `test_single_actor_human_self_resolution_no_warning`, `test_single_actor_ai_self_resolution_asymmetry` (self_res==1, attr_warn==0). `test_v041_decision_audit_hook.py`: `test_hook_multi_actor_human_self_resolution_flagged`. **All 108 P1 tests pass** |
| **M3 / evt_016 (≥2 actor TYPES)** | LANDED-CORRECT | `session.py:72-81` operates on `.type` only; ADR-002:34 + ADR-003 + spec consistent |
| **M1 (`decision-audit.sh` mirror)** | LANDED-CORRECT | See P1 decision-audit.sh row; CHANGELOG.md:58 re-run `trace-mcp-init` callout present |
| **P2 — only `:158` guarded, `:340` untouched** | LANDED-CORRECT | `query_tools.py:158` now `try: from …learn.store import load_store / except ImportError: return {zero-sentinel}`. Diff shows ONLY this site; `:340` not in diff. `test_v041_core_extension_boundary.py` (sys.modules block, non-destructive) passes |
| **P3 — L9.1 gate vs REAL fixture** | LANDED-CORRECT | `test_v041_l9_1_waggle_regression.py` loads real `trace_session_trace_20260513_446733.json`. Independent recompute: 28 events / 15 missing-snip contrib / 1 missing-snip corr / 2 same-instance ({evt_001,evt_025}) / actor types {ai,human}⇒multi-actor. Test asserts exactly these. 2/2 pass post-fix |
| **P4 — deleted duplicate ⚠️, "turned out" dropped, comment accurate** | LANDED-CORRECT | `session_tools.py`: the `if orphan_discovery_ids: audit_warnings.append(...)` block **deleted** (`:383-386` now explanatory comment); `_DISCOVERY_PHRASES` `:73-77` = `("discovered","found a bug","load-bearing fix")` (no "turned out"); comment `:67-72` corrected (old one falsely said it dropped "all along"/"as it turns out" while keeping "turned out") |
| **P5 — exporter GENUINELY conformant** | LANDED-CORRECT (scope-expanded, justified) | See §4 — independent rdflib probe + 13 passing predicate-level tests |
| **P6 — ADR-003 single canonical home** | LANDED-CORRECT | `docs/adr/003-core-extension-boundary.md` (NEW, A-R3-6): cites evt_002, core list, binding CI invariant, Tier-3 scope; Consequences explicitly "documented once … referenced (not re-prose'd)" |
| **P6 — ADR index 002+003** | LANDED-CORRECT | `docs/adr/README.md` now lists 001, **002**, **003** (002 was absent before — A-R3-6) |
| **P6 — CONTRIBUTING references, not duplicate** | LANDED-CORRECT | `CONTRIBUTING.md` core list widened (`exporters/`, `scratchpad.py`, `extension_status.py`), links ADR-003, notes CI-enforcement (C7) |
| **P6 — spec §4.4 A9 note** | LANDED-CORRECT | `specification.md:384` "Implementation note (v0.4.x): … hard-rejects … stricter than the SHOULD … deferred to v1.1" |
| **P6 — ci.yml boundary step + all-extras** | LANDED-CORRECT | `ci.yml:45-46` named "Core/extension boundary invariant (governance ADR 003)" step BEFORE main tests; `:37` `uv sync --all-extras` ⇒ rdflib/filelock in CI (A-R3-7) |
| **P7 — CHANGELOG dated + links** | PARTIAL (1 doc defect) | `CHANGELOG.md:10` `## [0.4.1] — 2026-05-18` (not "In progress") ✓; compare-links `:172-177` present (4/6 now resolve via the new tags; the 2 v0.4.1-dependent links 404 by-design until P7's release step). **Defect:** `:25` still lists `"turned out"` — see §1.1 |
| **P7 — 4 local tags correct commits** | LANDED-CORRECT | Annotated tags (`git cat-file -t`⇒`tag`): v0.1.0→7110528, v0.2.0→568c023, v0.3.0→50051ec, v0.4.0→0540346 — all match A-R3-4 map |
| **P7 — v0.4.1 deferred not faked** | LANDED-CORRECT | No `v0.4.1` tag; CHANGELOG body honestly dated; release/tag is P7 post-merge per plan |
| **P9(a) — lazy embedding** | LANDED-CORRECT | `embeddings.py`: `Model2VecEmbeddingProvider.__init__` no longer calls `StaticModel.from_pretrained`; `self._model=None`; lazy `_get_model()`; `dimensions` lazy property; Protocol `dimensions`→read-only property (OpenAI compat preserved). Module-level `_HAS_MODEL2VEC` probe untouched. 2/2 lazy tests pass |
| **P9(b) — lock** | LANDED-CORRECT | §2 above. 3/3 lock tests pass |
| **P9(c) — atomic .npy** | LANDED-CORRECT | `store.py:318-333` `save_embeddings_cache`: bare `np.save` → `mkstemp` + `os.fdopen` + `os.replace` + cleanup-on-`BaseException`. 2/2 atomic-npy tests pass |
| **extension-status feature (4 modes + fail-safe + boundary-safe)** | LANDED-CORRECT | `extension_status.py` in **core** (not extensions/); 4 modes (no-ext `:35-38`, OpenAI `:47-48`, model2vec `:49-54`, keyword-only `:55-59`); "Never raises" — every path try/except; guarded imports of optional ext; wired into `start_session` (`session_tools.py`) with boundary comment. 6/6 status tests pass |
| **§3 verified-solid — untouched** | CONFIRMED | A6 `_EXPLICIT_ABSENCE_MARKERS` 2-marker frozenset + `.strip()` unchanged (`session_tools.py:43-45,64`); L3.1/A10 `_is_uri_form_reference`/`_check_referential_integrity` present, not in any diff; L1.3 `Environment` has no `trace_version`; `schemas/` only `trace-v0.4.json`; `prov_mapping.py` PROV ns still `…/ns/v0.3#` (ADR-002 D6), file untouched |

No item is MISSING or WRONG.

---

## 4. P5 GENUINELY-CONFORMANT DEEP-DIVE (real triples listed)

I built a session exercising all 3 v0.4.1 correction/dispatch shapes + a retry, exported
via `export_prov_jsonld`, and parsed it with **rdflib** (`format="json-ld"`, raises on
invalid JSON-LD):

- **Total RDF triples parsed: 34** (14 in the PROV namespace) — NOT zero. The original
  defect (PROV-JSON under a JSON-LD context → a conformant parser extracted **zero**
  triples) is genuinely fixed by the node-object rewrite.

Real PROV triples produced (subject → predicate → object):

| Subject | Predicate | Object | Shape verified |
|---------|-----------|--------|----------------|
| `evt_001` | `prov:wasInvalidatedBy` | `<correction-annotation>` | **event-ID correction** (repudiatory) |
| `<annotation>` | `prov:qualifiedInfluence` | `_:infl_<evt>_0` | **URI-form correction** |
| `<annotation>` | `prov:wasInfluencedBy` | `_:infl_<evt>_0` | URI-form correction |
| `_:infl_<evt>_0` | `prov:atLocation` | `"external:https://example.org/doc#L5"` | URI-form correction |
| `<tool_call>` | `prov:wasInformedBy` | `evt_002` | **parent_event_id dispatch chain** |
| `<tool_call>` | `prov:wasRevisionOf` | `evt_003` | **retry — UNCHANGED, not broken by the split** |
| + `prov:wasAttributedTo`, `prov:wasAssociatedWith`, `prov:used`, `prov:startedAtTime` | | | standard PROV-O |

Predicate presence in the parsed graph: `wasInvalidatedBy` PRESENT, `qualifiedInfluence`
PRESENT, `atLocation` PRESENT, `wasInfluencedBy` PRESENT, `wasInformedBy` PRESENT,
`wasRevisionOf` PRESENT. Corrections emit `wasInvalidatedBy`/`qualifiedInfluence` and
**NOT** `wasRevisionOf` — the old conflation bug is genuinely gone; retries/decision-
revisions still correctly use `wasRevisionOf` (distinct semantics preserved).

**Rewritten tests assert REAL triples, not tautologies:** `test_v041_prov_ld_split.py`
(584-line rewrite) does `g.parse(data=raw, format="json-ld")` then asserts triple
*membership*, e.g. `assert (TRACE.evt_001, PROV.wasInvalidatedBy, TRACE.evt_002_annotation)
in g` (:87), `assert (TRACE.evt_001, PROV.wasRevisionOf, None) not in g` (:111),
`assert (infl, PROV.atLocation, Literal(uri)) in g` (:136),
`assert (TRACE.evt_002, PROV.wasInformedBy, TRACE.evt_001) in g` (:223), plus
`TestUnchangedRelations` proving retries/revisions still `wasRevisionOf`.
`test_v041_p5_prov_roundtrip.py` adds a parse-validity + PROV-namespace-presence guard.
**All 13 PROV tests pass post-fix.** Genuinely conformant — not a `json.loads`+key-presence
tautology.

**Scope note (justified):** P5 as written said "architecture correct; add a test". The
implementer **rewrote the exporter** (138+/182−) from PROV-JSON to conformant node-object
JSON-LD. This was *necessary* — Round-1 §2 (A4) itself recorded that the prior architecture
produced zero triples under a real parser, so a round-trip test was impossible without the
serialization fix. The §3 "L6.x split architecture" = the *predicate-mapping logic*, which
is **empirically preserved** (every split predicate is a real triple; 13 predicate-level
tests pass; `prov_mapping.py`/ADR-002-D6 v0.3 namespace untouched). Defensible scope, not
creep, but worth recording as a larger-than-stated change the implementer correctly judged
required.

---

## 5. DROPPED / SCOPE-CREEP / §3-ENDANGERED / COLLATERAL

- **Dropped from round1/2/3:** none found. M1/M2/M3 + all A-R3-1..8 + G1–G6 remediations
  present. "Resolved by Round 3" items (P3 counts, M3, M1 `--upgrade` not needed, P7
  option-(a) tags) all honored.
- **§3 verified-solid endangered:** none. A6, L3.1/A10, L1.3, schemas, PROV namespace
  (ADR-002 D6) all confirmed untouched. The single nearest-the-line case (P5 exporter
  rewrite) preserves L6.x predicate semantics — empirically verified.
- **Collateral from the recent fix:** none. The `_recall_hook` lock + the `session_tools.py`
  comment correction did not alter the other 5 RMW spans, the multi-actor gate, the
  duplicate-⚠️ removal, or any §3 item. 108 P1 + 14 P9/boundary/status + 13 PROV + 2 L9.1
  targeted tests all pass.
- **Scope additions (minor, flagged, non-blocking):**
  1. `.mcp.json` modified (outside P1–P9) — adds `--with openai/numpy/model2vec` but
     **omits `filelock`**, so the configured local server runs P9(b) as a warned no-op
     (graceful by design; recommend adding `--with filelock` for activation parity).
  2. `extension_status.py` + its `start_session` wiring is a new user-facing feature not
     in the P1–P9 list (cleanly boundary-safe, fail-safe, tested — benign, arguably
     adoption-surface, not creep).
  3. `uv.lock` +29 lines — expected lockfile sync for new dev deps; not creep.
- **Doc defect (the one real, non-blocking finding):** `CHANGELOG.md:25` still claims the
  orphan-discovery hint matches `"turned out"` after P4 removed it — 1-line truth-up needed
  before the PR ships, to honor the very G5 trust principle the remediation enforces.

**Bottom line:** the remediation is correct and complete; P9(b) is fully closed with no
collateral; P5 is genuinely (empirically) conformant. Ship after the two 1-line follow-ups
(CHANGELOG.md:25 phrase truth-up; `.mcp.json` `--with filelock` for runtime parity).
