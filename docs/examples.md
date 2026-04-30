# TRACE Usage Examples

Five worked examples showing what to log and when, in increasing
complexity. The full data model is defined in
[`specification.md`](specification.md); the protocol for *when* to call
each tool lives in your project's `CLAUDE.md` (installed by
`trace-mcp init`) or in the global `~/.claude/CLAUDE.md`.

---

## Example 1 — Basic decision from conversation

**User says:** "Let's use cosine similarity instead of Euclidean distance for the clustering."

**AI should do (BEFORE implementing the change):**

```python
trace_propose_decision(
  session_id=...,
  description="Use cosine similarity instead of Euclidean distance for document clustering",
  rationale="User preference — cosine similarity is standard for high-dimensional text embeddings where magnitude is less informative than direction",
  proposed_by_type="human", proposed_by_id="user",
  suggestion_type="requested",
  tags=["clustering", "distance-metric"],
)
# → returns evt_003

trace_resolve_decision(
  session_id=..., event_id="evt_003",
  disposition="accepted",
  resolved_by_type="human", resolved_by_id="user",
)
```

Then implement the change. The user's statement is both the decision and
the acceptance — log both before writing code.

---

## Example 2 — Recognising and logging a correction

**AI writes code using `pandas.read_csv()` with default encoding.**

**User says:** "That's breaking on the Unicode characters. You need to use `encoding='utf-8-sig'` for this dataset — it has a BOM."

**AI should do:**

```python
trace_log_annotation(
  session_id=...,
  category="correction",
  content="AI used default encoding for CSV read; user corrected to encoding='utf-8-sig' because the dataset has a UTF-8 BOM that causes parsing errors with default encoding",
  corrects_event_ids=["evt_005"],  # the tool_call or contribution where the bug was introduced
  tags=["data-loading", "encoding"],
)
```

The user didn't say "log a correction" — they just fixed the problem.
Recognise it as a correction and log it with `corrects_event_ids`
linking back to the event where the mistake happened.

---

## Example 3 — Contribution with artifact and decision link

**After implementing an analysis script, the AI writes the file.**

**AI should do (AFTER the file exists):**

```python
trace_log_contribution(
  session_id=...,
  description="Implemented topic modeling pipeline with LDA, coherence-based k selection, and visualization",
  direction="collaborative",  # user specified the approach, AI designed the implementation
  execution="ai",
  artifact="src/analysis/topic_model.py",
  related_decision_ids=["evt_003", "evt_007"],  # decisions that motivated this work
  tags=["topic-modeling", "LDA", "pipeline"],
)
```

Log AFTER the artifact exists (not before). Set `artifact` to the file
path. Link to the decisions that led to this deliverable.

If the same task also produced a visualisation file, log a SECOND
contribution for it:

```python
trace_log_contribution(
  session_id=...,
  description="Generated topic coherence plot showing optimal k=12",
  direction="ai", execution="ai",
  artifact="results/figures/coherence_plot.png",
  related_decision_ids=["evt_007"],
  tags=["topic-modeling", "visualization"],
)
```

---

## Example 4 — Decision chain with `revises_event_id`

Earlier in the session, AI proposed analysing all 5 climate scenarios:

```python
trace_propose_decision(description="Analyze all 5 SSP scenarios (SSP1-1.9 through SSP5-8.5)")
# → returns evt_004 (accepted)
```

**Later, user says:** "Actually, let's narrow to just SSP2-4.5 and SSP5-8.5 — we don't have time for all five."

**AI should do:**

```python
trace_propose_decision(
  session_id=...,
  description="Narrow analysis to SSP2-4.5 and SSP5-8.5 only, dropping SSP1-1.9, SSP1-2.6, and SSP3-7.0",
  rationale="Time constraint — user decided to focus on the moderate and high-emission scenarios which bracket the policy-relevant range",
  proposed_by_type="human", proposed_by_id="user",
  suggestion_type="requested",
  revises_event_id="evt_004",  # links to the original "all 5" decision
  tags=["scope", "SSP-scenarios"],
)
# → returns evt_009

trace_resolve_decision(
  session_id=..., event_id="evt_009",
  disposition="accepted",
  resolved_by_type="human", resolved_by_id="user",
)

# Also resolve the original decision as revised:
trace_resolve_decision(
  session_id=..., event_id="evt_004",
  disposition="revised",
  resolved_by_type="human", resolved_by_id="user",
  revision_note="Narrowed to 2 scenarios due to time constraint — see evt_009",
)
```

The new decision sets `revises_event_id` to link to the original. This
creates the chain that `trace_get_decision_chain()` can traverse.

---

## Example 5 — Complex multi-event scenario

User and AI are building a species distribution model. In one exchange:

1. AI proposes using MaxEnt.
2. User says "No, use random forest — MaxEnt doesn't handle interactions well for this species."
3. AI implements random forest, but gets the train/test split wrong (spatial autocorrelation).
4. User catches it: "You need spatial blocking for the CV, not random splits."
5. AI fixes the code and writes results.

**AI should log (in order):**

*Step 1 — AI proposes MaxEnt:*

```python
trace_propose_decision(
  description="Use MaxEnt for species distribution modeling",
  rationale="Standard approach for presence-only data with environmental covariates",
  proposed_by_type="ai", suggestion_type="proactive",
  tags=["SDM", "MaxEnt"],
)  # → evt_010
```

*Step 2 — User rejects, proposes random forest:*

```python
trace_resolve_decision(
  event_id="evt_010", disposition="rejected",
  resolved_by_type="human",
  revision_note="MaxEnt doesn't handle feature interactions well for this species; random forest preferred",
)

trace_propose_decision(
  description="Use random forest instead of MaxEnt for SDM",
  rationale="Better handling of feature interactions for this species' complex habitat requirements",
  proposed_by_type="human", suggestion_type="requested",
  revises_event_id="evt_010",  # links rejection to replacement
  tags=["SDM", "random-forest"],
)  # → evt_011 (resolve as accepted)
```

*Steps 3–4 — AI implements with wrong CV, user corrects:*

```python
trace_log_annotation(
  category="correction",
  content="AI implemented random k-fold CV for spatial species data; user corrected to spatial block CV to avoid spatial autocorrelation inflating accuracy estimates",
  corrects_event_ids=["evt_012"],  # the tool_call or contribution with the bad CV
  tags=["cross-validation", "spatial-autocorrelation", "SDM"],
)
```

*Step 5 — AI writes corrected results:*

```python
trace_log_contribution(
  description="Species distribution model with spatial block CV — RF model, 5 environmental covariates, AUC=0.84",
  direction="human", execution="ai",
  artifact="results/sdm_spatial_cv_results.csv",
  related_decision_ids=["evt_011"],
  tags=["SDM", "random-forest", "results"],
)
```

Total: 2 decisions (1 rejected + 1 accepted chain), 1 correction, 1 contribution.
A future reader can reconstruct: AI proposed MaxEnt → human chose RF
instead → AI made a CV mistake → human caught it → final results produced.

---

## Principles

- Log methodology decisions, not trivial ones.
- Decision rationales must be specific and technical:
  - **Bad**: "this seemed like a good threshold"
  - **Good**: "0.80 cosine similarity gives F1=0.78 on our 30-pair validation set; lowering to 0.75 adds 40% more hits but drops precision to 0.61"
- Tag events with relevant domain terms for later searchability.
- For messy data situations, always log the data quality issue as a `gotcha` AND the decision about how to handle it.
