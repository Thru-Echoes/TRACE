# TRACE Usage Examples

Nine worked examples showing what to log and when, in increasing
complexity. Examples 1–5 cover the baseline (decisions, corrections,
contributions, decision chains); Examples 6–9 demonstrate v0.4.1
additions (`suggestion_type="requested"`, `discovery` category,
URI-form `corrects_event_ids`, `host`/`parent_event_id` on `tool_call`).
The full data model is defined in [`specification.md`](specification.md);
the protocol for *when* to call each tool lives in your project's
`CLAUDE.md` (installed by `trace-mcp-init`) or in the global
`~/.claude/CLAUDE.md`.

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
  conversation_snippet="That's breaking on the Unicode characters. You need to use encoding='utf-8-sig' for this dataset — it has a BOM.",  # v0.4.1 MUST for corrections
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
  conversation_snippet="Let's add the LDA pipeline — pick k by coherence, save the plot too",  # v0.4.1 MUST
  tags=["topic-modeling", "LDA", "pipeline"],
)
```

Log AFTER the artifact exists (not before). Set `artifact` to the file
path. Link to the decisions that led to this deliverable. Under v0.4.1,
`conversation_snippet` is **required** for contributions (and corrections)
— it must be either the relevant ~200 chars of user message or the
explicit absence marker `"<autonomous-stretch>"`
when there was no triggering user turn.

If the same task also produced a visualisation file, log a SECOND
contribution for it:

```python
trace_log_contribution(
  session_id=...,
  description="Generated topic coherence plot showing optimal k=12",
  direction="ai", execution="ai",
  artifact="results/figures/coherence_plot.png",
  related_decision_ids=["evt_007"],
  conversation_snippet="<autonomous-stretch>",  # v0.4.1 MUST — no new user turn since evt_007
  tags=["topic-modeling", "visualization"],
)
```

---

## Example 4 — Decision chain with `revises_event_id`

Earlier in the session, AI proposed analysing all 5 climate scenarios:

```python
trace_propose_decision(
  description="Analyze all 5 SSP scenarios (SSP1-1.9 through SSP5-8.5)",
  proposed_by_type="ai", proposed_by_id="ai-assistant",  # required
  suggestion_type="proactive",
)
# → returns evt_004 (resolved as accepted earlier)
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
  proposed_by_type="ai", proposed_by_id="ai-assistant",
  suggestion_type="proactive",
  tags=["SDM", "MaxEnt"],
)  # → evt_010
```

*Step 2 — User rejects, proposes random forest:*

```python
trace_resolve_decision(
  event_id="evt_010", disposition="rejected",
  resolved_by_type="human", resolved_by_id="user",
  revision_note="MaxEnt doesn't handle feature interactions well for this species; random forest preferred",
)

trace_propose_decision(
  description="Use random forest instead of MaxEnt for SDM",
  rationale="Better handling of feature interactions for this species' complex habitat requirements",
  proposed_by_type="human", proposed_by_id="user",
  suggestion_type="requested",
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
  conversation_snippet="You need spatial blocking for the CV, not random splits.",  # v0.4.1 MUST for corrections
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
  conversation_snippet="<autonomous-stretch>",  # v0.4.1 MUST — final write follows the correction; no new user turn
  tags=["SDM", "random-forest", "results"],
)
```

Total: 2 decisions (1 rejected + 1 accepted chain), 1 correction, 1 contribution.
A future reader can reconstruct: AI proposed MaxEnt → human chose RF
instead → AI made a CV mistake → human caught it → final results produced.

---

## Example 6 — v0.4.1: question → AI-proposal → accept

**User says:** "What else can we do to lift recall without dropping
threshold further?"

**AI replies with a three-step plan** (tighten regex, NFKC normalization,
re-run at 0.80). **User accepts:** "yes, do those three".

Under the v0.4.1 **Proposer Identity Rule** (spec §3.6), `proposed_by`
identifies the author of the proposal *content*, not the speaker of the
resolving directive. The human asked a question; the AI authored the
plan; the human accepted it. So:

```python
trace_propose_decision(
  session_id=...,
  description="Three-step plan to improve recall: (1) tighten passage-extractor regex for multi-sentence quotations, (2) add Unicode NFKC normalization in preprocessing, (3) re-run matcher at threshold 0.80 and compare F1",
  rationale="Researcher asked what would lift recall without dropping threshold further; three interventions ordered by expected effect size on the held-out set",
  proposed_by_type="ai",                       # AI authored the plan
  proposed_by_id="ai-assistant",               # required; identifies the AI instance
  suggestion_type="requested",                 # AI proposed it in response to a human question (vs. proactive volunteering)
  conversation_snippet="what else can we do to lift recall without dropping threshold further?",  # v0.4.1 SHOULD
  tags=["methodology", "recall"],
)  # → evt_006

trace_resolve_decision(
  session_id=..., event_id="evt_006",
  disposition="accepted",
  resolved_by_type="human", resolved_by_id="researcher-jane",  # human accepts
)
```

Contrast with `suggestion_type="proactive"` (AI volunteered an idea the
human did not ask for) and `proposed_by_type="human"` (human stated the
substantive plan in their own words and the AI executed). All three
patterns are first-class. See §8.2 of the spec for the recognition
table.

---

## Example 7 — v0.4.1: discovery annotation

**AI is running a long-running migration script unattended. The script
emits a warning that one of the source tables has 12% duplicate rows
matching on `(user_id, event_time)` — a finding that affects the
downstream join.**

This is not a correction (no one was wrong) and not a gotcha for the
current code (the duplication is in upstream data). It is a *discovery*
made during autonomous work.

```python
trace_log_annotation(
  session_id=...,
  category="discovery",   # v0.4.1 new category
  content="Source table `raw_events` has 12% duplicate (user_id, event_time) pairs on 2026-04-01 partition. Affects the planned LEFT JOIN in the aggregation step. Recommend de-dup with ROW_NUMBER() before joining.",
  tags=["data-quality", "migration"],
)
```

Use `discovery` for non-trivial findings surfaced during autonomous
execution where the AI is working without immediate human review. Use
`gotcha` for surprises encountered in collaborative work (someone may
want to act on it immediately). Use `correction` only when a previous
event was actually wrong.

---

## Example 8 — v0.4.1: URI-form `corrects_event_ids`

A correction's target is not always a TRACE event ID — it can be a line
in a JSONL log, an external commit, output from a dispatched subagent,
or a chunk of tool output. v0.4.1 allows URI-form references in
`corrects_event_ids` alongside ordinary `evt_*` IDs.

```python
# Correcting a chunk of subagent output that recommended the wrong index:
trace_log_annotation(
  session_id=...,
  category="correction",
  content="Subagent A recommended a B-tree index on `event_time`; this is wrong because the query selects by `(user_id, date_trunc('day', event_time))` and benefits from a multi-column index. Switched to (user_id, event_time DESC).",
  corrects_event_ids=["subagent:agent_run_42/recommendation_3"],   # URI-form
  conversation_snippet="<autonomous-stretch>",  # absence marker, v0.4.1 §3.4.1
  tags=["indexing", "subagent-review"],
)

# Correcting a line in an upstream JSONL audit log:
trace_log_annotation(
  category="correction",
  content="The recorded latency at line 3142 of the perf log is from a stale buffer; rerun gave 84ms (not 312ms).",
  corrects_event_ids=["jsonl:logs/perf_2026-04-15.jsonl#L3142"],
  conversation_snippet="The 312ms number on line 3142 is wrong, can you rerun?",
)

# Correcting external commit history:
trace_log_annotation(
  category="correction",
  content="Commit abc123 introduced the off-by-one in pagination; fixed in this session by clamping `offset` at `total - page_size`.",
  corrects_event_ids=["external:git+https://github.com/example/repo@abc123"],
  conversation_snippet="That pagination bug from last week — fix it the right way this time",
)
```

Supported URI schemes (v0.4.1 §3.7.1): `external:`, `jsonl:`, `subagent:`,
`tool-result:`. Plain `evt_*` IDs continue to work for in-session
references. Consumers MUST treat unrecognized schemes as opaque
identifiers (no error, no warning).

---

## Example 9 — v0.4.1: `parent_event_id` on tool dispatch chains

When one tool call dispatches a child tool call (orchestrator → worker;
host-internal helper → user-visible tool), record the parent–child link
on the child's `parent_event_id`. This lets `trace_get_decision_chain()`
walk the dispatch tree.

```python
# Parent: a host-internal orchestrator (e.g., Claude Code's Agent tool)
# dispatching a subagent. The parent event has no parent itself.
trace_log_tool_call(
  session_id=...,
  server="claude-code",                     # the host that ran the tool
  tool_name="dispatch_review_subagents",
  input={"agents": ["security", "perf"]},
  status="success",
  host="internal",                          # v0.4.1: "mcp" | "internal" | "external"
)  # → evt_020

# Child: each subagent run dispatched by evt_020 — link via parent_event_id
trace_log_tool_call(
  session_id=...,
  server="claude-code",
  tool_name="security_audit_subagent",
  input={"scope": "src/auth/"},
  status="success",
  host="internal",
  parent_event_id="evt_020",                # v0.4.1: links child to dispatching parent
)  # → evt_021
```

The new `host` field on `tool_call` is an enum:
- `"mcp"` (default) — external MCP server (preserves v0.3.0 / v0.4.0 semantics).
- `"internal"` — a host-internal helper such as a Claude Code subagent dispatch.
- `"external"` — non-MCP external tools (e.g., a shell-invoked CLI).

`parent_event_id` MUST be an in-session event ID; the session-end audit
emits a "Dangling reference" warning if it isn't. The PROV-LD mapping
(spec §6) emits `prov:wasInformedBy` from child to parent so downstream
consumers can walk the dispatch tree.

---

## Principles

- Log methodology decisions, not trivial ones.
- Decision rationales must be specific and technical:
  - **Bad**: "this seemed like a good threshold"
  - **Good**: "0.80 cosine similarity gives F1=0.78 on our 30-pair validation set; lowering to 0.75 adds 40% more hits but drops precision to 0.61"
- Tag events with relevant domain terms for later searchability.
- For messy data situations, always log the data quality issue as a `gotcha` AND the decision about how to handle it.
