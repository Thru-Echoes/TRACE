# TRACE MVP Refactor Plan

## Goal
Refactor TRACE into a clean MVP core + optional extensions, suitable for:
1. Initial paper: MVP core (framework-agnostic audit schema)
2. Extended paper: MVP + extensions (Claude-specific, knowledge management, reports)

---

## New Directory Structure

```
TRACE/
├── mcp_server/
│   ├── server.py              # Main server (imports core + enabled extensions)
│   ├── schema.json            # Formal JSON Schema for trace.json
│   ├── core/                  # MVP Core (framework-agnostic)
│   │   ├── __init__.py
│   │   ├── types.py           # Core type definitions
│   │   ├── session.py         # Session management
│   │   ├── contribution.py    # Contribution logging
│   │   ├── suggestion.py      # Suggestion tracking
│   │   ├── environment.py     # NEW: Environment capture
│   │   ├── evaluation.py      # NEW: Evaluation logging
│   │   ├── integrity.py       # Hash chain (move from vv/)
│   │   └── export.py          # Machine-readable export
│   ├── ext/                   # Extensions
│   │   ├── __init__.py
│   │   ├── claude/            # Claude-specific features
│   │   │   ├── __init__.py
│   │   │   ├── knowledge_check.py
│   │   │   ├── checkpoints.py
│   │   │   └── triggers.py
│   │   ├── knowledge/         # Knowledge management
│   │   │   ├── __init__.py
│   │   │   ├── decisions.py
│   │   │   ├── learnings.py
│   │   │   ├── gotchas.py
│   │   │   └── ideas.py
│   │   └── reports/           # Reporting & analysis
│   │       ├── __init__.py
│   │       ├── trust_metrics.py
│   │       ├── text_analysis.py
│   │       └── publication.py
│   └── vv/                    # Keep V&V as separate module
│       └── (existing files)
├── trace.json                 # Data file (updated schema)
├── schema/                    # JSON Schema definitions
│   ├── trace-core.schema.json
│   └── trace-ext.schema.json
└── CLAUDE.md                  # Updated documentation
```

---

## Schema Changes

### trace.json v3.0 Structure

```json
{
  "schema_version": "3.0",
  "schema_uri": "urn:trace:schema:v3",

  "metadata": {
    "project_name": "...",
    "created": "...",
    "description": "..."
  },

  "environments": [
    {
      "id": "ENV001",
      "captured_at": "2026-02-03T10:00:00Z",
      "platform": {
        "os": "darwin",
        "arch": "arm64",
        "version": "Darwin 25.2.0"
      },
      "runtime": {
        "language": "python",
        "version": "3.11.4"
      },
      "agent": {
        "framework": "mcp",
        "name": "claude-opus-4-5-20251101",
        "parameters": {}
      },
      "mcp": {
        "spec_version": "2025-06-18",
        "server_version": "3.0.0"
      },
      "dependencies_hash": "sha256:..."
    }
  ],

  "sessions": [
    {
      "id": "S001",
      "environment_id": "ENV001",
      "started": "...",
      "ended": "...",
      "purpose": "...",
      "scientific_stage": "...",
      "summary": "..."
    }
  ],

  "contributions": [
    {
      "id": "C001",
      "session_id": "S001",
      "timestamp": "...",
      "file_path": "...",
      "content_type": "code|text|data",
      "contribution_type": "creation|modification|refactor|bugfix|optimization",
      "description": "...",
      "authorship": {
        "direction": "human_directed|ai_suggested|collaborative",
        "execution": {
          "ai_lines": 50,
          "human_lines": 10,
          "ai_words": 0,
          "human_words": 0
        }
      },
      "related_suggestion_id": null,
      "git_commit": null,
      "integrity": {
        "entry_hash": "sha256:...",
        "previous_hash": "sha256:...",
        "chain_position": 1
      }
    }
  ],

  "suggestions": [
    {
      "id": "SUG001",
      "session_id": "S001",
      "timestamp": "...",
      "description": "...",
      "suggestion_type": "code_change|architecture|approach|bugfix|optimization|refactor|feature",
      "proposed": {
        "lines": 25,
        "files": ["file.py"]
      },
      "confidence": "high|medium|low",
      "status": "pending|accepted|rejected|modified",
      "resolution": {
        "resolved_at": "...",
        "lines_accepted": 20,
        "lines_modified": 5,
        "lines_rejected": 0,
        "rationale": "..."
      },
      "integrity": {}
    }
  ],

  "interventions": [
    {
      "id": "INT001",
      "session_id": "S001",
      "timestamp": "...",
      "type": "correction|override|rejection|refinement",
      "ai_output_summary": "...",
      "human_action": "...",
      "rationale": "...",
      "lines_affected": 15,
      "integrity": {}
    }
  ],

  "errors": [
    {
      "id": "ERR001",
      "session_id": "S001",
      "timestamp": "...",
      "error_type": "syntax|logic|runtime|design|security|performance",
      "description": "...",
      "originated_from": "ai|human",
      "detected_by": "ai|human|automated_test",
      "resolution": "...",
      "integrity": {}
    }
  ],

  "evaluations": [
    {
      "id": "EVAL001",
      "session_id": "S001",
      "timestamp": "...",
      "evaluation_type": "unit_test|integration_test|benchmark|human_eval|code_review",
      "target": {
        "files": ["file.py"],
        "scope": "function|module|system"
      },
      "metrics": {
        "tests_passed": 42,
        "tests_failed": 3,
        "coverage": 0.85,
        "duration_ms": 1234
      },
      "tool": "pytest",
      "output_summary": "...",
      "integrity": {}
    }
  ],

  "integrity_chain": {
    "version": "1.0",
    "genesis_hash": "sha256:0000...",
    "current_position": 0,
    "entries": []
  },

  "audit_log": [],

  "_extensions": {
    "knowledge": {
      "decisions": [],
      "learnings": [],
      "gotchas": [],
      "ideas": []
    },
    "claude": {
      "checkpoints": [],
      "knowledge_checks": []
    }
  }
}
```

---

## Core Tools (8)

| Tool | Description |
|------|-------------|
| `trace_start_session` | Begin work context, auto-capture environment |
| `trace_end_session` | Close context with summary |
| `trace_log_contribution` | Record artifact change with authorship |
| `trace_log_suggestion` | Record AI proposal |
| `trace_resolve_suggestion` | Record human decision on proposal |
| `trace_log_evaluation` | Record test/benchmark results |
| `trace_verify` | Verify entry against actual changes |
| `trace_export` | Export machine-readable audit trail |

Plus these core utilities:
| Tool | Description |
|------|-------------|
| `trace_log_intervention` | Record human modification of AI output |
| `trace_log_error` | Record errors and their resolution |
| `trace_query` | Search trace data |
| `trace_get_context` | Get current trace context |
| `trace_compute_metrics` | Compute authorship/suggestion metrics |

---

## Extension Tools

### ext:knowledge (5 tools)
| Tool | Description |
|------|-------------|
| `trace_add_decision` | Record a decision with rationale |
| `trace_add_learning` | Record a learning/finding |
| `trace_add_gotcha` | Record a pitfall/gotcha |
| `trace_log_idea` | Record an idea |
| `trace_evaluate_idea` | Record idea outcome |

### ext:claude (4 tools)
| Tool | Description |
|------|-------------|
| `trace_knowledge_check` | NLP-based duplicate detection |
| `trace_checkpoint` | Session checkpoint with prompts |
| `trace_context_refresh` | Refresh context for topics |
| `trace_consolidate_learnings` | Consolidate session learnings |

### ext:reports (4 tools)
| Tool | Description |
|------|-------------|
| `trace_trust_report` | Generate trust metrics report |
| `trace_analyze_text` | Analyze LaTeX/Markdown documents |
| `trace_git_reconcile` | Cross-validate with git |
| `trace_export_report` | Generate publication report |

### ext:vv (V&V tools, 3 tools)
| Tool | Description |
|------|-------------|
| `trace_snapshot` | Capture file state |
| `trace_verify_integrity` | Verify hash chain |
| `trace_list_snapshots` | List snapshots |

---

## Migration Path

### Phase 1: Schema & Structure
1. Create `schema/trace-core.schema.json`
2. Create `mcp_server/core/` directory with modules
3. Create `mcp_server/ext/` directory structure
4. Add Environment and Evaluation to schema

### Phase 2: Refactor Server
1. Move core logic to `core/` modules
2. Move extension logic to `ext/` modules
3. Update `server.py` to import from modules
4. Add extension enable/disable config

### Phase 3: Add Missing Features
1. Implement environment capture
2. Implement evaluation logging
3. Update trace_export for machine-readable format

### Phase 4: Update Documentation
1. Rewrite CLAUDE.md for MVP focus
2. Create extension documentation
3. Create JSON Schema documentation

---

## Configuration

New `.trace.config.json`:
```json
{
  "extensions": {
    "knowledge": true,
    "claude": true,
    "reports": true,
    "vv": true
  },
  "environment": {
    "capture_on_session_start": true,
    "include_dependencies": true
  },
  "integrity": {
    "enabled": true,
    "auto_chain": true
  }
}
```

---

## Backwards Compatibility

- v2.x traces will auto-migrate to v3.0
- `code_contributions` → `contributions`
- `ai_suggestions` → `suggestions`
- Knowledge entries move to `_extensions.knowledge`
- All existing functionality preserved

---

## Files to Create/Modify

| File | Action |
|------|--------|
| `schema/trace-core.schema.json` | Create |
| `mcp_server/core/__init__.py` | Create |
| `mcp_server/core/types.py` | Create |
| `mcp_server/core/environment.py` | Create |
| `mcp_server/core/evaluation.py` | Create |
| `mcp_server/ext/__init__.py` | Create |
| `mcp_server/ext/claude/__init__.py` | Create |
| `mcp_server/ext/knowledge/__init__.py` | Create |
| `mcp_server/ext/reports/__init__.py` | Create |
| `mcp_server/server.py` | Major refactor |
| `trace.json` | Schema update |
| `CLAUDE.md` | Rewrite for MVP |
