# TRACE Protocol v2.1 - Claude Code Instructions

> **TRACE**: Transparent Research AI Collaboration Environment
> **Version**: 2.1
> **Last Updated**: 2026-02-02

---

## What's New in v2.1

1. **Smart TRACE Triggers**: Behavioral patterns that prompt logging of gotchas, decisions, learnings, ideas
2. **Knowledge Check Tool**: `trace_knowledge_check` validates events and prevents duplicates
3. **Automatic Checkpoints**: Periodic session checkpoints to capture unlogged knowledge
4. **Knowledge Persistence**: Cross-session learning consolidation and context refresh
5. **Relevance Scoring**: Surface relevant past learnings for current work

## What's in v2.0

1. **New Authorship Model**: Distinguishes between who **directed** vs who **executed**
2. **AI Suggestion Tracking**: Full tracking of AI proposals with accept/reject/modify outcomes
3. **Git Integration**: Auto-detect `[HUMAN-EDIT]` commits
4. **Line Counts**: Track lines for all categories
5. **Multi-Content-Type Support**: Code (lines), Text (lines + words), Data (lines + rows)
6. **Audit Log**: Immutable log of all TRACE operations

---

## Core Concept: Direction vs Execution

TRACE v2.0 separates two concepts:

| Concept | Question | Categories |
|---------|----------|------------|
| **Direction** | Who decided this change should happen? | human_directed, ai_suggested, collaborative |
| **Execution** | Who wrote the actual code? | AI, human |

### Examples

| Scenario | Direction | Execution | Category |
|----------|-----------|-----------|----------|
| Human: "Add a logout button" → AI writes code | Human | AI | `human_directed.ai_executed` |
| AI: "We should add caching" → Human accepts | AI | AI | `ai_suggested.accepted` |
| AI suggests, human modifies | AI | Both | `ai_suggested.modified` |
| Human edits file directly | Human | Human | `human_manual_edit` |

---

## Content Types

TRACE v2.0 supports different content types with appropriate metrics:

| Content Type | Description | Tracked Metrics |
|--------------|-------------|-----------------|
| `code` | Source code (scripts, programs) | Lines |
| `text` | Documents (papers, markdown) | Lines + Words |
| `data` | Data files (CSV, JSON) | Lines + Rows |

### When to Use Each Type

**Code**: `.py`, `.js`, `.ts`, `.java`, etc.
- Track lines of code contributed by human vs AI

**Text**: `.md`, `.txt`, `.tex`, `.rst`, etc.
- Track both lines AND words (useful for papers/documentation)
- Word counts help measure actual writing contribution

**Data**: `.csv`, `.json`, `.yaml`, etc.
- Track both lines AND rows/entries
- Useful for dataset curation work

---

## Git Convention

When you (human) make direct edits outside AI sessions, commit with:

```bash
git commit -m "[HUMAN-EDIT] Description of what you changed"
```

TRACE will auto-detect these commits and log them appropriately.

### Optional Sub-tags

```bash
[HUMAN-EDIT][BUGFIX] Fixed null pointer
[HUMAN-EDIT][REFACTOR] Simplified loop
[HUMAN-EDIT][TYPO] Fixed spelling
```

---

## TRACE Workflow (v2.0)

### At Session Start

```
1. trace_start_session
   - purpose: What you're working on
   - scientific_stage: exploration|analysis|etc
   - ai_model: claude-opus-4-5-20251101

2. trace_scan_git_commits
   - since: "1 day ago"
   - Detects any [HUMAN-EDIT] commits since last session
```

### During Work

#### When AI Suggests Something

**Step 1**: Log the suggestion
```
trace_log_suggestion
- description: "Refactor validation to use early returns"
- suggestion_type: code_change|architecture|approach|bugfix|optimization|refactor|feature
- lines_proposed: 25
- files_affected: ["validators.py"]
- ai_confidence: high|medium|low
```

**Step 2**: After human decides, resolve it
```
trace_resolve_suggestion
- suggestion_id: "SUG001"
- status: accepted|rejected|modified
- lines_accepted_as_is: 20 (if accepted/modified)
- lines_modified: 5 (if modified)
- lines_rejected: 0 (lines human didn't use)
- human_rationale: "Good idea, tweaked for our use case"
```

#### When Logging Code/Content

```
trace_log_code
- file_path: "path/to/file.py"
- content_type: code|text|data (default: code)
- contribution_type: creation|modification|refactor|bugfix|optimization
- description: "What this contribution does"

# IMPORTANT: Specify direction source
- direction_source: human_directed|ai_suggested|collaborative

# Then specify lines for the appropriate category:

# If human_directed:
- human_directed_ai_executed_lines: 50 (AI wrote based on human direction)
- human_directed_human_executed_lines: 10 (human wrote themselves)

# If ai_suggested (from a resolved suggestion):
- ai_suggested_accepted_lines: 40 (accepted as-is)
- ai_suggested_modified_lines: 10 (modified by human)
- related_suggestion_id: "SUG001"

# If human manually edited:
- human_manual_edit_lines: 15

# If collaborative:
- collaborative_lines: 30

# For TEXT content type, also specify words:
- human_directed_ai_executed_words: 500
- human_directed_human_executed_words: 100
- ai_suggested_accepted_words: 400
- ai_suggested_modified_words: 100
- human_manual_edit_words: 150
- collaborative_words: 50

# For DATA content type, also specify rows:
- human_directed_ai_executed_rows: 100
- human_directed_human_executed_rows: 20
- ai_suggested_accepted_rows: 50
- ai_suggested_modified_rows: 10
- human_manual_edit_rows: 30
- collaborative_rows: 5
```

#### When Human Modifies AI Output

```
trace_log_intervention
- intervention_type: correction|override|rejection|refinement
- ai_output_summary: "AI suggested using regex pattern X"
- human_action: "Changed to use library Y instead"
- rationale: "Library Y is more maintainable"
- lines_affected: 15
```

### At Session End

```
trace_end_session
- session_id: "S001"
- summary: "What was accomplished"
- ai_helpfulness_rating: 1-5

trace_compute_metrics
```

---

## New Tools in v2.0

| Tool | Purpose |
|------|---------|
| `trace_log_suggestion` | Log an AI suggestion (before human decides) |
| `trace_resolve_suggestion` | Record outcome: accepted/rejected/modified with line counts |
| `trace_scan_git_commits` | Auto-detect [HUMAN-EDIT] commits |
| `trace_log_code` | Log contributions with content_type: code/text/data |

---

## Metrics Computed (v2.0)

### Code/Content Metrics

```
code_metrics.total_lines:
  human_directed_ai_executed: 150
  human_directed_human_executed: 20
  ai_suggested_accepted: 80
  ai_suggested_modified: 30
  human_manual_edit: 45
  collaborative: 10

code_metrics.total_words: (for text content type)
  human_directed_ai_executed: 1500
  human_directed_human_executed: 200
  ai_suggested_accepted: 800
  ai_suggested_modified: 300
  human_manual_edit: 450
  collaborative: 100

code_metrics.total_rows: (for data content type)
  human_directed_ai_executed: 500
  human_directed_human_executed: 50
  ai_suggested_accepted: 200
  ai_suggested_modified: 30
  human_manual_edit: 100
  collaborative: 20

code_metrics.by_source:
  human_direction_percentage: 50.7
  ai_suggestion_percentage: 32.8
  human_manual_percentage: 13.4

code_metrics.by_source_words: (for text)
code_metrics.by_source_rows: (for data)

code_metrics.by_content_type:
  code: 10
  text: 5
  data: 3

code_metrics.git_integration:
  manual_edit_commits_detected: 5
  manual_edit_lines_added: 45
  manual_edit_lines_removed: 12
```

### Suggestion Metrics

```
suggestion_metrics:
  total_suggestions: 15
  accepted_count: 8
  rejected_count: 3
  modified_count: 4

  acceptance_rate: 0.533
  rejection_rate: 0.200
  modification_rate: 0.267

  lines_proposed_total: 250
  lines_accepted_as_is: 120
  lines_modified_by_human: 45
  lines_rejected: 85
```

---

## Example Session (v2.0)

```python
# =========================================
# SESSION START
# =========================================

trace_start_session(
    purpose="Add email validation feature",
    scientific_stage="analysis",
    ai_model="claude-opus-4-5-20251101"
)
# Returns: Session started: S001

# Check for manual edits since last session
trace_scan_git_commits(since="1 day ago", session_id="S001")
# Returns: Found 2 [HUMAN-EDIT] commits, logged 2 new.

# =========================================
# HUMAN DIRECTS: "Add email validation function"
# =========================================

# AI writes code based on human's direction
trace_log_code(
    file_path="validators.py",
    contribution_type="creation",
    description="Email validation function with regex",
    direction_source="human_directed",
    human_directed_ai_executed_lines=35,
    session_id="S001"
)
# Returns: Code contribution logged (CC001)
# Direction: human_directed
# Human-directed/AI-executed: 35

# =========================================
# AI SUGGESTS: "Use email-validator library instead"
# =========================================

# Step 1: Log the suggestion
trace_log_suggestion(
    description="Use email-validator library instead of custom regex for better RFC compliance",
    suggestion_type="optimization",
    lines_proposed=20,
    files_affected=["validators.py", "requirements.txt"],
    ai_confidence="high",
    what_prompted="Code review of email validation",
    session_id="S001"
)
# Returns: Suggestion logged (SUG001)

# Human reviews and decides to accept with modifications
# Step 2: Resolve the suggestion
trace_resolve_suggestion(
    suggestion_id="SUG001",
    status="modified",
    lines_accepted_as_is=12,
    lines_modified=8,
    lines_rejected=0,
    human_rationale="Good idea, but kept custom domain validation rules",
    modification_description="Added our internal domain whitelist check"
)
# Returns: Suggestion SUG001 resolved: modified
# Accepted: 12, Modified: 8, Rejected: 0

# Log the code from this suggestion
trace_log_code(
    file_path="validators.py",
    contribution_type="modification",
    description="Replaced custom regex with email-validator + domain rules",
    direction_source="ai_suggested",
    ai_suggested_accepted_lines=12,
    ai_suggested_modified_lines=8,
    related_suggestion_id="SUG001",
    session_id="S001"
)

# =========================================
# AI SUGGESTS SOMETHING HUMAN REJECTS
# =========================================

trace_log_suggestion(
    description="Add async email validation for batch processing",
    suggestion_type="feature",
    lines_proposed=45,
    files_affected=["validators.py", "async_utils.py"],
    ai_confidence="medium",
    session_id="S001"
)
# Returns: Suggestion logged (SUG002)

# Human rejects
trace_resolve_suggestion(
    suggestion_id="SUG002",
    status="rejected",
    lines_rejected=45,
    human_rationale="We don't need async - validation is always synchronous in our use case"
)
# Returns: Suggestion SUG002 resolved: rejected
# Rejected: 45

# =========================================
# SESSION END
# =========================================

trace_end_session(
    session_id="S001",
    summary="Added email validation. Used email-validator library per AI suggestion with modifications.",
    ai_helpfulness_rating=4
)

trace_compute_metrics()
# Returns all updated metrics
```

---

## For Publication

When preparing research, use:

```
trace_export_report(format="markdown")
```

This generates a publication-ready summary including:
- Total lines by direction source and execution
- AI suggestion acceptance/rejection/modification rates
- Git integration statistics
- All metrics

### Example Disclosure Statement

> AI assistance was documented using the TRACE protocol (v2.0).
> Of 500 total lines: 45% were human-directed (AI executed), 35% were from
> accepted/modified AI suggestions, and 20% were human manual edits.
> AI proposed 15 suggestions: 8 accepted, 4 modified, 3 rejected
> (53% acceptance rate, 27% modification rate).

---

## File Structure

```
project/
├── CLAUDE.md                 # This file
├── trace.json                # TRACE data (v2.0 schema)
├── mcp_server/
│   └── server.py             # TRACE MCP server v2.0
├── .mcp.json                  # MCP configuration
└── .trace/                    # Optional: diff storage
    └── diffs/                 # Stored diffs for audit
```

---

## Migration from v1.0

TRACE v2.0 automatically migrates v1.0 data:
- `ai_authored_lines` → `human_directed.ai_executed_lines` (assumes human direction)
- `human_authored_lines` → `human_directed.human_executed_lines`
- `human_improved_ai_lines` → `ai_suggested.modified_lines`

---

---

## Example: Logging Text Contributions (Paper Writing)

```python
# When AI helps write a paper section
trace_log_code(
    file_path="manuscript/introduction.tex",
    content_type="text",  # Track words in addition to lines
    contribution_type="creation",
    description="Introduction section for manuscript",
    direction_source="human_directed",
    human_directed_ai_executed_lines=45,
    human_directed_ai_executed_words=850,
    human_directed_human_executed_lines=10,
    human_directed_human_executed_words=200,
    session_id="S001"
)
# Returns: Text contribution logged (CC002): manuscript/introduction.tex
# Content type: text, Direction: human_directed
# Human-directed/AI-executed: 45 lines, Total words: 1050
```

---

## Example: Logging Data Contributions (Dataset Curation)

```python
# When AI helps curate a dataset
trace_log_code(
    file_path="data/processed_articles.csv",
    content_type="data",  # Track rows in addition to lines
    contribution_type="modification",
    description="Cleaned and labeled article metadata",
    direction_source="collaborative",
    collaborative_lines=500,
    collaborative_rows=250,
    session_id="S001"
)
# Returns: Data contribution logged (CC003): data/processed_articles.csv
# Content type: data, Direction: collaborative
# Collaborative: 500 lines, Total rows: 250
```

---

---

## Smart TRACE Triggers

TRACE v2.1 introduces **behavioral triggers** - conditions that should prompt logging of specific entry types. These triggers help ensure comprehensive documentation without requiring constant manual attention.

### How Triggers Work

1. **Situational Pattern** → Triggers fire when specific situations are detected
2. **Knowledge Check** → Call `trace_knowledge_check` to validate and check for duplicates
3. **Log Entry** → If recommended, call the appropriate `trace_log_*` or `trace_add_*` tool

### Trigger: Gotchas

**Log a gotcha when you encounter:**

| Trigger Condition | Example |
|-------------------|---------|
| Unexpected behavior | "The API returns 200 even on validation errors" |
| Documentation mismatch | "The docs say X but it actually does Y" |
| Non-obvious requirement | "Must call init() before any other method" |
| Workaround needed | "Had to disable strict mode due to false positives" |
| Environment-specific issue | "Only fails on Python 3.11+" |
| Silent failure | "Function returns None instead of raising" |
| Configuration gotcha | "Setting X also implicitly enables Y" |

**Action:** Call `trace_knowledge_check(context, event_type="gotcha")` then `trace_add_gotcha` if recommended.

### Trigger: Decisions

**Log a decision when you:**

| Trigger Condition | Example |
|-------------------|---------|
| Choose between approaches | "Using pandas vs polars for data processing" |
| Select a library/tool | "Chose pytest over unittest" |
| Make architectural choice | "Separating concerns into microservices" |
| Evaluate trade-offs | "Prioritizing readability over performance here" |
| Decide on data structure | "Using dict for O(1) lookup vs list" |
| Set a convention | "All timestamps will be UTC" |
| Reject an alternative | "Not using ORM due to query complexity needs" |

**Action:** Call `trace_knowledge_check(context, event_type="decision")` then `trace_add_decision` if recommended.

### Trigger: Learnings

**Log a learning when you:**

| Trigger Condition | Example |
|-------------------|---------|
| Understand codebase behavior | "The auth middleware caches tokens for 5min" |
| Discover design rationale | "Pagination uses cursor-based for consistency" |
| Learn library capability | "pytest fixtures can be scoped to session" |
| Find undocumented feature | "API supports batch mode with ?bulk=true" |
| Realize best practice | "Should validate input at boundary, not deep" |
| Understand failure mode | "Service degrades gracefully when cache unavailable" |

**Action:** Call `trace_knowledge_check(context, event_type="learning")` then `trace_add_learning` if recommended.

### Trigger: Ideas

**Log an idea when you:**

| Trigger Condition | Example |
|-------------------|---------|
| Notice improvement opportunity | "Could cache this expensive computation" |
| Identify optimization | "Batch these N API calls into one" |
| Think of new feature | "Users might want export to CSV" |
| See refactoring opportunity | "These 3 functions share 80% logic" |
| Propose alternative approach | "Could use event sourcing instead" |
| Suggest automation | "This manual step could be a pre-commit hook" |

**Action:** Call `trace_knowledge_check(context, event_type="idea")` then `trace_log_idea` if recommended.

### Trigger: Interventions

**Log an intervention when human:**

| Trigger Condition | Example |
|-------------------|---------|
| Modifies AI code before commit | "Changed variable names for clarity" |
| Corrects AI logic error | "Fixed off-by-one in loop condition" |
| Overrides AI recommendation | "Used simpler approach than suggested" |
| Adds missing error handling | "Added null check AI omitted" |
| Removes unnecessary code | "Deleted over-engineered abstraction" |
| Changes AI's approach | "Switched from recursion to iteration" |

**Action:** Call `trace_knowledge_check(context, event_type="intervention")` then `trace_log_intervention` if recommended.

### Trigger: Code Contributions

**Log a code contribution after:**

| Trigger Condition | Example |
|-------------------|---------|
| Creating new file | "Created utils/validators.py" |
| Significant modification | "Added authentication to API routes" |
| Feature completion | "Finished user registration flow" |
| Bug fix | "Fixed race condition in cache" |
| Refactoring | "Extracted common logic to base class" |
| Test creation | "Added unit tests for payment module" |

**Action:** Call `trace_knowledge_check(context, event_type="code")` then `trace_log_code` if recommended.

---

## Using trace_knowledge_check

The `trace_knowledge_check` tool validates whether an event should be logged and checks for duplicates.

### Basic Usage

```python
# When you encounter something that might need logging
trace_knowledge_check(
    context="The pytest-asyncio plugin requires explicit mode='auto' in pytest.ini, otherwise async tests silently pass without running",
    event_type="gotcha"  # Optional hint: gotcha|decision|learning|idea|intervention|code
)
```

### Response Format

```json
{
  "should_log": true,
  "recommended_types": ["gotcha", "learning"],
  "confidence": "high",
  "reasoning": "This describes unexpected behavior requiring a workaround",
  "similar_entries": [],
  "suggested_fields": {
    "gotcha": {
      "problem": "pytest-asyncio silently passes async tests without running them",
      "solution": "Add 'asyncio_mode = auto' to pytest.ini",
      "severity": "high",
      "tags": ["testing", "pytest", "async"]
    }
  }
}
```

### When to Call

1. **Proactively** - When a trigger condition is met
2. **Checkpoint** - Periodically during long sessions to ensure nothing was missed
3. **Before logging** - To validate and get pre-filled suggestions

### Duplicate Detection

The tool checks existing TRACE entries for similar content:
- Exact matches are flagged
- Semantic similarity is assessed
- Returns similar entries so you can decide whether to add or update

---

## Trigger Integration Example

```python
# =========================================
# SCENARIO: Discovering unexpected behavior
# =========================================

# 1. AI encounters something unexpected
# "The pandas merge() silently drops rows with NaN keys"

# 2. Recognize this matches "gotcha" trigger pattern

# 3. Call knowledge check
trace_knowledge_check(
    context="pandas merge() silently drops rows where the merge key is NaN, no warning given",
    event_type="gotcha"
)
# Returns: should_log=true, recommended_types=["gotcha", "learning"]

# 4. Log the gotcha
trace_add_gotcha(
    problem="pandas merge() silently drops rows with NaN merge keys",
    solution="Use df.merge(..., how='outer') and manually handle NaN keys, or fillna() before merge",
    severity="high",
    tags=["pandas", "data-processing", "silent-failure"]
)

# 5. Optionally also log as learning
trace_add_learning(
    learning="pandas merge behavior with NaN keys differs from SQL NULL handling",
    evidence="Lost 15% of rows due to NaN customer_ids being silently dropped",
    confidence="high",
    tags=["pandas", "data-quality"]
)
```

---

## Automatic Checkpoints

TRACE v2.1 introduces automatic checkpoints to ensure knowledge is captured even during long or complex sessions.

### When to Checkpoint

| Trigger | Description |
|---------|-------------|
| **Time-based** | Every 30-45 minutes of active work |
| **Milestone** | After completing a significant task or feature |
| **Context switch** | When switching to a different area of the codebase |
| **Before break** | When human indicates they're stepping away |
| **Problem solved** | After resolving a tricky bug or issue |

### Checkpoint Process

```python
# 1. Call checkpoint to review session and identify unlogged items
trace_checkpoint(
    session_id="S001",
    trigger="milestone",  # time|milestone|context_switch|break|problem_solved
    notes="Finished authentication module"  # Optional context
)

# 2. Review checkpoint output - it will identify:
#    - Potential unlogged decisions
#    - Learnings that should be captured
#    - Code contributions not yet recorded
#    - Suggestions that need resolution

# 3. Log any identified items using appropriate tools
```

### Checkpoint Output

```json
{
  "checkpoint_id": "CP001",
  "session_id": "S001",
  "timestamp": "2026-02-02T14:30:00",
  "session_duration_minutes": 45,
  "summary": {
    "interactions_since_last": 12,
    "files_modified": ["auth.py", "middleware.py"],
    "pending_suggestions": 1,
    "estimated_unlogged": {
      "decisions": 2,
      "learnings": 1,
      "code_contributions": 3
    }
  },
  "prompts": [
    "Decision needed: How should token refresh be handled?",
    "Learning detected: Discovered middleware execution order",
    "Code not logged: auth.py modifications (~50 lines)"
  ],
  "recommendations": [
    "Log decision about token refresh strategy",
    "Capture learning about middleware ordering",
    "Log code contribution for auth.py"
  ]
}
```

### AI Behavioral Rule: Proactive Checkpoints

**IMPORTANT**: As AI, you should proactively suggest checkpoints when:

1. The conversation has been going for 30+ minutes without a checkpoint
2. A significant piece of work was just completed
3. The human says things like "let me think", "I'll be back", "taking a break"
4. You notice multiple unresolved decisions or unlogged learnings accumulating
5. The topic/context shifts significantly

**Example prompt:**
> "We've been working for about 40 minutes and completed the auth module. Would you like me to run a checkpoint to capture any learnings or decisions we should document?"

---

## Knowledge Persistence & Context Refresh

Ensuring learnings persist across sessions and remain accessible when relevant.

### The Knowledge Lifecycle

```
┌─────────────────────────────────────────────────────────────────┐
│                    KNOWLEDGE LIFECYCLE                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. CAPTURE        2. CONSOLIDATE      3. SURFACE              │
│  ───────────       ──────────────      ─────────               │
│  During session    End of session      Start of session        │
│  - trace_add_*     - trace_checkpoint  - trace_get_context     │
│  - knowledge_check - consolidate       - trace_query           │
│                      learnings         - context_refresh       │
│                                                                 │
│                         ┌──────┐                                │
│                         │TRACE │                                │
│                         │ .json│                                │
│                         └──────┘                                │
│                            │                                    │
│                    ┌───────┴───────┐                            │
│                    ▼               ▼                            │
│               Learnings        Decisions                        │
│               Gotchas          Ideas                            │
│               Errors           Patterns                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### At Session Start: Context Refresh

Always begin sessions by refreshing context with relevant past knowledge:

```python
# 1. Start session
trace_start_session(
    purpose="Implement payment processing",
    scientific_stage="analysis"
)

# 2. Scan for manual edits since last session
trace_scan_git_commits(since="1 day ago")

# 3. Refresh context with relevant past knowledge
trace_context_refresh(
    topics=["payment", "stripe", "transactions", "api"],
    include_recent=True,  # Include recent learnings regardless of topic
    max_items=10
)
# Returns relevant: gotchas, decisions, learnings, patterns
```

### During Session: Continuous Learning

As you work, the knowledge base grows. Key behaviors:

| Behavior | Tool | When |
|----------|------|------|
| Check before logging | `trace_knowledge_check` | Before any trace_add_* call |
| Link related entries | `related_to` field | When entries connect to previous ones |
| Update existing | `trace_update_entry` | When learning more about existing topic |
| Tag consistently | `tags` field | Use consistent tags for retrieval |

### At Session End: Consolidation

Before ending a session, consolidate and review:

```python
# 1. Run final checkpoint
trace_checkpoint(session_id="S001", trigger="session_end")

# 2. Consolidate related learnings
trace_consolidate_learnings(
    session_id="S001",
    auto_link=True  # Automatically link related entries
)

# 3. End session with reflection
trace_end_session(
    session_id="S001",
    summary="Implemented payment processing with Stripe",
    reflection="Key insight: Stripe webhooks need idempotency keys",
    ai_helpfulness_rating=4
)
```

### Cross-Session Learning

To ensure learnings are useful across sessions:

#### 1. Consistent Tagging Strategy

Use hierarchical tags for better retrieval:

```python
tags = [
    "domain:payments",      # Domain area
    "tech:stripe",          # Technology
    "type:gotcha",          # Entry type
    "severity:high",        # Importance
    "project:checkout-v2"   # Project context
]
```

#### 2. Link Related Entries

When logging, reference related past entries:

```python
trace_add_learning(
    learning="Stripe webhook signatures expire after 5 minutes",
    evidence="Validation failed for delayed webhook processing",
    related_to=["G003", "D007"],  # Link to related gotcha and decision
    tags=["tech:stripe", "domain:payments", "type:timing"]
)
```

#### 3. Periodic Knowledge Review

Periodically review and curate knowledge:

```python
# Get knowledge summary for a topic
trace_query(
    query="stripe webhooks",
    categories=["learnings", "gotchas", "decisions"]
)

# Identify stale or superseded entries
trace_get_metrics(category="knowledge_health")
```

### AI Behavioral Rules: Knowledge Persistence

**CRITICAL**: As AI, follow these rules to ensure knowledge persists:

1. **Always check context first**
   - At session start, call `trace_get_context` and `trace_context_refresh`
   - Review relevant past entries before starting work

2. **Reference past knowledge**
   - When encountering something you've seen before, cite the existing entry
   - Example: "This relates to gotcha G003 about webhook timing"

3. **Update, don't duplicate**
   - If adding to existing knowledge, update the entry or link to it
   - Use `trace_knowledge_check` to find similar entries first

4. **Explain connections**
   - When logging, explain how new knowledge connects to existing entries
   - Use `related_to` field liberally

5. **Surface relevant context proactively**
   - When working on a topic, mention relevant past learnings
   - Example: "Before we implement this, note that we learned X in a previous session (L005)"

---

## Knowledge Health Metrics

Track the health and usefulness of your knowledge base:

```python
trace_get_metrics(category="knowledge")
```

Returns:

```json
{
  "knowledge_metrics": {
    "total_entries": {
      "learnings": 45,
      "gotchas": 23,
      "decisions": 31,
      "ideas": 18
    },
    "by_tag": {
      "domain:payments": 15,
      "tech:python": 28,
      "type:gotcha": 23
    },
    "staleness": {
      "fresh_30d": 25,
      "aging_90d": 35,
      "stale_180d": 28
    },
    "linkage": {
      "entries_with_links": 42,
      "orphan_entries": 75,
      "avg_links_per_entry": 1.8
    },
    "retrieval": {
      "most_accessed": ["G003", "L015", "D007"],
      "never_accessed": ["L042", "D028"]
    }
  }
}
```

---

## Changelog

| Date | Change |
|------|--------|
| 2026-02-02 | TRACE v2.1: Smart Triggers, knowledge_check, automatic checkpoints, knowledge persistence, context refresh, consolidation tools |
| 2026-02-02 | TRACE v2.0: New authorship model, AI suggestion tracking, git integration, multi-content-type support (code/text/data), word/row tracking |
| 2026-01-29 | Initial TRACE protocol v1.0 |
