#!/bin/bash
# trace-mcp:claude-code — PreToolUse hook (soft guard by default).
#
# Runs before Edit/Write tool calls. If no active TRACE session exists for
# this project today, emits a warning reminding the model to start one.
# Project detection and session matching go through the shared
# canonical-identity block below.
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


key = detect_project(project_dir)
state = "active" if key and has_active(key) else "none"
print(f"{key}|{state}")
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

MESSAGE="⚠️ TRACE: editing files in '$NAME' but no active session exists. Call trace_start_session so this edit is recorded in the audit trail. [trace-hooks v0.5]"

if [ "$MODE" = "strict" ]; then
    echo "$MESSAGE" >&2
    exit 2
fi

echo "$MESSAGE"
exit 0
