# ADR 008: Provenance marks — a shadow comparison period

**Status:** Accepted
**Date:** 2026-11-12
**Context:** TRACE stores what an AI session writes without recording whether a
person stands behind it, and later sessions read it back as project knowledge.
The learn extractor sends every event of a session to the model, including
decisions still in the `proposed` state, and the learnings it writes carry no
trace of that disposition. Recall prints only the category. One observed
failure: a learning extracted from an unresolved AI proposal, reworded by the
extraction model as a directive, was shown to a later session under "Related
learnings (review before proceeding)" within an hour of being written, with
nothing indicating it was an unresolved proposal.

## Summary

Add *marks* that say where an event or a learning came from, and run a period
in which today's behaviour (the **Live** arm) and the marked behaviour (the
**Marked** arm) are computed on the same sessions and compared. No session sees
the Marked arm during the period. A read-only report and a review sheet then
decide, part by part, whether anything becomes live. Making a part live is a
separate change with its own review.

The period depends on one core change that ships first and stands on its own:
a resolved decision records when it was resolved (`resolved_at`) and, when the
resolving session supplies them, the person's words that resolved it
(`resolution_snippet`); specification §3.6, wire version 0.5.2.

## Requirement (fixed)

- **Keep everything.** Nothing is dropped, withheld, or ranked lower because no
  person approved it.
- **Note what is known.** An entry no person approved is marked as such wherever
  it is stored and wherever it is shown.
- **Approval is not TRACE's part.** A mark records where an entry came from; it
  is not a ruling on it. Approving and admitting entries belong to a separate
  review layer.
- **Solid on its own.** Marks are computed from the record, not from what the
  writing session claims about itself, and are cheap.

## Decisions

### D1. Marks are short tags computed by a pure function of the record

Base mark, at most one per event:

| Logged | Mark |
|---|---|
| Decision still `proposed` | `[proposed]` |
| Decision resolved by a non-human actor, any disposition | `[ai-resolved]` |
| Decision `accepted` or `revised`, resolved by a human | `[accepted]` |
| Decision `rejected`, resolved by a human | `[rejected]` |
| Annotation by an AI actor | `[ai-note]` |
| Any other event | none |

Add-on marks:

| Condition | Add-on |
|---|---|
| `conversation_snippet` found in the person's words (D3) | `[quote-verified]` |
| Snippet present, checkable, and not found | `[quote-not-found]` |
| Human resolution whose `resolution_snippet` is found in the person's words | `[resolution-verified]` |
| Human resolution whose `resolution_snippet` is checkable and not found | `[resolution-not-found]` |
| Human resolution recorded with no `resolution_snippet` | `[resolution-unquoted]` |

A learning takes the marks of its source event. A learning whose source event
cannot be found is marked `[source-unknown]`.

**Quote check.** No model is used. Both sides are normalized (Unicode NFC, case
folding, whitespace runs collapsed). A snippet is split at ellipses (`...`,
`…`) into fragments, because a snippet is routinely an abridged quotation;
fragments shorter than 8 characters are ignored. The snippet is verified when
every remaining fragment is a substring of some entry of the person's words
recorded no later than the event — for a resolution quote, no later than
`resolved_at`. A snippet with no fragment of 8 characters ("yes", "go ahead")
is verified only when the whole snippet equals one whole entry of the person's
input in the *turn* in which the event or the resolution was logged: the
person's latest typed prompt before it, plus any dialog entries recorded after
that prompt. Otherwise it is *too short* to check, because a three-letter match
inside a longer message, or an old "yes" given to something else, proves
nothing. Outcomes that carry no add-on are counted separately: no
snippet on an event, an explicit absence marker (specification §3.4.1), too
short, *unchecked* — the session began before capture of the person's words
did, so a miss would prove nothing — and, for a resolution, *unknown*: the
decision was resolved before the record held a resolution time.

A miss is not evidence of an invented quote. Of 271 checkable snippets on
human-resolved decisions in past sessions, 215 were found and 56 were not. One
miss was found once typed commands were counted as the person's words; of the
other 55, 8 had at least 90% of their words in a single message, 33 between 50%
and 90%, and 14 fewer. The report splits misses by that share (D7), and a miss
goes to the review sheet for a person to judge.

**Where a quote is found.** Sessions quote the message of the turn they are
in. Of 229 quotes found on human-resolved decisions in past sessions, 196 were
in the person's input of the turn in which the event was logged and 33 only in
an older message; of 316 such resolutions, the person's latest typed message
was under 8 characters in 6. The check therefore asks nothing new of the
person. For each verified quote the report says whether it was found in the
turn or only in older words (D7), and a resolution quote found only in older
words goes to the review sheet.

*Why a substring test and not a model's judgment.* Whether a person typed given
words is a fact a substring test settles. A model asked the same question can
accept an invented quote that sounds right, which is the failure being checked,
and the person's words never leave the machine (D3). Whether the words amount
to approval of this decision is a judgment, and it belongs to the review layer.

**Resolution quote.** A decision recorded as resolved by a human rests on the
resolving session's word. From wire version 0.5.2 `trace_resolve_decision`
accepts the person's words that resolved the decision and stores them as
`resolution_snippet`; the same quote check then says whether those words were
typed. A human resolution given no quote is recorded and marked
`[resolution-unquoted]`. It is not refused: sessions on older instructions and
other hosts keep working, and the missing quote stays visible.

*Rejected:* a timing rule, under which a human resolution is unbacked unless a
person's message falls between the logged proposal and the logged resolution.
Of 308 human resolutions found in past session transcripts, 192 were proposed
and resolved in the same turn, 89 of them decisions the person had proposed
themself. The rule would mark most real approvals and cannot tell an approval
from a session resolving its own proposal. The report keeps the timing as a
count (D7), never as a mark.

*Rejected:* a fixed confirmation phrase, and approval only through the question
dialog. A fixed phrase is the same words for every decision, so once typed it
verifies for any of them. Requiring a dialog, or a proposal logged before the
person is asked, runs against how resolutions are logged — most in the turn of
the person's own message — for a case that is rare.

`get_event_marks` lives in a new core module, `trace_mcp/provenance_marks.py`,
with no import from `extensions/` (ADR 003).

*Rejected:* full-phrase marks ("Accepted by a person"). Longer at every recall
and harder to count mechanically; display wording is decided after the period
(D9).

### D2. Marks are never stored; the record holds what they are computed from

A mark is not written onto an event or a learning.

- **A stored mark goes stale.** A decision is `[proposed]` when logged and
  `[accepted]` once resolved, possibly in a later session: resolving a proposed
  decision is the one change permitted after a session completes (INV-2).
- **The record already holds the inputs.** Disposition, resolver, actor, the
  proposal time and — from wire version 0.5.2 — the resolution time and the
  resolution quote are fields of the event. A stored label would be a second
  copy that can disagree.
- **The period changes no session record.**

`get_event_marks` is called whenever a mark is needed, against the current
record, and takes an optional *as of* time. With both times in the record, a
decision is `[proposed]` from its event timestamp and takes its resolved mark
from `resolved_at`, so the report can state both what a learning's source is
now and what it was when the learning was written. A decision resolved before
`resolved_at` existed has no recoverable resolution time: an *as of* question
about it is answered with the current mark and flagged as unknown, and its
resolution quote status is `unknown`. Nothing is backfilled.

Where a mark is recorded in a shadow log (D5, D6), it is recorded with its
time, as an observation of what would have been shown then.

*Rejected:* a `marks` field on every event (stale on resolution, changes the
live record); a shadow append at every event write (a side effect on every
logging call, for information the record already holds); a shadow-only file of
resolution times (repairs the comparison but leaves the record itself unable to
say when a decision was resolved).

### D3. The person's words: two per-project hooks, local only

Two Claude Code hooks append to
`~/.trace/shadow/<project-key>/words/<host-session-id>.jsonl` (file mode
`0600`), one line per entry, each with a UTC timestamp, the host's session id,
a `kind`, and the path of the host's transcript:

- **Typed prompts.** The `UserPromptSubmit` hook (`prompt-reminder.sh`) records
  the submitted prompt (`kind: "prompt"`). Pasted text arrives expanded inside
  the prompt and is recorded with it: it is what the person submitted. The host
  also runs this hook for input no person typed — a finished background task, a
  scheduled task, a message from another session — so a prompt that begins with
  a host notice tag (`<task-notification`, `<system-reminder`) is not recorded.
- **Question-dialog answers.** A `PostToolUse` hook on the host's question
  dialog (`dialog-words.sh`, matcher `AskUserQuestion`) records each answer
  with its question (`kind: "dialog_answer"`) and each note the person typed
  beside an answer (`kind: "dialog_note"`). A picked option arrives as its
  label and a typed answer as the typed text.

Neither hook writes into the conversation or fails the host. Both capture only
while the period is running and the project is not excluded (D4). The files are
never sent to a provider.

Words are matched to events by time within the project, not by host session,
because a conversation can span several host sessions and the hooks do not know
the TRACE session id.

**What the hooks were confirmed to receive.** On Claude Code 2.1.322 a probe
hook recorded the question dialog's `PostToolUse` input: `tool_response.answers`
maps each question to its answer, a picked option as its label and a typed
answer verbatim. A second live probe, before the hooks shipped, confirmed every
input kind listed below and found one more shape: when the person types a note
and picks no option, the host sets the answer to the placeholder `"(notes only)"`
and puts the note in `annotations[question]["notes"]`. The placeholder is not the
person's words and is not recorded; the note is. The host's hooks reference documents the `prompt` field of
`UserPromptSubmit` and the non-typed inputs that also trigger it, and past
transcripts show the prompt hook running after background-task notices. In
those transcripts, 68 answered dialogs carried 119 answers (14 of them typed)
beside about 1,400 typed prompts; choices that resolve decisions are often made
there, which is why the dialog is captured.

**Not captured.** Text a person types when declining a tool call: no documented
hook carries it. Input given through a host other than Claude Code. A quote
that exists only there is reported as not found, which is one more reason a
miss is not read as an invention (D1).

**Keeping the capture honest.** The implementation is gated on a live probe of
each kind of input (typed, pasted, typed command, dialog answer, background
notice), and the report compares each host session's recorded entries with the
host's own transcript while that transcript still exists (D7, measure 10). The
transcript format is not a documented interface: it is read only for that
comparison, and an unreadable transcript is counted, not guessed at.

The hooks reach a project when `trace-mcp-init` is re-run there. The hook
version stamp is raised, so `trace-mcp doctor` and `fleet-check` report every
project still carrying the previous hooks.

*Rejected:* one user-level hook. It would record prompts in every directory on
the machine, including projects that do not use TRACE.

*Rejected:* the host transcript as the source of the person's words. It holds
every kind of input, but its format is undocumented and can change without
notice, and the host deletes transcripts on its own schedule.

### D4. Started and stopped explicitly, with reminders; fails open

- `trace-mcp shadow start` begins the period for every project and records the
  start time in `~/.trace/shadow/STATE.json`. `trace-mcp shadow stop` ends it.
  Nothing else ends the period: no expiry date, no automatic stop.
- `trace-mcp shadow status` prints the start date, days elapsed and per-project
  counts. `trace-mcp shadow exclude <key>` and `include <key>` leave one project
  out or bring it back.
- **Reminders.** From day 30 on, the `SessionStart` hook prints one line in
  every enrolled project, at most once per day across the machine: the period's
  start date and day count, and an instruction to tell the user to run the
  report and then `trace-mcp shadow stop`. The hook keeps its own bookkeeping in
  a separate file and never writes `STATE.json`, so a reminder cannot undo a
  stop.
- Every shadow step runs after the live step it shadows, inside its own
  exception boundary and under a time limit. A shadow failure never changes or
  delays-to-failure a live result; it is appended to
  `~/.trace/shadow/<project-key>/errors.jsonl`.
- An unreadable `STATE.json` means "not running" to the server and the hooks,
  and is reported by `trace-mcp shadow status`.
- Servers launched from a pinned or containerized TRACE build are outside the
  period unless that build is moved on purpose.

### D5. Shadow extraction at session end: nearest 50, proposals kept as proposals

After the live extraction finishes and its lock is released, the shadow
extractor runs on the same session with two differences:

1. **Nearest-50 deduplication.** Instead of the whole store, the prompt carries
   the 50 stored learnings most similar to the session's events, ranked with
   the store's own embedding backend (the most recent 50 when no embedding
   backend is available; all of them when the store holds 50 or fewer). The
   code-side duplicate check — token overlap (Jaccard) at
   `TRACE_DEDUP_THRESHOLD`, default 0.85 — still runs against the whole shadow
   store.
2. **Proposal wording.** One added instruction: a learning whose source is a
   decision in the `proposed` state is worded as a proposal, not as a rule or
   an instruction.

Everything else in the prompt is shared with the live extractor through one
prompt builder, and both arms make their model call through one function, so
the arms cannot drift apart and there is one registered egress site. The shadow
call is attested separately (`purpose="shadow-extraction"`). Under
`TRACE_LOCAL_ONLY`, or with cloud extraction off, the shadow step uses the
rule-based extractor and the record says so.

Shadow learnings go to a separate shadow store under `~/.trace/shadow/stores/`.
It is seeded once per project, at the first shadow run, as a copy of the live
store: seeded entries keep their live ids (they are the same learnings), and
learnings the shadow arm creates take ids from the shadow store's own counter
in a separate `slrn_` namespace. The live store's counter is never read for
writing, so no live id is consumed or reused (INV-12). Without the seed,
nearest-50 would deduplicate against an empty store and the comparison would
measure the seed, not the method.

For both calls the step records prompt tokens, completion tokens and
wall-clock seconds from the provider's usage fields, the size of the
existing-learnings block, each new learning's marks as of extraction, and each
new learning's similarity to the closest learning already in that arm's store.
Recording the live call's usage reads a response the live path already
receives; it changes nothing a session sees.

**Duplicates are measured, not cut.** Token overlap at 0.85 removes only
near-copies. Across 14 stores, 110 of 2,695 learnings had an earlier learning
at embedding cosine 0.95 or above, and none of those pairs reached the overlap
bar; five such pairs read by hand were all restatements. A similarity cutoff
was not adopted as a second drop. Of five pairs read at 0.90–0.95, one was
different content; of five at 0.85–0.90, four were, one of them a correction
replacing the earlier learning. The stores also hold vectors from three
embedding models, so one cutoff would mean different things in different
stores. Dropping on similarity would discard information no person reviewed.
The period records the similarity instead, so the report can show per arm how
many new learnings restate an existing one (measure 9) — the direct test of
whether sending 50 learnings instead of the whole store lets more restatements
through. The hand reading is five pairs per band from one store, and is
stated as that.

**Basis for N=50.** A local proxy over 14 knowledge stores (2,261 learnings
written at past session ends) ranked each store's earlier learnings by
similarity to the mean embedding of the learnings a session produced, and asked
whether each new learning's closest earlier learning was in the top N:

| N | 10 | 20 | 30 | 50 | 100 |
|---|---|---|---|---|---|
| Included | 1617 | 1874 | 1984 | 2106 | 2211 |
| of 2261 | 72% | 83% | 88% | 93% | 98% |

The existing-learnings block was the larger part of the input: over 763 past
session ends, its median was 62,365 characters (90th percentile 145,871),
against 3,493 for the events; at N=50 it was 17,940. N=50 removed a median 64%
of input characters per call. The proxy is optimistic (the real ranking query
is the events, not the session's own output). The period measures the effect
directly (measure 3).

*Rejected:* N=20. Its proxy coverage was 83%, and its earlier recommendation
rested on round numbers rather than a measurement.

### D6. Shadow recall

At each recall (`trace_start_session` with recall, the decision-proposal
warning, `trace_learn_recall`), the live result is returned unchanged. The step
appends to `~/.trace/shadow/<project-key>/recall.jsonl`: the recalled ids, each
item's marks, and the length in characters of the rendered items without marks
and with marks plus the D9 legend.

### D7. Report and review sheet: read-only CLI, no MCP tool

`trace-mcp shadow report [--from DATE] [--to DATE] [--project KEY] [--json]
[--csv PATH]` reads the shadow directory and the session records, writes
nothing except the optional CSV, and adds no MCP tool. Counts are printed with
their denominators, per project and in total:

1. Learnings by mark as of extraction, per arm.
2. Extraction cost per session end, per arm: prompt tokens, completion tokens,
   seconds.
3. Learnings made by one arm and not the other. Within one session end, a live
   and a shadow learning are the same when they share a source event, or failing
   that when their token overlap is at least 0.5.
4. Recall: calls, items by mark, characters with and without marks.
5. Quote check: verified, not found, absent, absence marker, too short,
   unchecked; verified quotes split by where they were found (the turn, or
   only older words); misses split by the share of the quote's words found in
   one entry (at least 90%, 50–90%, under 50%).
6. Human resolutions by resolution-quote status (verified, not found, no
   quote, absence marker, too short, unchecked, unknown), cross-tabulated with
   the quote check on the same decision; verified resolution quotes split by
   where they were found; and, where the resolution time is
   known, how many were resolved with no entry of the person's words between
   the proposal and the resolution.
7. Corrections logged in the period that reference an earlier event or
   learning, by the mark of the corrected item.
8. Shadow step failures, by step.
9. New learnings by similarity to the closest existing learning (0.95 and
   above, 0.90–0.95, 0.85–0.90, below 0.85, no vector), per arm.
10. Capture: entries recorded by kind; projects with no words recorded;
    recorded prompts that begin with a tag other than pasted text; and, for
    host sessions whose transcript still exists, typed prompts and dialog
    answers in the transcript beside those recorded.

The review sheet (`--csv`) has one row per item worth a person's judgment: a
learning whose source no person accepted, a quote not found, a human resolution
with no quote, with a quote not found, or with a quote found only in older
words, and a learning made by only one arm. It has empty columns for the
reviewer's name, "mark correct?", "useful?" and "reworded as a directive?". The
last column is how the proposal-wording instruction (D5) is judged; the period
does not detect directive wording automatically.

### D8. No policy file during the period

Rules on top of the marks are not designed here. Any later policy may add marks
or grouping and may not remove or hide an entry for lack of approval.

### D9. After the period: marks plus their meaning

If marks become visible to sessions, recall shows each item's tags plus one
legend line giving their meaning. The D6 character counts use this rendering.
The period cannot show whether sessions work better with marks; that needs a
controlled run.

## What the period cannot show

Whether a session behaves better when it is shown the marks: no session sees
them. The period shows what would have been stored and shown, and at what cost,
on one person's projects.

## Constraints

- Registered invariants hold: INV-1 (no new session-write path), INV-2 (no
  shadow step modifies a session record), INV-5 (the shadow call is attested;
  one registered call site serves both arms), INV-12 (separate `slrn_` id
  namespace; the live counter is never consumed), INV-14 (the state file is a
  durable publisher), INV-4/INV-9 (shadow data keyed by canonical project key),
  INV-11 (a freshly initialized project carries every shipped hook, each
  registered under one host event).
- The period adds no MCP tool, lengthens no tool description, and adds no field
  the calling model must write. The core change that precedes it adds one
  optional argument and one sentence to `trace_resolve_decision`.
- Shadow records are Pydantic v2 models; the state file is written atomically
  and the logs are single-line appends; pyright clean.
- Tests run on session fixtures with made-up project names; the model call is
  the only mock.

## Review tier

Tier 3 before merge: the change touches session-end extraction, the egress
call site, and the hooks installed in every project.
