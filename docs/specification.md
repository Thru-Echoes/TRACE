# Decision Provenance for AI-Assisted Workflows

## Specification v0.3.0

**Status**: Draft
**Last Updated**: 2026-03-19
**JSON Schema**: [`trace-v0.3.json`](../schemas/trace-v0.3.json)
**W3C PROV Namespace**: `https://trace-protocol.org/ns/v0.3#`

---

## 1. Introduction

### 1.1 Purpose

This specification defines a data model for recording **decision provenance** in AI-assisted workflows. It answers a question that existing provenance systems (MLflow, W&C, DVC) do not: *who proposed a methodological choice, who accepted or rejected it, and why?*

AI-assisted research introduces a new provenance challenge. When an AI agent selects a statistical method, chooses hyperparameters, or decides how to handle messy data, the resulting publication must be able to attribute that choice to the correct actor (human or AI) and record the rationale. Without this, the scientific record cannot distinguish between a researcher's deliberate methodology and an AI's default suggestion that was never critically examined.

This specification is **technology-agnostic**. It defines what to record, not how to collect it. Conforming implementations may be MCP servers, IDE plugins, Jupyter extensions, CLI wrappers, or manual logging tools. The interchange format is JSON.

### 1.2 Scope

This specification covers:

- A **session document** format for bounded units of collaborative work
- Five **event types** that capture the provenance-relevant actions in an AI-assisted workflow
- A **decision lifecycle** model with proposal, resolution, and revision chains
- An **actor taxonomy** that distinguishes human, AI, and system participants
- A **contribution model** that separates intellectual direction from execution
- A **W3C PROV mapping** for interoperability with existing provenance systems

This specification does not cover:

- How events are collected (instrumentation is implementation-specific)
- Storage backends or query interfaces
- Cross-session knowledge persistence or learning systems
- Authentication, authorization, or access control

### 1.3 Conformance

The key words "MUST", "MUST NOT", "SHOULD", "SHOULD NOT", and "MAY" in this specification are to be interpreted as described in [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

A **conforming document** is a JSON file that validates against the JSON Schema referenced in Section 7.

A **conforming producer** is a system that generates conforming documents. It MUST enforce the validation rules in Section 4.

A **conforming consumer** is a system that reads conforming documents. It SHOULD accept documents with unrecognized fields (forward compatibility).

---

## 2. Terminology

| Term | Definition |
|------|-----------|
| **Session** | A bounded unit of collaborative work between human and AI participants, producing a single provenance document. |
| **Event** | A single auditable action within a session: a tool invocation, a decision, an annotation, a state change, or a contribution. |
| **Actor** | An entity that performs actions. Actors are typed as `human`, `ai`, or `system`. |
| **Decision** | A methodological choice with a defined lifecycle: proposed by one actor, resolved (accepted, revised, or rejected) by another. |
| **Disposition** | The resolution status of a decision: `proposed`, `accepted`, `revised`, or `rejected`. |
| **Contribution** | A work product with dual attribution: who had the idea (*direction*) and who did the work (*execution*). |
| **Annotation** | A free-form observation, learning, correction, or note attached to a session. |
| **Decision Chain** | A sequence of linked decisions where each revises a predecessor, forming a directed acyclic graph of methodological evolution. |
| **Direction** | The intellectual origin of a contribution — who conceived the approach. |
| **Execution** | The operational origin of a contribution — who performed the work. |

---

## 3. Data Model

### 3.1 Session Document

A session document is the top-level unit of this specification. Each document describes one bounded unit of collaborative work.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `context` | string | SHOULD | URI identifying the specification version. Default: `"https://trace-protocol.org/v0.3"`. |
| `trace_version` | string | SHOULD | Semantic version of the specification. Default: `"0.3.0"`. |
| `id` | string | MUST | Unique identifier for this session. |
| `created` | datetime | MUST | UTC ISO 8601 timestamp of session start. |
| `ended` | datetime | MAY | UTC ISO 8601 timestamp of session end. Null while active. |
| `status` | enum | MUST | One of: `"active"`, `"completed"`, `"abandoned"`. Default: `"active"`. |
| `metadata` | SessionMetadata | MUST | Descriptive metadata (see 3.2). |
| `summary` | string | MAY | Human-written summary of what was accomplished. |
| `events` | Event[] | MUST | Ordered array of events (see 3.4). MAY be empty. |

**Session ID format**: Implementations SHOULD use human-readable IDs (e.g., `trace_20260205_a1b2c3`) rather than UUIDs, to facilitate browsing and discussion.

**Timestamps**: All timestamps MUST be UTC ISO 8601 format (e.g., `2026-02-05T14:30:00Z`).

### 3.2 Session Metadata

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `project` | string | MUST | Project name or identifier. |
| `experiment_id` | string | MAY | Experiment or run identifier within the project. |
| `description` | string | SHOULD | Human-readable description of the session's purpose. |
| `participants` | Actor[] | SHOULD | Actors involved in this session (see 3.3). |
| `environment` | Environment | MAY | Execution context for reproducibility (see 3.2.1). |
| `tags` | string[] | MAY | Free-form tags for categorization and search. |
| `doi` | string | MAY | Digital Object Identifier if the session relates to a published work. |
| `custom` | object | MAY | Extension point for domain-specific metadata. |

#### 3.2.1 Environment

Records the computational context in which the session took place. All fields are optional.

| Field | Type | Description |
|-------|------|-------------|
| `tools` | string[] | Names of external tools, services, or servers available during the session. |
| `client` | string | The application or interface used by participants. |
| `os` | string | Operating system. |
| `runtime_version` | string | Programming language or runtime version. |
| `spec_version` | string | Version of the implementation producing this document. |
| `custom` | object | Extension point for domain-specific environment data. |

> **Note on field naming**: The JSON Schema uses `mcp_servers` (for `tools`), `python_version` (for `runtime_version`), and `trace_version` (for `spec_version`) for historical reasons. These names are retained for backward compatibility. Conforming producers SHOULD use the schema field names. This specification uses generic names to remain technology-neutral.

### 3.3 Actor

An actor is any entity that performs actions within a session.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | enum | MUST | One of: `"human"`, `"ai"`, `"system"`. |
| `id` | string | MUST | Unique identifier within the session (e.g., `"researcher-jane"`, `"claude-sonnet-4"`). |
| `role` | string | MAY | Role in the workflow (e.g., `"lead"`, `"assistant"`, `"reviewer"`). |

**Actor types**:
- `human` — A person making decisions, providing feedback, or doing work.
- `ai` — An AI model or agent performing actions, proposing decisions, or generating outputs.
- `system` — An automated system acting without direct human or AI agency (e.g., a CI pipeline, a scheduled job).

### 3.4 Event

An event is the core unit of provenance. Each event records a single auditable action.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | MUST | Unique identifier within the session (e.g., `"evt_001"`). |
| `timestamp` | datetime | MUST | UTC ISO 8601 timestamp of when the event occurred. |
| `session_id` | string | MUST | Back-reference to the containing session. |
| `type` | enum | MUST | One of: `"tool_call"`, `"decision"`, `"annotation"`, `"state_change"`, `"contribution"`. |
| `actor` | Actor | MUST | The actor who performed this action. |
| `context` | EventContext | MAY | Additional conversational context (see 3.4.1). |

Each event MUST populate exactly one type-specific data field corresponding to its `type`, and MUST NOT populate the others. The five type-specific data fields are defined in Sections 3.5 through 3.9.

**Event ID format**: Implementations SHOULD use sequential IDs within a session (e.g., `evt_001`, `evt_002`) for readability.

#### 3.4.1 Event Context

Optional metadata about the conversational context in which an event occurred.

| Field | Type | Description |
|-------|------|-------------|
| `conversation_turn` | integer | Turn number in the human-AI conversation. |
| `reasoning_summary` | string | Brief summary of the AI's reasoning process. |
| `conversation_snippet` | string | Relevant excerpt from the conversation (~200 characters). |
| `related_event_ids` | string[] | IDs of other events in this session that are related to this one. |

The `conversation_snippet` field is particularly important for corrections and contributions: it captures the human's words that triggered the action, providing evidence for attribution.

### 3.5 Tool Invocation (`tool_call`)

Records an automated tool or service invocation — any computational action performed by an external system at the direction of a participant.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `server` | string | MUST | Name or identifier of the service invoked. |
| `method` | string | SHOULD | The method or endpoint called. Default: `"tools/call"`. |
| `name` | string | MUST | Name of the specific tool or function. |
| `input` | object | MUST | Input parameters passed to the tool. |
| `output` | any | MAY | Output returned by the tool. |
| `output_truncated` | boolean | MAY | Whether the output was truncated for storage. |
| `output_hash` | string | MAY | Hash of the full output for verification when truncated. |
| `duration_ms` | integer | MAY | Execution time in milliseconds. |
| `status` | enum | MUST | One of: `"success"`, `"error"`, `"timeout"`. Default: `"success"`. |
| `error_message` | string | MAY | Error details when status is not `"success"`. |
| `retries_event_id` | string | MAY | ID of a previous tool call event that this one retries. |

**Retry chains**: When a tool call fails and is retried, the retry event SHOULD set `retries_event_id` to the failed event's ID. This creates a chain of retry attempts, enabling analysis of failure patterns and recovery strategies.

**What to log**: Log tool invocations that perform substantive computation (database queries, API calls, model inference, file transformations). Do NOT log read-only exploration actions (file reads, directory listings, search queries used only for navigation).

### 3.6 Decision (`decision`)

Records a methodological choice with full attribution and a defined lifecycle. This is the core differentiator of this specification.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `description` | string | MUST | What is being decided. SHOULD be specific and technical. |
| `rationale` | string | SHOULD | Why this choice was proposed. SHOULD include quantitative justification where applicable (e.g., "F1=0.78 at threshold 0.85"). |
| `proposed_by` | Actor | MUST | The actor who proposed this decision. |
| `disposition` | enum | MUST | One of: `"proposed"`, `"accepted"`, `"revised"`, `"rejected"`. Default: `"proposed"`. |
| `resolved_by` | Actor | Conditional | The actor who resolved this decision. MUST be present when disposition is not `"proposed"`. |
| `revision_note` | string | Conditional | Explanation of why the decision was revised or rejected. SHOULD be present when disposition is `"revised"` or `"rejected"`. |
| `revises_event_id` | string | MAY | ID of a previous decision event that this one revises, forming a decision chain. |
| `suggestion_type` | enum | MAY | One of: `"proactive"` (AI volunteered), `"requested"` (human asked), `"collaborative"` (emerged from discussion). |
| `tags` | string[] | MAY | Domain-specific tags for categorization. |
| `warnings` | string[] | MAY | Guard-rail warnings surfaced during proposal (e.g., relevant past corrections). |

**Decision lifecycle**:

```
                    ┌─ accepted ──→ (resolved)
                    │
proposed ──────────┼─ revised ───→ (resolved, may create new proposal)
                    │
                    └─ rejected ──→ (resolved)
```

1. A decision is created with disposition `"proposed"` and a `proposed_by` actor.
2. The decision remains in `"proposed"` state until explicitly resolved.
3. Resolution sets the `disposition` to `"accepted"`, `"revised"`, or `"rejected"`, and records the `resolved_by` actor.
4. A revised decision SHOULD spawn a new decision event with `revises_event_id` pointing to the original.

**Attribution rule**: The actor who proposes a decision MUST NOT be the same instance that resolves it, when the workflow involves multiple actors. This ensures the provenance record reflects genuine deliberation rather than self-approval. In single-actor workflows (e.g., a human working alone with an AI assistant), the human typically resolves decisions proposed by the AI, and vice versa.

**What constitutes a decision**: Methodological choices that affect outcomes — which algorithm to use, what threshold to set, how to handle missing data, which data to include or exclude, how to interpret ambiguous results. NOT: trivial implementation choices (variable naming, file organization) or navigation decisions (which file to read next).

### 3.7 Annotation (`annotation`)

Records a free-form observation, learning, or note.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `category` | enum | MUST | One of: `"learning"`, `"gotcha"`, `"observation"`, `"correction"`, `"todo"`, `"question"`, `"other"`. |
| `content` | string | MUST | The annotation text. |
| `corrects_event_ids` | string[] | MAY | IDs of events that this annotation corrects. |
| `related_event_ids` | string[] | MAY | IDs of related events. |
| `tags` | string[] | MAY | Domain-specific tags. |

**Annotation categories**:

| Category | When to Use |
|----------|-------------|
| `learning` | Reusable knowledge for future sessions. |
| `gotcha` | Unexpected behavior, data quality issues, or surprising findings. |
| `observation` | Interesting but not immediately actionable. |
| `correction` | A participant catches and corrects a mistake. SHOULD set `corrects_event_ids`. |
| `todo` | Needs follow-up in a future session. |
| `question` | An unresolved question. |
| `other` | Anything that doesn't fit the above categories. |

**Corrections vs. rejections**: A `correction` annotation records that a mistake was made and fixed. A `rejected` decision records that a proposed approach was overridden. When a rejected decision was caused by a mistake (e.g., the AI used the wrong configuration), both a rejection AND a correction SHOULD be logged: the rejection on the decision, the correction annotation linking to the specific erroneous events via `corrects_event_ids`.

### 3.8 State Change (`state_change`)

Records a change in the computational environment or configuration.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `description` | string | MUST | What changed. |
| `field` | string | MAY | Dot-notation path to the changed configuration (e.g., `"environment.embedding_model"`). |
| `old_value` | any | MAY | Previous value. |
| `new_value` | any | MAY | New value. |
| `reason` | string | MAY | Why the change was made. |

**What to log**: Model switches, parameter changes, dependency updates, environment configuration changes. These provide context for why results might differ between sessions or within a session.

### 3.9 Contribution (`contribution`)

Records a work product with dual attribution: who had the idea (direction) and who did the work (execution).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `description` | string | MUST | What was contributed. |
| `artifact` | string | MAY | Path, URL, or identifier for the output (e.g., `"src/analysis.py"`, `"figures/fig3.png"`). |
| `direction` | enum | MUST | Who conceived the approach: `"human"`, `"ai"`, or `"collaborative"`. |
| `execution` | enum | MUST | Who performed the work: `"human"`, `"ai"`, or `"collaborative"`. |
| `related_decision_ids` | string[] | MAY | IDs of decision events that motivated this contribution. |
| `tags` | string[] | MAY | Domain-specific tags. |

**Direction vs. execution**: This distinction captures a nuance that traditional authorship models miss:

| Direction | Execution | Example |
|-----------|-----------|---------|
| human | ai | Researcher asks AI to implement a specific algorithm |
| ai | ai | AI proactively suggests and implements an optimization |
| human | human | Researcher manually edits a figure |
| ai | human | AI suggests an approach; researcher implements it themselves |
| collaborative | ai | Both discuss an approach; AI writes the code |
| collaborative | collaborative | Pair-programming style iteration |

**One contribution per artifact**: Each distinct work product SHOULD be logged as a separate contribution event, even if multiple artifacts were produced in the same conversation turn. This enables fine-grained attribution.

---

## 4. Validation Rules

Beyond the structural requirements defined by the JSON Schema, conforming producers MUST enforce the following semantic rules:

### 4.1 Event Type Consistency

Each event MUST populate exactly one type-specific data field, and it MUST match the event's `type`:

| `type` value | Required field | All other type fields |
|--------------|---------------|----------------------|
| `"tool_call"` | `tool_call` | MUST be null |
| `"decision"` | `decision` | MUST be null |
| `"annotation"` | `annotation` | MUST be null |
| `"state_change"` | `state_change` | MUST be null |
| `"contribution"` | `contribution` | MUST be null |

### 4.2 Decision Resolution

- When `disposition` is `"proposed"`, `resolved_by` MUST be null.
- When `disposition` is `"accepted"`, `"revised"`, or `"rejected"`, `resolved_by` MUST be a valid Actor.
- When `disposition` is `"revised"` or `"rejected"`, `revision_note` SHOULD be present.

### 4.3 Timestamps

- All timestamps MUST be UTC.
- `Session.created` MUST be earlier than or equal to `Session.ended` (if present).
- Event timestamps SHOULD be monotonically non-decreasing within a session, but implementations MUST accept out-of-order timestamps (clocks may drift).

### 4.4 References

- `retries_event_id`, `revises_event_id`, `corrects_event_ids`, `related_event_ids`, and `related_decision_ids` SHOULD reference valid event IDs within the same session.
- Consumers MUST tolerate dangling references (the referenced event may have been removed or may exist in a different session).

---

## 5. Decision Provenance

This section describes the provenance patterns that distinguish this specification from flat event logs.

### 5.1 Decision Chains

Decisions can form chains through `revises_event_id` links:

```
evt_001: "Use threshold 0.85" (proposed by AI, accepted by human)
    │
    ▼
evt_005: "Lower threshold to 0.80" (proposed by human, revises evt_001)
    │
    ▼
evt_009: "Use adaptive threshold" (proposed by AI, revises evt_005, rejected by human)
```

A conforming consumer can walk `revises_event_id` links in both directions (forward and backward) to reconstruct the full evolution of a methodological choice.

### 5.2 Correction Provenance

When a human catches and corrects an AI mistake, the provenance record SHOULD include:

1. The original erroneous events (tool calls, decisions, etc.)
2. A `correction` annotation with `corrects_event_ids` pointing to the erroneous events
3. A `conversation_snippet` capturing the human's correction in their own words
4. If a decision was involved, a `rejected` or `revised` disposition on that decision

This creates a traceable chain from mistake to correction, enabling analysis of AI error patterns and human intervention rates.

### 5.3 Attribution Matrix

For any session, the following attribution summary can be computed from the event log:

| Metric | Source |
|--------|--------|
| Decisions proposed by AI | Count of decisions where `proposed_by.type == "ai"` |
| Decisions proposed by human | Count of decisions where `proposed_by.type == "human"` |
| AI acceptance rate | Accepted / (Accepted + Revised + Rejected) for AI-proposed decisions |
| Human intervention rate | (Corrections + Rejections + Revisions) / Total events |
| Direction × Execution matrix | Cross-tabulation of contributions by direction and execution |
| Correction density | Corrections / Total events |

These metrics provide a quantitative fingerprint of how human-AI collaboration actually unfolded, independent of how it was intended.

---

## 6. W3C PROV Mapping

This specification maps to the [W3C PROV Data Model](https://www.w3.org/TR/prov-dm/) as follows:

| This Specification | W3C PROV | Relationship |
|-------------------|----------|--------------|
| Session | `prov:Bundle` | A session is a named set of provenance assertions. |
| Event | `prov:Activity` | An event is an activity that occurred at a specific time. |
| Actor | `prov:Agent` | An actor is an agent responsible for activities. |
| Tool invocation input | `prov:Entity` | Inputs are entities `prov:used` by the activity. |
| Tool invocation output | `prov:Entity` | Outputs are entities `prov:wasGeneratedBy` the activity. |
| Decision | `prov:Activity` | A decision is an activity with additional attribution properties. |
| Decision revision | `prov:wasRevisionOf` | A revised decision revises a previous entity. |
| Annotation | `prov:Entity` | An annotation is an entity `prov:wasAttributedTo` an agent. |
| Correction link | `prov:wasRevisionOf` | A correction revises the corrected events. |
| Contribution | `prov:Activity` | A contribution is an activity that generates artifacts. |

The namespace `https://trace-protocol.org/ns/v0.3#` defines extension properties not covered by W3C PROV (e.g., `trace:disposition`, `trace:direction`, `trace:execution`, `trace:warnings`). These namespace URIs are identifiers, not resolvable URLs, following standard W3C practice.

A conforming consumer MAY export session documents as PROV JSON-LD using this mapping.

---

## 7. Interchange Format

### 7.1 JSON Encoding

The canonical interchange format is JSON. Each session MUST be representable as a single JSON object conforming to the JSON Schema at:

```
https://trace-protocol.org/schemas/trace-v0.3.json
```

A copy of this schema is distributed alongside this specification.

### 7.2 File Conventions

When stored as files, conforming producers SHOULD:

- Use one file per session.
- Pretty-print with 2-space indentation for human readability.
- Use UTF-8 encoding.
- Name files using the session ID (e.g., `trace_20260205_a1b2c3.json`).

### 7.3 Extensibility

- The `custom` fields on `SessionMetadata` and `Environment` are extension points for domain-specific data.
- Conforming consumers MUST ignore unrecognized top-level and nested fields (forward compatibility).
- Custom extensions MUST NOT redefine the semantics of fields defined in this specification.

### 7.4 Why JSON

JSON was chosen as the canonical format for several reasons:

- **Universal readability** — JSON is human-readable and editable in any text editor, without specialized tooling.
- **Diff-friendly** — Pretty-printed JSON is git-diffable, enabling version control of provenance records alongside code.
- **Zero-dependency validation** — JSON Schema validators exist in every major language, allowing conformance checking without importing the producing system's libraries.
- **MCP compatibility** — The [Model Context Protocol](https://spec.modelcontextprotocol.io/) uses JSON-RPC as its transport. Session documents can be passed through MCP tool calls without serialization conversion.
- **W3C PROV compatibility** — PROV JSON-LD is a JSON format, so session documents can be exported to PROV without intermediate representation changes.

Alternative encodings (YAML, TOML, Protocol Buffers, etc.) are not defined by this specification but MAY be supported by implementations as secondary formats, provided they can round-trip losslessly to JSON.

### 7.5 MCP Integration Pattern

The [Model Context Protocol](https://spec.modelcontextprotocol.io/) (MCP) is a natural fit for provenance collection in AI-assisted workflows: the AI client already communicates with external tools via MCP, and a provenance server can run as an additional MCP server alongside domain servers.

This specification does not require MCP. However, implementations that use MCP SHOULD follow this pattern:

```
AI Client
    |
    +-- MCP --> Domain Server(s)    (does the work)
    |
    +-- MCP --> Provenance Server   (records what happened)
```

The provenance server acts as a **sidecar** — it does not proxy or intercept domain tool calls. The AI client explicitly calls provenance tools to log events after performing domain actions. This means:

- The provenance server has no coupling to domain servers.
- Domain servers need no modification to support provenance.
- The AI client is responsible for deciding what to log and when.
- Provenance collection fails independently of domain work (fail-open).

MCP tool names are implementation-specific and not part of this specification. A conforming MCP implementation SHOULD provide tools for: starting/ending sessions, logging each event type, proposing/resolving decisions, querying events, and exporting session documents.

---

## 8. Implementation Guidance

This section is informative (non-normative).

### 8.1 What to Record

The following table provides guidance on what events to log:

| Always | Usually | Sometimes | Never |
|--------|---------|-----------|-------|
| Decisions (propose + resolve) | Domain tool invocations | Observations | File reads |
| Corrections | State changes | Todos | Directory listings |
| Contributions (one per artifact) | Failed tool calls | Questions | Navigation actions |
| | | Learnings for future sessions | The provenance system's own calls |

### 8.2 Recognizing Events in Conversation

AI assistants implementing this specification SHOULD recognize provenance events from natural language:

| Human says | Event to log |
|-----------|-------------|
| "let's go with X" / "should we try Y" | Decision (proposal) |
| "sounds good" / "go ahead" | Decision (resolution: accepted) |
| "no, use X instead" / "that's wrong" | Decision (resolution: rejected or revised) + Correction |
| "I'll do X" / "here's the analysis" | Contribution |
| "interesting — I didn't expect that" | Annotation (gotcha or observation) |

### 8.3 Fail-Open Principle

Conforming producers SHOULD follow the fail-open principle: provenance logging errors MUST NOT block the primary workflow. A sparse honest record is more valuable than a dense fabricated one. If the provenance system fails, the workflow should continue and the failure should be logged when possible.

### 8.4 Atomic Writes

When storing session documents to files, implementations SHOULD use atomic writes (write to a temporary file, then rename) to prevent corruption from interrupted writes.

---

## Appendix A: Example Session Document

```json
{
  "context": "https://trace-protocol.org/v0.3",
  "trace_version": "0.3.0",
  "id": "trace_20260205_a1b2c3",
  "created": "2026-02-05T14:30:00Z",
  "ended": "2026-02-05T15:45:00Z",
  "status": "completed",
  "metadata": {
    "project": "climate-nlp-analysis",
    "experiment_id": "exp-017",
    "description": "Analyzing adaptation language shifts in IPCC AR6",
    "participants": [
      {"type": "human", "id": "researcher-jane", "role": "lead"},
      {"type": "ai", "id": "claude-sonnet-4", "role": "assistant"}
    ],
    "tags": ["ipcc", "adaptation", "nlp"]
  },
  "summary": "Analyzed 47 passages on adaptation framing across AR5-AR6.",
  "events": [
    {
      "id": "evt_001",
      "timestamp": "2026-02-05T14:32:00Z",
      "session_id": "trace_20260205_a1b2c3",
      "type": "decision",
      "actor": {"type": "ai", "id": "claude-sonnet-4"},
      "decision": {
        "description": "Use cosine similarity threshold of 0.85 for passage matching",
        "rationale": "F1=0.78 on validation set at this threshold",
        "proposed_by": {"type": "ai", "id": "claude-sonnet-4"},
        "disposition": "proposed",
        "suggestion_type": "proactive",
        "tags": ["methodology", "threshold"]
      },
      "context": {
        "reasoning_summary": "Evaluated thresholds 0.7-0.95 on held-out set"
      }
    },
    {
      "id": "evt_002",
      "timestamp": "2026-02-05T14:35:00Z",
      "session_id": "trace_20260205_a1b2c3",
      "type": "decision",
      "actor": {"type": "human", "id": "researcher-jane"},
      "decision": {
        "description": "Lower threshold to 0.80 for higher recall",
        "rationale": "Exploratory analysis — prefer false positives over missed passages",
        "proposed_by": {"type": "human", "id": "researcher-jane"},
        "disposition": "revised",
        "resolved_by": {"type": "human", "id": "researcher-jane"},
        "revision_note": "Want higher recall; will manually review extras",
        "revises_event_id": "evt_001",
        "tags": ["methodology", "threshold"]
      },
      "context": {
        "conversation_snippet": "Let's lower it to 0.80 — I'd rather review extra passages than miss something"
      }
    },
    {
      "id": "evt_003",
      "timestamp": "2026-02-05T14:40:00Z",
      "session_id": "trace_20260205_a1b2c3",
      "type": "tool_call",
      "actor": {"type": "ai", "id": "claude-sonnet-4"},
      "tool_call": {
        "server": "corpus-search",
        "name": "search_passages",
        "input": {"query": "adaptation", "threshold": 0.80},
        "output": {"passages_found": 47},
        "duration_ms": 3200,
        "status": "success"
      }
    },
    {
      "id": "evt_004",
      "timestamp": "2026-02-05T14:42:00Z",
      "session_id": "trace_20260205_a1b2c3",
      "type": "annotation",
      "actor": {"type": "ai", "id": "claude-sonnet-4"},
      "annotation": {
        "category": "gotcha",
        "content": "IPCC AR5 PDFs have inconsistent Unicode encoding — ligatures (fi, fl) are sometimes split into separate characters, causing keyword matching to miss passages.",
        "tags": ["preprocessing", "unicode"]
      }
    },
    {
      "id": "evt_005",
      "timestamp": "2026-02-05T15:30:00Z",
      "session_id": "trace_20260205_a1b2c3",
      "type": "contribution",
      "actor": {"type": "ai", "id": "claude-sonnet-4"},
      "contribution": {
        "description": "Implemented cosine similarity function with Unicode normalization",
        "artifact": "src/similarity.py",
        "direction": "human",
        "execution": "ai",
        "related_decision_ids": ["evt_002"],
        "tags": ["implementation"]
      },
      "context": {
        "conversation_snippet": "Can you write the similarity function? Use the 0.80 threshold we agreed on"
      }
    }
  ]
}
```

---

## Appendix B: Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1.0 | 2026-02-06 | Initial data model: sessions, events (tool_call, decision, annotation, state_change), actors. |
| 0.2.0 | 2026-02-16 | Added: contribution events with direction/execution attribution, `suggestion_type` on decisions, `corrects_event_ids` on annotations, `retries_event_id` on tool calls, `conversation_snippet` on event context. |
| 0.3.0 | 2026-03-05 | Added: `warnings` on decisions, attribution audit at session end, path sanitization for session IDs. Removed: deprecated `verification` field on events, `parent_event_id` from event context. |
