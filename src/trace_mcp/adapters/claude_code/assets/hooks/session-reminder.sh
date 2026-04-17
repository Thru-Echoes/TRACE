#!/bin/bash
# trace-mcp:claude-code — SessionStart hook.
# Emits a reminder when no active TRACE session exists for THIS project today.
#
# Project detection (same order as /trace-session skill):
#   1. CLAUDE.md line matching `TRACE project name: "..."` → that value
#   2. basename of the git repo toplevel
#   3. basename of the project directory
#
# Session match: metadata.project == detected project AND status == "active"
# AND filename matches today's YYYYMMDD prefix.

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
SESSIONS_DIR="${TRACE_SESSIONS_DIR:-$HOME/.trace/sessions}"

if [ ! -d "$SESSIONS_DIR" ]; then
    exit 0
fi

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

if [ -z "$REPORT" ] || [ "$STATE" = "none" ]; then
    if [ -z "$NAME" ]; then
        NAME="this project"
    fi
    echo "⚠️ TRACE MCP is available but no active session exists for project '$NAME'. Start one with trace_start_session before logging events. If you forget, events will auto-create a session, but explicit sessions have better descriptions and tags."
fi
