# TRACE Protocol v2.0 - Claude Code Instructions

> **TRACE**: Transparent Research AI Collaboration Environment
> **Version**: 2.0
> **Last Updated**: 2026-02-02

---

## What's New in v2.0

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

## Changelog

| Date | Change |
|------|--------|
| 2026-02-02 | TRACE v2.0: New authorship model, AI suggestion tracking, git integration, multi-content-type support (code/text/data), word/row tracking |
| 2026-01-29 | Initial TRACE protocol v1.0 |
