# Decision Provenance for AI-Assisted Workflows

## Specification v0.4.1

**Status**: Draft
**Last Updated**: 2026-05-14
**JSON Schema**: [`trace-v0.4.json`](../schemas/trace-v0.4.json)
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
| `trace_version` | string | SHOULD | Semantic version of the specification. Default: `"0.4.1"`. |
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

> **Note on field naming**: The JSON Schema uses `mcp_servers` (for `tools`) and `python_version` (for `runtime_version`) for historical reasons. These names are retained for backward compatibility. Conforming producers SHOULD use the schema field names. This specification uses generic names to remain technology-neutral. **v0.4.1**: the `trace_version` field was removed from `Environment` to eliminate two-source-of-truth — the single canonical version lives on `Session.trace_version`. Conforming consumers MUST silently ignore an unrecognized `environment.trace_version` field when reading pre-v0.4.1 session files (no error, no warning), so that v0.3.x and v0.4.0 sessions remain readable without migration.

### 3.3 Actor

An actor is any entity that performs actions within a session.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | enum | MUST | One of: `"human"`, `"ai"`, `"system"`. |
| `id` | string | MUST | Unique identifier within the session (e.g., `"researcher-jane"`, `"claude-opus-4.7"`). |
| `role` | string | MAY | Role in the workflow (e.g., `"lead"`, `"assistant"`, `"reviewer"`). |

**Actor types**:
- `human` — A person making decisions, providing feedback, or doing work.
- `ai` — An AI model or agent performing actions, proposing decisions, or generating outputs.
- `system` — An automated system acting without direct human or AI agency (e.g., a CI pipeline, a scheduled job).

**AI actor IDs** are typically the model identifier published by the client's vendor. The TRACE specification is vendor-neutral; common examples spanning multiple organizations include `"claude-opus-4.7"` (Anthropic), `"gpt-5.5"` (OpenAI), `"gemini-3-pro"` (Google), `"llama-4-405b"` (Meta), and `"deepseek-r1"` (DeepSeek). Producers SHOULD use the canonical model ID from their AI client.

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

**Conformance for `conversation_snippet` (v0.4.1+)**:

- Producers MUST set `conversation_snippet` on every `contribution` event and every `annotation` event with `category="correction"` when a user message within the session motivated the action.
- When no user message motivated the event (e.g., during a long autonomous-execution stretch where the AI proceeds from a previously-accepted plan), producers SHOULD set `conversation_snippet` to an explicit absence marker. The recommended markers are `"<autonomous-stretch>"` (no user message since the most recent decision) and `"<no recent user message>"` (general fallback). Implementations MAY define additional absence markers, but they MUST begin with `<` and end with `>` to distinguish them from real user text.
- Silent omission (leaving the field null) on `contribution` or `correction` events SHOULD be treated by conforming consumers as a protocol violation, distinguishable from the explicit-absence case.
- When a single user message motivates multiple contributions (e.g., a multi-task plan executed by an AI), producers SHOULD reuse the same snippet across those contributions. Honesty does not require uniqueness.

This distinction lets a downstream auditor separate three states: (a) "user said this" (real snippet), (b) "no user message — AI proceeded autonomously" (absence marker), and (c) "controller forgot" (null). Only states (a) and (c) were distinguishable under earlier v0.3.x conventions; (b) was invisible.

The field remains optional on `decision`, `tool_call`, and `state_change` events.

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
| `host` | enum | MAY | One of: `"mcp"`, `"internal"`, `"external"`. Default: `"mcp"`. Identifies the integration surface: external MCP servers, host-internal tools (subagent dispatchers, file-mutation tools), or external non-MCP services (HTTP APIs, CLI subprocesses). Default `"mcp"` preserves v0.3.0 / v0.4.0 semantics. (v0.4.1+) |
| `parent_event_id` | string | MAY | ID of the controller-side event (typically a decision or contribution) that motivated this tool call. Used for subagent dispatches and other host-internal invocations to reconstruct the dispatch graph. Distinct from `retries_event_id` (which is for retry chains). Maps to `prov:wasInformedBy` in PROV-LD export (§6). (v0.4.1+) |

**Retry chains**: When a tool call fails and is retried, the retry event SHOULD set `retries_event_id` to the failed event's ID. This creates a chain of retry attempts, enabling analysis of failure patterns and recovery strategies.

**Dispatch chains (v0.4.1+)**: When the AI client invokes a host-internal tool (e.g., a subagent dispatcher), the dispatch event SHOULD set `host="internal"`, `server` to the host identifier (e.g., `"claude-code"`, `"codex"`), and `parent_event_id` to the controller-side event that motivated the dispatch. Walking `parent_event_id` links lets a consumer reconstruct the dispatch graph that produced a contribution.

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

**Proposer Identity Rule (v0.4.1+)**: `proposed_by` MUST identify the actor who authored the **content** of the proposal — the words populating `description` — not the actor who spoke the directive to act. In a question→AI-proposal→acceptance flow (the human asks "what should we do?", the AI proposes a specific course of action, the human accepts with "proceed" or "go ahead"), the AI is the proposer with `suggestion_type="requested"`; the human resolves with `disposition="accepted"` and `resolved_by` set to the human.

Disambiguation table for canonical patterns:

| Conversational pattern | `proposed_by` | `suggestion_type` | `disposition` | `resolved_by` |
|---|---|---|---|---|
| AI volunteers a course of action; human accepts | `ai` | `proactive` | `accepted` | `human` |
| Human asks a question; AI replies with a specific plan; human accepts ("proceed", "go ahead", "sounds good") | `ai` | `requested` | `accepted` | `human` |
| Human states a directive in their own words ("use threshold 0.80"); AI executes | `human` | — | `accepted` | `ai` |
| Human and AI iterate; final wording originated with one actor, resolved by the other | whoever authored the final `description` text | `collaborative` | `accepted` | the other actor |

The disambiguating test: copy the proposal's `description` text back into the conversation log and ask *which message in the transcript does it most closely paraphrase?* The actor of that message is the proposer. If `description` paraphrases the AI's reply to a human question, `proposed_by` is the AI even though the human spoke last.

**What constitutes a decision**: Methodological choices that affect outcomes — which algorithm to use, what threshold to set, how to handle missing data, which data to include or exclude, how to interpret ambiguous results. NOT: trivial implementation choices (variable naming, file organization) or navigation decisions (which file to read next).

### 3.7 Annotation (`annotation`)

Records a free-form observation, learning, or note.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `category` | enum | MUST | One of: `"learning"`, `"gotcha"`, `"observation"`, `"correction"`, `"todo"`, `"question"`, `"discovery"`, `"other"`. |
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
| `discovery` | A non-trivial finding from autonomous or unattended work that carries causal load — a bug found in flight, an unexpected dataset property, a confirmed root cause. Differs from `gotcha` (surprising but nobody was wrong) and `correction` (something prior was wrong; this surfaces new information rather than fixing a prior claim). SHOULD be logged at the moment of discovery, not in a post-hoc summary. |
| `other` | Anything that doesn't fit the above categories. |

**Corrections vs. rejections**: A `correction` annotation records that a mistake was made and fixed. A `rejected` decision records that a proposed approach was overridden. When a rejected decision was caused by a mistake (e.g., the AI used the wrong configuration), both a rejection AND a correction SHOULD be logged: the rejection on the decision, the correction annotation linking to the specific erroneous events via `corrects_event_ids`.

#### 3.7.1 External References in `corrects_event_ids` (v0.4.1+)

When the corrected item is not itself a TRACE event (e.g., a subagent's output, a non-logged tool result, or a statement made in an unlogged message), `corrects_event_ids` MAY contain URI-form references instead of in-session event IDs.

Each entry in `corrects_event_ids` MUST be either (a) an event ID within the same session matching the producer's event-ID pattern (e.g., `evt_001`), or (b) a URI-form reference distinguishable by a scheme prefix.

**Prefix disambiguation (normative)**: URI-form references MUST match the pattern `[a-z][a-z0-9-]+:` — a lowercase ASCII scheme name (starting with a letter, optionally followed by letters / digits / hyphens) followed by a colon. In-session event IDs MUST NOT match this pattern; producers SHOULD continue to use the `evt_NNN` convention per §3.4. This guarantees unambiguous discrimination between event IDs and URI references at the parser level without context-sensitive heuristics.

**Universal fallback (normative)**:

| Scheme | Form | When to use |
|--------|------|-------------|
| `external:` | `external:<uri>` | Any out-of-band source (issue tracker, chat thread, external document). The fallback for cases none of the more specific schemes fit. |

**Implementation examples (non-normative — producers MAY define additional schemes)**:

| Scheme | Form | When to use |
|--------|------|-------------|
| `jsonl:` | `jsonl:<path>#L<line>` or `jsonl:<path>#L<start>-L<end>` | Reference to a verbatim conversation transcript line. |
| `subagent:` | `subagent:<agent-id>` | Reference to a host-internal subagent invocation that is not represented as a `tool_call` event. |
| `tool-result:` | `tool-result:<call-id>` | Reference to a tool result whose dispatch was not logged as a `tool_call` event. |

Conforming consumers MUST tolerate both event IDs and URI-form references in `corrects_event_ids`. Consumers performing graph queries over corrections SHOULD treat URI references as terminating nodes (analogous to the dangling-reference rule in §4.4).

**Recommended pattern when the corrected item is not a TRACE event**:

1. **Preferred**: emit one URI-form reference plus a `conversation_snippet` quoting the corrected statement. This keeps the correction anchored to a verifiable artifact without fabricating an event.
2. **Acceptable**: leave `corrects_event_ids` empty *only when* the correction's `content` field self-describes the corrected statement verbatim and `conversation_snippet` quotes it. Producers SHOULD warn when both fields are empty on a `correction` annotation.
3. **Discouraged**: use `related_event_ids` as a workaround for the correction relationship. `related_event_ids` carries weaker semantics (loose association); consumers cannot recover correction provenance from it. If the corrected item has an in-session ancestor (e.g., the decision under which the corrected work was performed), that ancestor belongs in `related_event_ids`, but the correction's primary anchor belongs in `corrects_event_ids`.

Producers MUST NOT fabricate a TRACE event whose only purpose is to give `corrects_event_ids` a target. A sparse honest record is more valuable than a dense fabricated one (cf. §8.3 Fail-Open Principle).

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

- `retries_event_id`, `revises_event_id`, `related_event_ids`, and `related_decision_ids` SHOULD reference valid event IDs within the same session.
- `corrects_event_ids` entries SHOULD reference valid event IDs within the same session, OR MAY use URI-form references as defined in §3.7.1 when the corrected item is not a TRACE event.
- Consumers MUST tolerate dangling references (the referenced event may have been removed or may exist in a different session).
- **Implementation note (v0.4.x):** the reference implementation currently *hard-rejects* a relation reference (e.g. `revises_event_id`) that points outside the current session, at `append_event` time — stricter than the SHOULD above. Relaxing this to full spec-compliant dangling-tolerance is deferred to v1.1; producers should not rely on cross-session references in v0.4.x.

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

When a human (or a reviewing actor) catches and corrects a mistake, the provenance record SHOULD include:

1. **An anchor for the corrected statement** — either an in-session event ID capturing the erroneous claim (a previously-logged tool call, decision, or annotation), OR a URI-form reference per §3.7.1 when the corrected statement was not itself logged as a TRACE event (e.g., a subagent output, an out-of-band claim).
2. **A `correction` annotation** with `corrects_event_ids` set to the anchor from (1), and a `conversation_snippet` quoting the corrected statement in its own words.
3. **If an in-session decision was involved**, a `rejected` or `revised` disposition on that decision, with the correction annotation linking to both the decision and the original erroneous events (or external references) via `corrects_event_ids`.

Three anchor cases are acceptable, in order of preference:

- (a) **Event-ID anchor** — the corrected entity is a previously-logged TRACE event. Recommended whenever possible.
- (b) **URI-form anchor** — the corrected entity exists outside the TRACE event log (per §3.7.1). Recommended when the corrected item could not have been logged as a TRACE event.
- (c) **Snippet-only anchor** — both `corrects_event_ids` and any external reference are unavailable; the correction's `content` and `conversation_snippet` together identify the corrected statement. Acceptable only when no anchor of type (a) or (b) is possible.

This creates a traceable chain from mistake to correction, whether or not the corrected statement is itself a first-class TRACE event.

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
| Correction (event-ID target) | `prov:wasInvalidatedBy` | The corrected event was invalidated by the correction activity. Distinct from a revision: invalidation says the prior artifact is no longer valid; revision says it has a successor. Use `wasInvalidatedBy` for repudiatory corrections (v0.4.1+). |
| Correction (URI-form target) | `prov:wasInfluencedBy` with qualified influence | The correction is connected to the externally-located artifact via `prov:wasInfluencedBy`, reified through `prov:qualifiedInfluence` pointing to a blank node of type `prov:Influence`. The URI is carried on that blank node as `prov:atLocation`. This is the W3C PROV-O qualified-influence pattern for influences that need annotation. (v0.4.1+) |
| Tool-call dispatch parent | `prov:wasInformedBy` | A subagent dispatch activity (`tool_call` with `host="internal"`) was informed by the controller activity that issued it. Encoded via `tool_call.parent_event_id`. (v0.4.1+) |
| Contribution | `prov:Activity` | A contribution is an activity that generates artifacts. |

The namespace `https://trace-protocol.org/ns/v0.3#` defines extension properties not covered by W3C PROV (e.g., `trace:disposition`, `trace:direction`, `trace:execution`, `trace:warnings`). These namespace URIs are identifiers, not resolvable URLs, following standard W3C practice.

A conforming consumer MAY export session documents as PROV JSON-LD using this mapping.

---

## 7. Interchange Format

### 7.1 JSON Encoding

The canonical interchange format is JSON. Each session MUST be representable as a single JSON object conforming to the JSON Schema at:

```
https://trace-protocol.org/schemas/trace-v0.4.json
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

**Real-time logging discipline (v0.4.1+)**: Annotations of category `discovery`, `correction`, and `gotcha` SHOULD be logged at the moment of the underlying event, not folded into a later contribution's description. A contribution event that introduces a fact (a bug discovered, a root cause found, a regression observed) which no prior event in the session records is evidence of deferred logging — the underlying fact should have its own event at its moment of discovery.

**Autonomous-execution windows (v0.4.1+)**: When an AI controller executes for an extended period with no user messages and no `trace_*` events written, conforming hosts SHOULD detect this and prompt the controller to either confirm that no provenance-relevant event occurred or log the events that did. A silent multi-hour window in the event timeline is a provenance failure mode regardless of what was happening underneath. Specific thresholds (wall-clock duration, tool-call count) and detection mechanisms are implementation-defined.

### 8.2 Recognizing Events in Conversation

AI assistants implementing this specification SHOULD recognize provenance events from natural language:

| Human says | Event to log |
|-----------|-------------|
| "let's go with X" / "should we try Y" | Decision (proposal by human) |
| "what should we do about Y?" → AI replies with a specific plan → "proceed" / "go ahead" | Decision proposed by AI with `suggestion_type="requested"`, then resolution by human with `disposition="accepted"` (see §3.6 Proposer Identity Rule). The acceptance phrase is a resolution, not a proposal. |
| "sounds good" / "go ahead" | Decision (resolution: accepted). The proposer is whichever actor authored the proposal content, not the speaker of these acceptance words. |
| "no, use X instead" / "that's wrong" | Decision (resolution: rejected or revised) + Correction |
| "I'll do X" / "here's the analysis" | Contribution |
| "interesting — I didn't expect that" | Annotation (gotcha or observation) |

**Discovery patterns (v0.4.1+)** — log immediately at the moment of discovery, not in a post-hoc summary:

| Speaker says | Event to log |
|--------------|--------------|
| "I discovered that X" / "X turned out to be Y" / "found a bug in X" / "the load-bearing fix is X" | Annotation with `category="discovery"` (see §3.7) |

### 8.3 Fail-Open Principle

Conforming producers SHOULD follow the fail-open principle: provenance logging errors MUST NOT block the primary workflow. A sparse honest record is more valuable than a dense fabricated one. If the provenance system fails, the workflow should continue and the failure should be logged when possible.

### 8.4 Atomic Writes

When storing session documents to files, implementations SHOULD use atomic writes (write to a temporary file, then rename) to prevent corruption from interrupted writes.

---

## Appendix A: Example Session Document

```json
{
  "context": "https://trace-protocol.org/v0.3",
  "trace_version": "0.4.1",
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
      {"type": "ai", "id": "claude-opus-4.7", "role": "assistant"}
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
      "actor": {"type": "ai", "id": "claude-opus-4.7"},
      "decision": {
        "description": "Use cosine similarity threshold of 0.85 for passage matching",
        "rationale": "F1=0.78 on validation set at this threshold",
        "proposed_by": {"type": "ai", "id": "claude-opus-4.7"},
        "disposition": "revised",
        "resolved_by": {"type": "human", "id": "researcher-jane"},
        "revision_note": "Human revised to 0.80 for higher recall — see evt_002",
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
        "disposition": "accepted",
        "resolved_by": {"type": "ai", "id": "claude-opus-4.7"},
        "revision_note": "Human directed lower threshold; AI applied. Originally proposed by AI at 0.85 (evt_001).",
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
      "actor": {"type": "ai", "id": "claude-opus-4.7"},
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
      "actor": {"type": "ai", "id": "claude-opus-4.7"},
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
      "actor": {"type": "ai", "id": "claude-opus-4.7"},
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
    },
    {
      "id": "evt_006",
      "timestamp": "2026-02-05T15:35:00Z",
      "session_id": "trace_20260205_a1b2c3",
      "type": "decision",
      "actor": {"type": "ai", "id": "claude-opus-4.7"},
      "decision": {
        "description": "Three-step plan to improve recall: (1) tighten passage-extractor regex for multi-sentence quotations, (2) add Unicode NFKC normalization in preprocessing, (3) re-run matcher at threshold 0.80 and compare F1 against current run.",
        "rationale": "Researcher asked what would lift recall further without dropping the threshold below 0.80. Three interventions ordered by expected effect size on the held-out set.",
        "proposed_by": {"type": "ai", "id": "claude-opus-4.7"},
        "disposition": "accepted",
        "resolved_by": {"type": "human", "id": "researcher-jane"},
        "suggestion_type": "requested",
        "tags": ["methodology", "recall"]
      },
      "context": {
        "conversation_snippet": "researcher: 'what else can we do to lift recall without dropping threshold further?' / claude: 'three things in priority order: tighten the extractor regex...' / researcher: 'yes, do those three'",
        "reasoning_summary": "Human posed an open question; AI authored the three-step plan; human accepted with 'yes, do those three'. Proposer is AI (authored the content); resolver is human (accepted). This is the canonical v0.4.1 §3.6 Proposer Identity Rule pattern — see §8.2 recognition table for the speech-act mapping."
      }
    }
  ]
}
```

**Reading evt_006 against the v0.4.1 Proposer Identity Rule (§3.6)**:
The human asked an open question ("what else can we do?"); the AI authored the specific three-step plan; the human accepted with "yes, do those three". Under the disambiguation table:
- The substantive content of `description` paraphrases the AI's reply, not the human's question.
- Therefore `proposed_by={type: ai}`, even though the human spoke first.
- `suggestion_type="requested"` because the proposal was made in response to a human question rather than volunteered.
- `disposition="accepted"`, `resolved_by={type: human}` because the human's "yes, do those three" closes the deliberation.

Contrast with evt_001 (`suggestion_type="proactive"` — AI volunteered) and evt_002 (`proposed_by={type: human}` — researcher stated a directive in their own words and the AI executed). All three patterns are first-class.

---

## Appendix B: Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1.0 | 2026-02-06 | Initial data model: sessions, events (tool_call, decision, annotation, state_change), actors. |
| 0.2.0 | 2026-02-16 | Added: contribution events with direction/execution attribution, `suggestion_type` on decisions, `corrects_event_ids` on annotations, `retries_event_id` on tool calls, `conversation_snippet` on event context. |
| 0.3.0 | 2026-03-05 | Added: `warnings` on decisions, attribution audit at session end, path sanitization for session IDs. Removed: deprecated `verification` field on events, `parent_event_id` from event context. |
| 0.4.1 | 2026-05-14 | **Additive, fully backwards compatible with v0.3.x and v0.4.0.** Added: **Proposer Identity Rule** (§3.6) — `proposed_by` identifies the author of proposal content, not the speaker of the resolving directive; `discovery` annotation category (§3.7) — non-trivial findings from autonomous work; §3.7.1 **External References in `corrects_event_ids`** with URI-form scheme (`external:`, `jsonl:`, `subagent:`, `tool-result:`); `host` and `parent_event_id` fields on `tool_call` (§3.5) to cover MCP, external non-MCP, and host-internal tools; normative MUST clause on `conversation_snippet` for contributions and corrections with absence-marker convention (§3.4.1); real-time logging guidance + autonomous-execution-window detection (§8.1); question→AI-proposal→accept recognition rows (§8.2). Changed: PROV-LD correction mapping split — event-ID targets emit `prov:wasInvalidatedBy`, URI-form targets emit qualified `prov:wasInfluencedBy` with `prov:atLocation` (§6); dispatch chains emit `prov:wasInformedBy`. Schema additions are optional with defaults that preserve v0.3.0 / v0.4.0 semantics. |
