# TRACE Protocol Changes — Exact Edits Preview

**Date:** 2026-03-03
**Source:** Audit of session `trace_20260303_cb682d` → gap analysis in `trace_protocol_improvement_analysis.md`

This document shows the **exact text changes** for each target file, organized as find → replace blocks. Review these, then apply.

---

## Priority Assessment

| File | Priority | Reason |
|------|----------|--------|
| `skill/TRACE.md` | **PRIMARY** | Operational skill — what the AI actually reads at runtime |
| `~/.claude/CLAUDE.md` | **PRIMARY** | Global instructions — fallback if skill not loaded |
| `USER_GUIDE.md` | SKIP | Uses v1.0 data model (sessions, code contributions, ideas, errors). Needs broader rewrite beyond these 4 changes. |
| `TRACE_PROTOCOL.md` | SKIP | Formal spec v1.0. Same issue — schema-level document, not operational rules. |

**Recommendation:** Only edit the two primary files. `USER_GUIDE.md` and `TRACE_PROTOCOL.md` need a separate, larger update to align with the current trace-mcp v0.2.0 event model.

---

## File 1: `skill/TRACE.md`

**Path:** `/Users/echoes/Documents/Berkeley/Research/TRACE/skill/TRACE.md`

### Change 1A: Add per-type timing table + one-artifact rule (after line 20, before Logging Priority)

**INSERT** the following new section between "Session Lifecycle" and "Logging Priority":

```markdown
## Event Timing

**Log events AS THEY HAPPEN, with timing appropriate to the event type:**

| Event Type | When to Log |
|-----------|-------------|
| Decision | **Before** acting on it — propose, then implement |
| Contribution | **After** the artifact exists — don't log unwritten deliverables |
| Correction | When the correction is identified |
| State change | When it occurs |
| Annotation | When observed |

Log one contribution per distinct artifact. If a task produces 3 files,
log 3 contributions. When in doubt, one file = one contribution; a
directory of related files produced together may be one contribution if
they can't be used independently.
```

### Change 1B: Add revises_event_id rule to Decisions subsection (after line 34)

**FIND** (lines 25-34):
```markdown
- **Decisions** (`trace_propose_decision` / `trace_resolve_decision`):
  Every methodological choice — which method/algorithm, parameters,
  thresholds, data inclusion/exclusion, how to handle messy data, which
  model, how to interpret ambiguous results. Propose BEFORE acting.
  - Use `disposition: "rejected"` with `revision_note` when the human
    overrides your approach.
  - Use `disposition: "revised"` when the human modifies your suggestion.
  - Use `suggestion_type`: "proactive" (you volunteered), "requested"
    (human asked), or "collaborative" (emerged from discussion).
  - Wait for human confirmation on consequential decisions.
```

**REPLACE WITH:**
```markdown
- **Decisions** (`trace_propose_decision` / `trace_resolve_decision`):
  Every methodological choice — which method/algorithm, parameters,
  thresholds, data inclusion/exclusion, how to handle messy data, which
  model, how to interpret ambiguous results. Propose BEFORE acting.
  - Use `disposition: "rejected"` with `revision_note` when the human
    overrides your approach.
  - Use `disposition: "revised"` when the human modifies your suggestion.
  - Use `suggestion_type`: "proactive" (you volunteered), "requested"
    (human asked), or "collaborative" (emerged from discussion).
  - Wait for human confirmation on consequential decisions.
  - When proposing a decision that refines, narrows, or supersedes a
    previous decision in this session, set `revises_event_id` to the
    earlier decision's ID. Before proposing, check existing decisions
    in the session for relationships.
```

### Change 1C: Add micro-sessions to Session Lifecycle (after line 19)

**FIND** (lines 14-19):
```markdown
## Session Lifecycle

1. **Start**: Call `trace_start_session` with project name, description,
   and participant list at the beginning of any multi-step workflow.
2. **End**: Call `trace_end_session` with a summary when the workflow is
   complete. See "Session-End Checklist" below.
```

**REPLACE WITH:**
```markdown
## Session Lifecycle

1. **Start**: Call `trace_start_session` with project name, description,
   and participant list at the beginning of any multi-step workflow.
2. **End**: Call `trace_end_session` with a summary when the workflow is
   complete. See "Session-End Checklist" below.
3. **Micro-sessions**: If a provenance-relevant event occurs outside a
   multi-step workflow (e.g., a state change mentioned in a quick Q&A),
   start a session, log the event, and end it immediately. A session is
   a unit of provenance, not necessarily a long workflow.
```

---

## File 2: `~/.claude/CLAUDE.md`

**Path:** `/Users/echoes/.claude/CLAUDE.md`

### Change 2A: Replace "Real-Time Logging" intro with per-type timing table + one-artifact rule

**FIND** (lines 25-40):
```markdown
### Real-Time Logging (CRITICAL)

**Log decisions AS THEY HAPPEN, not retroactively.** The user should
never need to ask "did you log that?" — if they do, logging has failed.

- The user will NOT prefix messages with `[decision]` or `[correction]`.
  You MUST recognize these from natural conversation:
  - "I want to use X" / "let's go with Y" → `trace_propose_decision`
  - "Can you add metric Z" / "should we try W" → `trace_propose_decision`
  - "That's wrong, it should be X" → `trace_log_annotation(correction)`
  - Any deliverable produced → `trace_log_contribution`
- **Interleave logging with code work.** Do not defer all logging to the
  end. When the user makes a decision, log it before writing the code
  that implements it.
- After `/compact`: check `.claude/compact-context.md` and start a new
  session before doing any work.
```

**REPLACE WITH:**
```markdown
### Real-Time Logging (CRITICAL)

**Log events AS THEY HAPPEN, with timing appropriate to the event type:**

| Event Type | When to Log |
|-----------|-------------|
| Decision | **Before** acting on it — propose, then implement |
| Contribution | **After** the artifact exists — don't log unwritten deliverables |
| Correction | When the correction is identified |
| State change | When it occurs |
| Annotation | When observed |

The user should never need to ask "did you log that?" — if they do,
logging has failed.

- The user will NOT prefix messages with `[decision]` or `[correction]`.
  You MUST recognize these from natural conversation:
  - "I want to use X" / "let's go with Y" → `trace_propose_decision`
  - "Can you add metric Z" / "should we try W" → `trace_propose_decision`
  - "That's wrong, it should be X" → `trace_log_annotation(correction)`
  - Any deliverable produced → `trace_log_contribution`
- **Interleave logging with code work.** Do not defer all logging to the
  end. When the user makes a decision, log it before writing the code
  that implements it.
- Log one contribution per distinct artifact. If a task produces 3 files,
  log 3 contributions. When in doubt, one file = one contribution.
- After `/compact`: check `.claude/compact-context.md` and start a new
  session before doing any work.
```

### Change 2B: Add revises_event_id rule to Decisions subsection

**FIND** (lines 46-52):
```markdown
- **Decisions** (`trace_propose_decision` / `trace_resolve_decision`):
  Every methodological choice — which method to use, which parameters,
  how to handle ambiguous data, which approach to take. Propose BEFORE
  acting. Resolve when the human accepts, revises, or rejects.
  - Use `disposition: "rejected"` with `revision_note` when the human
    overrides your approach (e.g. wrong environment, wrong method).
  - Use `disposition: "revised"` when the human modifies your suggestion.
```

**REPLACE WITH:**
```markdown
- **Decisions** (`trace_propose_decision` / `trace_resolve_decision`):
  Every methodological choice — which method to use, which parameters,
  how to handle ambiguous data, which approach to take. Propose BEFORE
  acting. Resolve when the human accepts, revises, or rejects.
  - Use `disposition: "rejected"` with `revision_note` when the human
    overrides your approach (e.g. wrong environment, wrong method).
  - Use `disposition: "revised"` when the human modifies your suggestion.
  - When proposing a decision that refines, narrows, or supersedes a
    previous decision in this session, set `revises_event_id` to the
    earlier decision's ID. Before proposing, check existing decisions
    in the session for relationships.
```

### Change 2C: Add micro-sessions to Session Lifecycle

**FIND** (lines 14-23):
```markdown
### Session Lifecycle

0. **Acknowledge**: At the start of the conversation, briefly tell the user
   that TRACE audit logging is active for this project. Do this once per
   conversation, not per tool call.
1. You MUST **start a session** at the beginning of any multi-step workflow.
   This includes conversations that resume after `/compact` — check for
   prior session state and start a new session immediately.
2. You MUST **end the session** with a summary when the workflow is complete.
   See "Session-End Checklist" below.
```

**REPLACE WITH:**
```markdown
### Session Lifecycle

0. **Acknowledge**: At the start of the conversation, briefly tell the user
   that TRACE audit logging is active for this project. Do this once per
   conversation, not per tool call.
1. You MUST **start a session** at the beginning of any multi-step workflow.
   This includes conversations that resume after `/compact` — check for
   prior session state and start a new session immediately.
2. You MUST **end the session** with a summary when the workflow is complete.
   See "Session-End Checklist" below.
3. **Micro-sessions**: If a provenance-relevant event occurs outside a
   multi-step workflow (e.g., a state change mentioned in a quick Q&A),
   start a session, log the event, and end it immediately. A session is
   a unit of provenance, not necessarily a long workflow.
```

---

## Summary of All Edits

| File | Change | Lines Affected | Net Lines Added |
|------|--------|----------------|-----------------|
| `skill/TRACE.md` | Insert Event Timing section | After line 20 | +14 |
| `skill/TRACE.md` | Add `revises_event_id` rule to Decisions | Lines 25-34 | +3 |
| `skill/TRACE.md` | Add micro-sessions to Session Lifecycle | Lines 14-19 | +3 |
| `~/.claude/CLAUDE.md` | Replace Real-Time Logging intro with timing table | Lines 25-40 | +10 |
| `~/.claude/CLAUDE.md` | Add `revises_event_id` rule to Decisions | Lines 46-52 | +3 |
| `~/.claude/CLAUDE.md` | Add micro-sessions to Session Lifecycle | Lines 14-23 | +3 |
| `USER_GUIDE.md` | **SKIPPED** — needs broader v1.0 → v0.2.0 rewrite | — | — |
| `TRACE_PROTOCOL.md` | **SKIPPED** — needs broader v1.0 → v0.2.0 rewrite | — | — |

**Total: ~36 lines added across 2 files. No deletions. No new tools. No schema changes.**

---

## What Each Change Addresses

| Change | Gap It Fixes | Root Cause |
|--------|-------------|------------|
| Per-type timing table | Contributions logged before artifacts exist | "Real-time" was ambiguous — means different things for different event types |
| One-artifact-one-contribution | Multiple deliverables bundled into one event | No explicit granularity guidance existed |
| `revises_event_id` rule | Decision chains not linked | No rule specified WHEN to use the field |
| Micro-sessions | Post-session state changes go unlogged | No guidance for events outside multi-step workflows |
