# TRACE

**Transparent Recording of AI-assisted Collaboration Experiments**

TRACE is an MCP server that provides a standardized audit trail for AI-assisted research workflows. It records tool calls, decisions, annotations, and actor attribution — who proposed what, who accepted or revised it, and why.

TRACE runs as a **sidecar** alongside your domain MCP servers. It doesn't proxy or intercept calls — the AI client explicitly logs events to TRACE, creating a complete, human-readable provenance record.

## Quick Start

### Install

```bash
pip install -e .
```

### Configure for Claude Code

Add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "trace": {
      "command": "trace-mcp",
      "args": []
    }
  }
}
```

### Configure for Claude Desktop

Add to your Claude Desktop MCP settings:

```json
{
  "mcpServers": {
    "trace": {
      "command": "trace-mcp",
      "args": []
    }
  }
}
```

### Run a First Session

Once configured, TRACE tools are available to the AI client:

```
You: "Start a TRACE session for our climate NLP analysis"

Claude: -> trace_start_session(project="Climate NLP", ...)
        "Session started: trace_20260205_a1b2c3"

You: "Search for adaptation passages in the IPCC corpus"

Claude: -> [calls corpus-search-mcp/search_passages]
        -> trace_log_tool_call(server="corpus-search-mcp", ...)
        -> trace_propose_decision(description="Focus on chapters 14-17", ...)

You: "Also include chapter 6"

Claude: -> trace_resolve_decision(disposition="revised", ...)
        -> trace_log_annotation(category="learning", ...)

You: "End the session"

Claude: -> trace_end_session(summary="Analyzed 47 passages...")
```

Session files are written to `~/.trace/sessions/`.

## Available Tools

| Tool | Description |
|------|-------------|
| `trace_start_session` | Start a new audit session |
| `trace_end_session` | End a session with optional summary |
| `trace_log_tool_call` | Record a tool invocation on another MCP server |
| `trace_log_annotation` | Record a learning, gotcha, observation, or todo |
| `trace_log_state_change` | Record an environment or configuration change |
| `trace_propose_decision` | Propose a methodological decision |
| `trace_resolve_decision` | Accept, revise, or reject a proposed decision |
| `trace_get_session` | Get session metadata |
| `trace_get_events` | List events (filterable by type) |
| `trace_get_decisions` | List decisions (filterable by disposition) |
| `trace_get_decision_chain` | Walk linked decision revisions |
| `trace_search` | Search events by text content |
| `trace_export` | Export as JSON, Markdown, or PROV JSON-LD |
| `trace_list_sessions` | List all sessions (filterable by project) |

## How It Works

```
AI Client (Claude Code, Claude Desktop, etc.)
    |
    +-- connects to: Domain MCP Server(s)
    |                 (corpus search, NLP pipeline, etc.)
    |                 --> does the actual work
    |
    +-- connects to: TRACE MCP Server
                     --> records what happened to JSON files
```

Each session is stored as a self-contained JSON file in `~/.trace/sessions/`. Files are human-readable (pretty-printed), git-diffable, and shareable.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TRACE_SESSIONS_DIR` | `~/.trace/sessions/` | Directory for session JSON files |
| `TRACE_LOG_LEVEL` | `INFO` | Logging verbosity |

## Export Formats

- **JSON**: The native session file — always available, always complete
- **Markdown**: Human-readable summary with decision log, tool call table, annotations, and statistics
- **PROV JSON-LD**: W3C PROV-compatible provenance graph for interoperability

## Schema Reference

The formal protocol specification is a JSON Schema generated from the Pydantic models:

- [`schemas/trace-v0.1.json`](schemas/trace-v0.1.json)

Regenerate with: `python scripts/generate_schema.py`

## Using with Claude Code Skill

Copy the skill file to teach Claude Code to automatically use TRACE:

```bash
cp skill/TRACE.md ~/.claude/skills/
```

Or reference it in your project's Claude Code configuration.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

Apache 2.0
