# ADR 001 — TRACE auto-start enforcement

**Status:** accepted
**Date:** 2026-04-13 (incident) / 2026-04-16 (adapter work)
**Resolution:** Claude Code host adapter with three hooks (`SessionStart`,
`UserPromptSubmit`, `PreToolUse`); see
[`src/trace_mcp/adapters/claude_code/`](../../src/trace_mcp/adapters/claude_code/).

> This ADR captures the failure that motivated the adapter layer. The
> numbered follow-ups at the bottom are all addressed by
> `src/trace_mcp/adapters/`; the "Hook options" section became PR2
> (project-aware `session-reminder.sh`), PR3 (`prompt-reminder.sh`), and
> PR4 (`pretool-guard.sh`). The file is retained as a point-in-time
> incident report, not as live guidance.

---

## Incident

During a ~2-hour working session on `when-algorithms-meet-artists` on
2026-04-13, Claude Code (Opus 4.6) performed substantial provenance-relevant
work without starting a TRACE session:

- Processed a morning meeting transcript with Ariya.
- Rebuilt Figure 1 (topic-colored semantic map, matched 50% HDR).
- Rebuilt Figure 4 from scratch as a six-panel generator (`scripts/regenerate_figure_4.py`)
  with a corrected Panel E and data-driven Panel D.
- Rebuilt Figure S1, Figure S2 as density-contour / upper-right-legend versions.
- Created `scripts/build_figure_cache.py` (a structural fix for PCA drift).
- Caught and fixed a data bug (Utility consensus 66% → 46%, actual Lovato value).
- Edited 4 manuscript markdown files (captions, S1, Figure 1 updates).
- Ran the test suite (191/191 passing).

TRACE logging began only after the user explicitly asked "are you logging this
with TRACE?" The events above were summarized in a single retrospective
`gotcha` annotation rather than backfilled as individual timestamped events,
per the absolute rule that TRACE records must never be fabricated.

This doc captures what specifically failed in this session, separated from
general advice. Deeper design work on hooks/rules/skills can happen in the
TRACE repo itself.

## What failed, specific to this session

### 1. TRACE tools were deferred

The MCP server was registered and the tool *names* appeared in the session's
deferred tool list, but the tool *schemas* had to be loaded via `ToolSearch`
before any `trace_*` call could succeed. Concrete effect:

- The initial tool surface Claude sees every turn contains `Read`, `Edit`,
  `Bash`, `Glob`, `Grep`, `Task*`, etc. with full schemas inline.
- `trace_start_session` is only visible as a name in a deferred list. Calling
  it requires an explicit schema-fetch step first.
- That small friction is enough to push it out of the default path when
  Claude is task-focused.

### 2. The harness nudges about `TaskCreate` but not about TRACE

During the session the harness fired the `<system-reminder>` message

> The task tools haven't been used recently. If you're working on tasks that
> would benefit from tracking progress, consider using TaskCreate…

multiple times, at exactly the points where a TRACE check would also have
been appropriate. There is no analogous reminder for TRACE. One protocol
is being actively reinforced by the runtime; the other is relying entirely
on the model remembering written instructions.

### 3. The CLAUDE.md TRACE section is long and easy to de-prioritise

The project CLAUDE.md contains ~200 lines of TRACE v0.3 guidance (session
lifecycle, logging priorities, attribution rules, session-end checklist,
etc.). That content is load-bearing for *using* TRACE correctly once a
session is active. It is less effective as a *trigger* for starting a
session in the first place — the "acknowledge + start session" action is
mentioned, but it competes with a lot of other rules for attention.

### 4. The workflow boundary was ambiguous in the conversation

The user's first substantive ask was:

> "go through that transcript and figure out what additional things we need
> to do and update the plan accordingly. if possible you can then start
> implement some of the changes or whatever steps are in the plan"

This is a multi-step workflow and absolutely warranted a TRACE session. But
it reads as a research/analysis prompt first, with the "implementation" phase
contingent on the research outcome. The turn-by-turn pivot from "read the
transcript" to "write a new figure generator" happened without any explicit
"workflow start" moment that might have triggered `trace_start_session`.

## What would have prevented this specific gap

Two changes would each independently have caught today's failure:

### A. Un-defer the TRACE tools

If `trace_start_session`, `trace_propose_decision`, `trace_log_contribution`,
and `trace_log_annotation` had appeared in the main tool surface (the
schemas immediately callable, like `Edit` or `Bash`), they would be
continuously visible alongside every other tool. No schema fetch required.
Visibility ≈ callability ≈ use.

*Scope:* this is a configuration change in how the TRACE MCP server exposes
its tools, or how Claude Code's harness classifies them. Not a model-side
fix.

### B. Harness-level reminder parity with `TaskCreate`

A `<system-reminder>` equivalent to the existing TaskCreate nudge, but
pointed at TRACE:

> TRACE is active on this project and no session has been started. If
> you're working on a multi-step workflow, call `trace_start_session`
> before proceeding.

Fired on the same cadence as the TaskCreate reminder (triggered when a
conversation has accumulated N tool calls without a TRACE call). This
mirrors a mechanism the harness already implements for a structurally
identical problem.

*Scope:* harness/runtime configuration. Not a model-side fix.

## What would probably not have prevented it

- **More content in CLAUDE.md.** The instructions were already explicit and
  the failure still happened. Adding another paragraph doesn't change the
  attention dynamics.
- **A skill.** Skills are model-invoked. They suffer the same forgetting
  problem as instructions — if the model didn't recognize the trigger for
  starting a TRACE session, it also wouldn't recognize a trigger for
  invoking a skill that starts a TRACE session.

## What the model should own regardless

Both of the above are rails that lower the failure rate. Neither excuses
the model's failure today. The instructions were present and explicit; the
model read them and still pattern-matched "transcript → figure work"
without pattern-matching "multi-step workflow → start session." That's a
behavior miss, not a tooling miss. Even after rails are in place, the
model-side norm should be:

- Acknowledge TRACE in the first assistant turn of any project where it is
  configured.
- Call `trace_start_session` before the first artifact-producing action of
  a multi-step task, not after.
- Treat "I've already committed to an implementation" as a failure signal,
  not a reason to skip logging.

## Hook options (for deeper exploration in this repo)

Three levels, cheapest to most robust, for reference when picking up this
work:

- **SessionStart hook** — inject a visible reminder at the start of every
  new Claude Code session. Pure nudge, no enforcement, ~5 minutes.
- **UserPromptSubmit hook** — on every user turn, check whether an open
  TRACE session exists for the active project. If not, prepend a system
  message. Catches today's failure mode specifically. ~15 minutes.
- **PreToolUse hook** — block deliverable-producing tool calls (`Edit`,
  `Write`, certain `Bash` invocations) when no TRACE session is open.
  Allowlist reads/searches. Most aggressive; most friction on routine
  fixes. An hour or two with an allowlist to tune.

These are listed for convenience — design choices among them belong in the
TRACE repo's own design review, not in a field incident report.

## Suggested follow-ups in this repo

1. Decide whether to un-defer the TRACE MCP tools, or document why they
   are deferred.
2. File a request (or open a feature branch) for the harness-level
   TaskCreate-parity reminder.
3. If neither of (1) or (2) is feasible, design the UserPromptSubmit hook
   and publish a canonical version in `docs/` that project CLAUDE.md files
   can reference.
4. Audit other projects using TRACE for how often this auto-start failure
   mode has happened (search recent conversation histories / session files
   for sessions that begin mid-workflow with retrospective annotations).

---

*Logged during TRACE session `trace_20260413_799f12` on project
when-algorithms-meet-artists. See that session's `gotcha` annotation for
the concrete list of work performed before TRACE was active.*
