# TRACE v1.0 — FINAL Fix Plan

**Status:** Final after 3-set verification (correctness / exhaustiveness / bloat-scope).
**Supersedes:** `trace_v1_proposed_fix_plan.md`.
**Companion:** `trace_v1_release_checklist.html` (actionable checklist).

**Goal:** Fix the correctness/reliability/quality issues surfaced by the waggle audit, without bloating the codebase and without removing functionality. Quality + reliability + correctness are the only priorities; cost/latency/convenience are not.

## What changed from the proposed plan

The three verification sets surfaced 13 substantive corrections. Numbered with the original plan section they affect:

1. **Version target wrong.** Repo is already at `0.4.0` (`pyproject.toml:7`, `__init__.py:3`). Plan's "bump to 0.3.1" was a downgrade. Target is `0.4.1`.
2. **Schema validator gap — URI-form refs would be rejected.** `_check_referential_integrity` (`session_tools.py:~358-390`) raises `ValueError` on any `corrects_event_ids` entry not in the session's known IDs. The URI scheme is non-functional until that validator skips URI-prefixed entries.
3. **Existing FM1 only catches `ai→ai` self-resolution.** `decision_tools.py:~76` guards `resolved_by_type == "ai"`. The evt_025 pattern (`human→human`) silently passes. One-line generalization needed; was missed entirely.
4. **Symmetric spec violation unenforced.** Spec §3.6 line 220 says proposer MUST NOT be the same instance that resolves it in multi-actor workflows. evt_001 and evt_025 both violate this with `human→human`. Needs a schema-level OR tool-level guard, not just session-end heuristic.
5. **Actor ID drift.** Session participants declare `id="claude"` but events use `actor.id="ai-assistant"` (default). Plan did not flag.
6. **`trace_version` internal inconsistency.** Session JSON has `trace_version: "0.3.0"` at top-level and `environment.trace_version: "0.4.0"` from `_auto_environment()`. Two sources of truth, no invariant.
7. **Scratchpad event-count off-by-one.** Auto-summary says "27 events" but 28 events were logged. Plan missed.
8. **Regex transcription bug.** §3.5 FM37 regex had Markdown-escape `\|` that would not function in Python.
9. **FM17 vs FM5 label confusion.** Plan's §4.1 said "FM17" but the snippet-on-correction warning is FM5.
10. **`_is_explicit_absence` over-matches.** Predicate `<...>` would return true for `<script>`, `<my draft>`. Use explicit allow-list.
11. **URI scheme overreach.** Four named schemes commit to unproven host conventions. Normative text should be `external:` + `jsonl:` with others non-normative.
12. **English phrase lists are brittle.** Orphan-discovery and recognition-table phrase lists should be examples, not normative content. Fire on structural signals where possible.
13. **`parent_event_id` field omitted but PROV mapping referenced it.** Mild incoherence; one-line schema add closes it.

## What did NOT change from the proposed plan

Confirmed sound by reviewers, do not re-litigate:
- §1.1 conversation_snippet normative MUST + absence-marker convention (`<autonomous-stretch>`, `<no recent user message>`)
- §1.3 Proposer Identity Rule + disambiguation table — load-bearing
- §1.11 Appendix A worked example (with naming correction — see below)
- Adding `discovery` to `AnnotationData.category` literal
- Adding `host` to `ToolCallData` with default `"mcp"`
- Extending `AttributionAudit` with missing-snippet counts + rendering
- Sharpening FM5 / FM17 / FM3 warning text
- PROV-LD split for corrections (event-target → `wasInvalidatedBy`; URI-target → `wasInfluencedBy`)
- Version-bump pattern (additive only, no major-minor jump)
- All 13 rejected items (`trace_log_discovery` wrapper, `subagent_dispatch` type, auto-snippet-extraction, hard-required snippet, etc.)

---

## FINAL plan — by implementation layer

### Layer 1: Version + scaffolding

| ID | Change | File | Issue |
|---|---|---|---|
| L1.1 | Bump version to `0.4.1` | `pyproject.toml:7`, `src/trace_mcp/__init__.py:3` | scaffolding |
| L1.2 | Bump `Session.trace_version` default | `src/trace_mcp/schema/session.py:53` | scaffolding |
| L1.3 | **NEW:** Resolve `trace_version` two-source problem. Either drop `trace_version` from `Environment` (read from `Session.trace_version` only), OR add a validator asserting `Session.trace_version == Session.metadata.environment.trace_version`. **Pick option A** (drop from Environment) — single source of truth is better than runtime invariant. | `src/trace_mcp/schema/session.py:32`, `src/trace_mcp/tools/session_tools.py:~227-234` (`_auto_environment`) | audit gap #6 |
| L1.4 | Add `0.4.1` entry to `CHANGELOG.md` listing all behavioral and schema changes | `CHANGELOG.md` | scaffolding |
| L1.5 | Regenerate JSON Schema. Verify diff is additive only. Decide: rename `schemas/trace-v0.3.json` → `schemas/trace-v0.4.json` to track major-minor, OR document that the v0.3.x and v0.4.x families share schema files. **Pick option A** (rename and update references in spec + tests). | `schemas/trace-v0.3.json` → `schemas/trace-v0.4.json`, `scripts/generate_schema.py:18` | scaffolding |

### Layer 2: Schema additions (`src/trace_mcp/schema/events.py`)

All additive, default-preserving.

| ID | Change | File:line | Issue |
|---|---|---|---|
| L2.1 | Add `"discovery"` to `AnnotationData.category` literal | `events.py:~71` | 3 |
| L2.2 | Add `host: Literal["mcp", "internal", "external"] = "mcp"` to `ToolCallData` (place last in field order, not first — preserves diff readability per Set 1) | `events.py:~25-38` | 5 |
| L2.3 | **NEW:** Add `parent_event_id: str \| None = None` to `ToolCallData`. Optional relation field that enables manual dispatch-chain logging on day one. Defers prose-summary fields to v1.1. | `events.py:~25-38` | 5 |

### Layer 3: Validator changes (`src/trace_mcp/schema/events.py` + `tools/session_tools.py`)

| ID | Change | File:line | Issue |
|---|---|---|---|
| L3.1 | **NEW:** Update `_check_referential_integrity` to skip URI-form entries in `corrects_event_ids`. Heuristic: if entry contains `:` and prefix matches `[a-z][a-z0-9-]+` followed by `:`, treat as URI and skip event-ID lookup. | `session_tools.py:~358-390` | 4 (critical — §1.5 non-functional without this) |
| L3.2 | **NEW:** Add proposer-resolver symmetry warning at decision-resolve time. In `decision_tools.py:~76` generalize FM1: drop `resolved_by_type == "ai"` guard; fire when `proposed_by.type == resolved_by_type` for any type. Warning text: "Same actor type proposed and resolved this decision. Per spec §3.6, in multi-actor workflows the proposer should differ from the resolver." | `src/trace_mcp/tools/decision_tools.py:~76-82` | 2 (critical — this is the evt_025 pattern at originating layer) |

### Layer 4: Logging tool changes (`src/trace_mcp/tools/logging_tools.py`)

Sharpen existing warnings; add one missing co-occurrence warning. No new tools.

| ID | Change | File:line | Issue |
|---|---|---|---|
| L4.1 | Sharpen FM5 text (snippet-on-correction): "Correction logged without conversation_snippet. Set to the relevant user message (~200 chars), or use '<no recent user message>' if no user message motivated this correction. Silent omission is a v0.4.1 protocol violation per spec §3.4.1." | `logging_tools.py:~108-113` | 1 |
| L4.2 | Sharpen FM5 text (snippet-on-contribution): "Contribution logged without conversation_snippet. Set to the relevant user message (~200 chars), or use '<autonomous-stretch>' if working autonomously from a prior decision. Silent omission is a v0.4.1 protocol violation per spec §3.4.1." | `logging_tools.py:~162-167` | 1 |
| L4.3 | Sharpen FM17 text (corrects_event_ids on correction): also mention URI-form. "Correction logged without corrects_event_ids OR conversation_snippet. Provide (a) an event ID, (b) a URI-form external reference (e.g., 'jsonl:transcript.jsonl#L225', 'external:https://...'), or (c) a conversation_snippet quoting the corrected statement. See spec §3.7.1." | `logging_tools.py:~101-106` | 4 |
| L4.4 | **NEW:** FM17 co-occurrence warning. When `category="correction"` AND `corrects_event_ids` is empty AND `related_event_ids` is non-empty, warn: "Correction has empty corrects_event_ids but non-empty related_event_ids. If related_event_ids contains the corrected item's anchor, move it to corrects_event_ids. related_event_ids is for loose association, not for the correction relationship." | `logging_tools.py:~106` | 4 (audit gap from remediation plan, dropped from proposed plan) |
| L4.5 | Demote FM3 (related_decision_ids) warning to fire only when session has at least one decision event | `logging_tools.py:~170` | 1 (warning fatigue) |
| L4.6 | Update `log_tool_call` function signature to accept `host: str = "mcp"`, `parent_event_id: str \| None = None` kwargs | `logging_tools.py:~21-37` | 5 |
| L4.7 | Update `log_tool_call` docstring: "Log an automated tool or service invocation. Covers MCP tools, external non-MCP tools, and host-internal subagent dispatchers (set `host` accordingly)." | `logging_tools.py:~38` | 5 |
| L4.8 | Make FM23 host-aware: only fire on `host="mcp"` for the MCP-typical names (`read`/`glob`/`grep`/`bash`). Drop the speculative "host-internal exploratory names" clause from the proposed plan — no such convention exists yet. | `logging_tools.py:~50-56` | 5 |

### Layer 5: AttributionAudit extension (`src/trace_mcp/tools/session_tools.py`)

The load-bearing visibility change. Surface silent failures in the audit block.

| ID | Change | File:line | Issue |
|---|---|---|---|
| L5.1 | Add fields to `AttributionAudit`: `missing_snippet_contribution_count`, `missing_snippet_correction_count`, `explicit_absence_snippet_count`, `orphan_discovery_warning_count`, `attribution_warning_count`. All `int = 0`. | `session_tools.py:~40-57` (AttributionAudit model) | 1, 2, 3 |
| L5.2 | Helper `_is_explicit_absence(s: str \| None) -> bool` — return True ONLY for explicit allow-list: `s in {"<autonomous-stretch>", "<no recent user message>"}`. Reject generic `<...>` matching. | `session_tools.py:~130` | 1 (over-match fix from Set 1) |
| L5.3 | Loop in `_build_attribution_audit`: count contributions and correction-annotations with null `conversation_snippet`; count those with marker strings separately. | `session_tools.py:~142-200` (loop body) | 1 |
| L5.4 | **Structural** attribution-warning detector: count decisions where `proposed_by.type == resolved_by.type` (regardless of type). This fires on the evt_025 pattern via the structural tuple, no regex needed. | same loop | 2 |
| L5.5 | Orphan-discovery detector: scan contributions for the phrase set (kept as a module-level constant `DISCOVERY_PHRASES = ("discovered", "found a bug", "load-bearing fix", "turned out")` — dropped "all along" and "as it turns out" per Set 1 false-positive risk). For each contribution containing a phrase, check whether a `discovery`/`correction`/`gotcha` annotation exists within 30 min before. If not, increment counter and add to `audit_warnings`. | same loop | 3 |
| L5.6 | **NEW:** Subagent-dispatch visibility detector. If session has ≥5 contributions AND 0 `tool_call` events AND `client="Claude Code"` (or similar internal-dispatch-capable hosts), warn: "Session has N contributions but 0 tool_call events. If subagent dispatches occurred, consider logging them as `tool_call(host='internal', server='<host>')` per spec §3.5." | same loop | 5 (Set 2 recommendation) |
| L5.7 | **NEW:** Auto-derived event count in scratchpad summary. Auto-compute from `len(session.events)` rather than from human-written summary text. Fixes the "27 events but actually 28" off-by-one. | `src/trace_mcp/scratchpad.py` (count-emit site) | audit gap #4 |
| L5.8 | Update `AttributionAudit.render` to surface new counts in severity order: unresolved decisions > unlinked corrections > attribution-warning > orphan-discovery > dispatch-visibility > missing-snippet. | `session_tools.py:~59-126` | 1, 2, 3, 5 |

### Layer 6: PROV-LD mapping updates

| ID | Change | File:line | Issue |
|---|---|---|---|
| L6.1 | Split correction mapping in `PROV_MAPPING` dict. Event-ID target → `prov:wasInvalidatedBy`. URI target → `prov:wasInfluencedBy`. Drop the conflated `prov:wasRevisionOf` for corrections (it remains correct for revisions). | `src/trace_mcp/schema/prov_mapping.py:~7-16` | 4 |
| L6.2 | In exporter `prov_jsonld.py:~150-155`, dispatch on entry shape: `evt_*` → `wasInvalidatedBy` triple; URI → `wasInfluencedBy` triple with `prov:Influence` qualified-influence node bearing `prov:atLocation` (proper PROV-O usage, not shorthand — correction from Set 1). | `src/trace_mcp/exporters/prov_jsonld.py:~150-155` | 4 |
| L6.3 | Add `parent_event_id` → `prov:wasInformedBy` mapping. | `prov_mapping.py:~16`, exporter | 5 |

### Layer 7: Spec edits (`docs/specification.md`)

| ID | Section | Change | Issue |
|---|---|---|---|
| L7.1 | §3.4.1 (line ~164) | Replace soft "particularly important" prose with normative MUST on `contribution` + `correction`-category `annotation` snippets, plus absence-marker convention. Mention reuse: when one user message motivates multiple contributions, reuse is honest and recommended. | 1 |
| L7.2 | §3.5 (line 168) | Generalize tool_call: covers MCP, non-MCP external (HTTP APIs, CLI), and host-internal tools (subagent dispatchers). Document `host` field (mcp/internal/external), `parent_event_id` for dispatch chains. | 5 |
| L7.3 | §3.6 (line 220 area) | Add **Proposer Identity Rule** as new normative paragraph after existing Attribution rule. Include 4-row disambiguation table for canonical patterns (proactive / requested / direct-directive / collaborative). | 2 |
| L7.4 | §3.7 (line ~238) | Add `discovery` to annotation categories table with criteria distinguishing from `gotcha` and `correction`. SHOULD be logged at moment of discovery. | 3 |
| L7.5 | §3.7.1 (NEW) | "External References in `corrects_event_ids`". Normative: each entry MUST be either an event ID or URI-form ref distinguishable by `<scheme>:` prefix; `external:<uri>` is universal fallback. Non-normative examples: `jsonl:<path>#L<line>`, `subagent:<id>`, `tool-result:<id>`. Forbid fabricating events purely to give corrections a target. **Trimmed from 4 normative schemes to 1 normative + 3 non-normative examples per Set 3.** | 4 |
| L7.6 | §4.4 (line ~322) | Split: `corrects_event_ids` entries SHOULD reference valid event IDs OR MAY use URI-form per §3.7.1. Other relation fields stay event-ID-only. | 4 |
| L7.7 | §5.2 (line ~349) | Rewrite Correction Provenance: three anchor cases (event ID / URI / snippet-only). Snippet-only acceptable only when both other anchors unavailable. | 4 |
| L7.8 | §6 (line ~375) | Split correction PROV mapping into two rows: event target → `prov:wasInvalidatedBy`; URI target → `prov:wasInfluencedBy` (qualified, per Set 1). Add `parent_event_id` → `prov:wasInformedBy` row. | 4, 5 |
| L7.9 | §8.1 (line ~466) | Add: discoveries / corrections / gotchas SHOULD be logged at the moment of the underlying event, not in post-hoc contributions. Add: hosts SHOULD detect long autonomous-execution windows (drop specific numeric thresholds per Set 3 — defer to implementer guidance). | 3 |
| L7.10 | §8.2 (line ~477) | Add two rows: (a) `"what should we do about X?" → AI replies with plan → "proceed"` → AI-proposed decision with `suggestion_type=requested`; (b) discovery-language patterns → annotation (category: discovery), log immediately. **Patterns described, examples advisory (not exhaustive list)** per Set 3. | 2, 3 |
| L7.11 | Appendix A | Add worked example using next sequential event ID (e.g., `evt_006`, not `evt_002a` per Set 1 spec convention) for the question→AI-proposal→accept pattern. `proposed_by={type:ai}`, `suggestion_type="requested"`, `resolved_by={type:human}`. | 2 |
| L7.12 | Appendix B (Version History) | Bump to `0.4.1`. List additive changes by section. Note: fully backwards compatible with 0.3.x and 0.4.0. | scaffolding |

### Layer 8: Configuration / documentation updates

| ID | File | Change | Issue |
|---|---|---|---|
| L8.1 | `~/.claude/CLAUDE.md:~52` (Decision row) | Reference Proposer Identity Rule. Add the heuristic: "before logging, ask whose words `description` paraphrases — that actor is the proposer." | 2 |
| L8.2 | `~/.claude/CLAUDE.md:~53` (Correction row) | Add URI-form anchor option for non-event correction targets. Reference §3.7.1. | 4 |
| L8.3 | `~/.claude/CLAUDE.md:~58` (Annotations: SOMETIMES tier) | Add `discovery` category with: "log AT THE MOMENT, not in post-hoc summary." | 3 |
| L8.4 | `~/.claude/CLAUDE.md:~91` (Attribution rules — snippet) | Add absence-marker convention with `<autonomous-stretch>` and `<no recent user message>`. Add: "When one user message motivates multiple contributions, reuse the snippet — honesty does not require uniqueness." | 1 |
| L8.5 | **NEW:** `~/.claude/CLAUDE.md` "What to Log" / USUALLY tier | Add: "Subagent dispatches when outcome is summarized by a contribution. Set `host='internal'`, `server='claude-code'`. Use `parent_event_id` to link to the controller event." Without this guidance, the `host` schema field is write-only. | 5 (Set 2 gap) |
| L8.6 | Codex adapter spec (`src/trace_mcp/adapters/codex/README.md`) | Update placeholder spec to reference new `host="internal"` shape and `parent_event_id` so future Codex implementor doesn't reinvent. | 5 |
| L8.7 | Project `CLAUDE.md` | No changes — defers to global. | — |

### Layer 9: Test coverage requirements

Set 2 flagged that the proposed plan said "~1 day of tests" without specifics. Required tests before merge:

| ID | Test | Justification |
|---|---|---|
| L9.1 | **Waggle-session regression test.** Load `audit_2026-05-13_waggle_session/trace_session_trace_20260513_446733.json` under v0.4.1 schema. Assert: (a) schema validates with no errors, (b) `AttributionAudit` shows `missing_snippet_contribution_count=15`, `missing_snippet_correction_count=1`, `attribution_warning_count≥2` (evt_001 + evt_025), `orphan_discovery_warning_count≥1` (evt_027's plausibility_score reference). | This is the canonical regression scenario. If v0.4.1 ships without these warnings firing on the original audit subject, the fix is invalid by construction. |
| L9.2 | Unit tests for `_is_explicit_absence`: boundary cases (`""`, `"<>"`, `"<autonomous-stretch>"`, `"<some other angle thing>"`, `"<script>alert(1)</script>"`). Verify allow-list semantics. | Prevents the over-match issue Set 1 flagged. |
| L9.3 | Unit test for `_check_referential_integrity` URI-form carve-out. Verify URI-form `corrects_event_ids` entries do NOT trigger `ValueError`. Verify malformed prefixes (no `:`) still throw. | Prevents the §1.5 non-functional-as-written bug from Set 1. |
| L9.4 | Unit test for FM1 generalization: `human→human` self-resolution emits the warning. `ai→human` and `human→ai` do not. | Verifies the evt_025-class detection. |
| L9.5 | Round-trip test: existing v0.3.0 sample session at `specification.md` Appendix A still validates under v0.4.1 schema. Existing v0.4.0 sessions in `~/.trace/sessions/` (if any) still load. | Backwards-compat guard. |
| L9.6 | PROV-LD export test: emit a session with both event-ID and URI-form `corrects_event_ids`. Verify event-ID entries → `wasInvalidatedBy`; URI entries → `wasInfluencedBy` + qualified-influence-with-atLocation. | Verifies the PROV mapping split. |
| L9.7 | AttributionAudit render-order test: with all five new counts non-zero, assert lines appear in declared severity order (unresolved decisions first; missing-snippet last). | Locks down user-facing rendering. |

### Layer 10: Items explicitly NOT included in v0.4.1

Same as proposed plan's exclusion list, retained:

- `trace_log_discovery` convenience wrapper — bloat; use `trace_log_annotation(category="discovery")`
- FM37-style attribution validation at log time — superseded by L3.2 (FM1 generalization) which catches structurally without regex
- `idle-gap-nudge.sh` hook — host-specific; protocol-level recommendation lives in spec §8.1
- `dispatch-start.sh` + `dispatch-end.sh` hooks — depends on hook infrastructure; manual logging supported on day one via L4.6
- `dispatch_kind`, `prompt_summary`, `result_summary` fields on `ToolCallData` — wait until hooks land to auto-populate
- New `subagent_dispatch` event type — rejected on merits
- New `subagent_claim` event type — rejected on merits
- Auto-extraction of `conversation_snippet` from a buffer — rejected on philosophy
- Hard-required `conversation_snippet` schema field — rejected (pushes to fabrication)
- Markdown export rendering changes for dispatches — cosmetic, depends on hooks
- `trace_project_summary` separation — depends on hooks
- `scripts/audit_coverage.py` retroactive utility — nice-to-have, not protocol-blocking
- Schema version bump to `0.5.0` — all changes additive
- Retroactive event injection — fabricates provenance

## Effort estimate (final)

For an experienced contributor with the codebase loaded:

- Layer 1 (version + scaffolding): ~1 hour
- Layer 2 (schema additions, 3 lines): ~30 min
- Layer 3 (validator changes, 2 functions): ~2 hours
- Layer 4 (logging tool changes, 8 items): ~3 hours
- Layer 5 (AttributionAudit extension, 8 items): ~half day
- Layer 6 (PROV mapping, 3 items): ~2 hours
- Layer 7 (spec edits, 12 sections): ~half day
- Layer 8 (CLAUDE.md + adapter docs, 7 items): ~1 hour
- Layer 9 (tests, 7 items): ~1 day

**Total: ~3 days of focused work.** All changes are additive; no breaking changes; backwards compatible with v0.3.x and v0.4.0.

## Coverage check (post-corrections)

| Audit issue | Items addressing it | Coverage |
|---|---|---|
| 1 — `conversation_snippet` | L4.1, L4.2, L4.5, L5.1-5.3, L5.8, L7.1, L8.4 | **Full.** Spec MUST + sharpened warnings + session-end audit visibility + snippet-reuse guidance |
| 2 — evt_025 attribution | L3.2 (FM1 generalization), L5.4 (structural detector), L7.3 (Proposer Identity Rule), L7.10, L7.11, L8.1 | **Full.** Tool-time guard + session-end detector (no brittle regex) + spec rule + worked example |
| 3 — v3 discovery timing | L2.1 (discovery category), L5.5 (orphan detector), L5.8, L7.4, L7.9, L7.10, L8.3 | **Full.** New category + spec guidance + audit-side heuristic with tightened phrase list |
| 4 — `corrects_event_ids` | L3.1 (URI carve-out), L4.3, L4.4 (co-occurrence warning), L6.1-6.3 (PROV split), L7.5-L7.8, L8.2 | **Full.** Validator carve-out + spec URI scheme + correction provenance rewrite + PROV split (qualified) + workaround warning |
| 5 — Agent dispatches | L2.2 (host), L2.3 (parent_event_id), L4.6 (signature), L4.7-4.8, L5.6 (visibility detector), L7.2, L8.5, L8.6 | **Substantial** (not Full). Schema + spec + CLAUDE.md guidance + visibility nudge. Auto-capture hooks deferred to v1.1 as before, but the visibility signal in L5.6 surfaces uncaptured dispatch work even without hooks. |

| Cross-cutting | Items | Coverage |
|---|---|---|
| `trace_version` consistency | L1.3 (single source of truth) | **Full** |
| Scratchpad event-count integrity | L5.7 (auto-derive count) | **Full** |
| Test surface | L9.1-L9.7 (seven specified tests) | **Specified** (pre-merge gate) |

## What ships v0.4.1 vs v1.0

**Note on naming:** the user's "v1.0 OSS release" framing maps to **TRACE schema/spec version 0.4.1** in this plan. The protocol semver is independent of the project release version. The OSS release is "v1.0 of trace-mcp the package" but the protocol stays in the 0.x family until the actually-breaking change (e.g., the eventual `mcp_servers` → `tools` rename). All changes here are additive 0.4.0 → 0.4.1.

If the project chooses to align package version with protocol version, both can advance to 1.0.0 — but that decision is independent of this plan's contents.
