#!/bin/bash
# trace-mcp:claude-code — UserPromptSubmit hook.
# Periodically nudges when the user is working in a project with no active
# TRACE session. Catches the "started acting before a session existed" failure
# mode where SessionStart fires once and is ignored.
#
# Behavior:
#   - If an active session exists for this project today → no output.
#   - Else: track turns and nudge only after N turns (default 3) and at
#     most once every COOLDOWN_SEC (default 300s) to avoid spam.
#
# State file: ~/.trace/runtime/<canonical-project-key>.state.json — the key is
# filesystem-safe by construction, so no separate sanitizer is needed (one used
# to live here and folded characters differently from the canonical key, which
# is exactly the kind of divergence the shared block below exists to prevent).

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
SESSIONS_DIR="${TRACE_SESSIONS_DIR:-$HOME/.trace/sessions}"
RUNTIME_DIR="${TRACE_RUNTIME_DIR:-$HOME/.trace/runtime}"
MIN_TURNS="${TRACE_PROMPT_MIN_TURNS:-3}"
COOLDOWN_SEC="${TRACE_PROMPT_COOLDOWN_SEC:-300}"

OUTPUT=$(python3 - "$PROJECT_DIR" "$SESSIONS_DIR" "$RUNTIME_DIR" "$MIN_TURNS" "$COOLDOWN_SEC" <<'PYEOF' 2>/dev/null
# --- trace-project-detect v0.5 begin ---
# SHARED BLOCK — byte-identical in all four trace-mcp hook scripts, guarded by
# tests/test_hook_assets_consistency.py. Edit every copy together or the hooks
# drift apart: one such drift (a pin regex that did not tolerate markdown bold)
# made one hook read the CLAUDE.md project name while another fell through to
# the git basename, minting two labels for a single repository.
#
# Pure stdlib, and deliberately no registry read: a hook canonicalizes a label,
# it never resolves aliases. Alias resolution belongs to the server and the
# identity CLI, which can fail closed on a damaged registry; a hook must not.
import re
import subprocess
import unicodedata
from pathlib import Path

_SEP_RUN = re.compile(r"[\s/_]+")
_NON_KEY_RUN = re.compile(r"[^\w.-]+")
_DOT_RUN = re.compile(r"\.{2,}")
_DASH_RUN = re.compile(r"-{2,}")
# Bold-tolerant: matches both `TRACE project name: "x"` and the bolded
# `**TRACE project name**: "x"` form that renders as a definition line.
_PIN_LINE = re.compile(r'TRACE project name\**\s*:\s*"([^"]+)"')


def canonical_key(label: str) -> str:
    """Return the canonical project key for *label*, or "" if it yields none.

    Mirrors trace_mcp.project_identity.canonical_project_key exactly, except
    that a degenerate label yields "" instead of raising: a hook advises, and
    must never fail the tool call it runs alongside.
    """
    s = unicodedata.normalize("NFC", label).strip().casefold()
    s = _SEP_RUN.sub("-", s)
    s = _NON_KEY_RUN.sub("-", s)
    s = _DOT_RUN.sub(".", s)
    s = _DASH_RUN.sub("-", s)
    return s.strip(".-")


def detect_project(project_dir: Path) -> str:
    """Return the canonical project key for *project_dir*, or "" if undetectable.

    Order: the `.claude/trace.project` pin file written by `trace-mcp init`,
    then a CLAUDE.md pin line, then the git toplevel basename, then the
    directory name. Every candidate is canonicalized, so the four sources agree
    on one identity instead of minting variants of it.
    """
    pin = project_dir / ".claude" / "trace.project"
    if pin.is_file():
        key = canonical_key(pin.read_text(errors="replace").strip())
        if key:
            return key
    md = project_dir / "CLAUDE.md"
    if md.is_file():
        match = _PIN_LINE.search(md.read_text(errors="replace"))
        if match:
            key = canonical_key(match.group(1))
            if key:
                return key
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
            key = canonical_key(Path(top).name)
            if key:
                return key
    except Exception:
        pass
    return canonical_key(project_dir.name)


def session_key(data: dict) -> str:
    """Return the canonical key a session record belongs to.

    `metadata.project_key` is authoritative when present (sessions written by a
    pinned server carry it); otherwise the display label is canonicalized, which
    is what keeps legacy sessions matchable.
    """
    meta = data.get("metadata") or {}
    key = meta.get("project_key")
    if isinstance(key, str) and key:
        return key
    label = meta.get("project")
    return canonical_key(label) if isinstance(label, str) else ""
# --- trace-project-detect v0.5 end ---

import json
import sys
from datetime import datetime, timezone

project_dir = Path(sys.argv[1])
sessions_dir = Path(sys.argv[2])
runtime_dir = Path(sys.argv[3])
min_turns = int(sys.argv[4])
cooldown_sec = int(sys.argv[5])


def has_active(key: str) -> bool:
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
        if session_key(data) == key:
            return True
    return False


def state_path(key: str) -> Path:
    return runtime_dir / f"{key or 'unknown'}.state.json"


def load_state(key: str) -> dict:
    path = state_path(key)
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def save_state(key: str, state: dict) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    state_path(key).write_text(json.dumps(state, indent=2))


key = detect_project(project_dir)

if key and has_active(key):
    save_state(key, {"turn_count": 0, "last_nudged": None})
    sys.exit(0)

now = datetime.now(timezone.utc)
state = load_state(key)
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
    save_state(key, state)
    print(f"NUDGE|{key}")
else:
    state["turn_count"] = turn_count
    save_state(key, state)
PYEOF
)

if [[ "$OUTPUT" == NUDGE\|* ]]; then
    NAME="${OUTPUT#NUDGE|}"
    if [ -z "$NAME" ]; then
        NAME="this project"
    fi
    echo "⚠️ TRACE: you've been working in '$NAME' for several turns without an active session. Call trace_start_session now so this work is part of the audit record. [trace-hooks v0.5]"
fi
