# Codex CLI adapter — placeholder

Codex CLI does not currently expose the kind of pre/post-tool-use hook
surface that Claude Code does. Until it does, this adapter is a stub. Its
purpose is to reserve the namespace and make the adapter story concrete in
`trace-mcp init --client=codex` output.

## What a full Codex adapter would need

To reach parity with the Claude Code adapter, Codex would need one of:

1. **Tool-call hooks** — pre/post hooks fired around tool calls, similar to
   Claude Code's `PreToolUse` / `PostToolUse`. If this lands, mirror
   `src/trace_mcp/adapters/claude_code/` one-to-one with Codex's event
   names and config file format.

2. **Turn-start / turn-end hooks** — if Codex only exposes turn-level
   hooks (analogous to `UserPromptSubmit` / `Stop`), the `UserPromptSubmit`
   reminder is still workable; the `PreToolUse` guard is not.

3. **Neither of the above** — fall back to a watchdog daemon that tails
   Codex's session log and emits nudges out-of-band (e.g. via a desktop
   notification). This is weaker than in-process hooks but better than
   nothing.

## What to ship when Codex support lands

- `assets/hooks/` directory with Codex's equivalent scripts.
- `assets/config_template.*` with Codex's native config format (TOML? JSON?).
- `assets/CODEX_BLOCK.md` — minimal prompt prefix / project instruction block.
- Update `_REGISTRY` in `src/trace_mcp/adapters/__init__.py` so
  `detect_adapter()` finds Codex directories automatically.

Until one of the above paths is viable, leave `CodexAdapter.install` raising
`NotImplementedError` and `detect` returning `False`.

## v0.4.1 schema additions relevant to Codex

The TRACE protocol v0.4.1 added two optional fields to `ToolCallData`
that a future Codex adapter should populate when capturing Codex's
subagent dispatches (e.g., Codex's `task` tool or equivalent):

- `host: Literal["mcp", "internal", "external"] = "mcp"` — set to
  `"internal"` for host-internal tools, with `server="codex"`.
- `parent_event_id: str | None = None` — links a dispatch event to
  the controller-side event (typically a decision or contribution)
  that motivated it. Enables reconstruction of the dispatch graph
  via the PROV-LD `prov:wasInformedBy` relation (spec §6).

When implementing Codex auto-capture, follow the pattern that the
Claude Code `dispatch-{start,end}.sh` hooks will use:
PreToolUse-equivalent records dispatch start, PostToolUse-equivalent
records `duration_ms`/`status`/output summary and then calls
`trace_log_tool_call(host="internal", server="codex", parent_event_id=...)`.

Protocol-level guidance lives in spec §3.5; the schema is in
`src/trace_mcp/schema/events.py` (`ToolCallData`).
