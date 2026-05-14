<!-- trace-mcp:claude-code -->

## TRACE Audit Protocol (v0.4.1+)

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
  - **Proposer Identity Rule (v0.4.1, spec §3.6)**: set `proposed_by` to the
    actor who authored the proposal *content* (whose words populate
    `description`), not the speaker of the resolving directive.
    Question→AI-proposal→accept means `proposed_by=ai`, `resolved_by=human`.
- **Corrections** when a participant catches a mistake.
  - If the corrected entity is not a TRACE event (subagent output, tool
    result, external claim), use a URI-form reference per spec §3.7.1:
    `external:<uri>` (universal fallback), `jsonl:<path>#L<line>`,
    `subagent:<id>`, or `tool-result:<id>`. `related_event_ids` is NOT
    for the correction relationship.
- **Discoveries (v0.4.1, `category="discovery"`)**: non-trivial findings
  from autonomous work — log AT THE MOMENT of discovery, not in a
  post-hoc summary.
- **Contributions** — one per artifact, with `direction` (who had the idea)
  and `execution` (who did the work). Always set `conversation_snippet`
  to the relevant user message (~200 chars). If no user message
  motivated the event (autonomous-execution stretch), use
  `<autonomous-stretch>` rather than omitting. Silent omission is a
  v0.4.1 protocol violation per spec §3.4.1.
- **Subagent dispatches** when their outcome is summarized by a
  contribution — `trace_log_tool_call(host="internal", server="claude-code",
  parent_event_id=...)` per spec §3.5. Skip routine file reads, greps,
  or TRACE's own calls.

Full protocol, including attribution rules, URI-form references, and
worked examples, lives at the [TRACE specification](https://github.com/Thru-Echoes/TRACE/blob/main/docs/specification.md).

<!-- /trace-mcp:claude-code -->
