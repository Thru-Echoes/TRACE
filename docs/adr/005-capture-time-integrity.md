# ADR 005: Capture-time integrity via hash-chained, append-only session records

**Status:** proposed · **Date:** 2026-07-02

## Context

TRACE sessions are stored as mutable JSON documents (one file per session
under `~/.trace/sessions/`). Any process or user with file access can edit
recorded history after the fact without detection. Two consequences:

1. The protocol's core integrity rule ("never fabricate, falsify, or
   retroactively alter TRACE data; closed sessions are never retro-refiled")
   is enforced by convention only. Nothing in the storage layer detects a
   violation.
2. Downstream evidence exports can hash session files at bundle time, but
   that proves integrity only from bundling forward. For audit and
   compliance consumers, the difference between "logs" and "evidence" is
   integrity that starts at capture.

A design constraint specific to TRACE: the write path is not purely
append-only today. `trace_resolve_decision` updates the disposition of a
previously written decision event, and `trace_end_session` finalizes the
session. A naive whole-file hash would break on these legitimate updates, so
the design must first make every mutation an append.

## Decision

### 1. Append-only journal

Persist each session as an append-only JSONL journal
(`~/.trace/sessions/<session_id>.jsonl`), one record per line. Every write
is an append. Operations that previously mutated prior state become new
records that reference their target:

- Decision resolution: a `resolution` record carrying `target_event_id`,
  disposition, resolver identity, and revision note. The decision record
  itself is never rewritten.
- Session end: an `end` record. After it, the server refuses further
  appends for that session.

The existing session JSON document becomes a derived, materialized view
rebuilt deterministically from the journal, kept for backward-compatible
readers (exports, `trace_get_events`, external scripts). The journal is the
source of truth; the view is disposable and regenerable.

### 2. Hash chain

Each journal record is wrapped in an envelope:

```json
{"seq": 42, "ts": "...", "kind": "event", "body": {...},
 "prev_hash": "<hex>", "hash": "<hex>"}
```

`hash = SHA-256(canonical_json({seq, ts, kind, body, prev_hash}))`.

Canonicalization is pinned to RFC 8785 (JCS): UTF-8, lexicographically
sorted keys, no insignificant whitespace, fixed number serialization. Golden
fixtures in the test suite guard against canonicalization drift across
Python versions.

The genesis record (`seq = 0`) binds the session header: session id,
project, start timestamp, and spec version.

### 3. Heads registry and cross-session chaining

A session journal alone can be rewritten wholesale (regenerate every hash
from a forged genesis). Two mitigations:

- **Heads registry:** `~/.trace/integrity/heads.json`, updated atomically
  on every append, maps `session_id` to `(seq, head_hash, updated_at)`. A
  forger must now rewrite two independent surfaces consistently.
- **Cross-session chaining:** each new session's genesis record includes the
  head hash of the previous session in the same project
  (`prev_session_head`). Rewriting one historical session then requires
  rewriting every subsequent session in the project plus the registry.

### 4. MCP tool surface

- **No signature changes** to any existing `trace_*` tool. The envelope is
  applied inside the server; callers and hosts see identical behavior.
  `trace_resolve_decision` keeps its current interface and appends a
  resolution record; the materialized view still presents the decision as
  resolved, so existing consumers are unaffected.
- **Verification ships CLI-first:** `trace-mcp verify <session_id>` and
  `trace-mcp verify --all`, reporting the first broken link (seq, expected
  vs found). Per ADR 004, every registered MCP tool enlarges the cached
  prompt prefix, and verification is an operator task, not a host-model
  task. An optional `trace_verify_session` MCP tool is deferred until a
  host-side need is demonstrated.

### 5. Failure semantics: fail closed

On session open (and before every append), the server verifies the journal
tail against the heads registry. On mismatch it raises and refuses to
append. There is no warn-and-continue mode: a broken chain that silently
degrades is worse than a stopped server, because the whole point is that a
missed invariant must be visible to the caller.

Recovery is explicit and leaves scar tissue: a documented flow appends a
`discontinuity` record (operator identity, reason, old head, new genesis)
and starts a new chain segment. Verification always reports discontinuities;
they cannot be hidden.

Concurrent writers: single-writer lock per session journal; lock contention
fails closed with a distinct error rather than interleaving appends.

### 6. Legacy sessions

Existing mutable-JSON sessions (several hundred) cannot gain retroactive
integrity, and the protocol forbids pretending otherwise. A one-time
attestation sweep hashes each legacy file and records it in the heads
registry with an `attested_at` date. Labeling is honest and permanent:
legacy sessions are "attested from <date>"; new sessions are "hash-chained
from capture". No stronger claim is ever made for legacy data.

### 7. Specification impact

Normative additions land in `docs/specification.md` for v0.5: record
envelope, canonicalization rules, chain construction, verification
procedure, discontinuity records, and the attestation labeling rule. Tool
names and the `trace_` prefix are unchanged.

### 8. Export integration

Bundle/evidence exports include the relevant journal segment plus the chain
head, so an exported bundle proves integrity from capture time rather than
from bundle time.

## Threat model (honest bounds)

Detects:

- Post-hoc edit, deletion, insertion, or reordering of any record by an
  actor who does not also consistently rewrite the entire downstream chain,
  the heads registry, and (for project-chained sessions) every subsequent
  session.

Does not detect:

- Fabrication at capture time by an actor with full control of the machine
  (a forged history chained correctly from genesis).
- A complete rewrite of journal plus registry plus downstream sessions.
- Author identity: v0.5 has no signatures; the chain proves sequence
  integrity, not who wrote a record.

Non-goals for v0.5, with anchor points deliberately left in the design
(the head hash is the natural anchor value): RFC 3161 timestamps,
transparency-log anchoring, per-record signatures, encryption at rest.

## Alternatives considered

- **Whole-file hash in a sidecar over the existing mutable JSON.** Rejected:
  legitimate updates (decision resolution) force re-hashing windows in which
  tampering is indistinguishable from normal writes, and there is no
  per-record granularity for localizing a break.
- **Git-backed storage (commit per event).** Rejected as the primary
  mechanism: local history rewrite is a first-class git feature, it adds a
  runtime dependency, and per-event commits are heavyweight. Pushing chain
  heads to a remote remains a cheap future anchoring option.
- **SQLite with WAL.** Viable and revisitable, but a larger migration, and
  plain-text JSONL keeps sessions greppable and transparent, which is part
  of the project's trust posture.

## Consequences and implementation phases

- **P1:** journal write path + hash chain + materialized JSON view +
  `verify` CLI. E2E tests with real session data: record, tamper each field
  class (body, seq, ts, prev_hash, deletion, reordering, truncation),
  assert detection and correct first-break reporting. Pydantic v2 envelope
  models; pyright clean.
- **P2:** heads registry, cross-session chaining, legacy attestation sweep.
- **P3:** export/bundle integration; optional MCP verify tool if a host-side
  need appears.
- **Invariants:** add to `docs/INVARIANTS.md`: all session persistence goes
  through the append-only journal API; no code path writes session files
  directly. Guarded by an enumeration test that fails when a new write site
  bypasses the journal.
- **Performance:** one SHA-256 per record; negligible against MCP call
  latency.
- **Risks:** canonicalization drift (pinned JCS implementation + golden
  fixtures); registry corruption (atomic write via temp-file rename;
  registry is itself rebuildable from journal tails, with the loss of only
  its second-surface property for the rebuild window).
