# TRACE Protocol Specification

> **Transparent Research AI Collaboration Environment**
> Version 1.0 | January 2026

---

## Abstract

TRACE (Transparent Research AI Collaboration Environment) is a protocol for systematically documenting AI-human collaboration in scientific research. As AI assistants become integral to research workflows, the scientific community requires standardized methods for tracking AI contributions, ensuring reproducibility, and maintaining accountability. TRACE provides a schema, tooling, and methodology for capturing fine-grained provenance of AI involvement across the research lifecycle.

---

## 1. Introduction

### 1.1 Motivation

The integration of AI assistants into scientific research presents novel challenges:

1. **Attribution**: How do we credit AI vs. human contributions?
2. **Reproducibility**: How do we document AI-assisted methods?
3. **Accountability**: How do we ensure human oversight?
4. **Transparency**: How do we disclose AI use appropriately?

Existing approaches—ad hoc notes, generic logging, or no documentation—are insufficient for rigorous science.

### 1.2 Design Principles

TRACE is designed around five principles:

| Principle | Description |
|-----------|-------------|
| **Transparency** | All AI involvement is explicitly documented |
| **Granularity** | Fine-grained tracking at the idea/line-of-code level |
| **Neutrality** | Objective measurement without value judgment |
| **Reproducibility** | Sufficient detail to replicate workflows |
| **Practicality** | Low friction for real-world adoption |

### 1.3 Scope

TRACE covers AI-assisted research activities including:
- Code development
- Data analysis
- Hypothesis generation
- Literature review
- Writing and editing
- Experimental design

---

## 2. Protocol Overview

### 2.1 Data Model

TRACE organizes collaboration data into interconnected entities:

```
┌─────────────────────────────────────────────────────────────────┐
│                         TRACE Data Model                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────┐    contains    ┌──────────────┐                   │
│  │ Session  │───────────────>│ Interactions │                   │
│  └──────────┘                └──────────────┘                   │
│       │                            │                             │
│       │ produces                   │ generates                   │
│       ▼                            ▼                             │
│  ┌──────────────────────────────────────────────────────┐       │
│  │                    Artifacts                          │       │
│  ├──────────────┬───────────┬─────────┬────────────────┤       │
│  │ Code         │ Ideas     │ Errors  │ Decisions      │       │
│  │ Contributions│           │         │ & Learnings    │       │
│  └──────────────┴───────────┴─────────┴────────────────┘       │
│       │                            │                             │
│       │ may trigger                │ may trigger                 │
│       ▼                            ▼                             │
│  ┌──────────────┐           ┌──────────────┐                    │
│  │ Interventions│           │ Validations  │                    │
│  └──────────────┘           └──────────────┘                    │
│                                                                  │
│                    ┌──────────────┐                             │
│                    │ Attributions │ (computed)                   │
│                    └──────────────┘                             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Entity Definitions

#### 2.2.1 Session

A bounded period of AI-assisted work.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier (S001, S002, ...) |
| `started` | datetime | Session start time |
| `ended` | datetime | Session end time |
| `duration_minutes` | integer | Computed duration |
| `purpose` | string | What was worked on |
| `scientific_stage` | enum | Stage in scientific method |
| `ai_system_id` | string | Reference to AI system used |
| `model_version` | string | Specific model version |

**Scientific stages**:
- `exploration` - Understanding problem space
- `hypothesis` - Forming testable claims
- `data_collection` - Gathering/preparing data
- `analysis` - Running analyses
- `interpretation` - Making sense of results
- `validation` - Verifying findings
- `writing` - Documentation/papers

#### 2.2.2 Code Contribution

A unit of code creation or modification.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier (CC001, ...) |
| `session_id` | string | Associated session |
| `file_path` | string | File location |
| `contribution_type` | enum | creation/modification/refactor/bugfix |
| `authorship.ai_authored_lines` | integer | Lines written by AI |
| `authorship.human_authored_lines` | integer | Lines written by human |
| `authorship.ai_improved_lines` | integer | AI improvements to human code |
| `authorship.human_improved_ai_lines` | integer | Human improvements to AI code |
| `git_commit` | string | Associated commit hash |

#### 2.2.3 Idea

A concept, approach, or hypothesis.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier (IDEA001, ...) |
| `idea` | string | Description of the idea |
| `idea_type` | enum | approach/hypothesis/optimization/feature/design |
| `origin.source` | enum | ai_suggested/human/collaborative |
| `outcome.adopted` | boolean | Whether idea was used |
| `outcome.rejection_reason` | string | If rejected, why |
| `outcome.modification_description` | string | If modified, how |

#### 2.2.4 Error

A mistake or bug with attribution.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier (ERR001, ...) |
| `error_type` | enum | syntax/logic/runtime/design/security |
| `severity` | enum | critical/high/medium/low |
| `source.originated_from` | enum | ai/human |
| `detection.detected_by` | enum | ai/human/automated_test |
| `detection.detection_method` | enum | code_review/testing/runtime/static_analysis |
| `resolution.resolved` | boolean | Whether fixed |
| `resolution.resolution_description` | string | How it was fixed |

#### 2.2.5 Intervention

Human modification of AI output.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier (H001, ...) |
| `intervention_type` | enum | correction/override/rejection/refinement |
| `ai_output.summary` | string | What AI produced |
| `human_action.description` | string | What human changed |
| `human_action.rationale` | string | Why change was made |
| `expertise_applied` | array | Types of expertise used |
| `impact.significance` | enum | critical/major/minor |

#### 2.2.6 Decision & Learning

Knowledge artifacts with provenance.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier |
| `decision/learning` | string | The content |
| `provenance.proposed_by/discovered_by` | enum | human/ai_suggested/collaborative |
| `confidence` | object | Confidence level and history |
| `validation` | object | How it was validated |

---

## 3. Metrics Specification

TRACE computes standardized metrics for analysis.

### 3.1 Code Metrics

| Metric | Formula | Unit |
|--------|---------|------|
| Total AI Lines | Σ ai_authored_lines | lines |
| Total Human Lines | Σ human_authored_lines | lines |
| AI Authorship Ratio | ai_lines / (ai_lines + human_lines) | ratio |
| AI Improvement Lines | Σ ai_improved_lines | lines |
| Human Improvement Lines | Σ human_improved_ai_lines | lines |
| AI Acceptance Rate | accepted / (accepted + modified + rejected) | ratio |
| AI Modification Rate | modified / total_ai_contributions | ratio |

### 3.2 Error Metrics

| Metric | Formula | Unit |
|--------|---------|------|
| AI Errors | Σ errors where originated_from = ai | count |
| Human Errors | Σ errors where originated_from = human | count |
| AI Errors Caught by Human | Σ errors where originated_from = ai AND detected_by = human | count |
| Human Errors Caught by AI | Σ errors where originated_from = human AND detected_by = ai | count |
| AI Error Rate | ai_errors / total_errors | ratio |
| Human Catch Rate | ai_errors_caught_by_human / ai_errors | ratio |
| AI Catch Rate | human_errors_caught_by_ai / human_errors | ratio |

### 3.3 Idea Metrics

| Metric | Formula | Unit |
|--------|---------|------|
| AI Ideas | Σ ideas where source = ai_suggested | count |
| Human Ideas | Σ ideas where source = human | count |
| AI Ideas Accepted | Σ ai_ideas where adopted = true | count |
| AI Ideas Rejected | Σ ai_ideas where adopted = false | count |
| AI Ideas Modified | Σ ai_ideas where modification_description exists | count |
| AI Idea Acceptance Rate | ai_accepted / ai_total | ratio |
| AI Idea Rejection Rate | ai_rejected / ai_total | ratio |

### 3.4 Intervention Metrics

| Metric | Formula | Unit |
|--------|---------|------|
| Total Interventions | Σ interventions | count |
| Corrections | Σ interventions where type = correction | count |
| Overrides | Σ interventions where type = override | count |
| Rejections | Σ interventions where type = rejection | count |
| Intervention Rate | interventions / interactions | ratio |

### 3.5 Session Metrics

| Metric | Formula | Unit |
|--------|---------|------|
| Total Sessions | Σ sessions | count |
| Total Time | Σ duration_minutes | minutes |
| Average Session Duration | total_time / total_sessions | minutes |
| Average Interactions per Session | interactions / sessions | count |

---

## 4. Implementation

### 4.1 Reference Implementation

TRACE provides a reference implementation as an MCP (Model Context Protocol) server, enabling integration with AI assistants like Claude Code.

**Architecture**:
```
┌─────────────┐     MCP      ┌──────────────┐
│ AI Assistant│◄────────────►│ TRACE Server │
│ (Claude)    │              └──────────────┘
└─────────────┘                     │
                                    ▼
                             ┌──────────────┐
                             │  trace.json  │
                             └──────────────┘
```

### 4.2 Tool Interface

The MCP server exposes these tool categories:

| Category | Tools |
|----------|-------|
| Session | `trace_start_session`, `trace_end_session` |
| Code | `trace_log_code` |
| Ideas | `trace_log_idea`, `trace_evaluate_idea` |
| Errors | `trace_log_error` |
| Interventions | `trace_log_intervention` |
| Knowledge | `trace_add_decision`, `trace_add_learning`, `trace_add_gotcha` |
| Metrics | `trace_get_metrics`, `trace_compute_metrics` |
| Export | `trace_export_report` |

### 4.3 Data Storage

TRACE data is stored in a single JSON file for:
- Simplicity (no database required)
- Portability (easy to version control)
- Transparency (human-readable)

For large projects, alternative backends (SQLite, PostgreSQL) may be implemented.

---

## 5. Validation

### 5.1 Schema Validation

All TRACE data should conform to the JSON schema defined in `trace_schema.json`.

### 5.2 Completeness Checks

A valid TRACE log should have:
- [ ] At least one session
- [ ] Code contributions linked to sessions
- [ ] Ideas with origin attribution
- [ ] Errors with source and detection attribution
- [ ] Computed metrics updated

### 5.3 Consistency Checks

- All `session_id` references must exist
- All `interaction_id` references must exist
- Timestamps must be chronologically valid
- Metrics should match raw data

---

## 6. Privacy & Ethics

### 6.1 Data Minimization

TRACE stores:
- Summaries, not full prompts (by default)
- Hashes for reproducibility verification
- No personal data beyond researcher identifiers

### 6.2 Consent

Researchers using TRACE consent to:
- Logging their AI interactions
- Potential publication of aggregate metrics
- Sharing detailed logs as supplementary materials (optional)

### 6.3 AI Rights

TRACE treats AI as a tool, not an author. Attribution is for:
- Transparency and reproducibility
- Not intellectual property claims

---

## 7. Extensibility

### 7.1 Custom Fields

TRACE schemas allow custom fields via `_custom` prefix:
```json
{
  "id": "CC001",
  "_custom_review_status": "approved",
  "_custom_reviewer": "colleague_name"
}
```

### 7.2 Domain Extensions

Domain-specific extensions (e.g., TRACE-Bio, TRACE-Physics) may add:
- Additional entity types
- Specialized metrics
- Domain-specific validation

---

## 8. Adoption Guidelines

### 8.1 Minimal Adoption

For basic compliance:
1. Start/end sessions
2. Log code contributions with authorship
3. Log significant errors with attribution
4. Export summary for publication

### 8.2 Full Adoption

For comprehensive documentation:
1. All minimal requirements
2. Log all ideas with origin
3. Log all interventions
4. Track confidence levels
5. Regular metric computation
6. Detailed session reflections

### 8.3 Publication Requirements

When publishing TRACE-documented research:
1. Include key metrics in methods section
2. Provide `trace.json` as supplementary material
3. Reference TRACE protocol version
4. Include AI disclosure statement

---

## 9. Future Work

- Integration with additional AI platforms
- Automated logging via code analysis
- Cross-project aggregation tools
- Community metric benchmarks
- Formal verification of audit trails

---

## 10. References

1. Model Context Protocol Specification
2. FAIR Principles for Research Software
3. CRediT (Contributor Roles Taxonomy)
4. Data Provenance Standards (W3C PROV)

---

## Appendix A: Full JSON Schema

See `trace_schema.json` for the complete JSON Schema definition.

---

## Appendix B: Example TRACE Log

See `examples/example_trace.json` for a complete example.

---

## Appendix C: Glossary

| Term | Definition |
|------|------------|
| **Attribution** | Assignment of credit for contributions |
| **Intervention** | Human modification of AI output |
| **Provenance** | Origin and history of an artifact |
| **Session** | Bounded period of AI-assisted work |
| **TRACE** | Transparent Research AI Collaboration Environment |

---

## Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-01-29 | [Your Name] | Initial specification |

---

## License

This specification is released under CC BY 4.0.
