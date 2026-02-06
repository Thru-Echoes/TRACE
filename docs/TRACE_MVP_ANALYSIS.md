# TRACE Protocol: Gap Analysis & MVP Proposal

## The Problem TRACE Solves

**MCP Spec (v2025-06-18) provides:**
- Standardized JSON-RPC transport
- Optional logging hooks
- Security guidance
- Tool/resource/prompt primitives

**MCP does NOT define:**
- WHAT to log (schema for audit records)
- How to structure provenance data
- Authorship attribution models
- Verification mechanisms

**Literature Gap:** No paper proposes a standardized audit schema that is:
1. Applied across different scientific domains
2. Interoperable between different agent frameworks
3. Machine-readable for automated compliance checking
4. Aligned with MCP's existing logging hooks

---

## Assessment of Current TRACE Against Requirements

### (A) End-to-End Logging, Environment Capture, Evaluation Harnesses

| Requirement | Current Status | Gap |
|-------------|----------------|-----|
| Session logging | ✅ Strong | start/end, purpose, scientific_stage |
| Contribution logging | ✅ Strong | file, lines, authorship model |
| Audit log | ✅ Present | all tool operations logged |
| **Environment capture** | ⚠️ Weak | Only `ai_model`, missing: platform, versions, dependencies, parameters |
| **Evaluation harnesses** | ❌ Missing | No test/benchmark recording, no metric capture |

**Key Gap:** No structured environment snapshot for reproducibility.

### (B) Replication, Uncertainty Reporting, Verification Layers

| Requirement | Current Status | Gap |
|-------------|----------------|-----|
| **Replication support** | ⚠️ Partial | Git tracking helps, but no prompt/response capture, no random seeds |
| **Uncertainty reporting** | ⚠️ Minimal | Only `ai_confidence: high/medium/low` on suggestions |
| Verification layers | ✅ Strong | V&V system with snapshots, diff verification, hash chain |

**Key Gap:** Coarse uncertainty model, no structured replication metadata.

### (C) Verification/Validation Loops and Grounded Tool Use

| Requirement | Current Status | Gap |
|-------------|----------------|-----|
| Verification engine | ✅ Strong | Claims vs actual diffs, tolerance-based |
| Git reconciliation | ✅ Strong | Cross-validates TRACE with git |
| Integrity chain | ✅ Strong | SHA-256 tamper detection |
| **Grounded tool use** | ⚠️ Partial | Audit log captures calls, but no output validation |

**Key Gap:** No verification that tool outputs match claimed effects.

### (D) Infrastructure Patterns for Provenance Across Contributions

| Requirement | Current Status | Gap |
|-------------|----------------|-----|
| Authorship model | ✅ Strong | Direction vs execution distinction |
| Contribution linking | ✅ Good | Suggestion → resolution → code chain |
| **Cross-agent provenance** | ⚠️ Missing | Single MCP server only |
| **Domain-agnostic schema** | ⚠️ Weak | Very code-centric, text/data feel tacked on |
| **Framework interop** | ❌ Missing | Claude-specific behavioral instructions |

**Key Gap:** No multi-agent coordination, framework-agnostic core schema not separated.

---

## What's Over-Engineered for MVP

The current v2.1 implementation has grown features that are useful but **not core to the audit schema goal**:

### 1. Knowledge Management System (v2.1)
- `trace_knowledge_check` with NLP similarity detection
- Automatic checkpoints with behavioral prompts
- Context refresh and consolidation
- Knowledge health metrics

**Problem:** This is a Claude-specific productivity feature, not an audit standard.

### 2. Complex Trigger System
- Behavioral patterns for gotchas, decisions, learnings
- AI behavioral rules ("proactively suggest checkpoints")

**Problem:** Not portable to other agent frameworks.

### 3. Trust Metrics Computation
- Weighted trust scores (30%/25%/25%/20%)
- Publication-ready reports

**Problem:** Weights are arbitrary; downstream of core schema.

### 4. Text Analysis (LaTeX Parsing)
- Section-level tracking for manuscripts
- Word counts per section

**Problem:** Domain-specific; not part of core audit schema.

---

## What's Missing for the Goals

### 1. Environment Capture (Critical for Replication)

```json
{
  "environment": {
    "captured_at": "2026-02-02T10:00:00Z",
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
      "framework": "claude-code",
      "model": "claude-opus-4-5-20251101",
      "parameters": {
        "temperature": 0.7,
        "max_tokens": 4096
      }
    },
    "mcp": {
      "spec_version": "2025-06-18",
      "server_version": "2.1.0"
    },
    "dependencies_hash": "sha256:abc123..."
  }
}
```

### 2. Schema Definition & Interoperability

- Formal JSON Schema for all entry types
- URIs for type identifiers (e.g., `urn:trace:contribution:v1`)
- Clear separation: **Core Schema** vs **Framework-Specific Profile**

### 3. Evaluation Logging

```json
{
  "evaluation": {
    "id": "EVAL001",
    "type": "unit_test|benchmark|human_eval",
    "target": "validators.py",
    "metrics": {
      "tests_passed": 42,
      "tests_failed": 3,
      "coverage": 0.85
    },
    "environment_id": "ENV001",
    "session_id": "S001"
  }
}
```

### 4. MCP Alignment

Current TRACE is a standalone MCP server. Should leverage MCP's:
- `logging/setLevel` for trace verbosity
- `notifications/message` for event streaming
- Standard tool result formats

---

## Proposed MVP Simplification

### Core Schema (Framework-Agnostic)

Keep these as the **portable, standardized audit records**:

| Entity | Purpose | Status |
|--------|---------|--------|
| **Session** | Bounded work context | ✅ Keep, add environment |
| **Contribution** | Artifact changes with authorship | ✅ Keep as-is |
| **Suggestion** | AI proposal + outcome | ✅ Keep as-is |
| **Environment** | Reproducibility context | 🆕 Add |
| **Evaluation** | Test/benchmark results | 🆕 Add |
| **Integrity** | Hash chain for tamper detection | ✅ Keep |

### Move to Optional Extensions

| Feature | Reason |
|---------|--------|
| Knowledge check (NLP) | Claude-specific |
| Automatic checkpoints | Claude-specific |
| Behavioral triggers | Claude-specific |
| Trust score computation | Downstream analysis |
| LaTeX/text parsing | Domain-specific |
| Context refresh | Claude-specific |

### Simplified Tool Set (MVP)

**Core Tools (8):**
```
trace_start_session    - Begin work context with environment
trace_end_session      - Close context with summary
trace_log_contribution - Record artifact change with authorship
trace_log_suggestion   - Record AI proposal
trace_resolve_suggestion - Record human decision on proposal
trace_log_evaluation   - Record test/benchmark results
trace_verify           - Verify entry against actual changes
trace_export           - Export machine-readable audit trail
```

**Optional Tools (moved to extensions):**
```
trace_knowledge_check     → ext:claude
trace_checkpoint          → ext:claude
trace_context_refresh     → ext:claude
trace_consolidate_learnings → ext:claude
trace_add_decision        → ext:knowledge (optional)
trace_add_learning        → ext:knowledge (optional)
trace_add_gotcha          → ext:knowledge (optional)
trace_log_idea            → ext:knowledge (optional)
trace_log_intervention    → core (keep - important for provenance)
trace_log_error           → core (keep - important for debugging)
trace_trust_report        → ext:reports
trace_analyze_text        → ext:text
```

---

## MVP Schema Structure

```
trace.json
├── schema_version: "3.0"
├── metadata
│   └── project, created, etc.
├── environments[]           # NEW: Reproducibility context
│   └── id, platform, runtime, agent, mcp, dependencies_hash
├── sessions[]
│   └── id, environment_id, started, ended, purpose, summary
├── contributions[]          # Renamed from code_contributions
│   └── id, session_id, file_path, content_type, authorship{}, integrity{}
├── suggestions[]            # Renamed from ai_suggestions
│   └── id, session_id, description, status, outcome{}
├── interventions[]          # Keep - important for provenance
│   └── id, session_id, type, ai_output, human_action, rationale
├── errors[]                 # Keep - important for debugging
│   └── id, session_id, type, description, resolution
├── evaluations[]            # NEW: Test/benchmark results
│   └── id, session_id, type, target, metrics{}
├── integrity_chain          # Keep
│   └── entries[], genesis_hash, current_position
└── audit_log[]              # Keep
    └── timestamp, operation, arguments_hash
```

---

## Metrics (What You Want to Track)

These metrics are **computed from core schema**, not stored:

### Authorship Metrics
```
- % lines human-directed, AI-executed
- % lines human-directed, human-executed
- % lines AI-suggested, accepted as-is
- % lines AI-suggested, modified by human
- % lines human manual edit
- % lines collaborative
```

### Suggestion Metrics
```
- AI suggestion acceptance rate
- AI suggestion modification rate
- AI suggestion rejection rate
- Lines proposed vs lines accepted
```

### Coverage Metrics
```
- % of git commits tracked in TRACE
- % of file changes with contributions logged
- % of sessions with evaluations
```

### Integrity Metrics
```
- Hash chain validity
- Verification pass rate
- Temporal consistency
```

---

## Implementation Priority

### Phase 1: Core Schema (MVP)
1. Add Environment capture
2. Add Evaluation logging
3. Simplify tools to core set
4. Create formal JSON Schema
5. Write MCP alignment documentation

### Phase 2: Extensions
1. Package Claude-specific features as `ext:claude`
2. Package knowledge features as `ext:knowledge`
3. Package reporting as `ext:reports`

### Phase 3: Interoperability
1. Define extension interface
2. Create adapters for other frameworks (LangChain, AutoGen, etc.)
3. Compliance checking tool

---

## Summary

| Current State | MVP Target |
|---------------|------------|
| 35+ tools | 8 core + extensions |
| Claude-specific | Framework-agnostic core |
| No environment capture | Full reproducibility context |
| No evaluation logging | Test/benchmark support |
| Monolithic | Core + optional extensions |
| Implicit MCP alignment | Explicit MCP hooks integration |

The core value of TRACE is the **authorship model** (direction vs execution) and the **provenance chain**. Everything else should be optional.
