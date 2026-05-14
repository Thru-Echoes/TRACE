# TRACE Audit Remediation Plan

**Source audit:** `trace_audit_findings.md` (this directory)
**This document:** verification of those findings + recommended fixes + hooks analysis + AI-agnostic protocol changes
**Methodology:** 5 independent verification subagents + 5 independent fix-design subagents (one per issue), running in parallel. Each subagent instructed to challenge findings, not validate them.

---

## Executive summary

The verification subagents **corrected three substantive errors** in the original audit and confirmed the rest. Most consequential corrections:

1. **Issue 1 (conversation_snippet):** "No enforcement" was wrong. The tool already emits warnings on missing snippet for contributions and for `correction`-category annotations (`logging_tools.py:108-113` and `:162-167`). 16 such warnings fired in this session and were ignored. The defect is not "no enforcement" — it is "warnings are non-blocking and skimmed past." Also: the published spec does **not** assert the "always set" rule — that language lives only in the user's private `~/.claude/CLAUDE.md`. The spec only SHOULDs the snippet for `correction` (§5.2). For an OSS release this is a separate, more important gap than coverage.

2. **Issue 3 (v3 bug discovery):** "2-hour silence between evt_024 and evt_026" was wrong. `evt_025` was logged at 17:51 inside that range. The actual gap is 57 minutes between `evt_025` and `evt_026`. The substantive concern survives — the discovery sat unlogged for 20m 51s and surfaces only in a post-hoc `revision_note` and a contribution description 4 hours later — but the framing needs correction.

3. **Issue 5 (Agent dispatches):** "tool_call schema is MCP-bound" was wrong. The schema is generic (`server: str`, no enum), and spec §3.5 explicitly reads "any computational action performed by an external system at the direction of a participant." The MCP framing lives only in the Pydantic docstring (`events.py:26`) and the default `method="tools/call"`. The real gap is in **guidance** (`~/.claude/CLAUDE.md` "What to Log" doesn't name subagent dispatches), not schema. The fix is a small schema additive (`host` discriminator + optional dispatch fields) plus spec text — not a new event type.

Other verifications:

4. **Issue 2 (evt_025 attribution):** CONFIRMED. Verbatim quotes accurate. The AI authored the three-lever content; the human said "proceed." `evt_025` violates the spec's own line 220 ("proposer MUST NOT be the same instance that resolves it") because it has `proposed_by=human` AND `resolved_by=human` AND `disposition=accepted` — that's the self-approval pattern the spec explicitly forbids in multi-actor workflows. This is systematic, will recur.

5. **Issue 4 (corrects_event_ids):** PARTIALLY CONFIRMED. The schema is permissive — `list[str]` with no validator (`events.py:73`). The spec at §4.4 explicitly says consumers MUST tolerate dangling references. Workarounds were available (URI refs, log-then-correct, log the subagent dispatch as a tool_call first). The split is ~40% protocol/docs gap (no guidance for non-event correction targets) and ~60% controller discipline (workarounds existed and weren't used). Also: `evt_008` (Task 3 test flake) is **correctly** classified as `gotcha`, not a misclassified correction — the original audit reached too far on that one.

After verification, **the five issues remain real but two need narrative correction.** The fixes below are the OSS-release-blocking subset.

---

## Per-issue verification + fix bundle

### Issue 1 — conversation_snippet

**Verification verdict:** PARTIALLY CONFIRMED with critical narrative correction.

**What's true:**
- Coverage numbers: 4/28 (14.3%), 2/2 decisions, 2/17 contributions, 0/9 annotations — exact.
- Snippets live only at `context.conversation_snippet`; sub-objects don't carry the field (`events.py:21`).
- Controllers ignored 16 in-tool warnings.

**What was wrong in the original audit:**
- "Protocol asserts a requirement with no enforcement" is doubly wrong:
  - The published spec does NOT assert the requirement. Only §5.2 SHOULDs it for corrections. The "always set" rule lives in `~/.claude/CLAUDE.md:91`, which is **private user config**, not the published protocol.
  - The tool DOES enforce — `logging_tools.py:109-113` warns on missing snippet for correction annotations; `:163-167` warns unconditionally for contributions. Both warnings fired this session and were ignored.
- The sharpest violation is `evt_003`, which is a `correction`-category annotation — the only event with a spec-level SHOULD requirement on snippet. The original audit lumped it into the generic "0/9 annotations" stat.

**Fix bundle (ranked, pre-release):**

| # | Layer | Change | Priority |
|---|---|---|---|
| 1 | Session-end audit | Add `missing_snippet_contribution_count` and `missing_snippet_correction_count` to the `AttributionAudit` returned by `trace_end_session`. Render in the audit block (post-hoc visibility — controllers do read the audit block, they don't read per-call warnings). | **PRE-RELEASE** |
| 2 | Tool warnings | Sharpen the existing warning text in `logging_tools.py:109,163` to be prescriptive: name the failure mode and give an out (`<autonomous-stretch>` marker). | **PRE-RELEASE** |
| 3 | Spec language | Replace the soft "particularly important" prose in `specification.md:164` with a normative MUST on contributions + corrections, paired with an explicit-absence escape valve. Producers MUST set the field when a recent user message exists; MUST use a marker (`<autonomous-stretch>` or `<no recent user message>`) otherwise. This makes "no user message" a first-class state, distinct from "controller forgot." | **PRE-RELEASE** |
| 4 | Schema | Keep `conversation_snippet: str \| None = None`. No type change. | — |
| 5 | Auto-extraction | **REJECT.** TRACE is stateless across calls; there is no conversation buffer to extract from. And misattributed snippets are worse than missing ones. | **REJECT** |
| 6 | Hard-required schema field | **REJECT.** Blocking writes pushes controllers to fabricate snippets ("see above", "n/a") to clear the gate. Sparse honest > dense fabricated. | **REJECT** |

**Concrete code change** (the load-bearing one — see `trace_audit_remediation_plan_code.md` if needed for the full diff; here's the shape):

```python
# session_tools.py: AttributionAudit
class AttributionAudit(BaseModel):
    ...
    missing_snippet_contribution_count: int = 0
    missing_snippet_correction_count: int = 0
    explicit_absence_snippet_count: int = 0  # honest absences

# session_tools.py: _build_attribution_audit
def _is_explicit_absence(s: str | None) -> bool:
    return s is not None and s.startswith("<") and s.endswith(">")

# (loop over session.events, increment the three counters)

# render: include the counts in the audit block alongside unlinked_correction_count
```

**Spec edit:** in §3.4.1 replace lines 161-164 with a MUST clause + the absence-marker convention (full text in F1 subagent report; ~10 lines of spec).

---

### Issue 2 — evt_025 attribution

**Verification verdict:** CONFIRMED, including systematic-not-one-off.

**What's true:**
- JSONL line 883 (user 17:48): "what needs to happen now then to improve the matcher quality and %?" — a question.
- JSONL line 888 (assistant 17:49): the full "Three levers in priority order: 1. Stage B prompt tuning... 2. Stage A normalization... 3. Threshold calibration..." — the AI authored the proposal content.
- JSONL line 891 (user 17:49): "proceed with those steps..." — human accepts.
- `evt_025.description` is a paraphrase of the AI's line 888.
- `evt_025` has `proposed_by={type:human}` AND `resolved_by={type:human}` AND `disposition=accepted` — this is exactly the self-approval pattern the spec's line 220 explicitly forbids in multi-actor workflows.
- The spec's `suggestion_type="requested"` enum value is glossed only as "(human asked)" — ambiguous between "AI authored in response to a human question" and "the human's request itself constitutes the proposal." This is a real spec gap that produces the systematic behavior.

**What was right in the original audit:**
- Everything substantive. Quotes accurate. Diagnosis correct. The "systematic, not one-off" framing is correct.

**Comparison to evt_001:** `evt_001` is defensibly `proposed_by=human` because the Phase I plan existed in the prior session and the human directed continuation in this session ("continue with where the last claude code left off"). No AI proposal was generated in this session prior to evt_001. The contrast confirms `evt_025` is the bug, not `evt_001`.

**Fix bundle (ranked, pre-release):**

| # | Layer | Change | Priority |
|---|---|---|---|
| 1 | Spec | Add **Proposer Identity Rule** to §3.6: `proposed_by` MUST identify the actor who authored the **content** of the proposal — the words populating `description` — not the actor who spoke the directive to act. Include a disambiguation table covering the four canonical patterns. | **PRE-RELEASE** |
| 2 | Spec — Appendix A | Add a worked example for the question→AI-proposal→human-accept flow. Currently Appendix A only shows `proactive` AI proposals; no `requested` example exists. | **PRE-RELEASE** |
| 3 | Tool validation | Add FM37 warning in `decision_tools.py` that fires when `proposed_by_type="human"` + `conversation_snippet` is short + the snippet begins with an acceptance phrase (regex: `^[\s"']*(proceed\|go ahead\|sounds good\|do it\|yes\|ok\|okay\|approved\|ship it\|that works)\b` (case-insensitive). Soft-warn, not block. | **PRE-RELEASE** |
| 4 | Schema | **No change.** Existing fields (`proposed_by`, `resolved_by`, `suggestion_type`) encode the three required dimensions correctly. Adding a new field would shift the bug to a new location. | — |
| 5 | Global CLAUDE.md | Update the Decision row to reference the new spec rule + give the heuristic "before logging, ask whose words `description` paraphrases — that actor is the proposer." | **PRE-RELEASE** |

**The canonical rule (one sentence):** *`proposed_by` identifies whoever authored the content of the proposal, regardless of who spoke last; if `description` paraphrases the AI's reply to a human question, `proposed_by` is the AI.*

---

### Issue 3 — v3 bug discovery timing

**Verification verdict:** PARTIALLY CONFIRMED — substantive concern valid, framing needs correction.

**Narrative correction:**
- Original audit said "2-hour silence between evt_024 (16:27) and evt_026 (18:48)." Wrong — `evt_025` was logged at 17:51 inside that range.
- Correct framing: 57-minute gap between `evt_025` and `evt_026`, during which the v3 discovery happened (18:27) and sat unlogged for 20m 51s before the next TRACE call.
- The bug only surfaces in (a) `evt_025`'s `revision_note` at 18:48 — 20m after discovery — as a single trailing sentence in a multi-iteration roll-up, and (b) `evt_027`'s contribution description at 22:28 — 4 hours after discovery — as parenthetical text inside a v2/v3/v4 summary.

**What survives the correction:**
- The bug was load-bearing (AI's own assessment in `evt_027`: "Bug fix from v3 is load-bearing — without it v4 would be much worse").
- No dedicated annotation, correction, or gotcha was logged for the discovery moment.
- Global `~/.claude/CLAUDE.md:67` explicitly says "Interleave logging with code work. Do not defer to session end." That rule was violated.
- Future readers scrolling the TRACE timeline see only "sample-mode variance" (evt_026) as the highlighted learning of the iteration phase, with the Pydantic bug buried as parenthetical sub-text.

**Fix bundle (ranked, mixed pre- and post-release):**

| # | Layer | Change | Priority |
|---|---|---|---|
| 1 | Schema | Add `discovery` to the `AnnotationData.category` literal: a non-trivial finding surfaced by autonomous or unattended work that carries causal load (changes what happens next). Differs from `gotcha` (surprising but nobody was wrong) and `correction` (nothing prior was wrong; new information surfaces). | **PRE-RELEASE** |
| 2 | Tool | Add `trace_log_discovery` as a thin convenience wrapper around `trace_log_annotation(category="discovery")`. The named tool surfaces the action in the controller's tool list. | **PRE-RELEASE** |
| 3 | Spec | Add §8.1 paragraph: discoveries / corrections / gotchas SHOULD be logged at the moment of the underlying event, not folded into later contributions. A contribution that introduces a fact not present elsewhere is evidence of deferred logging. | **PRE-RELEASE** |
| 4 | Session-end audit | Heuristic warning: if a contribution's description contains discovery-language ("discovered", "turned out", "found a bug", "load-bearing fix", "all along") with no near-in-time `discovery`/`correction`/`gotcha` annotation in the prior 30 min, surface a warning in the attribution audit. | **PRE-RELEASE** |
| 5 | Adapter hook | Add `idle-gap-nudge.sh` Claude Code Stop-hook (or PostToolUse on substantive tools) that fires when N tool calls or T minutes pass without a `trace_*` event. Soft nudge: "if a discovery, correction, decision, or contribution happened in this window, log it now." Configurable: `TRACE_IDLE_MAX_SEC` (default 1200), `TRACE_IDLE_MAX_TOOLS` (default 15), `TRACE_IDLE_COOLDOWN_SEC` (default 600), `TRACE_IDLE` mode (`off`/`soft`/`strict`). | **POST-RELEASE v1.1** |
| 6 | Auto-extract / backdate | **REJECT.** TRACE never fabricates events. A backdated synthetic discovery event would dishonestly claim real-time provenance. The honest approach is a late-log annotation whose `content` field states "Discovered ~18:27 UTC during v3 implementer run; logged retroactively at session-end review" with the timestamp as logging time. | **REJECT** |

**The taxonomy distinction** (worth repeating because it's the load-bearing decision):
- `gotcha` — surprising but nobody was wrong (sample-mode variance: yes; Pydantic crash: no — implementer WAS wrong about clean tests)
- `correction` — someone catches and fixes a prior mistake (Task 1 pyright false claim)
- `discovery` — new information surfaces from autonomous work that carries causal load (v3 Pydantic crash, root-cause findings during long iteration loops)

---

### Issue 4 — corrects_event_ids

**Verification verdict:** PARTIALLY CONFIRMED — ~40% protocol/docs gap, ~60% controller discipline.

**Narrative correction:**
- The schema is permissive: `corrects_event_ids: list[str]` with no validator (`events.py:73`).
- Spec §4.4 explicitly says: "Consumers MUST tolerate dangling references."
- §5.2 SHOULDs (not MUSTs) the linking.
- Workarounds were genuinely available — the controller could have logged the implementer dispatch as a `tool_call` first (per CLAUDE.md's "USUALLY: domain tool calls" guidance) and then linked, or used a synthetic external ref like `subagent:ad9350bfec6ce79f9`. None of these workarounds are documented as patterns, but they all would have validated.
- `evt_008` (Task 3 test flake) is **correctly** classified as gotcha — the original audit overreached by calling it "structurally a correction." The controller's own text explicitly disclaimed wrongness: "The matcher is doing its job — the test fixture is non-deterministic." That's gotcha definition.

**What survives:**
- The protocol does not document a pattern for corrections whose target isn't a TRACE event. This is a real documentation gap, not just a controller miss.
- `evt_003` has empty `corrects_event_ids` and the TRACE auto-audit flagged it. That flag is the protocol working — but the controller had no documented next-step.

**Fix bundle (ranked, pre-release):**

| # | Layer | Change | Priority |
|---|---|---|---|
| 1 | Spec | Add §3.7.1 "External References in `corrects_event_ids`" — define URI schemes: `jsonl:<path>#L<line>`, `subagent:<agent-id>`, `tool-result:<call-id>`, `external:<uri>`. Each entry MUST be either an event ID or a URI-form ref. Prefix-discriminate by `:` in the string. | **PRE-RELEASE** |
| 2 | Spec | Rewrite §5.2 to handle three anchor cases: (a) in-session event ID, (b) URI-form external ref, (c) anchor in `conversation_snippet` only. (c) is acceptable only when both event IDs and URIs are unavailable. | **PRE-RELEASE** |
| 3 | Tool warning | Adjust FM17 in `logging_tools.py:101-106` to only fire when `corrects_event_ids` is empty AND `conversation_snippet` is null. Add new warning: when `corrects_event_ids` is empty but `related_event_ids` is non-empty on a correction, flag the workaround anti-pattern. | **PRE-RELEASE** |
| 4 | Schema | **No type change.** Keep `corrects_event_ids: list[str]`. The URI scheme rides on the existing string field via prefix-discrimination — backward compatible, no schema bump. | — |
| 5 | PROV mapping | Split correction mapping: `evt_*` entries → `prov:wasInvalidatedBy`; URI entries → `prov:wasInfluencedBy` + `prov:atLocation`. Current single mapping (`prov:wasRevisionOf`) conflates revision (evolutionary) with correction (repudiatory). | **PRE-RELEASE** |
| 6 | CLAUDE.md | Update Correction row: "If the corrected entity is not yet a TRACE event (subagent output, tool result, external claim), use a URI-form external reference like `jsonl:<path>#L<line>` or `subagent:<id>`, or anchor via `conversation_snippet`. `related_event_ids` is for loose association, not for the correction relationship." | **PRE-RELEASE** |
| 7 | New `subagent_claim` event type | **REJECT.** Wrong layer. A subagent claim is just an utterance; TRACE doesn't log every utterance. The correction is sufficient; the corrected claim doesn't need its own event. | **REJECT** |

---

### Issue 5 — Agent dispatches uncovered

**Verification verdict:** PARTIALLY CONFIRMED — count exact, schema-bound claim wrong.

**Narrative correction:**
- 42 Agent dispatches confirmed.
- Original audit's category breakdown is numerically off in two of six bins: 18 implementer (not 16), 3 re-review (not 6), 6 fix (not 5). Total still 42; internal proportions different.
- The `tool_call` schema is **NOT** MCP-bound. `server: str` is unconstrained (`events.py:28`). Spec §3.5 is technology-neutral: "any computational action performed by an external system at the direction of a participant." Spec §7.5 explicitly says "This specification does not require MCP." The MCP framing exists in two docstrings (`events.py:26`, `logging_tools.py:38`) and the default `method="tools/call"` — that's it.
- The real gap is in CLAUDE.md guidance, which doesn't mention subagent dispatches at all. Under spec §3.5's "perform substantive computation (database queries, API calls, model inference, file transformations)" guidance, subagent dispatches qualify (they ARE model inference). The controller followed CLAUDE.md silence, not spec direction.

**What survives:**
- 42 LLM-billable work events with cost/latency/error information are uncaptured.
- The dispatch graph that produced 17 contributions cannot be reconstructed from TRACE alone.
- Future TRACE consumers studying "subagent-driven development as methodology" would have to read JSONL transcripts instead of querying events.

**Fix bundle (ranked, pre-release schema + post-release adapter):**

| # | Layer | Change | Priority |
|---|---|---|---|
| 1 | Schema | Add to `ToolCallData`: `host: Literal["mcp","internal","external"] = "mcp"` (default preserves v0.3 semantics), `dispatch_kind: str \| None = None`, `prompt_summary: str \| None = None`, `result_summary: str \| None = None`, `parent_event_id: str \| None = None`. All optional, all additive. | **PRE-RELEASE** |
| 2 | Spec | Generalize §3.5: "Records an automated tool or service invocation — covering external MCP tools, external non-MCP tools (HTTP APIs, CLI subprocesses), and host-internal tools (subagent dispatchers in Claude Code, Codex, ChatGPT, etc.)." | **PRE-RELEASE** |
| 3 | Tool API | Add new kwargs to `trace_log_tool_call`: `host`, `method`, `dispatch_kind`, `prompt_summary`, `result_summary`, `parent_event_id`. Existing call sites work unchanged. | **PRE-RELEASE** |
| 4 | CLAUDE.md | Add to "What to Log" `USUALLY` tier: "Subagent dispatches when their outcome is summarized by a contribution. Set `host='internal'`, `server='claude-code'`, `dispatch_kind=<role>`. Use `parent_event_id` to link back to the controller event that motivated the dispatch." | **PRE-RELEASE** |
| 5 | Claude Code adapter hooks | New `dispatch-start.sh` (PreToolUse matcher `Task`) writes a tmp file with dispatch metadata + start time; `dispatch-end.sh` (PostToolUse matcher `Task`) reads tmp, computes `duration_ms`, calls `trace_log_tool_call` with the full dispatch shape. Fail-open. | **POST-RELEASE v1.1** |
| 6 | Volume management | Markdown export renders dispatch rows as collapsed one-liners (`Task[implementer] (187s, success)`); scratchpad still excludes tool_calls; `trace_project_summary` reports MCP vs internal dispatch counts separately. | **POST-RELEASE v1.1** |
| 7 | PROV mapping | `parent_event_id` → `prov:wasInformedBy`; add `trace:host`, `trace:dispatchKind` activity attributes. | **PRE-RELEASE** (small) |
| 8 | New `subagent_dispatch` event type | **REJECT.** Bloats every consumer (validator, exporters, scratchpad, PROV mapping, markdown, JSON schema regen). Sets a precedent of per-host event types. An Agent dispatch IS a tool call by every operational measure. | **REJECT** |
| 9 | Version bump | Use `trace_version: "0.3.1"` (additive). Do not bump to 0.4.0; reserve that for actually-breaking changes. | **PRE-RELEASE** |

**Backward compatibility:** `host` defaults to `"mcp"`. Existing sessions load unchanged. New optional fields are forward-tolerated per spec §1.3. JSON Schema additive only.

---

## Question 3 — Would pre/post hooks for Claude Code help these issues?

**Yes for four of five issues. Synthesis:**

| Issue | Hook helps? | Which hook | Why |
|---|---|---|---|
| 1 — conversation_snippet | **Indirect** | PostToolUse on `trace_end_session` (already exists: `decision-audit.sh`). Extend it to render the new `missing_snippet_*_count` fields prominently. | The fix is server-side (cumulative audit count). The hook just *displays* the result. No new hook needed — extend the existing one. |
| 2 — evt_025 attribution | **Yes (small)** | PostToolUse on `trace_propose_decision`. Echo the FM37 warning in a way the controller cannot skim past (the existing tool warning is in the return-value tail, which controllers ignore — same fail mode as Issue 1's snippet warning). | The validation belongs in the tool, the hook surfaces it loudly. Not strictly required; tool-side warning is enough for v1.0. |
| 3 — v3 bug discovery | **Yes (major)** | New `Stop` or `PostToolUse` hook: `idle-gap-nudge.sh`. Tracks time and tool-count since last `trace_*` event; nudges when threshold exceeded. | This is the **only mechanism** that closes the autonomous-execution-window gap. Hooks fire on user-triggered events today (SessionStart, UserPromptSubmit, PreToolUse Edit/Write) — none fire when the AI is grinding through subagent dispatches with no user in the loop. The Stop-hook closes that. **This is the single highest-leverage hook change.** |
| 4 — corrects_event_ids | **No** | — | Pure spec/documentation issue. Hooks don't fix protocol semantics; they enforce them. |
| 5 — Agent dispatches | **Yes (major)** | New `PreToolUse` + `PostToolUse` hook pair on `Task` matcher: `dispatch-start.sh` + `dispatch-end.sh`. Auto-capture each dispatch via `trace_log_tool_call(host='internal', ...)`. | The whole point of dispatch logging is that it should happen automatically, without controller discipline. Manual logging of 42 dispatches per session is impractical; hooks make the schema fix actually useful. |

**Three new hooks recommended for v1.0 / v1.1:**

1. **`idle-gap-nudge.sh`** (Stop or PostToolUse) — addresses Issue 3. v1.1 (host-specific; spec recommends but doesn't require).
2. **`dispatch-start.sh` + `dispatch-end.sh`** (PreToolUse + PostToolUse on `Task`) — addresses Issue 5. v1.1 (auto-capture makes the schema fix useful).
3. **Extension of existing `decision-audit.sh`** — addresses Issue 1 + 2 + 3's session-end visibility. v1.0 (small change to existing hook).

**What hooks CANNOT fix:**
- Issue 4 (corrects_event_ids) is purely a spec/docs problem. No hook can paper over missing protocol guidance.
- The fundamental attribution semantics in Issue 2 (whose words count) cannot be inferred reliably by a hook — it's a controller-discipline question informed by the spec's new rule.
- The `discovery` taxonomy gap (Issue 3) is a schema question, not a hook question. Hooks help *enforce* discovery logging, but the category has to exist first.

**Cost/benefit per hook:**
- `idle-gap-nudge.sh`: ~80 lines bash + state file. Pays back the first time it catches a 2-hour silent gap. Configurable env-var defaults so noisy projects can tune. Soft mode default (warn, don't block).
- `dispatch-{start,end}.sh`: ~150 lines bash total + tmp-file coordination. Pays back every session that uses subagents (every nontrivial session). Risk: error in the hook fails the underlying `Task` invocation if not implemented fail-open. Required: rigorous fail-open discipline (every error path → exit 0).
- Extension of `decision-audit.sh`: ~20 lines. Trivial.

---

## Question 4 — AI-agnostic protocol fixes

These are changes to TRACE itself (schema, spec, server tools) that benefit every AI client, not just Claude Code:

### Schema additions (additive, no version bump beyond 0.3.1)

1. **`AnnotationData.category`** — add `"discovery"` to the literal. (Issue 3)
2. **`ToolCallData`** — add optional `host`, `dispatch_kind`, `prompt_summary`, `result_summary`, `parent_event_id` fields. (Issue 5)
3. **`AttributionAudit`** (in `session_tools.py`) — add `missing_snippet_contribution_count`, `missing_snippet_correction_count`, `explicit_absence_snippet_count`, `orphan_discovery_warning_count` fields. (Issue 1, Issue 3)

### Spec text changes (`docs/specification.md`)

| Section | Change | Issue |
|---|---|---|
| §3.4.1 | Replace soft "particularly important" prose with normative MUST clause on contribution + correction snippets, plus absence-marker convention | 1 |
| §3.5 | Generalize "Tool Invocation" to cover host-internal tools (subagent dispatchers). Document `host` field semantics. Update "What to log" guidance | 5 |
| §3.6 | Add **Proposer Identity Rule** — proposer is whoever authored the proposal content, not the speaker of the directive. Add disambiguation table for the four canonical patterns | 2 |
| §3.7 | Add `discovery` category to the annotation categories table with criteria distinguishing from `gotcha` and `correction` | 3 |
| §3.7.1 (new) | "External References in `corrects_event_ids`" — define URI schemes (`jsonl:`, `subagent:`, `tool-result:`, `external:`). Document anchor patterns | 4 |
| §4.4 | Split: `corrects_event_ids` MAY use URI-form refs (per 3.7.1); other relation fields stay event-ID-only | 4 |
| §5.2 | Rewrite Correction Provenance to handle three anchor cases (event ID / URI / snippet-only) | 4 |
| §6 (PROV) | Split correction mapping: event-target → `prov:wasInvalidatedBy`; URI-target → `prov:wasInfluencedBy` + `prov:atLocation`. Add `prov:wasInformedBy` for `parent_event_id` | 4, 5 |
| §8.1 | Add: discoveries / corrections / gotchas SHOULD be logged at the moment of the underlying event, not folded into post-hoc contributions. Add: hosts SHOULD detect long autonomous-execution windows and nudge controllers to log | 3 |
| §8.2 | Add row to natural-language recognition table for discovery phrases ("discovered that X", "found a bug", etc.). Add row for question→AI-proposal→acceptance pattern | 2, 3 |
| Appendix A | Add a worked example for the question→AI-proposal→human-accept flow (currently only `proactive` examples exist) | 2 |
| Version history | Bump `trace_version` to `0.3.1`; note additive nature, fully backward compatible | 1-5 |

### New tools / tool changes (in `src/trace_mcp/tools/`)

1. **`trace_log_discovery`** — convenience wrapper around `trace_log_annotation(category="discovery")`. Surfaces "log a discovery now" in the tool list. (Issue 3)
2. **`trace_log_tool_call`** — add `host`, `dispatch_kind`, `prompt_summary`, `result_summary`, `parent_event_id` kwargs. (Issue 5)
3. **`trace_propose_decision`** — add FM37 warning (proposer-attribution heuristic). (Issue 2)
4. **`trace_log_contribution`** — sharpen FM5 warning text; relax `related_decision_ids` warning to only fire when session has decisions. (Issue 1)
5. **`trace_log_annotation`** (category=correction) — relax FM17 warning to only fire when both `corrects_event_ids` and `conversation_snippet` are empty; add new warning when `corrects_event_ids: []` co-occurs with non-empty `related_event_ids` on a correction. (Issue 4)
6. **`trace_end_session`** — extend `AttributionAudit` rendering with the new count fields and orphan-discovery warnings. (Issue 1, Issue 3)

### PROV-LD mapping changes (`src/trace_mcp/exporters/`)

- Correction → `prov:wasInvalidatedBy` (when target is event) or `prov:wasInfluencedBy` (when target is URI). Drop the conflated `prov:wasRevisionOf` for corrections.
- Dispatch `parent_event_id` → `prov:wasInformedBy`.
- `trace:host`, `trace:dispatchKind` activity attributes for non-MCP tool calls.

### Documentation changes (`~/.claude/CLAUDE.md`)

These are user-config changes — Claude Code-specific but the *content* generalizes to whatever CLAUDE.md-equivalent any future host AI uses:

- Add `discovery` to the "ALWAYS" log tier (or "SOMETIMES" with explicit criteria — needs user decision).
- Add subagent dispatch logging to "USUALLY" tier with the new schema shape.
- Update the Decision row to reference the Proposer Identity Rule.
- Update the Correction row to reference URI-form anchors.
- Update the snippet guidance to use the absence-marker convention.

### What stays Claude-Code-only

- The specific hooks (`idle-gap-nudge.sh`, `dispatch-start.sh`, `dispatch-end.sh`) live in `src/trace_mcp/adapters/claude_code/assets/hooks/`. They are host-specific because hooks ARE host-specific.
- The spec recommends the *protocol-level behavior* ("hosts SHOULD detect idle windows", "hosts SHOULD auto-capture dispatch events when feasible"). Other adapters (Codex, future ChatGPT, future Cursor) implement the equivalent in their own native mechanisms.
- Codex adapter spec at `src/trace_mcp/adapters/codex/README.md` should be updated to reference the new `host="internal"` shape so a future Codex implementor doesn't reinvent.

---

## Sequencing for OSS release

### v1.0 pre-release blockers (must ship)

These are spec/server-side changes that lock the protocol shape. Anything not landed before v1.0 freezes either becomes a breaking change later, or ships as "non-conforming" in real-world TRACE sessions.

| Order | Change | Effort estimate | Why pre-release |
|---|---|---|---|
| 1 | `discovery` annotation category (schema) | XS | Locks event taxonomy |
| 2 | `host` + dispatch fields on `ToolCallData` (schema) | S | Locks event taxonomy |
| 3 | Spec §3.5 generalization | S | Aligns text with already-generic schema |
| 4 | Spec §3.6 Proposer Identity Rule + Appendix A example | M | Disambiguates the single most common attribution failure |
| 5 | Spec §3.7.1 + §4.4 + §5.2 URI-form refs | M | Documents the corrects_event_ids workaround so controllers know it exists |
| 6 | Spec §3.4.1 conversation_snippet MUST + absence marker | S | Aligns published spec with the de-facto protocol |
| 7 | `AttributionAudit` extended counts + rendering | M | Surfaces the silent ignored-warning failures from this audit |
| 8 | `trace_log_discovery` tool | XS | One-line wrapper, but the named tool surfaces the new category |
| 9 | FM37 attribution warning | S | Catches the evt_025 pattern at log time |
| 10 | FM17 + FM5 warning refinements | XS | Sharpen text, demote redundant warnings |
| 11 | PROV mapping split (correction: invalidated-by vs influenced-by) | S | Aligns export with new spec |
| 12 | `~/.claude/CLAUDE.md` updates (user's own config) | XS | Pairs with each spec change |
| 13 | `trace_version` bump to `0.3.1` + changelog | XS | Documents the additive changes |

Total: ~3-5 days of focused work for an experienced contributor. None requires breaking changes.

### v1.1 post-release (high-value additions)

| Change | Why post-release |
|---|---|
| `idle-gap-nudge.sh` hook | Host-specific; spec recommends behavior at protocol level, ship implementation when there's adoption signal |
| `dispatch-start.sh` + `dispatch-end.sh` hooks | Host-specific; needs careful fail-open testing; iterate on threshold defaults based on real telemetry |
| Markdown export rendering for dispatches | Cosmetic; depends on dispatch logging being in use |
| `trace_project_summary` separating MCP vs internal dispatch counts | Depends on dispatch logging being in use |
| `scripts/audit_coverage.py` — retroactive coverage analysis utility | Useful for OSS adopters auditing their own sessions; not protocol-blocking |
| Codex adapter implementation (currently placeholder) | Independent of these audit findings |

### Explicit non-goals for v1.0

These do NOT need to change before OSS release:

- **Conversation buffer for auto-snippet-extraction** — rejected on philosophical grounds.
- **`subagent_dispatch` as a new top-level event type** — rejected in favor of `host` field on `tool_call`.
- **`subagent_claim` as a new event type** — rejected; correction is sufficient with URI anchor.
- **Hard-required `conversation_snippet` at schema level** — rejected; pushes to fabrication.
- **Schema version bump to 0.4.0** — all changes are additive; reserve 0.4 for actually-breaking changes (e.g., eventual `mcp_servers` → `tools` rename).
- **Retroactive event injection for existing sessions** — would fabricate provenance. Going forward only.

### On efficiency (your earlier framing)

Confirmed: none of the v1.0 blockers above are efficiency concerns. They are correctness, coverage, and protocol-clarity items — appropriate priorities for an OSS release that needs to be trustworthy first.

Post-release efficiency work (atomic writes overhead, scratchpad generation latency, BM25 vs LLM matching speed) remains valid as v1.2+ scope.

---

## Corrections to original `trace_audit_findings.md`

For honesty in the audit record, the following claims in the original findings need correction (changes also tracked in this remediation plan, but recording the deltas explicitly here):

1. **Dimension E** — "protocol asserts a requirement with no enforcement" is wrong. The spec does NOT assert the requirement for contributions (only for corrections at §5.2 SHOULD). The tool DOES enforce via warnings (FM5 in `logging_tools.py:108-113, 162-167`). Correct framing: "the de-facto protocol requires snippets via private user config, the published spec is silent on contributions, and the tool warnings fire but are skimmed past."

2. **Dimension G** — "2-hour silence between evt_024 (16:27) and evt_026 (18:48)" is wrong. `evt_025` was logged at 17:51 in that range. Correct framing: "57-minute gap between evt_025 and evt_026, during which the v3 discovery happened (18:27) and sat unlogged for 20m 51s."

3. **Dimension C** — `evt_008` (Task 3 test flake) was characterized as "structurally a controller correction of three converging false-passes." It is correctly classified as `gotcha`. The controller's own framing ("the matcher is doing its job; the test fixture is non-deterministic") disclaims wrongness — gotcha definition.

4. **Dimension F** — "tool_call schema is MCP-bound" is wrong. The schema is generic; only docstrings and the default `method="tools/call"` lean MCP. The gap is in CLAUDE.md guidance, not schema.

5. **Dimension F — dispatch count breakdown** — original audit said 16 implementer + 6 spec + 5 code-review + 5 fix + 4 verifier + 6 re-review = 42. Verified count: 18 implementer + 6 spec + 5 code-review + 6 fix + 4 verifier + 3 re-review = 42. Total matches; internal categories off in three bins.

These corrections do not change the conclusions, but they do change the framing of two issues (Dimension E, Dimension F) from "protocol bug" to "documentation/discipline gap." That's a meaningful distinction for the OSS release because it affects which fix layer carries the burden.
