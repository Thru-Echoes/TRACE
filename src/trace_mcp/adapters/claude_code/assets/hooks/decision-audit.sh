#!/bin/bash
# trace-mcp:claude-code — PostToolUse hook for trace_end_session.
# v0.4.1: extended to surface the new audit metrics from the server-side
# AttributionAudit extension. Reads the most recently ended session JSON
# and surfaces guard-rail warnings.
#
# Detection logic generalized per spec §3.6 Proposer Identity Rule —
# self-resolution check now fires on ANY same-instance pair (type AND
# id match), not just ai→ai. Catches the evt_025 pattern (human→human
# self-resolution in multi-actor session).

SESSIONS_DIR="${TRACE_SESSIONS_DIR:-$HOME/.trace/sessions}"

LATEST=$(ls -t "$SESSIONS_DIR"/trace_*.json 2>/dev/null | head -1)

if [ -z "$LATEST" ]; then
    exit 0
fi

# Compute all v0.4.1 audit metrics in one Python invocation for efficiency.
# Each metric is on its own line; bash reads them via mapfile.
mapfile -t METRICS < <(python3 << PYEOF 2>/dev/null
import json
try:
    data = json.load(open("$LATEST"))
except Exception:
    # Fail-open: emit zeros if we can't parse
    for _ in range(6):
        print(0)
    raise SystemExit

events = data.get("events", [])

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
            # v0.4.1 generalized: same (type, id) pair
            if pb.get("type") == rb.get("type") and pb.get("id") == rb.get("id"):
                same_instance_self_resolved += 1

    if a and a.get("category") == "correction":
        if not a.get("corrects_event_ids"):
            orphan_correction += 1
        # Missing snippet on a correction is a spec §3.4.1 MUST violation
        if snip is None or (not is_absence(snip) and not snip.strip()):
            if snip is None:
                missing_snippet_correction += 1

    if c:
        # Missing snippet on a contribution is a spec §3.4.1 MUST violation
        if snip is None:
            missing_snippet_contrib += 1

print(unresolved)
print(ai_self_resolved)
print(same_instance_self_resolved)
print(orphan_correction)
print(missing_snippet_contrib)
print(missing_snippet_correction)
PYEOF
)

UNRESOLVED="${METRICS[0]:-0}"
AI_SELF_RESOLVED="${METRICS[1]:-0}"
SAME_INSTANCE="${METRICS[2]:-0}"
ORPHANED="${METRICS[3]:-0}"
MISSING_CONTRIB="${METRICS[4]:-0}"
MISSING_CORR="${METRICS[5]:-0}"

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
    echo "TRACE Decision Audit: ${WARNINGS}Review the Attribution Audit above and fix any misattributions before closing."
fi
