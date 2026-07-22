#!/bin/bash
# trace-mcp:claude-code — PostToolUse hook for trace_end_session.
# Reads the most recently ended session JSON FOR THIS PROJECT and surfaces
# guard-rail warnings from the server-side AttributionAudit extension.
#
# Project scoping: the session is selected through the shared
# canonical-identity block below. Selecting the globally newest session
# regardless of project — as this hook once did — reports another project's
# audit findings whenever two servers run side by side.
#
# Detection logic generalized per spec §3.6 Proposer Identity Rule —
# self-resolution check fires on ANY same-instance pair (type AND id match),
# not just ai→ai. This catches same-instance self-resolution between non-ai
# actors (e.g. human→human in a multi-actor session), not only the ai→ai case.

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
SESSIONS_DIR="${TRACE_SESSIONS_DIR:-$HOME/.trace/sessions}"

# Compute all v0.4.1 audit metrics in one Python invocation for efficiency.
# This script must run on macOS bash 3.2 (the default /bin/bash on macOS; Apple
# has not shipped a newer bash since the GPLv3 transition). bash 3.2 does NOT
# have `mapfile`, so we emit a single space-separated line from Python and parse
# it with `read`, which is POSIX-portable.
METRICS_RAW=$(python3 - "$PROJECT_DIR" "$SESSIONS_DIR" 2>/dev/null << 'PYEOF'
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

project_dir = Path(sys.argv[1])
sessions_dir = Path(sys.argv[2])

ZEROS = "0 0 0 0 0 0"
_DATED_NAME = re.compile(r"trace_(\d{8})_")


def name_date(path):
    """The filename's YYYYMMDD stamp, or "" when it has none (sorts oldest)."""
    match = _DATED_NAME.match(path.name)
    return match.group(1) if match else ""


def newest_for_project(key):
    """Return the parsed newest session belonging to *key*, or None.

    Ordered by the filename's YYYYMMDD stamp — the same "newest" notion the
    other three hooks use — with mtime breaking ties inside a day, which is
    what actually identifies the session that just ended.

    Walks newest-date-first and stops once the date changes after a match, so a
    store holding hundreds of sessions costs a handful of reads rather than a
    full parse of every file on every session end.
    """
    best = None
    best_mtime = 0.0
    best_date = None
    for path in sorted(sessions_dir.glob("trace_*.json"), key=lambda p: (name_date(p), p.name), reverse=True):
        date = name_date(path)
        if best_date is not None and date != best_date:
            break  # dates descend: nothing further can be newer than the match
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if session_key(data) != key:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best_mtime:
            best, best_mtime, best_date = data, mtime, date
    return best


key = detect_project(project_dir)
data = newest_for_project(key) if key and sessions_dir.is_dir() else None
if data is None:
    # Fail-open: no session for this project (or none parseable) → emit zeros.
    print(ZEROS)
    raise SystemExit

events = data.get("events", [])

# Multi-actor guard, mirroring server-side Session.is_multi_actor()
# (see docs/adr/002-v041-protocol-additions.md). Union of declared
# participants and observed event actor *types*; the generalized (non-ai)
# same-instance warning only fires when the session has >=2 distinct actor
# types. A single-actor session (solo human, system->system) legitimately
# self-resolves and must NOT be flagged — gating on multi-actor avoids that
# false positive.
_meta = data.get("metadata") or {}
_actor_types = {
    (p or {}).get("type")
    for p in (_meta.get("participants") or [])
    if (p or {}).get("type")
}
for _e in events:
    _at = (_e.get("actor") or {}).get("type")
    if _at:
        _actor_types.add(_at)
multi_actor = len(_actor_types) >= 2

unresolved = 0
ai_self_resolved = 0           # backward-compat (v0.3): ai→ai only
same_instance_self_resolved = 0  # v0.4.1: any same-instance pair
orphan_correction = 0
missing_snippet_contrib = 0
missing_snippet_correction = 0

EXPLICIT_ABSENCE = {"<autonomous-stretch>", "<no recent user message>"}

def is_absence(s):
    if s is None:
        return False
    return s.strip() in EXPLICIT_ABSENCE

for e in events:
    d = e.get("decision")
    a = e.get("annotation")
    c = e.get("contribution")
    ctx = e.get("context") or {}
    snip = ctx.get("conversation_snippet")

    if d:
        if d.get("disposition") == "proposed":
            unresolved += 1
        elif d.get("resolved_by"):
            pb = d.get("proposed_by") or {}
            rb = d.get("resolved_by") or {}
            # v0.3 ai-only backward-compat metric
            if pb.get("type") == rb.get("type") == "ai":
                ai_self_resolved += 1
            # v0.4.1 generalized: same (type, id) pair, gated to
            # multi-actor sessions (see docs/adr/002-v041-protocol-additions.md).
            if multi_actor and pb.get("type") == rb.get("type") and pb.get("id") == rb.get("id"):
                same_instance_self_resolved += 1

    if a and a.get("category") == "correction":
        if not a.get("corrects_event_ids"):
            orphan_correction += 1
        # Missing snippet on a correction is a spec §3.4.1 MUST violation.
        # v0.4.1 amendment: also count whitespace-only / empty snippet as
        # missing — a blank snippet would otherwise silently pass this check.
        if snip is None or (not is_absence(snip) and not snip.strip()):
            missing_snippet_correction += 1

    if c:
        # Missing snippet on a contribution is a spec §3.4.1 MUST violation.
        # v0.4.1 amendment: also count whitespace-only / empty snippet as missing.
        if snip is None or (not is_absence(snip) and not snip.strip()):
            missing_snippet_contrib += 1

print(f"{unresolved} {ai_self_resolved} {same_instance_self_resolved} "
      f"{orphan_correction} {missing_snippet_contrib} {missing_snippet_correction}")
PYEOF
)

# Parse the single space-separated line. POSIX-portable; works on bash 3.2+.
read -r UNRESOLVED AI_SELF_RESOLVED SAME_INSTANCE ORPHANED MISSING_CONTRIB MISSING_CORR <<< "${METRICS_RAW:-0 0 0 0 0 0}"

# v0.4.1: derive "non-ai same-instance" = SAME_INSTANCE − AI_SELF_RESOLVED.
# This is the genuinely new v0.4.1 visibility (human→human / system→system
# self-resolutions that v0.3 silently allowed).
NON_AI_SELF=$((SAME_INSTANCE - AI_SELF_RESOLVED))

WARNINGS=""
if [ "$UNRESOLVED" -gt 0 ]; then
    WARNINGS="${WARNINGS}$UNRESOLVED unresolved decision(s). "
fi
if [ "$AI_SELF_RESOLVED" -gt 0 ]; then
    WARNINGS="${WARNINGS}$AI_SELF_RESOLVED AI self-resolution(s). "
fi
if [ "$NON_AI_SELF" -gt 0 ]; then
    WARNINGS="${WARNINGS}$NON_AI_SELF same-instance self-resolution(s) [v0.4.1, spec §3.6]. "
fi
if [ "$ORPHANED" -gt 0 ]; then
    WARNINGS="${WARNINGS}$ORPHANED orphaned correction(s). "
fi
if [ "$MISSING_CONTRIB" -gt 0 ]; then
    WARNINGS="${WARNINGS}$MISSING_CONTRIB contribution(s) missing conversation_snippet [v0.4.1, spec §3.4.1]. "
fi
if [ "$MISSING_CORR" -gt 0 ]; then
    WARNINGS="${WARNINGS}$MISSING_CORR correction(s) missing conversation_snippet [v0.4.1, spec §3.4.1]. "
fi

if [ -n "$WARNINGS" ]; then
    echo "TRACE Decision Audit: ${WARNINGS}Review the Attribution Audit above and fix any misattributions before closing. [trace-hooks v0.5]"
fi
