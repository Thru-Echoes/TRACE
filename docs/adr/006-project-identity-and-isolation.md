# ADR 006: Project identity and cross-project isolation

**Status**: proposed
**Date**: 2026-07-17

## Context

TRACE stores every session in one flat directory (`~/.trace/sessions/`), with a
session's project identity held only in a free-text `metadata.project` string.
Segregation between projects is cooperative labeling, not enforcement. On a
real deployment holding 500+ sessions across 30+ project labels — client
work, academic research, and personal projects in one store — this produced
concrete failure modes:

- **Label drift with inconsistent identity semantics.** The same project
  accumulates multiple labels (case variants, pre/post-rename names). Session
  queries compare labels with exact case-sensitive equality, so drifted labels
  read as *distinct* projects — while the knowledge store's filename
  derivation (`sanitize_name` + a case-insensitive filesystem) *merges* some
  of the same pairs, so learnings silently commingle. Storage identity and
  query identity are two inconsistent equivalence relations over the same
  labels.
- **Drift originates at the producers.** Labels are minted by heuristics — a
  CLAUDE.md marker regex, git/directory basenames, env defaults, free-form
  tool parameters — with no normalization or validation anywhere. One
  verified mechanical cause: the adapter hooks' pin-line regex does not
  tolerate markdown bold, so hooks fall back to the git basename while a model
  reading the same file sees the bolded marker's value — deterministically
  producing a drift pair for the same repository.
- **Cross-project bleed paths.** The current-session pointer can be captured
  by any active session regardless of project; `trace_learn_extract` accepts a
  mismatched `(project, session_id)` pair, wiring one project's events into
  another's knowledge store — and sending the second store to a cloud LLM as
  dedup context interleaved with the first project's events, while the egress
  ledger row names only the session's project; auto-created sessions pool all
  projects' extracted learnings in one `auto` store; the decision-audit hook
  reads the globally newest session with no project filter; id-only
  read/export tools are label-blind. A store's save path derives from the
  label embedded *inside* the file rather than the requested label, so a
  mislabeled file silently migrates content on the next load→save, and the
  store lock can guard a different path than the file actually written.
- **Sequencing pressure from ADR 005.** The capture-time-integrity design
  binds the project label into hashed genesis records (ADR 005 §2). If
  hash-chaining lands before identity is repaired, today's drift is
  cryptographically frozen: a later relabel becomes indistinguishable from
  tampering. Identity must land first.
- **Config is machine-global.** Privacy posture (`TRACE_LOCAL_ONLY`,
  `TRACE_LLM_ENABLED`, embedding backend) cannot differ per project, so a
  confidentiality-bound client project cannot be forced local-only while a
  personal project allows cloud calls.

The project's provenance rule — never retroactively alter TRACE data —
constrains the repair itself: bulk-rewriting existing capture records to fix
labels is the same class of alteration the rule forbids, and the session store
doubles as study data for the companion research repository.

## Decision

### 1. Two-layer identity: canonical key + display labels

A new core module `src/trace_mcp/project_identity.py` (stdlib + pydantic only)
provides `canonical_project_key(label)`: NFC-normalize, strip, casefold, fold
separator/invalid-character runs to `-`, collapse repeats, reject empty. Two
mechanically guarded properties make filename identity equal semantic identity
on every filesystem, including case-insensitive APFS:

- idempotence: `key(key(x)) == key(x)`
- `sanitize_name` fixed point: `sanitize_name(key) == key`

`metadata.project` keeps its existing meaning (human display label, schema
unchanged and unconstrained). A new **additive, optional**
`metadata.project_key` carries the canonical key on new sessions
(authoritative-if-present; version-skewed writers preserve it via
`extra="allow"` but do not re-derive it).

### 2. Alias registry

`~/.trace/projects.json`: a versioned Pydantic model (own format version,
independent of `SCHEMA_VERSION`; unknown-major fails closed and is never
rewritten) mapping canonical keys to entries `{display_label, aliases, status,
config, history}`. Every historical drifted label is recorded as an alias —
required forever, because legacy sessions and already-exported PROV artifacts
resolve through this table. Writes are atomic (temp + `os.replace`) under a
fail-closed `O_EXCL` + PID-liveness lock (the session-lock pattern), with an
append-only in-file history. No alias may resolve to two entries. Alias and
enrollment minting is CLI-only; pinned server processes never auto-enroll,
and unpinned processes may auto-enroll unknown labels as *new* keys only —
never as aliases of existing keys.

### 3. Process pin: `TRACE_PROJECT`

One server process per project is already true for every real launch config;
this is upgraded from convention to enforcement. `trace-mcp init` enrolls the
project and writes `TRACE_PROJECT=<key>` into the consumer's `.mcp.json` env
block (and a `.claude/trace.project` pin file for hooks). Precedence:
`TRACE_PROJECT` (hard pin) > `TRACE_DEFAULT_PROJECT` (retained, soft default
for auto-created sessions) > the reserved `auto` sentinel. Unpinned processes
keep legacy behavior with loud warnings and `pinned:false` in health output;
`TRACE_REQUIRE_PIN=1` (default off until the fleet is swept) fails closed.
Wrong-pin risk is mitigated by init-derived pins, a bootstrap echo of the
bound identity on every session start, and a startup/health cross-check of
the pin against repository heuristics.

### 4. Enforcement points

- **Pointer adoption**: promoting an explicit `session_id` to the current
  session requires the session's resolved key to equal the pin.
- **(project, session) coherence**: a single core validator
  `validate_project_session(storage, project, session_id)` must run before
  any pair touches a store — mandatory for `trace_learn_extract` and the
  extraction hook. Registered as an invariant with an AST enumeration guard
  (the egress-attestation guard pattern), spanning core and the extension
  without imports.
- **Cross-project reads**: id-only read/export tools on a pinned process
  hard-deny foreign sessions by default. Escape hatch:
  `TRACE_ALLOW_CROSS_PROJECT_READS=1`, with responses stamped
  `cross_project:true` and both keys named. Operator-level cross-project
  auditing moves to the CLI, whose output is dual-key stamped.
- **Tool parameters**: `project` on `trace_start_session` and all
  `trace_learn_*` tools becomes optional; under a pin, `None` resolves to the
  pin and any supplied label must resolve to it or the call errors naming
  both keys. Every project parameter passes registry resolution at tool
  entry (invariant-guarded).
- **Hooks**: all four adapter hooks share one byte-identical detection block
  (pin file first, then a bold-tolerant CLAUDE.md regex, then git/dirname),
  match sessions by canonical key, and emit a version stamp so stale fleet
  copies self-identify. The decision-audit hook gains the same detection and
  a project filter.
- **Honest orientation**: `session_brief` becomes a bounded per-project scan
  (newest-first, read ceiling, canonical-key matching) that reports window
  exhaustion instead of falsely asserting that no prior sessions exist.

### 5. Knowledge and embeddings containment

A knowledge store, its `.embeddings.npy` sidecar (a write-only derived
artifact — regenerated, never migrated), and its lock form one unit keyed by
the canonical key. `load_store` fails closed when the in-file label disagrees
with the requested key (closing silent relabel-on-save); `save_store` derives
its path from the requested key; the lock keys on the same path it guards.
The store lock is rewritten on the core dependency-free fail-closed pattern
(raise on timeout; the optional `filelock` dependency and all warn-and-proceed
branches are removed). Cloud payloads become structurally single-project:
extraction, LLM matching, and embedding batches can never interleave two
projects, and all three egress-attestation sites gain a `project_key` column
(two are attributable for the first time). The egress ledger stays one global
append-only file; historical rows are never rewritten.

### 6. Per-project config: restrict-only ratchet

Registry entries carry `{local_only, llm_enabled, embedding_backend_max}`,
applied at the three egress decision points as a ratchet: a project entry can
force a client project local-only against a permissive global config, but can
never loosen a global restriction. The `.env` precedence order is untouched.
If the registry is unreadable while pinned, knowledge and egress paths fail
closed; session *capture* degrades to canonicalization-only with loud
warnings — isolation is the learn/egress contract, capture-over-attribution
is the standing auto-session commitment, and the split honors both.

### 7. Reserved keys, the `auto` quarantine, and `adopt`

`auto` and `shared` are reserved registry keys. Explicit session starts and
learn tools reject labels resolving to them. End-session extraction and
recall are gated off for `auto` sessions (no recorded policy mandates
extraction from unattributed sessions), freezing the commingled
`knowledge/auto.json` as quarantined data. Auto-creation itself is preserved:
capture never fails closed on a missing session, and the sentinel's
documented no-disk-inference role stands for unpinned servers. A
provenance-honest `adopt` operation (auto → real project only) appends a
`state_change` event recording old→new/actor/reason through the locked write
path and stamps the additive `project_key` — append-shaped, so it survives
ADR 005 as a journal record.

### 8. Alias-table-first repair — capture records are never bulk-rewritten

Legacy sessions keep their raw labels verbatim forever and are interpreted
through the alias table. There is no bulk `project_key` stamping sweep;
`adopt` is the only sanctioned in-place touch. Knowledge stores (mutable,
derived-adjacent data) *are* consolidated per alias group — Jaccard-dedup
union under a live-writer preflight gate, originals kept as
`*.premerge-<date>` files, every merge ledgered with content hashes in an
append-only `migrations.jsonl` — after a mandatory full-store snapshot and a
human-signed label→key plan. Semantically distinct labels (pre/post-rename
pairs) are never auto-merged; the human decides. Migration execution is an
operator runbook, strictly separated from the PRs that land the tooling.
Rollback is bit-identical for capture records by construction.

### 9. Versioning and spec impact

`SCHEMA_VERSION` 0.4.1 → 0.5.0 (additive `project_key` is an on-disk format
change); schema `$id` moves to `trace-v0.5.json` via the established rename
cascade. `project` remains unconstrained in the emitted schema (a pattern
would retroactively invalidate live documents). The spec gains: Terminology
entries (Project, Canonical Project Key, Alias); a normative §3.2.x canonical
key algorithm (spec-defined, explicitly independent of any implementation's
filename sanitization); a §4.5 rule that consumers must not treat equal-key
labels as distinct projects; `trace:projectKey` (and an optional reified
project entity with alias labels) added to PROV exports under the frozen
`ns/v0.3#` namespace — `trace:project` keeps its display-label meaning
forever, so the already-exported corpus is never reinterpreted. The registry
gets a published interchange schema (`trace-projects-v1.json`), since
interpreting historical exports depends on the alias table.

### 10. Adaptive-learning cross-project sharing is foreclosed

Ambient cross-project knowledge (a shared corpus, cross-store search,
background sync) is incompatible with the containment invariant and with
per-project `local_only`, and is foreclosed. The only sanctioned future
replacement is explicit per-learning transfer — a deliberate, logged,
lineage-stamped copy between named stores, blocked out of `local_only`
projects — recorded here so a future roadmap cannot resurrect the
shared-corpus design by accident. Not implemented in this workstream.

### 11. ADR 005 inheritance contract

Everything above lands before ADR 005 P1. Genesis records will bind the
already-canonical, registry-stable `project_key` (display label carried as a
non-identity field), so drift can never be cryptographically frozen and
repair stays distinguishable from tampering. The planned flat journal layout
needs no amendment — canonical keys make physical per-project directories
permanently unnecessary. The heads registry adopts the projects-registry
conventions (versioned model, fail-closed lock, invariant-registered writers)
and is keyed by `(project_key, session_id)`; the first chained record may
commit a hash of the pre-P1 registry and `migrations.jsonl`, sealing the
repair audit trail.

## Alternatives considered

- **Physical per-project directories now** — rejected: session-file moves
  during live multi-process operation require tombstones that convert laggard
  capture into permanent provenance loss, a lockstep fleet sweep with the
  worst blast radius, and dual-layout code that ADR 005 P1 would rewrite
  anyway. The one unique payoff (per-project handover) is captured at export
  time by a project bundle command instead.
- **Capability-style storage wrapper** (project-scoped handles replacing the
  storage ABC) — rejected: contradicted the existing storage interface
  contract and added construction machinery; a single AST-guarded validator
  achieves the same non-bypassability with far less surface.
- **Bulk `project_key` stamping of legacy sessions** — rejected as
  retroactive alteration of capture records; alias-table-first is the
  conservative reading of the provenance rule.
- **Warn-and-stamp cross-project reads** (instead of hard-deny) — rejected:
  leaves a laundering path where foreign content is pulled into a pinned
  context and re-persisted into the pinned store.
- **Per-project egress ledger files** — rejected: the ledger is
  cross-project metadata by design; a `project_key` column suffices, and the
  single-file audit workflow is documented and taught.
- **Fail-closing all tools on registry damage** — rejected: one damaged file
  must not stop provenance capture machine-wide; only isolation-bearing
  paths (knowledge, egress) fail closed.

## Consequences

- Five invariants are added or rewritten (project-match predicate on
  canonical keys; (project, session) coherence; registry integrity;
  fail-closed dependency-free knowledge lock; no free-form label reaches a
  store path), each with an enumeration guard in `tests/test_invariants.py`
  and a row in `docs/INVARIANTS.md`.
- Previously silent states become visible errors: corrupt knowledge stores
  raise instead of silently resetting; lock timeouts raise instead of
  proceeding; cross-project reads deny instead of succeeding quietly. An
  adjustment period of new error surfaces is expected and intended.
- Enforcement completeness is coupled to the fleet sweep: until every
  consumer config carries the pin, unpinned processes retain legacy behavior
  (loudly). The coupling is time-boxed and terminates in a fail-closed
  default (`TRACE_REQUIRE_PIN`, registry strict mode).
- The knowledge history already merged by case-insensitive filesystems is
  unrecoverable per-learning; consolidation records the merge rather than
  reconstructing attribution, and the limitation is documented for research
  use of the store.
- Implementation is sliced into seven independently landable PRs, one
  operator runbook, and one post-observation hardening step; the detailed
  sequence lives with the implementation plan, not this ADR.
