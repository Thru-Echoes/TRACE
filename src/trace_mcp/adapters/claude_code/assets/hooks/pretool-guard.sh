#!/bin/bash
# trace-mcp:claude-code — PreToolUse hook (soft guard by default).
#
# Runs before Edit/Write tool calls. If no active TRACE session exists for
# this project today, emits a warning reminding the model to start one.
#
# Modes (set via TRACE_GUARD env var):
#   soft    — print a warning, allow the tool call to proceed (default)
#   off     — disable the guard entirely
#   strict  — exit 2 so Claude Code blocks the tool call until a session
#             is started. Opt-in, not default.
#
# Stdin: JSON from Claude Code describing the tool call (ignored for now).
# Exit codes:
#   0 — allow (with optional warning on stdout)
#   2 — block (strict mode only; message on stderr)

MODE="${TRACE_GUARD:-soft}"

if [ "$MODE" = "off" ]; then
    exit 0
fi

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
SESSIONS_DIR="${TRACE_SESSIONS_DIR:-$HOME/.trace/sessions}"

REPORT=$(python3 - "$PROJECT_DIR" "$SESSIONS_DIR" <<'PYEOF' 2>/dev/null
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

project_dir = Path(sys.argv[1])
sessions_dir = Path(sys.argv[2])


def detect_project() -> str:
    md = project_dir / "CLAUDE.md"
    if md.is_file():
        match = re.search(r'TRACE project name:\s*"([^"]+)"', md.read_text(errors="replace"))
        if match:
            return match.group(1)
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        ).stdout.strip()
        if top:
            return Path(top).name
    except Exception:
        pass
    return project_dir.name


def has_active(project: str) -> bool:
    if not sessions_dir.is_dir():
        return False
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    for path in sessions_dir.glob(f"trace_{today}_*.json"):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if data.get("status") != "active":
            continue
        if data.get("metadata", {}).get("project") == project:
            return True
    return False


project = detect_project()
state = "active" if has_active(project) else "none"
print(f"{project}|{state}")
PYEOF
)

NAME="${REPORT%%|*}"
STATE="${REPORT##*|}"

if [ -z "$NAME" ]; then
    NAME="this project"
fi

if [ "$STATE" = "active" ]; then
    exit 0
fi

MESSAGE="⚠️ TRACE: editing files in '$NAME' but no active session exists. Call trace_start_session so this edit is recorded in the audit trail."

if [ "$MODE" = "strict" ]; then
    echo "$MESSAGE" >&2
    exit 2
fi

echo "$MESSAGE"
exit 0
