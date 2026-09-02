# TRACE beside Gents

[Gents](https://github.com/source-inc/gents) is an agent runtime where the
database is the control plane: every request, response, tool call, and approval
is a document in an embedded [DefraDB](https://github.com/sourcenetwork/defradb)
store, written under the agent's decentralized identifier (DID). It reconstructs
a request's event stream from those documents (`gents trace timeline`) and
exports it in external shapes (`gents trace project`).

That record answers **what the agent did** and **what a human authorized**. A
TRACE session sits next to it and answers a different question: **who proposed
each step, and what got revised or rejected.** Neither replaces the other. This
page shows how to run both at once.

Verified against Gents v0.14.0 and TRACE v0.5.0. No Gents source changes are
required.

## How the pieces fit

Gents reaches external MCP services over Streamable HTTP, resolved from a
service registry document, and agents call them through three meta tools
(`discover_tools`, `describe_tool`, `call_tool`). So TRACE runs as a normal
sidecar: the agent decides what is worth recording and calls a TRACE tool, the
same way a human-driven client does. Nothing is intercepted or inferred.

```
  Gents runtime                            TRACE
  ─────────────                            ─────
  AgentRequest ──► owned completion loop
                      │
                      ├─ host tools (read_file, bash, ...)
                      │
                      └─ call_tool ──HTTP──► trace_propose_decision
                                             trace_log_contribution   ──► session JSON
                                             trace_log_annotation
       every call persisted as an
       AgentToolCall document under
       the agent DID
```

## Setup

### 1. Serve TRACE over HTTP

```bash
TRACE_PROJECT=my-project trace-mcp --transport streamable-http --port 8765
```

Serves at `http://127.0.0.1:8765/mcp`. Keep the bind on loopback: TRACE has no
authentication layer, so anything that can reach the port can write to the
session store. See the README section on this transport for the full caveats.

### 2. Register TRACE in the service registry

On a Gents build that has the subcommand:

```bash
gents mcp register trace \
  --endpoint http://127.0.0.1:8765/mcp \
  --send-agent-did \
  --display-name "TRACE decision provenance" \
  --version 0.5.0
```

The v0.14.0 release binary ships `gents mcp probe` but not `gents mcp register`.
On that build, write the same `ToolServiceRegistry` document directly against the
runtime's GraphQL endpoint:

```graphql
mutation {
  upsert_ToolServiceRegistry(
    filter: { service_id: { _eq: "trace" } }
    add: {
      service_id: "trace"
      display_name: "TRACE decision provenance"
      lan_ip: "127.0.0.1"
      mcp_port: 8765
      mcp_path: "/mcp"
      send_agent_did: true
      status: "online"
      version: "0.5.0"
    }
    update: {
      lan_ip: "127.0.0.1" mcp_port: 8765 mcp_path: "/mcp"
      send_agent_did: true status: "online" version: "0.5.0"
    }
  ) { _docID service_id }
}
```

Confirm the runtime can reach it:

```bash
gents mcp probe trace --timeout 10s
# SERVICE  HEALTH_STATE  LATENCY_MS  LAST_ERROR
# trace    healthy       9           -
```

### 3. Allow the service on a behavior

Registration on its own grants nothing. A behavior reaches the service only when
its `ToolSelection` enables the meta tools and lists the service id. Both
default to off.

```bash
gents config tools set \
  --graphql http://127.0.0.1:9191/api/v0/graphql \
  --agent-did "$AGENT_DID" \
  --selection-id "$AGENT_DID:default-tools" \
  --enable-meta-tools true \
  --allowed-mcp-service-id trace

gents tools explain --behavior-id "$AGENT_DID:default"
```

The resolved surface should now include:

```json
"meta_mcp": ["call_tool", "describe_tool", "discover_tools"]
```

Use `--allowed-mcp-service-id`, not the `required_mcp_service_ids` field: the
required list is a dependency contract that makes the behavior unrunnable
whenever TRACE is down, which is rarely what you want from a provenance sidecar.

## Telling the agent when to log

Discovery is not enough. An agent that can reach TRACE still needs to know what
is worth recording. Add something like this to the behavior's system prompt or a
Gents Skill document:

> You have an MCP service `trace` that records decision provenance. Start a
> session with `trace_start_session` at the beginning of a multi-step task, and
> pass the returned `session_id` on every later call.
>
> - Before you commit to a significant choice (an approach, a parameter, what to
>   include or exclude, how to handle something ambiguous), call
>   `trace_propose_decision` with your rationale. Leave it in the proposed state;
>   you may not resolve your own proposal. If a human accepts, revises, or
>   rejects it, record that with `trace_resolve_decision` attributed to them.
> - After you produce something durable, call `trace_log_contribution` with
>   `direction` (whose idea it was) and `execution` (who did the work).
> - When something surprises you or a human corrects you, call
>   `trace_log_annotation` with the matching category.
> - Call `trace_end_session` with a summary that says what is next.
>
> Do not log file reads, searches, or exploratory calls. Record what a future
> reader would need in order to understand why the work went the way it did.

The proposer rule is the part worth keeping. TRACE holds an AI-proposed decision
in `proposed` until a human resolves it, so an unresolved proposal stays visible
as an open question rather than disappearing into an approved-looking log.

## What a run produces

One turn, two records. From a verified run against Gents v0.14.0:

**TRACE session:**

```
trace_20260902_403b56 | status: completed | project_key: gents-experiment
  evt_001  decision      proposed_by=ai/gents-agent  disposition=proposed  resolved_by=None
  evt_002  annotation    category=discovery
  evt_003  contribution  direction=human  execution=ai
```

**Gents documents** (`gents query --collection AgentToolCall`):

```
    1  call_tool  trace  trace_start_session
    1  call_tool  trace  trace_propose_decision
    1  call_tool  trace  trace_log_annotation
    1  call_tool  trace  trace_log_contribution
    1  call_tool  trace  trace_end_session
```

Gents holds every call as a DID-attributed document with arguments and results.
TRACE holds what the calls were about: a proposal with a rationale that is still
waiting on a human, a discovery, and a contribution with direction and execution
attribution.

## Things to know before you rely on this

- **TRACE keeps one current-session pointer per process.** Pass `session_id`
  explicitly on every call after `trace_start_session`, or run one TRACE process
  per behavior. Gents sends the agent's DID in an `x-agent-did` header, but TRACE
  does not yet map that header to a per-agent session.
- **Your free text lands in both stores.** Everything logged to TRACE is also
  persisted by Gents as `AgentToolCall` arguments and results. Treat the runtime's
  document store as holding whatever you log, and keep client names and personal
  detail out of both.
- **Attributable is not verified.** Gents documents are attributable to an agent
  DID; a TRACE record is attributable to the actors it was told about. Neither
  proves a human actually approved anything. Gents has open work on binding
  document signatures to the claimed principal and on signature-bound approvals;
  until such a mechanism exists end to end, do not describe either record as
  tamper-evident or verified.
- **Version drift.** The registry write above and the tool-selection fields were
  checked against v0.14.0. Gents is pre-1.0 and ships frequently; re-check the
  field names against the version you run.
