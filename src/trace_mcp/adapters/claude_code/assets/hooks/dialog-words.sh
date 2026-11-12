#!/bin/bash
# trace-mcp:claude-code — PostToolUse hook for the question dialog (matcher: AskUserQuestion).
# Hook version: [trace-hooks v0.6]
#
# Shadow period (ADR 008): while `trace-mcp shadow start` has the period running
# and this project is not excluded, each answer the person gave in a question
# dialog is appended to ~/.trace/shadow/<key>/words/<host-session-id>.jsonl
# (mode 0600, never sent anywhere): one `dialog_answer` line per answer (a picked
# option arrives as its label, a typed answer verbatim) and one `dialog_note`
# line per note the person typed beside an answer. A picked option's preview is
# the session's text, not the person's, and is not recorded.
#
# Prints nothing and always exits 0. With no STATE.json, stdin is not read.

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
SHADOW_DIR="${TRACE_SHADOW_DIR:-$HOME/.trace/shadow}"

[ -f "$SHADOW_DIR/STATE.json" ] || exit 0
[ -t 0 ] && exit 0
HOOK_INPUT_FILE=$(mktemp 2>/dev/null) || exit 0
trap 'rm -f "$HOOK_INPUT_FILE"' EXIT
cat > "$HOOK_INPUT_FILE"

python3 - "$PROJECT_DIR" "$SHADOW_DIR" "$HOOK_INPUT_FILE" >/dev/null 2>&1 <<'PYEOF'
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
import os
import sys
from datetime import datetime, timezone

project_dir = Path(sys.argv[1])
shadow_dir = Path(sys.argv[2])
hook_input_file = sys.argv[3]


def _make_private_dirs(path):
    """Create path and any missing parents with mode 0700."""
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for d in reversed(missing):
        d.mkdir(mode=0o700)


def _words_file(key, payload):
    """Return (words file, host session id) for this hook input, or None when capture is off."""
    if not key or not isinstance(payload, dict):
        return None
    state = json.loads((shadow_dir / "STATE.json").read_text())
    if not isinstance(state, dict) or state.get("started_at") is None or state.get("stopped_at") is not None:
        return None
    excluded = state.get("excluded")
    if isinstance(excluded, list) and key in excluded:
        return None
    sid = payload.get("session_id")
    sid = sid if isinstance(sid, str) and sid else "unknown"
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", sid)
    return shadow_dir / key / "words" / (safe + ".jsonl"), sid


def capture_dialog(key):
    """Append the dialog's answers and typed notes to the shadow words file, in one write."""
    with open(hook_input_file, encoding="utf-8", errors="replace") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict) or payload.get("tool_name") != "AskUserQuestion":
        return
    response = payload.get("tool_response")
    if not isinstance(response, dict) or not isinstance(response.get("answers"), dict):
        return
    target = _words_file(key, payload)
    if target is None:
        return
    path, sid = target
    transcript = payload.get("transcript_path")
    common = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "host_session_id": sid,
        "transcript": transcript if isinstance(transcript, str) and transcript else None,
    }
    lines = []
    for question, answer in response["answers"].items():
        if isinstance(answer, str) and answer:
            lines.append(dict(common, kind="dialog_answer", question=question, text=answer))
    annotations = response.get("annotations")
    if isinstance(annotations, dict):
        for question, value in annotations.items():
            notes = value.get("notes") if isinstance(value, dict) else None
            if isinstance(notes, str) and notes:
                lines.append(dict(common, kind="dialog_note", question=question, text=notes))
    if not lines:
        return
    data = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines).encode("utf-8")
    _make_private_dirs(path.parent)
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


try:
    capture_dialog(detect_project(project_dir))
except Exception:
    pass
PYEOF
exit 0
