#!/bin/bash
# trace-mcp:claude-code — UserPromptSubmit hook.
# Periodically nudges when the user is working in a project with no active
# TRACE session. Catches the 2026-04-13 "started acting before session
# existed" failure mode where SessionStart fired once and was ignored.
#
# Behavior:
#   - If an active session exists for this project today → no output.
#   - Else: track turns and nudge only after N turns (default 3) and at
#     most once every COOLDOWN_SEC (default 300s) to avoid spam.
#
# State file: ~/.trace/runtime/<project>.state.json

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
SESSIONS_DIR="${TRACE_SESSIONS_DIR:-$HOME/.trace/sessions}"
RUNTIME_DIR="${TRACE_RUNTIME_DIR:-$HOME/.trace/runtime}"
MIN_TURNS="${TRACE_PROMPT_MIN_TURNS:-3}"
COOLDOWN_SEC="${TRACE_PROMPT_COOLDOWN_SEC:-300}"

OUTPUT=$(python3 - "$PROJECT_DIR" "$SESSIONS_DIR" "$RUNTIME_DIR" "$MIN_TURNS" "$COOLDOWN_SEC" <<'PYEOF' 2>/dev/null
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

project_dir = Path(sys.argv[1])
sessions_dir = Path(sys.argv[2])
runtime_dir = Path(sys.argv[3])
min_turns = int(sys.argv[4])
cooldown_sec = int(sys.argv[5])


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


def _sanitize(name: str) -> str:
    # Keep it filesystem-friendly without importing trace_mcp
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "unknown"


def load_state(project: str) -> dict:
    path = runtime_dir / f"{_sanitize(project)}.state.json"
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def save_state(project: str, state: dict) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_dir / f"{_sanitize(project)}.state.json"
    path.write_text(json.dumps(state, indent=2))


project = detect_project()

if has_active(project):
    save_state(project, {"turn_count": 0, "last_nudged": None})
    sys.exit(0)

now = datetime.now(timezone.utc)
state = load_state(project)
turn_count = int(state.get("turn_count", 0)) + 1

last_nudged_str = state.get("last_nudged")
cooldown_expired = True
if last_nudged_str:
    try:
        last = datetime.fromisoformat(last_nudged_str)
        cooldown_expired = (now - last).total_seconds() >= cooldown_sec
    except Exception:
        pass

should_nudge = turn_count >= min_turns and cooldown_expired

if should_nudge:
    state = {"turn_count": turn_count, "last_nudged": now.isoformat()}
    save_state(project, state)
    print(f"NUDGE|{project}")
else:
    state["turn_count"] = turn_count
    save_state(project, state)
PYEOF
)

if [[ "$OUTPUT" == NUDGE\|* ]]; then
    NAME="${OUTPUT#NUDGE|}"
    echo "⚠️ TRACE: you've been working in '$NAME' for several turns without an active session. Call trace_start_session now so this work is part of the audit record."
fi
