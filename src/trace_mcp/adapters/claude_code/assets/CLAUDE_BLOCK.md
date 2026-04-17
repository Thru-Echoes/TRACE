<!-- trace-mcp:claude-code -->

## TRACE Audit Protocol

This project uses [TRACE](https://github.com/Thru-Echoes/TRACE) for transparent
documentation of AI-human collaboration. The TRACE MCP server is configured in
`.mcp.json` and enforced via `.claude/hooks/`.

**Absolute rule**: Never fabricate, falsify, or retroactively alter TRACE
data. A sparse honest record beats a dense fabricated one.

**Session lifecycle**

- **Start** a TRACE session at the beginning of any multi-step workflow.
- **End** with a summary when the workflow is complete. Review the
  Attribution Audit returned by `trace_end_session` before closing.

**What to log**

- **Decisions** (propose BEFORE acting, resolve when the human responds).
- **Corrections** when the human catches an AI mistake.
- **Contributions** — one per artifact, with `direction` (who had the idea)
  and `execution` (who did the work).
- Domain tool calls (not file reads, greps, or TRACE's own calls).

Full protocol, including attribution rules and examples, lives at the
[TRACE specification](https://github.com/Thru-Echoes/TRACE/blob/main/docs/specification.md).

<!-- /trace-mcp:claude-code -->
