# TRACE v1.0 Proposed Minimal-Fix Plan

**Goal:** Fix the correctness/reliability/quality issues surfaced by the waggle audit, WITHOUT bloating the codebase and WITHOUT removing functionality. Pre-release scope only. Quality > reliability > correctness is the top priority; nothing else (cost, latency, convenience tooling) matters more.

**Discipline applied:** Each item below is here because it addresses a correctness or reliability gap. Items that only add quality-of-life surface area (new convenience tools, host-specific hooks, markdown render tweaks) are explicitly deferred to v1.1.

**Source documents:**
- `trace_audit_findings.md` (original 5 issues identified)
- `trace_audit_remediation_plan.md` (10-subagent remediation analysis)

---

## Pre-release blocker set (v1.0)

### 1. Specification edits (`docs/specification.md`)

All text-only. No schema or code changes here, just protocol clarification.

| # | Section | Change | Issue addressed |
|---|---|---|---|
| 1.1 | §3.4.1 (Event Context) | Replace soft "particularly important" prose at line 164 with normative MUST clause: producers MUST set `conversation_snippet` on every `contribution` event and every `correction`-category `annotation` when a user message motivated the action; MUST use an explicit absence marker (`<autonomous-stretch>` or `<no recent user message>`, angle-bracketed) when no user message exists. Silent null on those event types SHOULD be treated by consumers as a protocol violation, distinguishable from explicit-absence. | 1 |
| 1.2 | §3.5 (Tool Invocation) | Generalize: "Records an automated tool or service invocation — covering external MCP tools, external non-MCP tools (HTTP APIs, CLI subprocesses), and host-internal tools (subagent dispatchers in Claude Code, Codex, ChatGPT, etc.)." Document the new optional `host` field semantics (mcp/internal/external). Update "What to log" to apply uniformly across hosts. | 5 |
| 1.3 | §3.6 (Decision) | Add **Proposer Identity Rule** as a new normative paragraph after the existing Attribution rule (line 220): `proposed_by` MUST identify the actor who authored the **content** of the proposal — the words populating `description` — not the actor who spoke the directive to act. In question→AI-proposal→acceptance flows, the AI is the proposer with `suggestion_type="requested"`; the human resolves with `disposition="accepted"`. Include a 4-row disambiguation table for the canonical patterns. | 2 |
| 1.4 | §3.7 (Annotation) | Add `discovery` to the category enum table: a non-trivial finding surfaced by autonomous or unattended work that carries causal load. Differs from `gotcha` (surprising but nobody was wrong) and `correction` (nothing prior was wrong). SHOULD be logged at the moment of discovery, not in a post-hoc summary. | 3 |
| 1.5 | §3.7.1 (NEW subsection) | "External References in `corrects_event_ids`" — define four URI schemes: `jsonl:<path>#L<line>`, `subagent:<agent-id>`, `tool-result:<call-id>`, `external:<uri>`. Each `corrects_event_ids` entry MUST be either an event ID or URI-form. Prefix-discriminate by `:`. Recommend external-ref-then-correct pattern when the corrected item isn't a TRACE event. Forbid fabricating events purely to give corrections a target. | 4 |
| 1.6 | §4.4 (References) | Split: `retries_event_id`, `revises_event_id`, `related_event_ids`, `related_decision_ids` SHOULD reference valid event IDs within the same session. `corrects_event_ids` entries SHOULD reference valid event IDs OR MAY use URI-form per §3.7.1. | 4 |
| 1.7 | §5.2 (Correction Provenance) | Rewrite to handle three anchor cases: (a) in-session event ID, (b) URI-form external reference, (c) `conversation_snippet` only (acceptable only when both event IDs and URIs are unavailable). Update bullet list with new normative language. | 4 |
| 1.8 | §6 (PROV-LD Mapping) | Split correction mapping: event-ID target → `prov:wasInvalidatedBy`; URI target → `prov:wasInfluencedBy` + `prov:atLocation`. Add `prov:wasInformedBy` for dispatch `parent_event_id`. Drop the conflated `prov:wasRevisionOf` for corrections. | 4, 5 |
| 1.9 | §8.1 (What to Record) | Add paragraph: discoveries / corrections / gotchas SHOULD be logged at the moment of the underlying event, not folded into a later contribution's description. Add: hosts SHOULD detect long autonomous-execution windows (>15 min wall-clock or >15 substantive tool invocations with no `trace_*` write) and nudge controllers to log. **Protocol-level recommendation only — implementation is host-specific.** | 3 |
| 1.10 | §8.2 (Recognizing Events) | Add two rows: (a) `"what should we do about X?" → AI replies with plan → "proceed"` → Decision proposed by AI with `suggestion_type=requested`, then resolution by human; (b) `"discovered that X" / "found a bug" / "load-bearing fix"` → Annotation (category: discovery), log immediately. | 2, 3 |
| 1.11 | Appendix A | Add `evt_002a` worked example after current `evt_002`: question→AI-proposal→accept flow correctly attributed. Use `proposed_by={type:ai, id:claude-opus-4.7}`, `suggestion_type="requested"`, `disposition="accepted"`, `resolved_by={type:human}`. Include `conversation_snippet` quoting the three-turn exchange. | 2 |
| 1.12 | Appendix B (Version History) | Bump `trace_version` to `0.3.1`. Note: additive changes only, fully backwards compatible with 0.3.0. List the changes by section. | All |

### 2. Schema additions (`src/trace_mcp/schema/events.py`)

Both purely additive. No type signature changes elsewhere. JSON Schema regen is a strict superset of `trace-v0.3.json`.

| # | File:line | Change | Issue addressed |
|---|---|---|---|
| 2.1 | `events.py:~71` (`AnnotationData.category` literal) | Add `"discovery"` to the union. Existing literals stay. | 3 |
| 2.2 | `events.py:~25-38` (`ToolCallData`) | Add `host: Literal["mcp", "internal", "external"] = "mcp"` as the first field. Default preserves v0.3 semantics for existing sessions. Update docstring to reflect the generalization. | 5 |

**Explicitly NOT changing:** `corrects_event_ids: list[str]` stays as-is. The URI scheme (§3.7.1) uses prefix-discrimination on the existing string field — no type union, no migration.

**Explicitly NOT adding:** `dispatch_kind`, `prompt_summary`, `result_summary`, `parent_event_id` on `ToolCallData`. These are convenience fields for dispatch logging that the v1.0 doesn't auto-capture. Defer to v1.1 when hooks land.

### 3. Server-side AttributionAudit extension (`src/trace_mcp/tools/session_tools.py`)

This is the single load-bearing code change. It surfaces the silent-warning failures (Issues 1, 2, 3) in the session-end audit block where the controller actually reads them.

| # | Change | Issue addressed |
|---|---|---|
| 3.1 | Add fields to `AttributionAudit` model: `missing_snippet_contribution_count: int = 0`, `missing_snippet_correction_count: int = 0`, `explicit_absence_snippet_count: int = 0`, `orphan_discovery_warning_count: int = 0`, `attribution_warning_count: int = 0`. All default 0; backward compatible. | 1, 2, 3 |
| 3.2 | Helper: `_is_explicit_absence(s: str | None) -> bool` — returns True when `s` begins with `<` and ends with `>`. | 1 |
| 3.3 | In `_build_attribution_audit`, loop over events: count contributions and correction-annotations with null `conversation_snippet`; count those with explicit-absence markers separately; aggregate into the new fields. | 1 |
| 3.4 | Orphan-discovery heuristic in `_build_attribution_audit`: for each contribution containing discovery-language ("discovered", "turned out", "found a bug", "load-bearing fix", "all along", "as it turns out", "in flight"), check whether a discovery/correction/gotcha annotation exists within 30 min before that contribution; if not, increment `orphan_discovery_warning_count` and add to `audit_warnings`. | 3 |
| 3.5 | Attribution-warning heuristic in `_build_attribution_audit`: for each decision with `proposed_by.type="human"` AND `conversation_snippet` set AND the snippet matches `^[\s"']*(proceed\|go ahead\|sounds good\|do it\|yes\|ok\|okay\|approved\|ship it\|that works\|let'?s do)\b` (case-insensitive) AND snippet length < 200 chars, increment `attribution_warning_count` and add to `audit_warnings`. This catches the evt_025 pattern at session end without adding per-call complexity. | 2 |
| 3.6 | `AttributionAudit.render` — surface the new counts in the rendered block, ordered by severity: unresolved decisions > unlinked corrections > attribution-warning > orphan-discovery > missing-snippet. Use ordered output so the most-important issue is always first. | 1, 2, 3 |

### 4. Tool warning refinements (`src/trace_mcp/tools/logging_tools.py`)

Text-only changes. No new warnings, no removed warnings. Sharpen what's there.

| # | Location | Change | Issue addressed |
|---|---|---|---|
| 4.1 | `logging_tools.py:~109` (FM17, `log_annotation` correction branch) | Sharpen text: "Correction logged without conversation_snippet. Set to the relevant user message (~200 chars), or use '<no recent user message>' if no user message motivated this correction. Silent omission is a v1.0 protocol violation per spec §3.4.1." | 1 |
| 4.2 | `logging_tools.py:~163` (FM5, `log_contribution` snippet branch) | Sharpen text: "Contribution logged without conversation_snippet. Set to the relevant user message (~200 chars), or use '<autonomous-stretch>' if working autonomously from a prior decision. Silent omission is a v1.0 protocol violation per spec §3.4.1." | 1 |
| 4.3 | `logging_tools.py:~170` (`log_contribution` related_decision_ids warning) | Demote: only fire when the session has at least one decision event. If the session has no decisions, "consider linking to the decision(s)" is noise that trains the controller to ignore the whole warning block. | 1 (warning fatigue) |
| 4.4 | `logging_tools.py:~38` (`log_tool_call` docstring) | Replace "Log a tool call made to another MCP server" with "Log an automated tool or service invocation. Covers MCP tools, external non-MCP tools, and host-internal subagent dispatchers (set `host` accordingly)." | 5 |
| 4.5 | `logging_tools.py:~50-56` (FM23 exploratory-call warning) | Make host-aware: only fire on `host="mcp"` for the MCP-typical names (`read`/`glob`/`grep`/`bash`); for `host="internal"` warn separately on the host's internal-exploratory names if needed. Keep FM22 (TRACE self-call block) unchanged. | 5 |

### 5. PROV-LD exporter changes (`src/trace_mcp/schema/prov_mapping.py` and exporter)

| # | Change | Issue addressed |
|---|---|---|
| 5.1 | Update `PROV_MAPPING` dict to split correction into two entries: `AnnotationData.corrects_event_ids[evt_*]` → `prov:wasInvalidatedBy`; `AnnotationData.corrects_event_ids[<scheme>:*]` → `prov:wasInfluencedBy`. Add `AnnotationData.related_event_ids` → `prov:wasInformedBy`. | 4 |
| 5.2 | In the PROV exporter, when emitting correction annotations, dispatch on entry shape: event-ID → `wasInvalidatedBy` triple; URI → `wasInfluencedBy` triple with `prov:atLocation` carrying the URI. | 4 |
| 5.3 | Update spec §6 table to match. | 4 |

### 6. Configuration / documentation updates

Claude Code-specific files but the content generalizes — other host AIs would mirror in their own CLAUDE.md-equivalent.

| # | File | Change | Issue addressed |
|---|---|---|---|
| 6.1 | `~/.claude/CLAUDE.md:~52` (Decision row in What-to-Log table) | Update to reference Proposer Identity Rule: "Set proposed_by to the actor who authored the proposal content (whose words populate description), not the speaker of the resolving directive. Question→AI-proposal→accept means proposed_by=ai, resolved_by=human." | 2 |
| 6.2 | `~/.claude/CLAUDE.md:~53` (Correction row) | Update: "Set corrects_event_ids linking to the events being corrected. If the corrected entity is not yet a TRACE event (subagent output, tool result, external claim), use a URI-form reference per spec §3.7.1: `jsonl:<path>#L<line>`, `subagent:<id>`, `tool-result:<call-id>`, or `external:<uri>`." | 4 |
| 6.3 | `~/.claude/CLAUDE.md:~58` (Annotations: SOMETIMES tier) | Add `discovery` to the category list with: "non-trivial finding from autonomous work that carries causal load — log AT THE MOMENT, not in a post-hoc summary." | 3 |
| 6.4 | `~/.claude/CLAUDE.md:~91` (Attribution rules — conversation_snippet) | Update: "Always set conversation_snippet on contributions and corrections to the relevant user message (~200 chars). If no user message motivated the event (long autonomous execution stretch), set the field to `<autonomous-stretch>` rather than omitting. Silent omission is a protocol violation; explicit absence is honest." | 1 |
| 6.5 | Project `CLAUDE.md` | No changes needed — defers to global protocol. | — |

### 7. Version bump + changelog

| # | File | Change |
|---|---|---|
| 7.1 | `pyproject.toml` | `version = "0.3.1"` (or whatever the project semver scheme is) |
| 7.2 | `src/trace_mcp/schema/session.py` | Update default `trace_version: str = "0.3.1"` |
| 7.3 | `src/trace_mcp/__init__.py` | `__version__ = "0.3.1"` |
| 7.4 | `CHANGELOG.md` | New `0.3.1` entry listing all spec/schema/server changes |
| 7.5 | `schemas/trace-v0.3.json` | Regenerate via `scripts/generate_schema.py`. Verify diff is purely additive. |

---

## Explicitly excluded from v1.0 (deferred to v1.1+)

These were considered and explicitly NOT included, despite some of them being recommended by the per-issue fix subagents. Each has a rationale:

| Excluded item | Source recommendation | Rationale for exclusion |
|---|---|---|
| `trace_log_discovery` convenience tool | F3 fix subagent | Bloat — it's a thin wrapper around `trace_log_annotation(category="discovery")`. Tool surface area should be earned by distinct semantics, not by convenience. |
| FM37 attribution warning at log time | F2 fix subagent | Adds tool complexity at log time. The session-end version (item 3.5 above) catches the same pattern without per-call instrumentation. Defer if field data shows the at-log-time variant is needed. |
| `idle-gap-nudge.sh` Stop hook | F3 fix subagent | Host-specific; cannot generalize. Spec item 1.9 provides the protocol-level recommendation; specific implementation deferred. |
| `dispatch-start.sh` + `dispatch-end.sh` hooks | F5 fix subagent | Host-specific; depends on the optional dispatch metadata fields (which are also deferred). v1.1 lands hooks + metadata together. |
| Additional `ToolCallData` fields (`dispatch_kind`, `prompt_summary`, `result_summary`, `parent_event_id`) | F5 fix subagent | Premature without hooks to auto-populate them. Adding fields no one writes to is bloat. v1.1 adds them when hooks land. |
| New `subagent_dispatch` event type | F5 alternative | Rejected on merits — bloats every consumer (scratchpad, exporters, PROV mapping, JSON schema, markdown export, audit code). An Agent dispatch IS a tool call by every operational measure. |
| New `subagent_claim` event type | F4 alternative | Rejected on merits — wrong layer. A subagent claim is just an utterance; TRACE doesn't log every utterance. Correction with URI anchor is sufficient. |
| Auto-extraction of `conversation_snippet` from a buffer | F1 alternative | Rejected on philosophical grounds — TRACE is stateless across calls. Auto-extracted snippets risk misattribution, violating "Never fabricate, falsify, or retroactively alter." |
| Hard-required `conversation_snippet` at schema level | F1 alternative | Rejected — would push controllers to fabricate snippets ("n/a", "see above") to clear the validator. Sparse honest > dense fabricated. |
| Markdown export rendering changes for dispatches | F5 fix subagent | Cosmetic; depends on dispatch logging being in use, which depends on hooks (deferred). |
| `trace_project_summary` separating MCP/internal counts | F5 fix subagent | Depends on dispatch logging being in use. v1.1. |
| `scripts/audit_coverage.py` retroactive utility | F1 fix subagent | Nice-to-have; not protocol-blocking. v1.1. |
| Schema version bump to 0.4.0 | various | All changes are additive; backward compatible. Reserve 0.4 for actually-breaking changes. |
| Retroactive event injection for existing sessions | various | Would fabricate provenance. Forbidden. Going forward only. |

---

## Effort estimate

For an experienced contributor with the codebase loaded:

- §1 spec edits (12 items): ~half day
- §2 schema additions (2 lines + docstring): ~30 min
- §3 AttributionAudit extension (~80 lines): ~half day
- §4 tool warning text refinements (text-only): ~30 min
- §5 PROV mapping split (~30 lines + spec table): ~1 hour
- §6 CLAUDE.md updates (~10 lines): ~30 min
- §7 version bump + changelog: ~30 min
- Tests: write/update tests for AttributionAudit fields, snippet warning text, PROV mapping changes: ~1 day

**Total: ~2-3 days of focused work.** All changes are additive, no breaking changes, no migrations needed.

---

## Coverage check (does this plan address every audit issue?)

| Audit issue | Pre-release items addressing it | Coverage level |
|---|---|---|
| 1 — conversation_snippet | 1.1, 3.1-3.6, 4.1-4.3, 6.4 | Full: spec MUST + absence-marker + session-end visibility + sharpened tool text |
| 2 — evt_025 attribution | 1.3, 1.10, 1.11, 3.5, 6.1 | Full: spec Proposer Identity Rule + worked example + recognition table row + session-end heuristic warning + CLAUDE.md update |
| 3 — v3 discovery timing | 1.4, 1.9, 1.10, 2.1, 3.4, 6.3 | Full: new category + spec real-time guidance + autonomous-window protocol-level rule + orphan-discovery heuristic + CLAUDE.md update |
| 4 — corrects_event_ids | 1.5, 1.6, 1.7, 1.8, 5.1-5.3, 6.2 | Full: spec URI scheme + correction provenance rewrite + PROV split + CLAUDE.md update |
| 5 — Agent dispatches | 1.2, 2.2, 4.4, 4.5 | **Partial:** schema `host` field + spec generalization + warning host-awareness. Auto-capture hooks deferred to v1.1. This is acceptable — the v1.0 plan unblocks future dispatch logging without committing to a specific host implementation. Manual dispatch logging is supported on day one. |

---

## What this plan deliberately does NOT do

- **No new tools.** The existing 23-tool surface is sufficient. `trace_log_annotation(category="discovery")` covers the new category.
- **No new hooks.** Host-specific concerns deferred.
- **No new event types.** Both `subagent_dispatch` and `subagent_claim` rejected on merits.
- **No schema-required field changes.** All additions are optional with defaults.
- **No version 0.4.0.** Additive only.
- **No removal of any existing functionality, behavior, or field.**

This is the smallest delta that closes the 5 audit issues at the correctness/reliability level. Anything smaller leaves at least one issue under-addressed; anything larger is bloat.
