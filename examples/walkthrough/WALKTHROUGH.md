<!-- GENERATED FILE — do not edit by hand.
Regenerate with: python examples/walkthrough/render_walkthrough.py
Source of truth: examples/walkthrough/scenario.py -->

# TRACE walkthrough

A single pass through the TRACE provenance loop, from opening a session to a
cross-project denial. Every step below is one MCP tool call and the response to
look for. This same scenario is replayed against a live server by
`tests/test_walkthrough_e2e.py`, which asserts the responses shown here, so what
you read is what the server does.

To follow along hermetically — never touching your real `~/.trace` — launch a
server pinned to the `walkthrough` project with every data path redirected to a
fresh scratch location (knowledge store, sessions, scratchpads, the egress
ledger, and the registry):

```bash
DIR="$(mktemp -d)"; mkdir -p "$DIR/knowledge"
TRACE_PROJECT=walkthrough \
  TRACE_KNOWLEDGE_DIR="$DIR/knowledge" \
  TRACE_SESSIONS_DIR="$DIR/sessions" \
  TRACE_SCRATCHPAD_DIR="$DIR/scratchpads" \
  TRACE_EGRESS_LOG="$DIR/egress.log" \
  TRACE_REGISTRY_PATH="$DIR/projects.json" \
  TRACE_EMBEDDING_BACKEND=none TRACE_LLM_ENABLED=false \
  trace-mcp
```

`$SESSION_ID` stands for the session id printed in step 1;
substitute the real one as you go.

## Step 1 — Start a session

Open a session at the start of a workflow. The server is pinned, so `project` is omitted and resolves to the pinned key. The banner reports the session id used below.

```json
{
  "tool": "trace_start_session",
  "arguments": {
    "description": "Golden walkthrough of the TRACE provenance loop.",
    "participants": [
      {
        "id": "human",
        "type": "human",
        "role": "researcher"
      },
      {
        "id": "claude",
        "type": "ai",
        "role": "assistant"
      }
    ],
    "tags": [
      "walkthrough"
    ]
  }
}
```

Look for in the response:

- `TRACE audit logging is now active`
- `Project: walkthrough`
- `Session:`

## Step 2 — Propose a decision (AI)

Log the decision BEFORE acting. The AI authored the proposal, so `proposed_by` is the AI — regardless of who later accepts it. It stays in the proposed state until resolved. It is the first event of the session, `evt_001`, which the next steps reference.

```json
{
  "tool": "trace_propose_decision",
  "arguments": {
    "description": "Use median imputation for the three missing station readings.",
    "proposed_by_type": "ai",
    "proposed_by_id": "claude",
    "suggestion_type": "proactive",
    "rationale": "Median resists the two outliers a mean would chase.",
    "session_id": "$SESSION_ID"
  }
}
```

Look for in the response:

- `Decision proposed: evt_001`

## Step 3 — Resolve the decision (human accepts)

The human accepts the AI's proposal. Proposer stays the AI, resolver is the human — the attribution the record is built to keep. The proposal was `evt_001` in this session.

```json
{
  "tool": "trace_resolve_decision",
  "arguments": {
    "event_id": "evt_001",
    "disposition": "accepted",
    "resolved_by_type": "human",
    "resolved_by_id": "human",
    "session_id": "$SESSION_ID"
  }
}
```

Look for in the response:

- `Decision evt_001 resolved: accepted`

## Step 4 — Log a contribution

One contribution per artifact, splitting who had the idea (direction) from who did the work (execution), and linked to the decision that motivated it.

```json
{
  "tool": "trace_log_contribution",
  "arguments": {
    "description": "Imputation applied to the station-readings table.",
    "direction": "human",
    "execution": "ai",
    "artifact": "data/stations.csv",
    "related_decision_ids": [
      "evt_001"
    ],
    "conversation_snippet": "use median imputation for the missing readings",
    "session_id": "$SESSION_ID"
  }
}
```

Look for in the response:

- `Logged contribution:`

## Step 5 — Log a correction

A correction records a caught mistake and links to what it corrects. This is how the record keeps the mistakes, not just the tidy final answer.

```json
{
  "tool": "trace_log_annotation",
  "arguments": {
    "category": "correction",
    "content": "The mean was used at first; the human caught it and the median was applied.",
    "corrects_event_ids": [
      "evt_001"
    ],
    "conversation_snippet": "that's wrong, use the median not the mean",
    "session_id": "$SESSION_ID"
  }
}
```

Look for in the response:

- `Logged annotation:`

## Step 6 — Propose a measured decision (AI)

A decision backed by a number carries that number, in a shape a reader can inspect: the estimate, the interval and its coverage, how it was computed, and how much data it rested on. `direction` is the raw metric's sense — mean absolute error, where lower is better — while the estimate is oriented so a positive value favours what the decision proposes. Pass this ONLY for a measurement that was actually computed; when there is none, omit it and put the reasoning in `rationale`.

```json
{
  "tool": "trace_propose_decision",
  "arguments": {
    "description": "Keep median imputation over mean for the station readings.",
    "proposed_by_type": "ai",
    "proposed_by_id": "claude",
    "suggestion_type": "proactive",
    "rationale": "Checked on the 24 stations held out of the imputation.",
    "confidence": {
      "statistic": "mae_reduction",
      "estimate": 0.42,
      "unit": "mm",
      "direction": "lower",
      "interval": {
        "lower": 0.18,
        "upper": 0.67,
        "level": 0.95
      },
      "method": {
        "name": "percentile_bootstrap",
        "resamples": 2000
      },
      "sample_size": 24
    },
    "session_id": "$SESSION_ID"
  }
}
```

Look for in the response:

- `Decision proposed: evt_004`

## Step 7 — Resolve the measured decision (human accepts)

The measurement informs the decision; it never resolves it. A human still accepts, and the record keeps both the number and who acted on it.

```json
{
  "tool": "trace_resolve_decision",
  "arguments": {
    "event_id": "evt_004",
    "disposition": "accepted",
    "resolved_by_type": "human",
    "resolved_by_id": "human",
    "session_id": "$SESSION_ID"
  }
}
```

Look for in the response:

- `Decision evt_004 resolved: accepted`

## Step 8 — End the session

Ending the session prints the Attribution Audit, and this is where the attribution is read back: the contribution's direction and execution, the decision proposed by the AI and accepted, and the correction linked to `evt_001`. The audit names the session id.

```json
{
  "tool": "trace_end_session",
  "arguments": {
    "session_id": "$SESSION_ID",
    "summary": "Walkthrough complete: a decision proposed, accepted, applied and corrected, and a second backed by a measurement.",
    "write_scratchpad": false
  }
}
```

Look for in the response:

- `Session ended:`
- `--- Attribution Audit ---`
- `Contributions (1):`
- `direction=human, execution=ai`
- `artifact=data/stations.csv`
- `Decisions (2):`
- `proposed_by=ai`
- `disposition=accepted`
- `Corrections: 1 (corrects: evt_001)`

## Step 9 — Read the decision back

Query the completed session's decisions. The record kept both halves of the attribution: proposed by the AI, resolved by the human, accepted. That proposer-vs-resolver split is the distinction the whole system exists to preserve. The second decision also reads back its measurement, interval and coverage intact.

```json
{
  "tool": "trace_get_decisions",
  "arguments": {
    "session_id": "$SESSION_ID"
  }
}
```

Look for in the response:

- `"proposed_by":{"type":"ai"`
- `"resolved_by":{"type":"human"`
- `"disposition":"accepted"`
- `"statistic":"mae_reduction"`
- `"level":0.95`

## Step 10 — Add a learning

Learnings persist in the project's knowledge store on disk. `project` is omitted and resolves to the pin, the same as the session tools. The response echoes the new learning's id and content.

```json
{
  "tool": "trace_learn_add",
  "arguments": {
    "content": "peregrine falcon telemetry sentinel: median imputation beat the mean on the station readings.",
    "category": "learning"
  }
}
```

Look for in the response:

- `"added"`
- `"id": "lrn_`
- `peregrine falcon telemetry sentinel`

## Step 11 — Extract learnings from the record

Extraction mines the session's decisions and annotations into durable learnings. It already ran when the session ended, so running it again here adds nothing: extraction is idempotent, and this call reports `new_learnings: 0`.

```json
{
  "tool": "trace_learn_extract",
  "arguments": {}
}
```

Look for in the response:

- `"new_learnings": 0`

## Step 12 — Recall a learning

Recall ranks the store against a query and reports the backend that ranked it. The sentinel learning comes back with a score; the backend name makes a degraded ranker visible, never silent.

```json
{
  "tool": "trace_learn_recall",
  "arguments": {
    "context": "peregrine falcon telemetry sentinel"
  }
}
```

Look for in the response:

- `"results"`
- `"score"`
- `"backend"`
- `peregrine falcon telemetry sentinel`

## Step 13 — A write is denied across projects

Under a pin, WRITING to another project fails closed the same way reading does — the error names both the pinned key and the label asked for, and no foreign store is created.

```json
{
  "tool": "trace_learn_add",
  "arguments": {
    "project": "some-other-project",
    "content": "should never be written"
  }
}
```

Look for in the response:

- `"error"`
- `walkthrough`
- `some-other-project`

## Step 14 — A read is denied across projects

And reading another project fails closed too. Cross-project reads and writes do not silently cross; a follow-up check confirms no `some-other-project` store was created.

```json
{
  "tool": "trace_learn_recall",
  "arguments": {
    "project": "some-other-project",
    "context": "peregrine falcon telemetry sentinel"
  }
}
```

Look for in the response:

- `"error"`
- `walkthrough`
- `some-other-project`
