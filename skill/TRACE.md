# TRACE Audit Protocol Skill

## When to Activate
Activate this skill for ANY scientific workflow, experiment, data analysis,
literature analysis, or multi-step technical research task.

## Absolute Rule

Never fabricate, falsify, or retroactively alter any TRACE data. A sparse
but honest record is infinitely more valuable than a dense but fabricated one.
If gaps exist, acknowledge them via honest retrospective annotations in new
sessions, not by patching old ones.

## Session Lifecycle

1. **Start**: Call `trace_start_session` with project name, description,
   and participant list at the beginning of any multi-step workflow.
2. **End**: Call `trace_end_session` with a summary when the workflow is
   complete. See "Session-End Checklist" below.
3. **Micro-sessions**: If a provenance-relevant event occurs outside a
   multi-step workflow (e.g., a state change mentioned in a quick Q&A),
   start a session, log the event, and end it immediately. A session is
   a unit of provenance, not necessarily a long workflow. Micro-sessions
   are for events that change the project's trajectory — a new tool
   dependency, a methodology decision, a discovered data issue. Do not
   create micro-sessions for routine questions or passing observations.

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

## Logging Priority (Tiered Guidance)

### ALWAYS log — core provenance events:

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

- **Corrections** (`trace_log_annotation` with `category: "correction"`):
  Every time the human catches and fixes an AI mistake. Include
  `corrects_event_ids` linking to the event(s) being corrected.

- **Contributions** (`trace_log_contribution`): Every deliverable — code
  written, analysis completed, document produced. Record who had the
  idea (`direction`) vs who did the work (`execution`).
  - Set the `artifact` field to the file path or identifier for the
    deliverable (e.g., `"src/analysis/results.py"`).
  - Link contributions to their motivating decisions via
    `related_decision_ids`.

### USUALLY log — important context:

- **Tool calls to domain MCP servers** (`trace_log_tool_call`): Calls
  that produce results used in the workflow. When a tool call fails and
  you retry, set `retries_event_id` to the previous failed attempt's
  event ID. Always set `status: "error"` and `error_message` for failures.

- **State changes** (`trace_log_state_change`): Model switches, environment
  changes, configuration changes, dependency updates.

### SOMETIMES log — when noteworthy:

- **Annotations** (`trace_log_annotation`): Surprising findings (gotcha),
  useful patterns (learning), open questions (question), tasks to
  revisit (todo). Log what a future reader would find valuable.

### DO NOT log — too noisy:

- Routine file reads, directory listings, grep searches
- Exploratory tool calls that don't produce workflow results
- TRACE's own tool calls (don't log trace_log_* calls in TRACE)

## When to use `correction` vs `gotcha` vs decision rejection

- **correction**: Human catches an AI mistake and provides the fix.
- **gotcha**: A surprising discovery about data, tools, or the domain.
  Nobody was "wrong" — it's just unexpected behavior.
- **Decision rejection**: Human explicitly rejects a proposed approach.
  Use alongside a correction annotation when the rejected decision
  led to failed actions.

## Session-End Checklist

Before calling `trace_end_session`, review the conversation for:
1. **Decisions** you proposed but did not log
2. **Corrections** — times the human corrected you that you did not log
3. **Contributions** — deliverables that were produced but not logged
4. **Decision chains** — related decisions that should be linked via
   `revises_event_id`

Log any missing events, then end the session with a summary.

## Cross-Session Knowledge

Use `trace_learn_*` tools for cross-session knowledge persistence:
- `trace_learn_recall` — find relevant past learnings for the current context
- `trace_learn_add` — add a learning manually
- `trace_learn_extract` — extract learnings from session annotations/decisions
- `trace_learn_list` — list all learnings
- `trace_learn_forget` — remove a learning

## Examples

Five scenarios showing what to log and when, in increasing complexity.

### Example 1: Basic Decision from Conversation

**User says:** "Let's use cosine similarity instead of Euclidean distance for the clustering."

**AI should do (BEFORE implementing the change):**
```
trace_propose_decision(
  session_id=...,
  description="Use cosine similarity instead of Euclidean distance for document clustering",
  rationale="User preference — cosine similarity is standard for high-dimensional text embeddings where magnitude is less informative than direction",
  proposed_by_type="human", proposed_by_id="user",
  suggestion_type="requested",
  tags=["clustering", "distance-metric"]
)
→ returns evt_003
```
```
trace_resolve_decision(
  session_id=..., event_id="evt_003",
  disposition="accepted",
  resolved_by_type="human", resolved_by_id="user"
)
```
Then implement the change. The user's statement IS the decision and the
acceptance — log both before writing code.

### Example 2: Recognizing and Logging a Correction

**AI writes code using `pandas.read_csv()` with default encoding.**
**User says:** "That's breaking on the Unicode characters. You need to use `encoding='utf-8-sig'` for this dataset — it has a BOM."

**AI should do:**
```
trace_log_annotation(
  session_id=...,
  category="correction",
  content="AI used default encoding for CSV read; user corrected to encoding='utf-8-sig' because the dataset has a UTF-8 BOM that causes parsing errors with default encoding",
  corrects_event_ids=["evt_005"],  # ← the tool_call or contribution where the bug was introduced
  tags=["data-loading", "encoding"]
)
```
Key: The user didn't say "log a correction" — they just fixed the problem.
Recognize it as a correction and log it with `corrects_event_ids` linking
back to the event where the mistake happened.

### Example 3: Contribution with Artifact and Decision Link

**After implementing an analysis script, the AI writes the file.**

**AI should do (AFTER the file exists):**
```
trace_log_contribution(
  session_id=...,
  description="Implemented topic modeling pipeline with LDA, coherence-based k selection, and visualization",
  direction="collaborative",  # user specified the approach, AI designed the implementation
  execution="ai",
  artifact="src/analysis/topic_model.py",
  related_decision_ids=["evt_003", "evt_007"],  # the decisions that motivated this work
  tags=["topic-modeling", "LDA", "pipeline"]
)
```
Key: Log AFTER the artifact exists (not before). Set `artifact` to the
file path. Link to the decisions that led to this deliverable.

If the same task also produced a visualization file, log a SECOND
contribution for that artifact:
```
trace_log_contribution(
  session_id=...,
  description="Generated topic coherence plot showing optimal k=12",
  direction="ai", execution="ai",
  artifact="results/figures/coherence_plot.png",
  related_decision_ids=["evt_007"],
  tags=["topic-modeling", "visualization"]
)
```

### Example 4: Decision Chain with `revises_event_id`

**Earlier in the session, AI proposed analyzing all 5 climate scenarios:**
```
trace_propose_decision(description="Analyze all 5 SSP scenarios (SSP1-1.9 through SSP5-8.5)")
→ returns evt_004 (accepted)
```

**Later, user says:** "Actually, let's narrow to just SSP2-4.5 and SSP5-8.5 — we don't have time for all five."

**AI should do:**
```
trace_propose_decision(
  session_id=...,
  description="Narrow analysis to SSP2-4.5 and SSP5-8.5 only, dropping SSP1-1.9, SSP1-2.6, and SSP3-7.0",
  rationale="Time constraint — user decided to focus on the moderate and high-emission scenarios which bracket the policy-relevant range",
  proposed_by_type="human", proposed_by_id="user",
  suggestion_type="requested",
  revises_event_id="evt_004",  # ← links to the original "all 5" decision
  tags=["scope", "SSP-scenarios"]
)
→ returns evt_009
```
```
trace_resolve_decision(
  session_id=..., event_id="evt_009",
  disposition="accepted",
  resolved_by_type="human", resolved_by_id="user"
)
```
Also resolve the original decision as revised:
```
trace_resolve_decision(
  session_id=..., event_id="evt_004",
  disposition="revised",
  resolved_by_type="human", resolved_by_id="user",
  revision_note="Narrowed to 2 scenarios due to time constraint — see evt_009"
)
```
Key: The new decision sets `revises_event_id` to link to the original.
This creates the chain that `trace_get_decision_chain()` can traverse.

### Example 5: Complex Multi-Event Scenario

**User and AI are building a species distribution model. In one exchange:**
1. AI proposes using MaxEnt
2. User says "No, use random forest — MaxEnt doesn't handle interactions well for this species"
3. AI implements random forest, but gets the train/test split wrong (spatial autocorrelation)
4. User catches it: "You need spatial blocking for the CV, not random splits"
5. AI fixes the code and writes results

**AI should log (in order):**

*Step 1 — AI proposes MaxEnt:*
```
trace_propose_decision(
  description="Use MaxEnt for species distribution modeling",
  rationale="Standard approach for presence-only data with environmental covariates",
  proposed_by_type="ai", suggestion_type="proactive",
  tags=["SDM", "MaxEnt"]
)  → evt_010
```

*Step 2 — User rejects, proposes random forest:*
```
trace_resolve_decision(
  event_id="evt_010", disposition="rejected",
  resolved_by_type="human",
  revision_note="MaxEnt doesn't handle feature interactions well for this species; random forest preferred"
)
```
```
trace_propose_decision(
  description="Use random forest instead of MaxEnt for SDM",
  rationale="Better handling of feature interactions for this species' complex habitat requirements",
  proposed_by_type="human", suggestion_type="requested",
  revises_event_id="evt_010",  # ← links rejection to replacement
  tags=["SDM", "random-forest"]
)  → evt_011 (resolve as accepted)
```

*Step 3-4 — AI implements with wrong CV, user corrects:*
```
trace_log_annotation(
  category="correction",
  content="AI implemented random k-fold CV for spatial species data; user corrected to spatial block CV to avoid spatial autocorrelation inflating accuracy estimates",
  corrects_event_ids=["evt_012"],  # ← the tool_call or contribution with the bad CV
  tags=["cross-validation", "spatial-autocorrelation", "SDM"]
)
```

*Step 5 — AI writes corrected results:*
```
trace_log_contribution(
  description="Species distribution model with spatial block CV — RF model, 5 environmental covariates, AUC=0.84",
  direction="human", execution="ai",
  artifact="results/sdm_spatial_cv_results.csv",
  related_decision_ids=["evt_011"],
  tags=["SDM", "random-forest", "results"]
)
```

Total: 2 decisions (1 rejected + 1 accepted chain), 1 correction, 1 contribution.
A future reader can reconstruct: AI proposed MaxEnt → human chose RF
instead → AI made a CV mistake → human caught it → final results produced.

## Principles

- Log methodology decisions, not trivial ones.
- Decision rationales must be specific and technical:
    BAD: "this seemed like a good threshold"
    GOOD: "0.80 cosine similarity gives F1=0.78 on our 30-pair validation
           set; lowering to 0.75 adds 40% more hits but drops precision to 0.61"
- Tag events with relevant domain terms for later searchability.
- For messy data situations, always log the data quality issue as a gotcha
  AND the decision about how to handle it.
