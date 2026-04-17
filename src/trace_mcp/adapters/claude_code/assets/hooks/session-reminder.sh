#!/bin/bash
# trace-mcp:claude-code — SessionStart hook.
# Emits a reminder if no active TRACE session exists for today.
# PR2 will improve this to be project-aware.

SESSIONS_DIR="${TRACE_SESSIONS_DIR:-$HOME/.trace/sessions}"

if [ ! -d "$SESSIONS_DIR" ]; then
    exit 0
fi

TODAY=$(date +%Y%m%d)
ACTIVE=$(grep -l '"status": "active"' "$SESSIONS_DIR"/trace_${TODAY}_*.json 2>/dev/null | head -1)

if [ -z "$ACTIVE" ]; then
    echo "⚠️ TRACE MCP is available but no active session exists. Start one with trace_start_session before logging events. If you forget, events will auto-create a session, but explicit sessions have better descriptions and tags."
fi
