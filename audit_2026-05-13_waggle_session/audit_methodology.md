# TRACE-vs-JSONL Audit — Instructions for Next Session

**Purpose:** Compare what TRACE recorded for session `trace_20260513_446733` against the verbatim Claude Code JSONL transcript of the same session, so we can evaluate TRACE's audit fidelity (coverage, accuracy, attribution, gaps).

This audit was the user's stated goal at the very start of the session ("compare what TRACE recorded and logged vs exactly what was said"). The implementation work (Phase I matcher + iterations) was running concurrently as the corpus to audit.

---

## The three artifacts

| Artifact | Path | Size | What it is |
|---|---|---|---|
| **JSONL transcript (ground truth)** | `/Users/echoes/.claude/projects/-Users-echoes-Documents-Contract-agentic-mcp-crm-waggle/a87354ce-fb29-4d7a-ab28-ad71fe404693.jsonl` | ~3.5 MB | Verbatim per-turn record written by the Claude Code harness. Every user message, assistant message, tool call, and tool result. Continues to grow as more turns happen in the original session. |
| **TRACE session JSON** | `/Users/echoes/.trace/sessions/trace_20260513_446733.json` | ~62 KB | What TRACE captured: 28 events (9 annotations, 17 contributions, 2 decisions). Frozen at session-end (2026-05-13 22:30 UTC). |
| **TRACE auto-scratchpad** | `/Users/echoes/Documents/Contract/agentic-mcp-crm/waggle/.claude/SCRATCHPAD.md` | ~36 KB | TRACE's auto-generated human-readable summary of the same 28 events. Frozen at session-end. |

⚠️ **Asymmetry:** The JSONL kept growing AFTER `trace_end_session` was called. Any work after 22:30 UTC on 2026-05-13 is in the JSONL but NOT in the TRACE session. This is expected. The audit evaluates TRACE's coverage of the work that happened DURING the TRACE session window.

---

## The TRACE session at a glance (what to expect to find)

- **Project:** waggle
- **Window:** 2026-05-13 12:22 UTC → 22:30 UTC (~10 hours)
- **28 events:**
  - **2 decisions:**
    - `evt_001` (proposed by human, requested, accepted) — Phase I execution kickoff
    - `evt_025` (proposed by human, requested, accepted) — matcher quality iteration kickoff
  - **17 contributions** — one per task or iteration cycle:
    - Tasks 1-7, 8, 9, 10, 11, 12, 13, 14, 16, 17 → `evt_004, evt_006, evt_009, evt_010, evt_011, evt_013, evt_014, evt_015, evt_016, evt_018, evt_019, evt_020, evt_021, evt_022, evt_023, evt_024`
    - v4 full-mode eval results → `evt_027`
  - **9 annotations:**
    - `evt_002` (gotcha) — `make reset-db` chain confusion
    - `evt_003` (correction) — Task 1 implementer's pyright false claim
    - `evt_005` (learning) — Task 2 deferral rationale + Task 4 design flag
    - `evt_007` (todo) — Task 3 deferred review items
    - `evt_008` (gotcha) — Task 3 test fixture seed-determinism flake
    - `evt_012` (learning) — process deviation: skipping round-2 reviewer for Task 5
    - `evt_017` (gotcha) — matcher quality gaps from Task 9 eval
    - `evt_026` (learning) — sample-mode eval variance problem
    - `evt_028` (todo) — v5 iteration plan for next session

---

## How to do the comparison

### Step 1 — Inventory the JSONL

The JSONL is line-oriented; each line is one harness event (user prompt, assistant message, tool call, tool result, system reminder). Suggested first pass:

```bash
# Total events
wc -l /Users/echoes/.claude/projects/-Users-echoes-Documents-Contract-agentic-mcp-crm-waggle/a87354ce-fb29-4d7a-ab28-ad71fe404693.jsonl

# Event types histogram
jq -r '.type' /Users/echoes/.claude/projects/-Users-echoes-Documents-Contract-agentic-mcp-crm-waggle/a87354ce-fb29-4d7a-ab28-ad71fe404693.jsonl | sort | uniq -c | sort -rn

# Just the user messages
jq -r 'select(.type=="user") | .message.content // "[non-text content]"' /Users/echoes/.claude/projects/-Users-echoes-Documents-Contract-agentic-mcp-crm-waggle/a87354ce-fb29-4d7a-ab28-ad71fe404693.jsonl | head -50
```

(Exact jq paths depend on Claude Code's JSONL schema — adapt to the actual structure if these queries miss.)

### Step 2 — Inventory the TRACE session

```bash
jq '.events | length' /Users/echoes/.trace/sessions/trace_20260513_446733.json
jq -r '.events[] | "\(.event_id) \(.event_type) \(.actor.type)"' /Users/echoes/.trace/sessions/trace_20260513_446733.json
```

Or read SCRATCHPAD.md for a human-readable list.

### Step 3 — Cross-check key dimensions

For each dimension below, check what TRACE captured vs what the JSONL shows actually happened:

**A. Decision coverage**
- TRACE has 2 decisions (evt_001, evt_025).
- Search the JSONL for moments where the controller (assistant) made a methodology choice that materially shaped subsequent work — for example: "skip round-2 reviewer for Task 5" (called out in evt_012 as a deviation), the judgment to bundle Tasks 16 and 17 differently than the plan, the per-task model selection (sonnet vs opus), the choice to defer specific minor review items.
- Question: are there decisions that should have been logged but weren't? Spot-check 2-3 candidate moments and check if there's a TRACE event for them.

**B. Contribution coverage**
- TRACE has 17 contributions (one per task + the v4 eval). Verify against the actual commit log:
  ```bash
  git -C /Users/echoes/Documents/Contract/agentic-mcp-crm/waggle log --oneline 84d0a88..bfae3b2
  ```
- Are there commits not represented by a contribution? E.g., the v2 (`ca76438`) and v3 (`aa9709f`) prompt-iteration commits don't have their own contribution events — they're folded into evt_027's combined v2/v3/v4 contribution. Is that the right granularity, or should each have been its own contribution?

**C. Correction coverage**
- TRACE has 1 correction (evt_003). The audit auto-noted: "1 correction lacks corrects_event_ids."
- The correction is about the Task 1 implementer's false pyright clean claim. Find the corresponding moment in the JSONL — is the correction text accurate? Does it cite the implementer's actual quote?
- Were there OTHER corrections not logged? Spot-check the JSONL for moments where the controller said "wait, that's wrong" or fixed an implementer's false claim.

**D. Attribution accuracy (direction + execution + actor)**
- All 17 contributions have `execution=ai`. That's accurate — subagents (AI) did all the work.
- Direction: 11 marked `direction=human`, 5 `collaborative`, 1 `ai`. The 1 ai-direction (evt_024 Task 17 deploy deferral) — was that accurate? Or was the deferral really a controller-recommended call that the user accepted, making it more `collaborative`?
- Spot-check 3 contributions for direction accuracy.

**E. conversation_snippet completeness**
- TRACE flagged 12+ contributions/annotations as missing `conversation_snippet`. The protocol asks for ~200 chars of relevant user message on each contribution.
- Skim the JSONL for the user messages that motivated each contribution. Were the snippets captured anywhere, or is the audit trail thin on user-side context?

**F. Tool-call logging**
- The protocol says "USUALLY: domain tool calls; NEVER: file reads, greps, exploratory calls."
- Check: does TRACE have any `tool_call` events? Looking at the event count (9 annotations + 17 contributions + 2 decisions = 28), there are zero tool_call events. Is that under-logging, or is "domain tool calls" actually a small set in this work? The matcher LLM calls (Stage B) and the eval-harness OpenAI calls would be candidates — were they expected to be logged?

**G. The v3 hidden-bug discovery**
- evt_010 (Task 4) and evt_027 (v4 full-mode) both mention the gpt-5.4-nano `plausibility_score` field bug.
- Spot-check: is this discovery moment well-represented in the TRACE record, or is it underplayed? It was a major mid-session finding.

### Step 4 — Write the audit report

Output a markdown file (suggested: `.reports/trace_audit_findings.md`) with:

1. **Coverage matrix** — per dimension above, score TRACE: complete / mostly-complete / sparse / missing.
2. **Specific gaps** — list events that should have been in TRACE but weren't (cite JSONL line ranges).
3. **Specific over-captures** — list TRACE events that were noisy or low-value (e.g., stub annotations).
4. **Attribution errors** — list any contribution/decision with wrong direction or actor.
5. **Recommendation** — for the user's TRACE protocol going forward: what's working, what should change, what's missing.

### Step 5 — Optional: compare against the protocol spec

The user's CLAUDE.md (global) and the project CLAUDE.md document the TRACE protocol. The session ran under that protocol; the audit can also evaluate "did the controller follow the protocol correctly?" separately from "did TRACE capture what it should have?"

---

## What "good" looks like for the audit findings

A well-functioning TRACE session should have:
- Every named artifact (commit, file, doc) represented by a contribution
- Every methodology-shaping moment represented by a decision (proposed → resolved)
- Every implementer correction or human-flagged AI mistake represented by a correction-category annotation with `corrects_event_ids` linked
- conversation_snippet on every contribution that has a corresponding user message
- Direction (human / ai / collaborative) accurately reflecting who proposed the artifact's shape
- Execution accurately reflecting who did the keyboard work

If TRACE missed >20% of named artifacts, missed corrections silently, or had wrong attributions on >10% of contributions, the protocol or the controller's discipline needs adjustment.

---

## For the next Claude Code session that picks this up

If you're a fresh session asked to do this audit:
1. Read this file end-to-end.
2. Read the JSONL paths and the TRACE session JSON before deciding methodology.
3. Don't START a new TRACE session for the audit itself unless the user wants it audited too — meta-recursion gets confusing.
4. If you do start a TRACE session for the audit, name it explicitly: e.g., description "AUDIT of trace_20260513_446733 against JSONL".
5. Ask the user before recommending changes to the global TRACE protocol — those are durable preferences and need their explicit sign-off.
