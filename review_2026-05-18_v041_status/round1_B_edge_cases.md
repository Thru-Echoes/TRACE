# Round 1 — Reviewer B: Edge-Case & Robustness Audit of v0.4.1 (MERGED CODE)

**Date:** 2026-05-18
**Branch:** `main` (PR #6 merged: `d20be80`)
**Scope:** Adversarial edge-case verification of the 7 assigned items + whether
Round-3 amendments A1–A10 / Layer 11 actually shipped in merged code.
**Method:** Read merged source (the plan is NOT evidence it shipped); targeted
read-only pytest; inline `uv run python -c` probes. The known
`test_fm7_batch_logging_undetected` hang is **out of scope** (owned by another
agent); not re-run.

Package version confirmed `0.4.1` (`trace_mcp.__version__`). Targeted suites
run: `test_decision_guards.py` (75 incl. prov-split), `test_v041_attribution_audit.py`
+ `test_v041_uri_corrects_event_ids.py` (48), `test_v041_prov_ld_split.py` (11).
**All targeted tests PASS** — which is itself a finding (see A1: the suite passes
*because* the multi-actor guard is missing).

---

## 1. Verdict

**v0.4.1 is ~80% robust at the edges. One CRITICAL false-positive regression
shipped: Round-3 amendment A1's multi-actor guard was never implemented, and the
CHANGELOG/spec/code text actively *claim* it exists.** The single-source
`trace_version` migration, schema rename, backward-compat, `_is_explicit_absence`
allow-list (A6), URI carve-out heuristic (A10), PROV-LD qualified-influence
architecture (A4 part 1), the A7 None-guards, A2's L5.7 removal, and the L11.6
ADR are all **correctly implemented and well-tested**. The defects are concentrated
in three places: (a) **A1 multi-actor guard absent** in *both* the decision-time
FM1/FM25 path and the session-end structural detector — a single-actor
`system→system` session (real production shape, explicitly cited in A1) emits a
false self-resolution warning at *both* layers, with warning text that literally
says "in multi-actor workflows" while firing on a single-actor session; (b) **A8
half-done** — the field rename landed but the orphan-discovery hint is still
rendered as a ⚠️ warning at full severity (innocuous prose like "turned out
cleaner" trips it); (c) **A4 part 2 and A9 documentation gaps** — no PROV-O
parser/ontology round-trip test (only `json.loads`), and spec §4.4 still does not
document that cross-session refs hard-reject. None are crashes; A1 is a
trust-eroding false-positive on an OSS release and contradicts shipped docs.

---

## 2. Severity-ranked findings

| # | Finding | file:line | Status | Recommended remediation (describe only) |
|---|---------|-----------|--------|------------------------------------------|
| F1 | **CRITICAL.** Round-3 A1 multi-actor guard NOT implemented in decision-time FM1. `is_self_resolution` = `(proposer.type==resolved_by_type and proposer.id==resolved_by_id)` with **no ≥2-distinct-actor session check**. Single-actor `system→system` fires the warning. | `decision_tools.py:80-99` | **VERIFIED** (ran probe: single-actor `system→system` → "Same actor instance proposed and resolved" warning; `git log -S participants -- decision_tools.py` empty = guard never in any commit) | Add the A1 conjunct: compute distinct actor set from `{e.actor.* for e in events}` ∪ `metadata.participants`; suppress the non-ai branch unless ≥2 distinct actor types/instances. Preserve `resolved_by is not None` short-circuit. Keep ai→ai branch as-is (backward compat). |
| F2 | **CRITICAL.** Same A1 gap in the session-end **structural** detector (L5.4). `attribution_warning_ids` appended on pure `(type==type and id==id)` with no multi-actor guard. | `session_tools.py:330-334` | **VERIFIED** (ran probe: legacy single-actor `system→system` session, env=None, participants=[] → `attribution_warning_count == 1`; A1 requires 0) | Same multi-actor guard as F1, applied before `attribution_warning_ids.append`. Reuse one helper across decision_tools + session_tools. |
| F3 | **CRITICAL (claimed-vs-actual).** CHANGELOG L24/L45, decision_tools.py:97 and session_tools.py:166/371 all assert the warning is scoped to "multi-actor sessions" / "(same Actor instance) in multi-actor sessions" — but no such scoping exists in code. Docs describe behavior the code does not have. | `CHANGELOG.md:24,45`; `decision_tools.py:97`; `session_tools.py:166,371` | **VERIFIED** (read all sites; grep `multi.actor` in `tools/` returns only text strings, zero logic) | Either implement F1/F2 (preferred — makes the claim true) or correct every doc/string to say "same-instance self-resolution, regardless of session actor count". Do not ship the false claim. |
| F4 | **HIGH.** A1's FM25 (fast-resolution) generalization shipped but inherits the same missing guard — single-actor `system→system` resolved <5s also gets the "Was the other actor consulted?" false positive. | `decision_tools.py:101-108` | **VERIFIED** (probe: single-actor `system→system` <5s → "Decision proposed and self-resolved in 0.0s" warning) | Gate FM25's same-instance branch behind the same multi-actor guard as F1. |
| F5 | **HIGH.** A8 only half-applied. Rename `orphan_discovery_warning_count`→`orphan_discovery_hint_count` DONE, but the hint is still appended to `audit_warnings` with a ⚠️ prefix at the **same severity tier as real warnings**. Innocuous contribution prose containing "turned out"/"discovered" trips it. | `session_tools.py:378-383` (appends to `audit_warnings`); render `:175-182` + `:205-207` | **VERIFIED** (probe: 3 innocuous descriptions — "turned out cleaner", "Discovered files reorganized", "CI turned out green" — all 3 produced a ⚠️ warning + the hint line) | Per A8: move the orphan-discovery line out of `audit_warnings`; render it in a clearly lower-severity block (like `explicit_absence` info line), no ⚠️, no contribution to any warning count semantics. |
| F6 | **MEDIUM.** A9 documentation gap. Cross-session / dangling `revises_event_id` (and `corrects_event_ids` event-IDs, `parent_event_id`, etc.) **hard-reject** with `ValueError` at `append_event`, blocking the event. Spec §4.4:381 still says only "SHOULD reference valid event IDs within the same session" — no note that the impl hard-rejects (a tightening beyond SHOULD), no v1.1-relaxation deferral note that A9 required. | `session_tools.py:662-667` (raise); spec `docs/specification.md:381` | **VERIFIED** (probe: `revises_event_id="evt_999"` → `ValueError: Invalid event references...`; grep spec for "hard-reject"/"tighten"/"v1.1" near §4.4 → none) | Add a normative note to spec §4.4: "Implementations MAY hard-reject out-of-session references at append time (the reference TRACE server does); cross-session reference support is deferred to a future version." Matches A9 verbatim intent. |
| F7 | **MEDIUM.** A4 part 2 not satisfied. PROV-LD round-trip test is `json.loads` + `assert "@context" in parsed` only. No third-party PROV parser, no PROV-O/SHACL ontology-constraint check, no JSON-LD expansion. Qualified-influence shape is string-matched, never validated as real PROV-O. | `tests/test_v041_prov_ld_split.py:437-459` (`TestRoundTrip`) | **VERIFIED** (read file end-to-end; only `json.loads`/key-presence asserts) | Add a test that loads the export with `rdflib`+`prov` (or `pyld` JSON-LD expansion against `@context`) and asserts the `prov:Influence`/`prov:qualifiedInfluence`/`prov:atLocation` triples form a valid PROV-O qualified-influence. At minimum validate against the W3C PROV-O constraints A4 named. |
| F8 | **LOW (orthogonal — NOT a live-session topic).** Orphaned-server lifecycle is fully delegated to upstream `mcp.server.stdio`. No TRACE-side parent-PID watchdog / idle timeout / signal / atexit. Genuine parent-SIGKILL → stdin pipe EOF → upstream `async for line in stdin` ends → clean exit (correct). But if the stdin write-end is retained elsewhere or stdin is a tty/file, the server would block forever with zero TRACE defense-in-depth. | `server.py:761` (`mcp.run(transport="stdio")`, no wrapper); upstream `mcp.server.stdio.stdio_server.stdin_reader` | **VERIFIED** (read server.py main; inspected upstream `stdio_server` source — exit path is `async for line in stdin` EOF only) | Acceptable for the genuine unclean-death case. Optionally (defense-in-depth, defer-OK): add an optional parent-PID poll or `--idle-timeout` so a pipe-retained orphan self-exits. State explicitly in docs that lifecycle is delegated to the MCP SDK. |
| F9 | **INFO.** L9.0 fixture migration essentially complete. Only residual `"0.3.0"` strings in `tests/` are *intentional*: `test_v041_decision_audit_hook.py:247,250` is the deliberate `test_hook_handles_pre_v041_session_cleanly` backward-compat fixture; `test_specification_conformance.py:1202` asserts the spec URL stays v0.3 per ADR-002-D6. No half-done migration. | `tests/test_v041_decision_audit_hook.py:247,250`; `tests/test_specification_conformance.py:1202` | **VERIFIED** (grep all `tests/` for `0.3.0`/`trace-v0.3.json`; only `trace-v0.4.json` exists in `schemas/`) | None — these are correct as-is. |

---

## 3. Per-item deep-dive (PLANNED vs ACTUALLY-IN-MERGED-CODE vs edge failures)

### Item 1 — `_is_explicit_absence` (A6) — ✅ FULLY SHIPPED, robust
- **Planned (FINAL L5.2 + A6):** strict allow-list `{"<autonomous-stretch>","<no recent user message>"}`, plus `.strip()` (A6) so leading/trailing whitespace still counts; reject generic `<...>`.
- **In merged code (`session_tools.py:43-64`):** `_EXPLICIT_ABSENCE_MARKERS` frozenset = exactly the two markers; `return s.strip() in _EXPLICIT_ABSENCE_MARKERS`; `None → False`. Exactly A6.
- **Edge cases probed (VERIFIED):** `"  <autonomous-stretch>  "`→True, `"\t<no recent user message>\n"`→True, `"<AUTONOMOUS-STRETCH>"`→False (case-sensitive — correct, no under-match), `"<script>"`/`"<my draft>"`→False (no over-match), `"<autonomous-stretch> extra"`→False, `""`/`None`→False, `"autonomous-stretch"`→False. **No over-match, no under-match.**
- **Test coverage:** `test_v041_attribution_audit.py:62-98` covers whitespace (A6), over-match (`<script>`, `<my draft>`), empty, None, prefix non-match. Gap: no explicit case-sensitivity assertion (`<AUTONOMOUS-STRETCH>`), but probe confirms behavior correct. **Verdict: clean.**

### Item 2 — Structural self-resolution + FM1/FM25 (A1) — ❌ AMENDMENT DID NOT LAND
- **Planned:** FINAL L3.2/L5.4 = type-only equality (no multi-actor guard). **Round-3 A1 superseded this**: require full Actor equality (type AND id) **AND ≥2 distinct actor types/instances in session**; generalize FM25 to match; rewrite `test_human_self_resolves_clean` and add `system→system`(no warn) / `claude+gpt`(no warn) / `same-human`(warn) tests.
- **In merged code:**
  - Full Actor equality (type AND id): ✅ shipped (`decision_tools.py:80-83`, `session_tools.py:330-333`) — the A1 *equality refinement*.
  - **Multi-actor guard (≥2 distinct actors): ✗ ABSENT in BOTH layers.** `git log -S "participants" -- src/trace_mcp/tools/decision_tools.py` → empty (never in any commit). grep `multi.actor` in `tools/` → only warning-text strings, zero logic.
  - FM25 generalized to `is_self_resolution`: ✅ (`decision_tools.py:101-108`) — but inherits the missing guard.
  - Tests: `test_human_self_resolves_warns` (warn) + `test_human_different_instance_self_resolves_clean` (no warn, diff id — also covers the claude/gpt different-instance case) exist. **Missing: explicit `system→system` single-actor no-warn test** (A1 required it; absent → the false positive is uncaught).
- **Edge failures (VERIFIED via probes):**
  - Single-actor `system→system` (1 actor type): FM1 warns + FM25 warns + session-end `attribution_warning_count==1`. **A1 explicitly requires 0** ("only 1 actor type in session"; cites real prod file `trace_20260320_205356.json`).
  - Solo-human `human→human` same id: warns. A1 wants this to warn *only in multi-actor sessions*; here it warns even in a 1-actor session. The test `test_human_self_resolves_warns` passes **because** the guard is missing (session has 1 participant) — the test encodes the bug.
  - Multi-AI claude(id=claude)+gpt(id=gpt): correctly no warn (different id) — but via id-inequality, not the multi-actor guard; coincidentally right.
- **Verdict:** A1's *core scoping fix* (the whole point of the amendment) is **NOT in merged code**. CRITICAL false positive on the most common single-actor automation shape, with self-contradicting "multi-actor workflows" warning text.

### Item 3 — Orphan-discovery detector (A8) — ⚠️ HALF-SHIPPED
- **Planned (A8):** rename `orphan_discovery_warning_count`→`orphan_discovery_hint_count` AND render in a lower-severity tier than warnings (it's heuristic, false-positive-prone).
- **In merged code:** rename ✅ (no `_warning_count` anywhere in `src/` or `tests/`; `_DISCOVERY_PHRASES` tightened to 4 phrases per L5.5). **Lower-severity render: ✗** — `session_tools.py:378-383` still `audit_warnings.append(...)` and render `:205-207` prefixes every `self.warnings` entry with ⚠️. The dedicated hint line (`:175-182`) also exists but the warning is *duplicated* into the ⚠️ tier.
- **Edge failures (VERIFIED):** 3 innocuous contribution descriptions ("Refactored…it turned out cleaner", "Discovered files reorganized per reviewer request", "CI turned out green on first try") → all 3 emitted a ⚠️ warning + the hint line. High false-positive surface at full warning severity — exactly what A8 set out to de-emphasize.
- **Verdict:** name fixed, severity intent unrealized.

### Item 4 — URI-form `corrects_event_ids` carve-out (L3.1 / A10) — ✅ SHIPPED, heuristic unambiguous
- **Planned:** L3.1 carve-out in `_check_referential_integrity`; A10 clarify scheme heuristic `[a-z][a-z0-9-]+:` vs `evt_NNN` in spec §3.7.1.
- **In merged code:** `_URI_SCHEME_RE = re.compile(r"^[a-z][a-z0-9-]+:")` (`session_tools.py:581`); skipped in `_check_referential_integrity` (`:607-611`). Spec §3.7.1:283 has the exact normative clause matching the regex.
- **Edge cases (VERIFIED probe):** `external:`/`jsonl:`/`subagent:`/`tool-result:`→URI; `evt_001`/`evt_42`→not URI; `evt_1:foo`→**not URI** (the `_` in `evt_` breaks `[a-z0-9-]` before the colon — `evt_NNN` with a colon suffix is safe); `C:/Users/...`→not URI (uppercase, `[a-z]` required); `c:/users/x`→not URI (1-char drive, needs ≥2 before `:` → correctly treated as event-id, would dangle-error not silently skip); `EVT_1:x`→not URI; `:foo`→not URI; empty→not URI; `ab:`→URI (scheme w/o path — acceptable). **`evt_NNN` and real schemes are unambiguously distinguishable.** A10 satisfied; misfire surface is benign (worst case: a lowercase 1-char Windows drive would be checked as an event id and raise a clear dangling-ref error, not silently pass).
- **Test coverage:** `test_v041_uri_corrects_event_ids.py:65-109` covers all schemes, event-id-no-match, empty, uppercase, digit-start, 1-char, hyphen, no-colon, mixed, dangling-still-raises. Strong.
- **Verdict:** clean.

### Item 5 — PROV-LD split (A4) — ✅ ARCHITECTURE SHIPPED; ❌ PROV-O VALIDATION TEST MISSING
- **Planned (A4):** L6.2 is real exporter architecture (new `bundle["influence"]`/`["wasInfluencedBy"]`, `prov:atLocation`), NOT a `wasRevisionOf` shortcut; effort ↑ to ~5h; **add a parser-roundtrip test validating against a third-party PROV parser or W3C PROV-O constraints.**
- **In merged code (`prov_jsonld.py`):** new bundle sections `wasInvalidatedBy`/`wasInfluencedBy`/`influence`/`wasInformedBy` (`:51-54`). Event-ID correction → `prov:wasInvalidatedBy` (`:197-201`). URI correction → qualified-influence: `prov:Influence` blank node + `prov:atLocation` + `prov:qualifiedInfluence` reification (`:187-195`) — **genuine PROV-O qualified relation, NOT a shortcut**. `parent_event_id` → `prov:wasInformedBy` (`:108-113`). Deterministic blank-node IDs (`_:infl_{evt}_{idx}`, not Python hash — comment shows the non-determinism trap was understood). `prov_mapping.py` split is consistent. **A4 part 1: fully done, good architecture.**
- **Gap:** `test_v041_prov_ld_split.py` has rich predicate-presence tests, BUT `TestRoundTrip` (`:437-459`) only does `json.loads` + `"@context"`/`"bundle"` key check. **No third-party PROV parser, no PROV-O/SHACL constraint validation, no JSON-LD expansion against `@context`.** The qualified-influence shape is only string-matched. **A4 part 2 NOT satisfied.**
- **Verdict:** export correct; validation depth as A4 required is absent (F7).

### Item 6 — Single-source `trace_version` + schema rename (L1.3/L1.5/A5/L9.0) — ✅ FULLY SHIPPED
- **Planned:** drop `Environment.trace_version`; `Session.trace_version` default `"0.4.1"`; rename `schemas/trace-v0.3.json`→`trace-v0.4.json`; keep `Session.context` spec URL + PROV namespace at v0.3 (ADR-002-D6); migrate fixtures (L9.0).
- **In merged code:** `Environment` has no `trace_version` (`session.py:35-39`); `Session.trace_version="0.4.1"`, `context="https://trace-protocol.org/v0.3"` (`:58-59`). Only `schemas/trace-v0.4.json` exists. Pydantic `extra` = default `"ignore"` (verified `pydantic 2.13.4`; no `model_config` override needed).
- **Backward-compat (VERIFIED probe):** a v0.3-format blob (`trace_version="0.3.0"`, `environment.trace_version="0.3.0"`, unknown `some_old_field`, valid events w/ `session_id`) loads cleanly: `session.trace_version` **preserved as `"0.3.0"`** (NOT force-bumped — on-disk value wins; default only applies when absent), `environment.trace_version` + unknown field silently dropped, round-trip dump keeps `0.3.0`. Matches CHANGELOG. Note: `TraceEvent.session_id` is required-no-default (`events.py:110`); genuine on-disk old sessions always have it (server sets it), so real files load — only synthetic blobs omitting it fail (not a regression).
- **L9.0:** complete; only intentional `"0.3.0"` residuals remain (F9).
- **Verdict:** clean, correctly backward-compatible.

### Item 7 — stdio orphaned-server self-exit — ✅ CORRECT for genuine death; no defense-in-depth (orthogonal)
- **Stated separately per instructions: this is unrelated to any live-session topic.**
- `server.py:738-761` `main()` → `mcp.run(transport="stdio")` with **no** custom stdin handler, signal handler, atexit, or parent-PID watchdog. Lifecycle 100% delegated to upstream `mcp`.
- Upstream `mcp.server.stdio.stdio_server.stdin_reader` (source inspected): `async for line in stdin` — on parent SIGKILL the stdin pipe write-end closes → iterator hits EOF → `read_stream_writer` closes → task group unwinds → `mcp.run()` returns → process exits. **Genuine unclean-host-death orphan → clean self-exit. Correct.**
- Edge: if stdin's write-end is retained by another process, or stdin is a tty/regular file, `async for` blocks forever; TRACE adds zero watchdog/idle-timeout. Acceptable for the in-scope orphan case; a hardening opportunity (F8), safely deferrable.

---

## 4. Claimed-vs-actual: Round-3 amendment status in merged code

| Amendment | Claimed (CHANGELOG/plan/docs) | Actual in merged code | Status |
|---|---|---|---|
| **A1** (multi-actor guard + ID-equality on FM1/L5.4, FM25 sync, test rewrite) | CHANGELOG:24,45 "in multi-actor sessions"; spec §3.6; "Items that survive R3" lists L5.4 "with scoping from A1" | ID-equality ✅; FM25 generalized ✅; **multi-actor guard ✗ (both layers, never in any commit)**; `system→system` no-warn test ✗ | **MISSING (core of A1)** — F1/F2/F3/F4 |
| **A2** (L5.7 redirect/remove; recommended: remove) | plan: "Recommended: remove L5.7" | scratchpad uses `len(session.events)` (`:182`); no summary-count regex anywhere | **DONE (removal path taken)** |
| **A3** (L5.6 → advisory hint, threshold ≥10 contrib & 0 tool_call & Claude Code, no counter) | CHANGELOG implies advisory | `session_tools.py:400-411` exactly: `contribution_count>=10 and tool_call_count==0 and env.client=="Claude Code"`, `[hint]` text into warnings, no counter | **DONE** (note: still inside `audit_warnings` list w/ ⚠️ — minor, advisory text is clear) |
| **A4** (L6.2 real qualified PROV-O; +PROV-O round-trip test) | CHANGELOG "qualified `prov:wasInfluencedBy` with `prov:atLocation`" | Architecture ✅ (real qualified-influence, deterministic blank nodes); **PROV-O/parser validation test ✗ (only `json.loads`)** | **PARTIAL** — arch done, A4 test requirement not met (F7) |
| **A5/L9.0** (fixture migration) | CHANGELOG migration notes | `trace-v0.4.json` only; residual `0.3.0` are intentional fixtures/ADR-002-D6 | **DONE** (F9) |
| **A6** (`_is_explicit_absence` `.strip()` + strict allow-list) | "Items that survive R3": "L5.2 (with `.strip()` from A6)" | `session_tools.py:64` `s.strip() in frozenset{2 markers}` | **DONE** (item 1) |
| **A7** (None-guard env / participants==[]) | implied by R3 | `env is not None` guard `:399-405`; audit builds fine with env=None, participants=[] (probed) | **DONE** |
| **A8** (rename `_warning_count`→`_hint_count` + lower severity tier) | CHANGELOG:23 lists `orphan_discovery_hint_count` | rename ✅; **lower-severity render ✗ (still ⚠️ in `audit_warnings`)** | **PARTIAL** — F5 |
| **A9** (spec §4.4 document cross-session hard-reject + defer relax to v1.1) | — | §4.4:381-382 has URI-form note ✅ but **no hard-reject/tightening/v1.1 note ✗**; impl DOES hard-reject (probed `ValueError`) | **PARTIAL** — F6 |
| **A10** (spec §3.7.1 scheme heuristic clarification) | CHANGELOG §3.7.1 | spec §3.7.1:283 normative `[a-z][a-z0-9-]+:`, "event IDs MUST NOT match", matches regex exactly | **DONE** (item 4) |
| **L11.6** (ADR 002) | — | `docs/adr/002-v041-protocol-additions.md` exists, well-formed, covers all 4 required rationales + D6 namespace decision | **DONE** |

**Net:** Of the Round-3 amendments in scope: **A2, A3, A5/L9.0, A6, A7, A10, L11.6
fully landed. A4 and A8 and A9 partially landed (architecture/rename yes,
validation/severity/spec-doc no). A1 — the single most important scoping fix of
Round 3 — did NOT land at all, and the code/CHANGELOG/spec text falsely claim it
did (F3).**

---

## Appendix — Commands run (all read-only)

- `git log --oneline -15`, `git log -S "participants" -- src/trace_mcp/tools/decision_tools.py` (empty)
- `uv run pytest tests/test_decision_guards.py::TestSameActorWarning -q` → 7 passed 0.06s
- `uv run pytest tests/test_v041_attribution_audit.py tests/test_v041_uri_corrects_event_ids.py -q` → 48 passed
- `uv run pytest tests/test_v041_prov_ld_split.py tests/test_decision_guards.py -q` → 75 passed
- `uv run ruff check session_tools.py decision_tools.py prov_jsonld.py` → all clean
- `PYTHONPATH=src uv run --no-project python -c …` probes: `_is_explicit_absence`/`_is_uri_form_reference` matrices; single-actor `system→system` FM1+FM25+session-end false positive; legacy session env=None/participants=[] (A7 + A1 re-confirm); pre-v0.4.1 backward-compat load/round-trip; orphan-discovery on innocuous prose; cross-session `revises_event_id` hard-reject; upstream `mcp.server.stdio` source inspection.
