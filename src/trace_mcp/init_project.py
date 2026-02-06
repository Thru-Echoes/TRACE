"""trace-mcp init — set up TRACE in an existing project directory.

Creates/updates .mcp.json and appends TRACE instructions to CLAUDE.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The block that gets appended to CLAUDE.md
TRACE_CLAUDE_BLOCK = """\

---

## TRACE Audit Protocol (Auto-Injected)

This project uses [TRACE](https://trace-protocol.org) for transparent documentation
of AI-human collaboration. The TRACE MCP server is configured in `.mcp.json`.

### Acknowledgement

At the start of every conversation in this project, briefly inform the user that
TRACE audit logging is active. For example:

> TRACE audit logging is active for this project. Tool calls, decisions, and
> annotations will be recorded for transparency and reproducibility.

This acknowledgement should happen once per conversation, not per tool call.

### Required: Every Session

1. **Start**: Call `trace_start_session` at the beginning of every workflow.
   Include: project name, description of the goal, participants.
2. **End**: Call `trace_end_session` with a summary when done.

### Required: Tool Call Logging

After every tool call to another MCP server, call `trace_log_tool_call` with:
- server name, tool name, inputs, outputs, status, duration
- a `reasoning` note explaining why the tool was called
- failed calls are especially important to log (status: "error")

### Required: Decision Logging

Before any significant methodological choice, call `trace_propose_decision`:
- which method/algorithm, parameters, thresholds, data inclusion/exclusion,
  how to handle messy data, which model, how to interpret ambiguous results
- include a specific, technical rationale
- wait for human confirmation on consequential decisions
- when the human responds, call `trace_resolve_decision` with their disposition

### Required: Annotations

Log observations as they occur with `trace_log_annotation`:
- **gotcha**: unexpected behavior, data quality issues, encoding problems
- **learning**: reusable knowledge for future sessions
- **observation**: interesting but not immediately actionable
- **todo**: needs follow-up
- **question**: unresolved questions

### Required: State Changes

When switching models, changing parameters, or updating configuration,
call `trace_log_state_change` with old and new values.

### Principles

- Log methodology decisions, not trivial ones
- Rationales must be specific: "F1=0.78 at threshold 0.85" not "seemed good"
- Tag events with domain terms for searchability
- When in doubt, log it
"""

MCP_CONFIG = {
    "trace": {
        "command": "trace-mcp",
        "args": [],
    }
}

TRACE_MARKER = "## TRACE Audit Protocol"


def init_project(directory: str | None = None) -> None:
    """Initialize TRACE in a project directory."""
    project_dir = Path(directory) if directory else Path.cwd()

    if not project_dir.is_dir():
        print(f"Error: {project_dir} is not a directory")
        sys.exit(1)

    # 1. Update .mcp.json
    mcp_path = project_dir / ".mcp.json"
    if mcp_path.exists():
        with open(mcp_path) as f:
            config = json.load(f)
    else:
        config = {"mcpServers": {}}

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    if "trace" in config["mcpServers"]:
        old = config["mcpServers"]["trace"]
        print(f"Updating existing TRACE config in .mcp.json (was: {old.get('command', '?')})")
    else:
        print("Adding TRACE to .mcp.json")

    config["mcpServers"]["trace"] = MCP_CONFIG["trace"]

    with open(mcp_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    print(f"  Wrote: {mcp_path}")

    # 2. Append to CLAUDE.md
    claude_path = project_dir / "CLAUDE.md"
    if claude_path.exists():
        existing = claude_path.read_text()
        if TRACE_MARKER in existing:
            print(f"TRACE instructions already present in {claude_path} — skipping")
        else:
            with open(claude_path, "a") as f:
                f.write(TRACE_CLAUDE_BLOCK)
            print(f"  Appended TRACE instructions to: {claude_path}")
    else:
        claude_path.write_text(f"# Project Instructions\n{TRACE_CLAUDE_BLOCK}")
        print(f"  Created: {claude_path}")

    print()
    print("TRACE is ready. Start Claude Code in this directory and the")
    print("TRACE tools will be available automatically.")


def main() -> None:
    """CLI entry point for trace-mcp init."""
    # Simple arg parsing — just takes an optional directory
    args = sys.argv[1:]

    if args and args[0] == "init":
        args = args[1:]

    directory = args[0] if args else None
    init_project(directory)
