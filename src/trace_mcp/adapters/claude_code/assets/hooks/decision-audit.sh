#!/bin/bash
# trace-mcp:claude-code — PostToolUse hook for trace_end_session.
# Reads the most recently ended session JSON and surfaces guard-rail
# warnings (unresolved decisions, AI self-resolutions, orphan corrections).

SESSIONS_DIR="${TRACE_SESSIONS_DIR:-$HOME/.trace/sessions}"

LATEST=$(ls -t "$SESSIONS_DIR"/trace_*.json 2>/dev/null | head -1)

if [ -z "$LATEST" ]; then
    exit 0
fi

UNRESOLVED=$(grep -c '"disposition": "proposed"' "$LATEST" 2>/dev/null || echo 0)
SELF_RESOLVED=$(python3 -c "
import json
try:
    data = json.load(open('$LATEST'))
    count = 0
    for e in data.get('events', []):
        d = e.get('decision')
        if d and d.get('resolved_by') and d['proposed_by']['type'] == d['resolved_by']['type'] == 'ai':
            count += 1
    print(count)
except Exception:
    print(0)
" 2>/dev/null)
ORPHANED=$(python3 -c "
import json
try:
    data = json.load(open('$LATEST'))
    count = 0
    for e in data.get('events', []):
        a = e.get('annotation')
        if a and a.get('category') == 'correction' and not a.get('corrects_event_ids'):
            count += 1
    print(count)
except Exception:
    print(0)
" 2>/dev/null)

WARNINGS=""
if [ "$UNRESOLVED" -gt 0 ]; then
    WARNINGS="${WARNINGS}$UNRESOLVED unresolved decision(s). "
fi
if [ "$SELF_RESOLVED" -gt 0 ]; then
    WARNINGS="${WARNINGS}$SELF_RESOLVED AI self-resolution(s). "
fi
if [ "$ORPHANED" -gt 0 ]; then
    WARNINGS="${WARNINGS}$ORPHANED orphaned correction(s). "
fi

if [ -n "$WARNINGS" ]; then
    echo "TRACE Decision Audit: ${WARNINGS}Review the Attribution Audit above and fix any misattributions before closing."
fi
