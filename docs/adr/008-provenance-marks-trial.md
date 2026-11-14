# ADR 008: Provenance marks

**Status:** Accepted; revised 2026-11-13 (marks are shown to sessions and in exports; the
nearest-50 comparison moved from session end to an offline replay); D10 added 2026-11-13
(a recalled learning's mark describes its source; what a person rejected or replaced steps
back from recall)
**Date:** 2026-11-12
**Context:** TRACE stores what an AI session writes without recording whether a
person stands behind it, and later sessions read it back as project knowledge.
The learn extractor sends every event of a session to the model, including
decisions still in the `proposed` state, and the learnings it writes carry no
trace of that disposition. Recall printed only the category. One observed
failure: a learning extracted from an unresolved AI proposal, reworded by the
extraction model as a directive, was shown to a later session under "Related
learnings (review before proceeding)" within an hour of being written, with
nothing indicating it was an unresolved proposal.

## Summary

Add *marks* that say where an event or a learning came from, computed from the
record and never stored, and show them wherever TRACE shows a recalled learning
or exports a record. A learning is context for the session that reads it, with
the mark of the event it came from; it is not an instruction. That mark
describes the source event, not the learning's wording, which is the
extractor's (D10).

Three things run beside that:

- **Capture of the person's words.** While a capture period is on, two hooks
  save the person's typed prompts and question-dialog answers on the machine, so
  a session's quote of the person can be checked against what the person typed.
- **A read-only report and review sheet** over the session records, the stored
  learnings, the recall records and the captured words.
- **An offline replay** that answers one cost question on past sessions:
  whether sending the 50 stored learnings nearest to a session, instead of the
  whole store, changes what extraction produces.

The marks depend on one core change that shipped first and stands on its own:
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
| Decision `accepted`, resolved by a human | `[accepted]` |
| Decision `revised`, resolved by a human | `[revised]` |
| Decision `rejected`, resolved by a human | `[rejected]` |
| Annotation by an AI actor | `[ai-note]` |
| Any other event | none |

A learning whose source event has no base mark shows `[ai-record]` or
`[person-note]` in its place (D10); the event itself keeps none.

Add-on marks:

| Condition | Add-on |
|---|---|
| `conversation_snippet` found in the person's words (D3) | `[quote-verified]` |
| Snippet present, checkable, and not found | `[quote-not-found]` |
| Human resolution whose `resolution_snippet` is found in the person's words | `[resolution-verified]` |
| Human resolution whose `resolution_snippet` is checkable and not found | `[resolution-not-found]` |
| Human resolution recorded with no `resolution_snippet` | `[resolution-unquoted]` |

A learning takes the marks of its source event, read from the session record
each time the learning is recalled. A learning with no recorded source, or
whose source session or event cannot be read, is kept and marked
`[source-unknown]`. The extractor records a source event id only when it names
an event of the session being extracted; an id the model omitted, malformed or
invented is not recorded.

A decision a person resolved as `revised` is not `[accepted]`: the person
changed it, and where the change was recorded only in the revision note the
description is still the proposal as first written. In one project's store, 6
of the 57 learnings that had shown `[accepted]` came from such a decision, and
at least one states the plan as it stood before the person changed it.

A contribution, a state change and a tool call have no base mark as events. A
learning extracted from one is not shown bare (D10): it shows `[ai-record]`,
or `[person-note]` when the source was recorded in a person's name.

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
and harder to count mechanically. The meaning of each tag in use is given once
per block (D9).

### D2. Marks are never stored; the record holds what they are computed from

A mark is not written onto an event or a learning.

- **A stored mark goes stale.** A decision is `[proposed]` when logged and
  `[accepted]` once resolved, possibly in a later session: resolving a proposed
  decision is the one change permitted after a session completes (INV-2).
- **The record already holds the inputs.** Disposition, resolver, actor, the
  proposal time and — from wire version 0.5.2 — the resolution time and the
  resolution quote are fields of the event. A stored label would be a second
  copy that can disagree.
- **Showing a mark changes no session record.**

`get_event_marks` is called whenever a mark is needed, against the current
record, and takes an optional *as of* time. With both times in the record, a
decision is `[proposed]` from its event timestamp and takes its resolved mark
from `resolved_at`, so the report can state both what a learning's source is
now and what it was when the learning was written. A decision resolved before
`resolved_at` existed has no recoverable resolution time: an *as of* question
about it is answered with the current mark and flagged as unknown, and its
resolution quote status is `unknown`. Nothing is backfilled.

Where a mark is recorded in a log (the recall record, D6; a replay record,
D5), it is recorded with its time, as an observation of what the record said
then.

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

### D4. The capture period: started and stopped explicitly; fails open

The capture period governs the two things that are only worth their cost while
someone is going to read the report: the words the hooks save (D3) and the
recall record (D6). Marks at recall and in exports do not depend on it.

- `trace-mcp shadow start` begins the period for every project and records the
  start time in `~/.trace/shadow/STATE.json`. `trace-mcp shadow stop` ends it.
  Nothing else ends the period: no expiry date, no automatic stop.
- `trace-mcp shadow status` prints the start date, days elapsed and per-project
  counts. `trace-mcp shadow exclude <key>` and `include <key>` leave one project
  out or bring it back.
- **Reminder.** From day 30 on, the `SessionStart` hook prints one line in every
  enrolled project, at most once per day across the machine: that the person's
  typed words have been saved since the start date, and an instruction to tell
  the user to run the report. The hook keeps its own bookkeeping in a separate
  file and never writes `STATE.json`, so a reminder cannot undo a stop.
- Recording a recall runs after the recall, inside its own exception boundary
  and under a time limit. A failure never changes or fails a recall; it is
  appended to `~/.trace/shadow/<project-key>/errors.jsonl`.
- An unreadable `STATE.json` means "not running" to the server and the hooks,
  and is reported by `trace-mcp shadow status`.

### D5. Extraction: one call at session end; nearest-50 tested by offline replay

**At session end** the extractor makes one model call, as before, with two
changes:

1. **Proposal wording.** Every extraction prompt ends with one instruction: a
   learning whose source is a decision in the `proposed` state is worded as a
   proposal, not as a rule or an instruction.
2. **Source ids.** A source event id is recorded only when it names an event of
   the session (D1).

**The nearest-50 question** is answered off-session. Extraction sends every
stored learning with a session's events so the model can avoid restating them.
Over 763 past session ends that block had a median of 62,365 characters (90th
percentile 145,871), against 3,493 for the events. `trace-mcp shadow replay
--project KEY [--limit N] [--from D] [--to D] [--session ID]` extracts each
chosen ended session twice, on the same frozen events and the same snapshot of
the stored learnings as they stood before that session was extracted:

- the **whole** arm sends every learning of the snapshot (the method in use);
- the **nearest** arm sends the 50 learnings most similar to the session's
  events, ranked with the store's own embedding backend (the most recent 50
  when no embedding backend is available; all of them when the snapshot holds
  50 or fewer).

Both arms build their prompt with the extractor's prompt builder, call the
model through its one registered egress site under their own attested purposes
(`replay-extraction-whole`, `replay-extraction-nearest`), and apply the
extractor's code-side duplicate check (token overlap at `TRACE_DEDUP_THRESHOLD`,
default 0.85) against the whole snapshot. Neither arm writes to a knowledge
store, so no learning id is consumed. Each replay appends one record to
`~/.trace/shadow/<project-key>/replay.jsonl`: both arms' prompt tokens,
completion tokens and seconds, the size of the existing-learnings block, what
each arm would have added (its text, its marks as of the session's end, its
similarity to the closest stored learning), and how the nearest arm's learnings
were chosen. A session is replayed once; a replay that fails is recorded in
`errors.jsonl` and the run goes on. Replay needs cloud extraction to be on for
the project and says so when it is not.

The snapshot is the store as it is now, less the learnings that came from the
session and those created at or after its end. A learning forgotten since then
is not restored, so the snapshot is a close reconstruction, not an archive.

**Duplicates are measured, not cut.** Token overlap at 0.85 removes only
near-copies. Across 14 stores, 110 of 2,695 learnings had an earlier learning
at embedding cosine 0.95 or above, and none of those pairs reached the overlap
bar; five such pairs read by hand were all restatements. A similarity cutoff
was not adopted as a second drop. Of five pairs read at 0.90–0.95, one was
different content; of five at 0.85–0.90, four were, one of them a correction
replacing the earlier learning. The stores also hold vectors from three
embedding models, so one cutoff would mean different things in different
stores. Dropping on similarity would discard information no person reviewed.
The replay records the similarity instead, so the report can show per arm how
many new learnings restate an existing one: the direct test of whether sending
50 learnings instead of the whole store lets more restatements through. The
hand reading is five pairs per band from one store, and is stated as that.

**Basis for N=50.** A local proxy over 14 knowledge stores (2,261 learnings
written at past session ends) ranked each store's earlier learnings by
similarity to the mean embedding of the learnings a session produced, and asked
whether each new learning's closest earlier learning was in the top N:

| N | 10 | 20 | 30 | 50 | 100 |
|---|---|---|---|---|---|
| Included | 1617 | 1874 | 1984 | 2106 | 2211 |
| of 2261 | 72% | 83% | 88% | 93% | 98% |

At N=50 the block's median was 17,940 characters, a median 64% fewer input
characters per call. The proxy is optimistic (the real ranking query is the
events, not the session's own output). The replay measures the effect directly.

*Rejected:* N=20. Its proxy coverage was 83%, and its earlier recommendation
rested on round numbers rather than a measurement.

*Rejected:* running the nearest-50 extraction as a second model call at every
session end for a month, writing to a separate store. It made every session
end wait for a call whose only purpose was a comparison, the two arms' stores
diverged from the first session on so later sessions were not compared on
equal inputs, and it needed a second store with its own id space. A replay
gives both arms identical inputs and costs nothing until someone asks.

### D6. Recall shows marks, and is recorded while a capture period runs

At each recall (`trace_start_session` with recall, the decision-proposal block,
`trace_learn_recall`) every result carries the mark of its source event (D9, D10).
While a capture period runs for the project, the recall also appends to
`~/.trace/shadow/<project-key>/recall.jsonl`: the recalled ids, every tag of
each item's source event, and the length in characters of the rendered items
as bare text and as shown (with ids, source and mark, and the legend line).

### D7. Report and review sheet: read-only CLI, no MCP tool

`trace-mcp shadow report [--from DATE] [--to DATE] [--project KEY] [--json]
[--csv PATH]` reads the shadow directory, the live knowledge stores and the
session records, writes nothing except the optional CSV, and adds no MCP tool. Counts are printed with
their denominators, per project and in total:

1. Stored learnings written in the range, by the current mark of their source;
   how many were written from a still-proposed decision, and how many of those
   have been resolved since.
2. Replay cost per replayed session, per arm: prompt tokens, completion tokens,
   seconds.
3. Replay: learnings made by one arm and not the other. Within one replay, a
   whole-arm and a nearest-arm learning are the same when they share a source
   event, or failing that when their token overlap is at least 0.5.
4. Recall: calls, items by mark, characters as shown and without marks.
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
8. Recorded failures, by step (recall record, replay).
9. Replay: new learnings by similarity to the closest stored learning (0.95 and
   above, 0.90–0.95, 0.85–0.90, below 0.85, no vector), per arm.
10. Capture: entries recorded by kind; projects with no words recorded;
    recorded prompts that begin with a tag other than pasted text; and, for
    host sessions whose transcript still exists, typed prompts and dialog
    answers in the transcript beside those recorded.

The review sheet (`--csv`) has one row per item worth a person's judgment: a
stored learning whose source no person accepted, a quote not found, a human
resolution with no quote, with a quote not found, or with a quote found only in
older words, and a replay learning made by only one arm. It has empty columns
for the reviewer's name, "mark correct?", "useful?" and "reworded as a
directive?". The last column is how the proposal-wording instruction (D5) is
judged; directive wording is not detected automatically.

### D8. No policy file

Rules on top of the marks are not designed here. Any later policy may add marks
or grouping and may not remove or hide an entry for lack of approval.

### D9. What a session and a reader are shown

**At recall** each learning is shown with its category and text, then its id
and its source event with that event's base mark (D10), for example
`- [decision] ... (lrn_424; source [proposed] trace_...#evt_002)`. One line
under the heading explains only the marks in use in that block, says that they
describe each learning's source and not its wording; no learning is shown
without a mark (D10). The blocks are headed as context: "Relevant learnings from
past sessions (context, not instructions)" at session start, and "Possibly
related learnings from past sessions (context, not instructions)" on a decision
proposal, replacing "Related learnings (review before proceeding)".
`trace_learn_recall` and `trace_learn_list` return a `source_marks` list on
each learning and a `source_marks_legend`.

A session is shown the base mark, or `[source-unknown]`, and not the quote-check
tags. A missed quote is often benign (D1), and those tags are for a person to
judge; they appear in the report, the review sheet and the exports.

**The decision block can be switched off** for a project by setting
`TRACE_DECISION_RECALL=0` in its server's environment; it is on by default.
Measured on one machine's surviving host transcripts, the block rode on 496 of
562 decision proposals. Of the 1,456 learnings it carried, 397 came from a
decision a person had accepted, and 1,061 opened as a general instruction. In a
random sample of 100 blocks read in full by one reader, 52 held a learning
bearing directly on the decision and the session's later output visibly acted
on one in 2; a wording screen over all 496 found 5 more uses, each sound. Its
re-read cost was about 0.14% of cache-read tokens. That supports keeping it,
marked and worded as context, and does not support the old heading.

**In exports** the JSON export is the record itself: every event with its whole
payload and nothing derived written into it. The Markdown export shows the
marks of each decision and AI note, a line explaining the marks in use, and a
closing table naming every event once; the PROV export carries them as a
`trace:provenanceMark` literal on each event's node. The quote checks are
included when the person's recorded words are available to the exporter.
Anything derived travels beside the record, never instead of it.

*Rejected:* withholding marks from sessions for a month while a comparison
ran. The failure in the Context had already happened, and a period in which no
session sees a mark cannot show whether marks help a session.

*Rejected:* turning the decision block off by default. The measurement above
found it cheap, mostly on topic and occasionally acted on to good effect; the
harm was in the unmarked items and the heading, which marks and the new heading
address.

### D10. A recalled learning's mark describes its source; what a person rejected or replaced steps back

The extractor rewords what it reads, so a mark inherited from the source event
can say only who stood behind that event. Observed: a person decided that one
audit's codebook needed a third category; the extractor wrote "when an audit
taxonomy is missing a concept, add it as its own unit", and later sessions
received it as guidance. With `[accepted]` first on the line, that widened rule
would read as one a person approved.

**Shown as a fact about the source.** The mark follows the text, beside the
learning's id and its source event's id: `(lrn_424; source [accepted]
trace_...#evt_002)`. The legend opens "Source marks describe the event each
learning was drawn from, not its wording, which is the extractor's". The ids
let a session look up the source and let later links name a learning.

**The extractor stays within the source.** The extraction request now says who
proposed and who resolved each decision and who wrote each note, asks for "a
specific account of what was learned" rather than an "actionable" insight, and
ends with an instruction: say what happened and where; word a learning as a
general rule only when a person accepted a decision that states that rule
itself; report a rule or judgement from any other source as that source's
claim; state found or measured facts plainly.

Compared on 12 recorded sessions of one project (a seeded sample of its 14
recent ended sessions with five or more events), two runs per request with the
deployed extraction model and no existing-learnings block: the previous request
produced 151 learnings and this one 125 (6.3 and 5.2 per run), fewer in 10 of
the 12 sessions; the paired difference is -1.1 learnings per session (95%
interval -2.5 to +0.3). The learnings are longer (351 and 447 characters on
average), so the text written is about the same (53,040 and 55,856
characters). A wording screen, checked by reading, found normative wording
that is not reported as someone's claim and comes from a source no person
accepted in 49 of the 151 and in 8 of the 125; those 8 read as accounts of
what was found. One 17-event session produced more learnings under this
request (13 and 12 against 7 and 8). A further sentence asking for
selectivity made no clear difference (-0.4 per session, interval -1.0 to
+0.3) and was not added.

**A new rule is a decision proposal.** The protocol block installed into each
project asks a session to propose a new rule for future work as a decision,
and to name the existing decision when restating one. It is an authoring
convention; nothing enforces it.

**What a person rejected or replaced steps back.** A learning whose source
decision a person rejected, or replaced by accepting a decision that revises
it, is left out of recall. Replacements are found in the source session by
`revises_event_id`, following the chain to its last revision a person
accepted or revised in turn; a revision an AI resolved, or a person rejected,
replaces nothing.
When a stored learning comes from the replacement, the best-ranked one that can
be shown is returned in the old one's place with `replaces` naming the old id.
If the old one matched the query, the replacement is shown as matched context
(`replaces_matched`), in the decision block too, whatever its own similarity,
which is reported unchanged; on the explicit-threshold path it is returned even
below the threshold, with its own score. A replacement the ranker did not score
has no score to show and is not returned; the notice then names the replacing
decision. A learning left out stays in the
store and in `trace_learn_list`, gains no exposure, and is named in the
recall's notice and in `trace_learn_recall`'s `stepped_back`; `trace_learn_list`
marks it `stepped_back` too. On a decision proposal, a matched learning that
was left out with no replacement shown is named in a closing line of the block
(`Left out, kept in the store: lrn_N (a person rejected its source
trace_...#evt_N)`): the rejected text is not shown as guidance, but a proposal
resembling something a person rejected says so. Measured on one project's
store of 407 learnings: 8 step back, 3 whose source a person rejected and 5
whose source a person replaced (one of those five was also rejected); 4 of the
5 have a stored learning from the replacement. All 22 revision links in the
store's source sessions name an event of the same session.

**No learning is shown without a mark.** A learning whose source event has no
base mark shows who wrote that record: `[ai-record]` for an AI's contribution,
a tool call or a state change, and `[person-note]` for an annotation recorded
with a person as its actor. In a side-by-side test of two reading models, the
weaker one followed wrong learnings shown with no mark as often as ones marked
`[accepted]`. In the store above, 100 learnings take `[ai-record]` and 5
`[person-note]`; the report and the replay count a learning under the same
mark, and the review sheet lists learnings from `[revised]` and `[ai-record]`
sources among those no person accepted. These two marks are shown on learnings only; an event itself
keeps no mark of this kind, and an export shows its type and actor beside it.

**A correction is a pointer.** A correction that names the source event, in
the source session, is shown as "source corrected, see trace_...#evt_N". It
does not step the learning back: a correction records no replacement a person
approved, and the correction is extracted as a learning of its own.

**Exposure alone keeps nothing evergreen.** A learning recalled three or more
times used to keep a decay multiplier of at least 0.8 whoever stood behind it;
the floor now holds only when a person accepted its source and it does not step
back. A source a person revised (`[revised]`) does not hold it either. On the
store above the floor held up 33 learnings, 3 of them with a source a person
accepted. On the default ranking, decay breaks ties and scales
the score shown; it filters only on the explicit-threshold path. Decay age is
still measured from the last time a learning surfaced.

**The scratchpad is a delivery path too.** Each line drawn from an event names
the event and shows its mark, a decision's bracket is its mark (`[accepted]`
only when a person accepted it; an AI's resolution also gives its disposition,
so an AI-rejected decision is not read as adopted), each entry stays on one
line so a line break in recorded text cannot start a peer entry or heading (the
same holds for recalled learnings), the summary is headed "AI-written", and the
header names the session it was generated from, when, and the sha256 of the
section. It is not snapshotted: it is regenerated from the session record,
which is kept. INV-18 lists every delivery path and fails on an unclassified
new tool.

*Rejected:* keeping the mark first and changing only the legend. The first
thing on the line would still read as the standing of the wording.

*Rejected:* ranking rejected or replaced learnings lower instead of leaving
them out. The operator decided they step back and stay in history, not appear
as equal guidance; anything still returned near a short limit would appear.

*Rejected:* finding replacements across sessions now. No recorded link does
that today, and scanning every session at each recall is too slow; explicit
supersession links, with who asserted and who approved them, are left for
later.

## What is not known

Whether a session behaves better when it is shown the marks: that needs a
controlled run with the marks on and off. Whether the marks are right: no
person has yet read a sample of marked records and learnings, and they are not
called accurate before that. Whether the decision block improves decisions: the
transcripts show visible use only. Whether the extraction instruction keeps
learnings in scope in other projects and under other models, and whether long
sessions grow the store: the comparison above is 12 sessions of one project.
Whether a learning extracted from a rejected decision held the lesson of the
rejection rather than the rejected proposal: those learnings now step back
either way. Whether wording that describes the source reduces over-obedience
to `[accepted]`: in the same side-by-side test the stronger reading model
followed 8 of 18 wrong learnings marked `[accepted]` against 2 shown unmarked,
and the source-describing form has not been tested.

## Constraints

- Registered invariants hold: INV-1 (no new session-write path), INV-2 (nothing
  here modifies a session record), INV-5 (each replay call is attested; one
  registered call site serves the extractor and both replay arms), INV-12 (the
  replay writes to no knowledge store, so no learning id is consumed), INV-14
  (the state file is a durable publisher), INV-4/INV-9 (shadow data keyed by
  canonical project key),
  INV-11 (a freshly initialized project carries every shipped hook, each
  registered under one host event).
- No MCP tool is added, no tool description is lengthened, and no field is
  added that the calling model must write. The core change that precedes this
  adds one optional argument and one sentence to `trace_resolve_decision`.
- Core imports nothing from the extension: the mark functions and the legend
  are core, the formatters read a `source_marks` list the recall hook supplies, and
  the exporters get the person's words through a hook an extension registers.
- Records are Pydantic v2 models; the state file is written atomically and the
  logs are single-line appends; pyright clean.
- Tests run on session fixtures with made-up project names; the model call is
  the only mock.

## Review tier

Tier 3 before merge: the change touches session-end extraction, the egress
call site, what every session is shown at recall, and the hooks installed in
every project.
