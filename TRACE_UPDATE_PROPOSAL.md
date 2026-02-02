# TRACE Protocol Update Proposal v2.0

> **Purpose**: Improve attribution tracking for AI-assisted development and scientific research
> **Date**: 2026-02-02
> **Status**: Proposed

---

## Summary of Changes

This update refines the TRACE protocol to better distinguish:
1. **Human-directed** work (human tells AI what to do)
2. **AI-suggested** work (AI proposes, human accepts/rejects/modifies)
3. **Human-manual** work (human does it themselves, detected via `[HUMAN-MANUAL-EDIT]` git tags)

---

## Core Problem Being Solved

The current protocol conflates two distinct concepts:
- **Who wrote the code** (AI vs human physically typing)
- **Who directed the change** (whose idea was it, who decided what to do)

For example:
- Human says "add a logout button" → AI writes code → This is **human-directed, AI-executed**
- AI suggests "we should add caching" → Human accepts → This is **AI-suggested, AI-executed, human-approved**
- Human directly edits code in their IDE → This is **human-directed, human-executed**

---

## New Authorship Model

### Current Model (Insufficient)
```
authorship: {
  ai_authored_lines: 100,
  human_authored_lines: 20,
  ai_improved_lines: 10,
  human_improved_ai_lines: 15
}
```

### Proposed Model (v2.0)
```json
{
  "authorship": {
    "human_directed": {
      "ai_executed_lines": 0,
      "human_executed_lines": 0
    },
    "ai_suggested": {
      "accepted_lines": 0,
      "rejected_lines": 0,
      "modified_lines": 0,
      "modification_description": null
    },
    "human_manual_edit": {
      "lines_added": 0,
      "lines_removed": 0,
      "lines_modified": 0,
      "git_commits": []
    },
    "collaborative": {
      "lines": 0,
      "description": null
    }
  }
}
```

### Definitions

| Category | Description | Example |
|----------|-------------|---------|
| `human_directed.ai_executed_lines` | Human told AI what to do, AI wrote the code | "Add a function to validate email" |
| `human_directed.human_executed_lines` | Human told AI what to do but wrote it themselves | Human describes algorithm, writes it themselves |
| `ai_suggested.accepted_lines` | AI proposed something, human accepted as-is | AI suggests refactor, human accepts |
| `ai_suggested.rejected_lines` | AI proposed something, human rejected entirely | AI suggests change, human says no |
| `ai_suggested.modified_lines` | AI proposed, human accepted with modifications | AI suggests code, human tweaks it |
| `human_manual_edit` | Human edited code directly (detected via git) | Commits with `[HUMAN-MANUAL-EDIT]` tag |
| `collaborative` | Back-and-forth discussion produced the solution | Extended dialog where both contribute |

---

## Git Integration for Manual Edit Detection

### Commit Message Convention
```
[HUMAN-MANUAL-EDIT] Fixed typo in variable name

[HUMAN-MANUAL-EDIT][BUGFIX] Corrected off-by-one error

[HUMAN-MANUAL-EDIT][REFACTOR] Simplified loop logic
```

### New Tool: `trace_scan_git_commits`

Automatically scan git history for `[HUMAN-MANUAL-EDIT]` commits and log them:

```python
Tool(
    name="trace_scan_git_commits",
    description="Scan git commits for [HUMAN-MANUAL-EDIT] tags and auto-log human manual edits.",
    inputSchema={
        "type": "object",
        "properties": {
            "since": {"type": "string", "description": "Git date filter (e.g., '1 week ago')"},
            "session_id": {"type": "string", "description": "Session to associate commits with"}
        }
    }
)
```

### Implementation Logic
```python
def scan_git_for_human_edits(since: str = "1 week ago") -> list:
    """Parse git log for [HUMAN-MANUAL-EDIT] commits."""
    import subprocess

    result = subprocess.run(
        ["git", "log", "--oneline", "--since", since, "--grep", "[HUMAN-MANUAL-EDIT]", "--stat"],
        capture_output=True, text=True
    )

    commits = []
    for line in result.stdout.split('\n'):
        # Parse commit hash, message, and stats
        # Extract files changed, insertions, deletions
        pass

    return commits
```

---

## AI Suggestion Tracking

### New Schema: `ai_suggestions`

Track every AI suggestion with its outcome:

```json
{
  "ai_suggestions": [
    {
      "id": "SUG001",
      "timestamp": "2026-01-29T00:00:00Z",
      "session_id": "S001",

      "suggestion": {
        "type": "code_change",
        "description": "Refactor the validation function to use early returns",
        "scope": {
          "files_affected": ["src/validators.py"],
          "lines_proposed": 25
        }
      },

      "outcome": {
        "status": "accepted|rejected|modified",
        "decision_timestamp": "2026-01-29T00:05:00Z",
        "human_rationale": "Good idea, but modified to use match statement instead",

        "lines_final": {
          "accepted_as_is": 15,
          "modified_by_human": 10,
          "rejected": 0
        }
      },

      "context": {
        "what_prompted_suggestion": "Human asked for code review",
        "human_was_aware_before": false
      }
    }
  ]
}
```

### New Tool: `trace_log_suggestion`

```python
Tool(
    name="trace_log_suggestion",
    description="Log an AI suggestion and track its outcome (accepted/rejected/modified with line counts).",
    inputSchema={
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "What AI suggested"},
            "suggestion_type": {
                "type": "string",
                "enum": ["code_change", "architecture", "approach", "bugfix", "optimization", "refactor"],
                "description": "Type of suggestion"
            },
            "lines_proposed": {"type": "integer", "description": "Lines of code/change proposed"},
            "files_affected": {"type": "array", "items": {"type": "string"}, "description": "Files that would be affected"},
            "session_id": {"type": "string"}
        },
        "required": ["description", "suggestion_type"]
    }
)

Tool(
    name="trace_resolve_suggestion",
    description="Record the outcome of a previously logged AI suggestion.",
    inputSchema={
        "type": "object",
        "properties": {
            "suggestion_id": {"type": "string", "description": "ID of the suggestion"},
            "status": {
                "type": "string",
                "enum": ["accepted", "rejected", "modified"],
                "description": "What happened to the suggestion"
            },
            "lines_accepted_as_is": {"type": "integer", "description": "Lines accepted without changes"},
            "lines_modified": {"type": "integer", "description": "Lines accepted but modified by human"},
            "lines_rejected": {"type": "integer", "description": "Lines not used at all"},
            "human_rationale": {"type": "string", "description": "Why human made this decision"},
            "modification_description": {"type": "string", "description": "If modified, what changed"}
        },
        "required": ["suggestion_id", "status"]
    }
)
```

---

## Updated Metrics

### New `suggestion_metrics`

```json
{
  "suggestion_metrics": {
    "total_suggestions": 0,
    "accepted_count": 0,
    "rejected_count": 0,
    "modified_count": 0,

    "acceptance_rate": null,
    "rejection_rate": null,
    "modification_rate": null,

    "lines_proposed_total": 0,
    "lines_accepted_as_is": 0,
    "lines_modified_by_human": 0,
    "lines_rejected": 0,

    "by_type": {
      "code_change": {"proposed": 0, "accepted": 0, "rejected": 0, "modified": 0},
      "architecture": {"proposed": 0, "accepted": 0, "rejected": 0, "modified": 0},
      "optimization": {"proposed": 0, "accepted": 0, "rejected": 0, "modified": 0}
    }
  }
}
```

### Updated `code_metrics`

```json
{
  "code_metrics": {
    "total_lines": {
      "human_directed_ai_executed": 0,
      "human_directed_human_executed": 0,
      "ai_suggested_accepted": 0,
      "ai_suggested_modified": 0,
      "human_manual_edit": 0,
      "collaborative": 0
    },

    "by_source": {
      "human_direction_percentage": null,
      "ai_suggestion_percentage": null,
      "human_manual_percentage": null
    },

    "git_integration": {
      "manual_edit_commits_detected": 0,
      "manual_edit_lines_added": 0,
      "manual_edit_lines_removed": 0
    }
  }
}
```

---

## Additional Improvements for Transparency, Auditability, and Extensibility

### 1. Prompt Hashing for Reproducibility

Store hashes of prompts to enable verification without storing full text:

```json
{
  "interactions": [
    {
      "prompt": {
        "summary": "Asked to implement validation",
        "hash_sha256": "abc123...",
        "character_count": 150,
        "full_text_stored": false,
        "full_text_path": null
      }
    }
  ]
}
```

**Why**: Allows verification that a specific prompt was used without storing potentially sensitive full prompts.

### 2. Change Diff Tracking

Store diffs for key changes to enable audit:

```json
{
  "code_contributions": [
    {
      "diff": {
        "stored": true,
        "format": "unified",
        "hash_sha256": "def456...",
        "storage_path": ".trace/diffs/CC001.diff"
      }
    }
  ]
}
```

**Why**: Enables exact reconstruction of what changed, critical for reproducibility.

### 3. Decision Chain Linking

Link related decisions, suggestions, and code to show causality:

```json
{
  "ai_suggestions": [
    {
      "id": "SUG001",
      "resulted_in": {
        "decisions": ["D003"],
        "code_contributions": ["CC005", "CC006"],
        "errors_discovered": ["ERR002"]
      }
    }
  ]
}
```

**Why**: Shows the impact of AI suggestions over time, enabling analysis of downstream effects.

### 4. Confidence Tracking for AI Outputs

Track how confident AI was in suggestions:

```json
{
  "ai_suggestions": [
    {
      "ai_confidence": {
        "level": "high|medium|low",
        "reasoning": "Well-established pattern with clear best practices"
      }
    }
  ]
}
```

**Why**: Helps analyze whether AI confidence correlates with acceptance rate.

### 5. Session Context Snapshots

Capture environment state at session start:

```json
{
  "sessions": [
    {
      "environment_snapshot": {
        "git_branch": "main",
        "git_commit": "abc123",
        "uncommitted_changes": 5,
        "python_version": "3.11.5",
        "key_package_versions": {
          "numpy": "1.24.0",
          "pandas": "2.0.0"
        }
      }
    }
  ]
}
```

**Why**: Critical for reproducibility - know exact state when work was done.

### 6. Extensible Plugin System

Support custom tracking via plugins:

```json
{
  "plugins": {
    "enabled": ["jupyter_cell_tracking", "test_coverage_tracking"],
    "custom_fields": {
      "jupyter_cell_tracking": {
        "cells_ai_authored": 0,
        "cells_human_authored": 0
      }
    }
  }
}
```

**Why**: Different research domains have different needs; extensibility enables customization.

### 7. Audit Log

Immutable log of all TRACE operations:

```json
{
  "audit_log": [
    {
      "timestamp": "2026-01-29T00:00:00Z",
      "operation": "trace_log_code",
      "arguments_hash": "xyz789...",
      "user": "researcher_id",
      "trace_version": "2.0"
    }
  ]
}
```

**Why**: Ensures TRACE data itself hasn't been tampered with; critical for scientific integrity.

### 8. Export Formats for Publication

Add structured export for papers:

```json
{
  "exports": {
    "latex_table": "...",
    "csv": "...",
    "bibtex_citation": "@misc{trace2026, ...}"
  }
}
```

**Why**: Makes it easy to include TRACE metrics in publications.

---

## Updated Tool: `trace_log_code` (v2.0)

```python
Tool(
    name="trace_log_code",
    description="Log a code contribution with detailed authorship breakdown distinguishing direction from execution.",
    inputSchema={
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file"},
            "contribution_type": {
                "type": "string",
                "enum": ["creation", "modification", "refactor", "bugfix", "optimization"],
                "description": "Type of contribution"
            },
            "description": {"type": "string", "description": "What this code does"},

            # Direction source
            "direction_source": {
                "type": "string",
                "enum": ["human_directed", "ai_suggested", "collaborative"],
                "description": "Who decided this change should happen"
            },

            # Human-directed breakdown
            "human_directed_ai_executed_lines": {
                "type": "integer",
                "description": "Lines written by AI based on human direction"
            },
            "human_directed_human_executed_lines": {
                "type": "integer",
                "description": "Lines written by human based on human direction (rare in AI sessions)"
            },

            # AI-suggested breakdown
            "ai_suggested_accepted_lines": {
                "type": "integer",
                "description": "Lines from AI suggestion accepted as-is"
            },
            "ai_suggested_modified_lines": {
                "type": "integer",
                "description": "Lines from AI suggestion that human modified"
            },
            "ai_suggested_rejected_lines": {
                "type": "integer",
                "description": "Lines AI proposed but human rejected"
            },

            # Human manual (will also be detected via git)
            "human_manual_edit_lines": {
                "type": "integer",
                "description": "Lines human edited directly (outside AI session)"
            },

            # Git integration
            "git_commit": {"type": "string", "description": "Git commit hash if available"},
            "has_human_manual_edit_tag": {
                "type": "boolean",
                "description": "Whether commit message contains [HUMAN-MANUAL-EDIT]"
            },

            "session_id": {"type": "string", "description": "Current session ID"},
            "related_suggestion_id": {"type": "string", "description": "If this resulted from an AI suggestion"}
        },
        "required": ["file_path", "contribution_type", "description", "direction_source"]
    }
)
```

---

## Migration Path

### Phase 1: Schema Update
- Update `trace.json` schema to v2.0
- Add new fields with defaults
- Keep backward compatibility with v1.0 data

### Phase 2: Tool Updates
- Update `trace_log_code` with new authorship model
- Add `trace_log_suggestion` and `trace_resolve_suggestion`
- Add `trace_scan_git_commits`

### Phase 3: Metrics Update
- Update `compute_metrics` to calculate new metrics
- Update export reports to include new data

### Phase 4: Documentation
- Update CLAUDE.md with new workflow
- Add examples for each scenario

---

## Example Workflow (v2.0)

```python
# 1. Start session
trace_start_session(
    purpose="Add email validation",
    scientific_stage="analysis",
    ai_model="claude-opus-4-5-20251101"
)

# 2. Human directs a task
#    "Add a function to validate email addresses"
#    AI writes the code

trace_log_code(
    file_path="validators.py",
    contribution_type="creation",
    description="Email validation function",
    direction_source="human_directed",
    human_directed_ai_executed_lines=25,
    session_id="S001"
)

# 3. AI suggests an improvement
trace_log_suggestion(
    description="Use regex pattern from email-validator library instead of custom regex",
    suggestion_type="optimization",
    lines_proposed=15,
    files_affected=["validators.py"],
    session_id="S001"
)

# 4. Human accepts with modifications
trace_resolve_suggestion(
    suggestion_id="SUG001",
    status="modified",
    lines_accepted_as_is=10,
    lines_modified=5,
    lines_rejected=0,
    human_rationale="Good idea, but kept custom pattern for domain-specific validation",
    modification_description="Added our domain rules on top of standard email regex"
)

# 5. Log the code from the suggestion
trace_log_code(
    file_path="validators.py",
    contribution_type="modification",
    description="Improved email regex with library pattern",
    direction_source="ai_suggested",
    ai_suggested_accepted_lines=10,
    ai_suggested_modified_lines=5,
    related_suggestion_id="SUG001",
    session_id="S001"
)

# 6. Later, human makes a manual edit outside the AI session
#    They commit with: "[HUMAN-MANUAL-EDIT] Fixed typo in error message"

# 7. At next session start, scan for manual edits
trace_scan_git_commits(since="1 day ago", session_id="S002")
# This auto-logs:
# - human_manual_edit for the commit
# - Updates metrics

# 8. View metrics
trace_compute_metrics()
# Returns:
# {
#   "suggestion_metrics": {
#     "total_suggestions": 1,
#     "accepted_count": 0,
#     "modified_count": 1,
#     "acceptance_rate": 0.0,
#     "modification_rate": 1.0,
#     "lines_proposed_total": 15,
#     "lines_accepted_as_is": 10,
#     "lines_modified_by_human": 5
#   },
#   "code_metrics": {
#     "total_lines": {
#       "human_directed_ai_executed": 25,
#       "ai_suggested_accepted": 10,
#       "ai_suggested_modified": 5,
#       "human_manual_edit": 3
#     }
#   }
# }
```

---

## Summary of Benefits

| Goal | How v2.0 Addresses It |
|------|----------------------|
| **Reproducibility** | Prompt hashing, environment snapshots, diff storage, git integration |
| **Transparency** | Clear distinction between direction and execution, suggestion tracking |
| **Auditability** | Audit log, immutable records, hash verification |
| **Extensibility** | Plugin system, custom fields, structured exports |

---

## Next Steps

1. Review this proposal
2. Decide on which features to implement in Phase 1
3. Update server.py with new tools
4. Update trace.json schema
5. Update CLAUDE.md documentation
