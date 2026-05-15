# TRACE-vs-JSONL Audit Findings — `trace_20260513_446733`

**Audit conducted:** 2026-05-14 (this conversation)
**Subject session:** `trace_20260513_446733` (project: `waggle`, 2026-05-13 12:22 → 22:30 UTC)
**Ground truth:** 1,040-line Claude Code JSONL transcript (3.6 MB) at the path in `audit_methodology.md`
**Auditor session:** `trace_20260514_e81b54` (project: `trace-mcp`)

---

## Executive summary

**Headline finding:** TRACE captured the structural skeleton of the waggle session correctly — every Phase I task has a contribution, both major direction-setting decisions were logged, gotchas and learnings are well-distributed. **But the audit surfaces five substantive quality issues that should shape the OSS release:**

1. **conversation_snippet is severely under-populated (4 of 28 events, 14%).** The protocol says "always set" for contributions and corrections; the controller set it on only 2 of 17 contributions and 0 of 1 corrections.
2. **One material attribution error.** `evt_025` (matcher iteration kickoff) is logged as `proposed_by=human` but the JSONL shows the actual flow was human-question → AI-proposal → human-accept. The AI's proposal content is captured in the decision description but credited to the human.
3. **The most important mid-session discovery is folded into a post-hoc summary, not logged at the discovery moment.** The v3 Pydantic crash discovery (gpt-5.4-nano emitting `plausibility_score` instead of `confidence`) happens at line 921 / 18:27 UTC, but no TRACE event exists between 16:27 and 18:48. The discovery only appears 4 hours later inside `evt_027`'s contribution description.
4. **`evt_003` (the one correction event) has empty `corrects_event_ids`** — flagged by TRACE's own audit. Root cause is a protocol design gap: the subagent's false pyright claim wasn't itself a TRACE event, so there's nothing to link to. The protocol needs explicit semantics for this case.
5. **42 Claude Code Agent tool dispatches (subagent work events) are not captured anywhere in TRACE.** `mcp_servers=[]` and 0 `tool_call` events, but those 42 dispatches are the actual implementer/reviewer work. The protocol's `tool_call` schema is MCP-specific and does not cover Claude Code's internal Agent tool.

**TRACE is structurally honest but tactically thin.** Nothing in the record is fabricated; the gaps are missing detail, not bad data. This is the better failure mode for a provenance system. None of the issues here would prevent the OSS release — but issues #1 (snippets) and #2 (attribution semantics) should be tightened before release because they affect how downstream readers interpret the audit trail.

**On the "efficiency last" question:** I agree. None of the findings here are efficiency issues. They are correctness, coverage, and protocol-clarity issues — i.e., exactly what should be fixed pre-release. Performance/cost concerns (re-write speed, BM25 vs LLM matching latency, scratchpad generation time) are not in this audit and can be deferred.

---

## Coverage matrix

| Dim | Description | Score | Notes |
|---|---|---|---|
| **A** | Decision coverage | **Partial** | 2/2 major direction-setting moments logged. 3+ material methodology decisions logged as annotations instead (Task 5 round-2 skip, Task 17 deferral, v2/v3/v4 cycle). |
| **B** | Contribution coverage | **Mostly-complete** | 17 contributions cover 16 tasks + 1 deferral. 3 prompt-iteration commits folded into one event (`evt_027`) — loses per-iteration evidence. Task 15 (legacy delete + rapidfuzz removal) has no dedicated contribution. |
| **C** | Correction coverage | **Sparse** | 1 correction logged (`evt_003`), with empty `corrects_event_ids`. The v3 Pydantic bug discovery would have warranted a second correction or gotcha at 18:27 UTC. The Task 3 test-flake (where controller's re-run disagreed with implementer + 2 reviewers) is logged as gotcha but is structurally a controller correction. |
| **D** | Attribution accuracy | **Mostly-accurate** | 1 clear error: `evt_025` proposed_by attribution. Direction split on the 17 contributions (11 human / 5 collaborative / 1 ai) is defensible. `evt_024` (Task 17 deferral, direction=ai) is accurate — AI made the call without human direction. |
| **E** | conversation_snippet completeness | **Severely sparse** | 4/28 events (14%). 2/17 contributions (12%). 0/9 annotations (0%). Protocol says "always set" on contributions and corrections. |
| **F** | Tool-call logging | **Schema gap (not a coverage failure)** | 0 `tool_call` events is correct given `mcp_servers=[]`. But 42 Agent dispatches are uncovered work events that the protocol's tool_call schema doesn't accommodate. |
| **G** | v3 hidden-bug discovery representation | **Underplayed** | Discovery moment (line 921, 18:27 UTC) has no dedicated TRACE event. Surfaces only in `evt_025`'s revision_note and `evt_027`'s contribution description, both written hours later. |

---

## Detailed findings

### Dimension A — Decision coverage

**Logged decisions (2):**
- `evt_001` at 12:47 UTC — "Proceed with executing the user-approved 17-task semantic-matcher implementation plan." Proposed_by=human, accepted. **Snippet matches User #9 verbatim (JSONL line 98, 12:45 UTC).** Attribution correct.
- `evt_025` at 17:51 UTC — "Begin matcher quality iteration: Stage B prompt tightening first." Proposed_by=human, accepted. **Attribution wrong — see Dimension D.**

**Material decisions logged as annotations instead of decisions:**

| Moment | JSONL evidence | TRACE event | Should it have been a decision? |
|---|---|---|---|
| Skip round-2 code-review reviewer at Task 5 | Line 593 (15:11 UTC): "Pragmatic call: skipping round-2 reviewer to conserve tokens — the fix subagent's work passes tests + pyright + matches every reviewer recommendation" | `evt_012` (learning) | **Arguably yes.** This is a deliberate protocol-deviation choice with a justification — exactly the shape of a decision. Logged as learning loses the "this is what we chose" signal. |
| Defer Task 17 deploy entirely | Line 880 (16:27 UTC): "**Task 17 (deploy) is deferred** because the eval harness from Task 9 surfaced real matcher quality gaps" | `evt_024` (contribution, direction=ai) | **Yes.** This is a material methodology decision (defer deployment despite plan saying ship). Currently it's a contribution about the deferral, not a logged decision-to-defer. |
| Combine v2/v3/v4 into one contribution | Implicit choice | none | Minor; reasonable as a granularity call. Note in Dimension B. |

**Material decisions not logged at all:**
- **Per-task model selection (sonnet vs opus).** `evt_003` mentions "Task 1 implementer (sonnet, agentId ad9350bfec6ce79f9)." There must have been a deliberate per-task model choice; no TRACE event captures the strategy or its rationale.
- **Subagent dispatch ramp-down (5 dispatches/task at Task 1–5 → 1–3 at Task 6–14).** Visible in the Agent dispatch timeline. `evt_012` describes the moment as a token-budget consideration but no decision event was logged for the broader pattern change.

### Dimension B — Contribution coverage

**Mapping 26 commits → 17 contributions:**

| Logical unit | Commits | TRACE event |
|---|---|---|
| Task 1 (migration) | `d29aec3`, `1311ece`, `75f86fb` | `evt_004` |
| Task 2 (Pydantic schemas) | `3280f84`, `d6c51ba`, `fdb9b53` | `evt_006` |
| Task 3 (Stage A) | `62631a8`, `022ba51` | `evt_009` |
| Task 4 (Stage B) | `92c0e43`, `1a9c18b` | `evt_010` |
| Task 5 (Stage C) | `4b648c3`, `61e070c`, `b93ef75` | `evt_011` |
| Task 6 (backfill) | `806175a` | `evt_013` |
| Task 7 (boot reindex) | `471e03c` | `evt_014` |
| Task 8 (corpus) | `dceb966`, `db2a4b0` | `evt_015` |
| Task 9 (eval harness) | `3040d2d` | `evt_016` |
| Task 10 (UI partials) | `cd5f28c` | `evt_018` |
| Task 11 (route) | `a9d26c0` | `evt_019` |
| Task 12 (orchestrator) | `d3a3542` | `evt_020` |
| Task 13 (order extractor) | `1cfe002` | `evt_021` |
| Task 14 (sweep) | `eb318a1` | `evt_022` |
| **Task 15 (delete legacy)** | (implied within Task 14?) | **none — gap** |
| Task 16 (Playwright) | `777a538` | `evt_023` |
| Task 17 (defer) | `cdd4c7a` | `evt_024` |
| v2/v3/v4 prompt iter | `ca76438`, `aa9709f`, `bfae3b2` | `evt_027` (combined) |

**Gap: Task 15.** The plan had a separate Task 15 ("delete legacy + rapidfuzz removal"). `evt_022`'s description says "All Task 2 deferred items now resolved" but does not call out the legacy-delete + dependency-removal commit independently. Either Task 15 was absorbed into the Task 14 sweep commit (`eb318a1`) without a separate commit, or its commit is unlabeled. Either way, the contribution log doesn't surface "we removed rapidfuzz as a dependency" — which is a non-trivial dependency-graph change that downstream auditors would want to know about.

**Granularity issue: prompt iterations collapsed.**
The three prompt-iteration cycles (v2, v3, v4) each had their own dispatch-implement-verify pattern. Folding them into `evt_027` means:
- The v2 regression discovery is lost from the timeline as an event
- The v3 Pydantic-bug fix is mentioned only in summary text
- The v4 stabilization is conflated with the prior two
A future reader looking at the timeline cannot tell "v2 happened at 17:52, regressed; v3 happened at 18:02, discovered+fixed Pydantic bug; v4 happened at 18:34, stabilized." That story is in the description text of one event, not in three sequenced events.

**Recommendation:** Each iteration cycle = one contribution. Lose the convenience of one summary event, gain three diff-able evidence points.

### Dimension C — Correction coverage

**The one logged correction (`evt_003`):**
- Logged at 13:19:42 UTC (JSONL line 271)
- Discovery moment in JSONL: line 238 (13:18 UTC) — "The implementer reported pyright clean but the harness just surfaced 4 unused-import warnings..."
- Correction text matches discovery — accurate
- `corrects_event_ids: []` — empty

**Why `corrects_event_ids` is empty (and why this is a protocol issue, not a controller issue):**

The "thing being corrected" is the Task 1 implementer subagent's false report. But the subagent's dispatch is not a TRACE event — it's a Claude Code Agent tool call (JSONL line 225, 12:55 UTC). The protocol requires `corrects_event_ids` to point to TRACE events, but the corrected statement was made by a subagent whose output exists only in the JSONL.

The controller did the next-best thing: set `related_event_ids: ["evt_001"]` (the decision under which Task 1 was executed). But that's not what `corrects_event_ids` is for.

**Other moments that arguably should have been corrections:**

| JSONL line | Time | Moment | TRACE handling |
|---|---|---|---|
| Line 921 | 18:27 UTC | v3 verifier discovers Pydantic crash from `plausibility_score` field name | No dedicated event; folded into `evt_027` description |
| `evt_008` body | 14:29 UTC | Controller's re-run disagrees with implementer + 2 reviewers on Task 3 test (5/5 pass vs 4/5 pass) | Logged as **gotcha**, structurally a controller correction of three converging false-passes |

The v3 Pydantic discovery is the bigger miss — it was the highest-impact mid-session finding and has no event at its moment.

### Dimension D — Attribution accuracy

**Confirmed error: `evt_025` proposed_by.**

The JSONL flow:
1. User #11 (line 883, 17:48): "what needs to happen now then to improve the matcher quality and %?" — **a question, not a directive**
2. Assistant (line 888, 17:49): proposes "Three levers in priority order: 1. Stage B prompt tuning... 2. Stage A normalization... 3. Threshold calibration..." — **AI proposal with specific Stage B prioritization**
3. User #12 (line 891, 17:49): "proceed with those steps to improve it and be sure to run additional agents..." — **human accepts AI's proposal**
4. TRACE event (line 897, 17:51): `evt_025` logged with `proposed_by=human`

The decision description is the AI's three-lever proposal. The conversation_snippet captures User #12's "proceed" message — which is a resolution, not a proposal. Correct attribution would be:

```
proposed_by: {"type": "ai", "id": "claude"}
suggestion_type: "requested"  # (in response to user's question)
disposition: "accepted"
resolved_by: {"type": "human", "id": "human"}
```

This is not a one-off — it's a **systematic protocol ambiguity**. The TRACE protocol says "AI proposes BEFORE acting." When a human asks a question and the AI proposes a course of action that the human accepts, the AI is the proposer. The current event logs the proposer as whoever spoke the last word before logging, which conflates resolution with proposal.

**Spot-check: contribution directions.**

| Event | Direction | JSONL evidence | Verdict |
|---|---|---|---|
| `evt_004` (Task 1) | human | Plan pre-approved before session; User #9 directs "continue with Phase I" | **Accurate at plan-level.** At task-level, no human direction occurred during execution. |
| `evt_015` (Task 8 corpus) | collaborative | Implementer subagent built the corpus; verifier subagent flagged 5 issues; controller made integration calls | **Accurate.** Mix of plan-driven and ad-hoc judgment. |
| `evt_024` (Task 17 defer) | ai | No user message between 17:48 and 16:27 UTC; AI made the deferral call based on 77% eval score < 95% threshold | **Accurate.** Genuinely AI-directed; the AI overrode the plan's "deploy" instruction with a judgment call. |
| `evt_027` (v4 full-mode) | collaborative | User #13 ("do option 1 and proceed") directed the full-mode run; AI ran it and interpreted results | **Accurate.** |

### Dimension E — conversation_snippet completeness

**Coverage breakdown:**

| Event type | Total | With snippet | Without |
|---|---:|---:|---:|
| Decisions | 2 | 2 (100%) | 0 |
| Contributions | 17 | 2 (12%) | 15 |
| Annotations | 9 | 0 (0%) | 9 |
| **Total** | **28** | **4 (14%)** | **24** |

**Contributions with snippets:** `evt_004` (uses User #9 "continue with Phase I"), `evt_027` (uses User #13 "do option 1 and proceed").

**Contributions without snippets but with an obvious candidate snippet:** all 15. Every Phase I task contribution (Tasks 1–17) could have reused User #9 as its motivating user message, the way `evt_004` did. The controller did this for the first contribution and then stopped.

**Why this matters:** Without snippets, the audit trail loses the link between user intent and AI work. A reader of `evt_010` (Task 4 contribution) can see what was built but not what the user asked for that led to it. The reader has to reconstruct intent from the session-level metadata. The protocol promises a tighter link.

**Root cause hypothesis:** the protocol guidance ("Always set conversation_snippet on contributions") is documentation, not enforcement. There is no validation that flags missing snippets when contributions are logged. The auto-audit at session-end did flag "12+ contributions missing conversation_snippet" but only as a post-hoc note — by then it's too late to correct.

### Dimension F — Tool-call logging

**The session has 0 `tool_call` events.**

This is *correct given* the session's `mcp_servers=[]` metadata. No domain MCP servers were configured, so no MCP-tool invocations happened.

**But 42 Claude Code Agent dispatches occurred and are not captured anywhere in TRACE.** Each dispatch is a self-contained unit of work:

```
implementer subagents:    16 dispatches (Task 1-14, Task 16, Iter 1-3)
spec-review subagents:     6 dispatches (Tasks 1, 2, 3, 4, 5, 6)
code-review subagents:     5 dispatches (Tasks 1, 2, 3, 4, 5)
fix subagents:             5 dispatches (Tasks 1, 2, 4, 5, 8)
verifier subagents:        4 dispatches (corpus + Iter v2/v3/v4)
re-review subagents:       6 dispatches (Tasks 1, 2, 4)
```

This is structural work — implementer + reviewer dispatches are how Phase I happened. Each one consumed a Claude API call (cost + latency) and produced output that the controller acted on. None of it is in TRACE.

**Protocol design issue:** `trace_log_tool_call`'s schema is anchored on "MCP tool invocation on another server." Claude Code's Agent tool is not an MCP server — it's a host-internal subagent dispatcher. The schema does not fit, and that mismatch is why 42 work-events are invisible.

**Two possible fixes:**
1. **Expand `tool_call` schema** to accept non-MCP tools (host-internal Agent, Task, etc.) with a `host` field distinguishing MCP from internal.
2. **Add a new event type `subagent_dispatch`** with `subagent_type`, `description`, `prompt_summary`, `result_summary` fields.

Either would let TRACE capture the dispatch graph that produced the contributions.

### Dimension G — v3 hidden-bug discovery representation

**Discovery moment (JSONL line 921, 18:27 UTC):**

> "V3 results are dramatic — 5/5 v2 regressions recovered (all 100%), pronoun_resolution 33→100%. BUT the implementer discovered a hidden Pydantic crash all along (`plausibility_score` vs `confidence` field mismatch) that was poisoning v2 readings. **This is huge.** Also noted LLM non-determinism..."

**Verifier confirms (JSONL line 930, 18:33 UTC):**

> "Verifier confirmed the Pydantic bug (introduced in v2, fixed in v3) AND flagged a residual risk: the prompt still says 'plausibility scores' at line 53, which is what primed gpt-5.4-nano to emit the wrong field name."

**Where this appears in TRACE:**
- `evt_025` revision_note (logged 18:48 UTC, ~20 min after discovery): mentions "Hidden Pydantic crash discovered + fixed in v3"
- `evt_027` description (logged 22:28 UTC, ~4 hours later): "Bug fix from v3 (plausibility_score → confidence field rename) is load-bearing — without it v4 would be much worse."

**Where it doesn't appear:** no dedicated annotation or correction at or near 18:27 UTC. Between `evt_024` (16:27) and `evt_026` (18:48) the TRACE timeline is silent for over 2 hours, during which:
- 3 iteration cycles ran (v2 implementer, v2 verifier, v3 implementer)
- A major bug was discovered and fixed
- A verifier confirmed the bug and flagged the priming prompt as the root cause

A future reader scrolling the TRACE timeline sees a 2-hour gap and then a "sample-mode variance" learning. They do not see "Pydantic crash discovered." They only see it if they read `evt_027`'s description carefully, hours later.

**This is the strongest argument for real-time logging discipline.** Summary events written at session-end conflate "what happened" with "what we now think happened" — losing the discovery moment as a discrete provenance artifact.

---

## Specific protocol issues (design, not discipline)

The following are issues with the TRACE protocol *itself*, not with how the controller followed it:

1. **`corrects_event_ids` and non-event corrected items.** When the corrected item is a subagent output, a tool result, or anything that isn't a TRACE event, the field has no valid value. The protocol needs explicit guidance: leave empty, link to the wrapping contribution, link to the relevant decision, or extend the field's semantics to allow JSONL line references / external pointers.

2. **proposed_by semantics in question→proposal→acceptance flows.** When a human asks a question and the AI proposes a course of action that the human accepts, who is the proposer? Current implementation suggests "whoever spoke the last directive before logging," which gives proposed_by=human but a description matching the AI's words. This is misleading. The protocol should clarify: the proposer is whoever produced the *content* of the proposal, regardless of which side speaks last.

3. **tool_call schema is MCP-bound.** Significant Claude Code Agent dispatches are not loggable. Either expand the schema or add a new event type.

4. **conversation_snippet enforcement.** The protocol says "always set" on contributions and corrections. With no validation hook, 86% of contributions were missing it in this session. Either add a pre-write validation (warn or block on missing snippet for contribution/correction event types), or relax the protocol language to "set when a relevant user message exists within the recent context."

5. **Event-count self-counting.** The scratchpad summary says "27 TRACE events logged" but the actual count is 28 (verified: 2 decisions + 9 annotations + 17 contributions). Off by one — small but a real fidelity issue for a provenance system.

---

## Specific controller discipline issues (not protocol issues)

These are things the controller could have done within the existing protocol:

1. **Reuse User #9 as conversation_snippet on every Phase I contribution.** The first contribution (`evt_004`) did this. The pattern was abandoned for evt_006 onwards.

2. **Log the v3 Pydantic discovery as a gotcha or correction at 18:27 UTC,** not in a post-hoc summary.

3. **Promote the Task 17 deferral to its own decision event** rather than embedding the decision in a contribution.

4. **Decompose `evt_027` into evt_027a (v2), evt_027b (v3), evt_027c (v4)** to preserve the iteration trajectory in the timeline.

---

## Things TRACE got right

For balance — the audit also surfaced several places where the discipline was strong:

- `evt_002` (make reset-db gotcha) was logged within 7 minutes of the issue surfacing — good real-time discipline.
- `evt_003` (correction) was logged at the discovery moment with accurate text citing the implementer's exact false claim and the harness diagnostic.
- `evt_008` (test flake) captured controller's re-run disagreement with implementer + 2 reviewers — the kind of latent issue that's easy to bury.
- `evt_012` captured the methodology deviation (skipping round-2 reviewer) with explicit token-budget rationale — exactly the right shape for an annotation.
- `evt_017` immediately followed `evt_016` — pairing the eval-harness contribution with its surfaced quality gaps.
- `evt_028` (v5 plan) is a clean handoff artifact for the next session — exactly what the protocol's "next session" guidance asks for.
- Both decision events have conversation_snippet set. Decisions get this right; contributions don't.

The session's `summary` field (the human-written summary, not the auto-scratchpad) is genuinely useful — it includes both "what was accomplished" and "what is next" with hand-off context. That's the protocol working as intended.

---

## Recommendations for the OSS release

### Pre-release fixes (should ship before v1.0)

1. **Tighten `conversation_snippet` enforcement.** Either: (a) add validation that warns or rejects contribution/correction events missing the field, or (b) explicitly relax the protocol language to "set when a recent user message is relevant context." The current state — strict requirement with no enforcement and 86% non-compliance — is the worst of both worlds.

2. **Clarify `proposed_by` semantics in question→proposal→acceptance flows.** Add a worked example to the spec covering the human-asks → AI-proposes → human-accepts case. The current Appendix A example only shows one-step human or AI proposals.

3. **Document `corrects_event_ids` for non-event corrected items.** Either widen the field's semantic scope (allow JSONL line refs or external pointers), or explicitly allow empty with guidance to use `related_event_ids` instead.

4. **Fix the scratchpad event-count.** Off-by-one in the auto-summary. Small but visible.

### Post-release improvements (v1.1+)

5. **Expand `tool_call` schema or add `subagent_dispatch` event type.** Claude Code's Agent tool is increasingly the workhorse of AI-assisted development. Not capturing it leaves a large class of work events outside the audit trail.

6. **Encourage finer iteration granularity** in protocol docs. "One contribution per logical work cycle" can mean three things for three prompt iterations — the protocol could nudge toward "one per dispatch-verify cycle" with examples.

7. **Real-time logging discipline guidance.** Add a "log discoveries at the moment of discovery, not in post-hoc summaries" line to the protocol's "What to log" section.

### Not blockers, not even soft improvements

These were checked and are working as designed:

- Per-task contribution coverage is solid
- `mcp_servers=[]` correctly produces 0 `tool_call` events (no false positives)
- Decision lifecycle (propose → resolve) was used correctly for both logged decisions
- Annotation categorization (gotcha / learning / todo / correction) is consistent and informative
- The scratchpad's "Critical handoff context" is genuinely useful

### On the "efficiency last" question

Agreed. None of the issues above are efficiency concerns. They are correctness, coverage, and protocol-clarity concerns — appropriate priorities for an OSS release that needs to be trustworthy first. Performance work (matcher backend latency, scratchpad generation speed, event-write atomicity overhead) is appropriate post-release work once correctness is locked in.

---

## Appendix A: Audit methodology

This audit followed the 7-dimension framework in `audit_methodology.md` (Dimensions A–G). Findings cite specific JSONL line numbers and TRACE event IDs throughout. The auditor performed:

1. Full read of `trace_session_trace_20260513_446733.json` (62 KB, 28 events)
2. Full read of `trace_scratchpad.md` (37 KB, auto-generated summary)
3. Full read of `audit_methodology.md` (10 KB, audit framework)
4. Structural extraction of all 1,040 JSONL lines into:
   - 16 user messages (filtered for text content)
   - 116 assistant messages with text
   - 42 Agent (subagent) dispatches with timestamps + descriptions
   - 32 TRACE tool-call invocations
5. Targeted keyword searches for: `plausibility`, `skip round-2`, `defer`, `option`, `implementer reported`, `crash`
6. Chronological merge of TRACE events + Agent dispatches + user messages to identify gaps

## Appendix B: Selected JSONL line references

| Moment | JSONL line | Time UTC |
|---|---:|---|
| User #4 (audit setup directive) | 11 | 12:13 |
| User #9 (continue Phase I) | 98 | 12:45 |
| Task 1 false-pyright-claim discovery | 238 | 13:18 |
| Task 5 round-2 reviewer skip | 593 | 15:11 |
| Phase I structural complete announcement | 880 | 16:27 |
| User #11 (what needs to improve matcher?) | 883 | 17:48 |
| Assistant proposes three-lever approach | 888 | 17:49 |
| User #12 (proceed with those steps) | 891 | 17:49 |
| **v3 Pydantic crash discovery** | **921** | **18:27** |
| **v3 verifier confirms bug + flags priming prompt** | **930** | **18:33** |
| v4 full-mode eval results posted | 988 | 20:32 |
| User #15 (log all this, set up v5) | 991 | 22:27 |
| Session end | 1008 | 22:30 |
