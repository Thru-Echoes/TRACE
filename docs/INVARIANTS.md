# TRACE Invariants Registry

This file is the **single source of truth for TRACE's correctness invariants** —
the properties that, if violated at *any* site, corrupt the audit record TRACE
exists to protect. Each invariant lists its exact statement, the **exhaustive
set of sites** where it must hold, the mechanism that enforces it, and the test
that pins it.

**Why this file exists.** Every serious data-integrity defect found in this
codebase has had the same shape: *an invariant enforced in one place but not
uniformly* (immutability on append/end but not resolve; validation at
construction but bypassed on assignment; locking on the happy path but silently
degrading on timeout). The durable fix for that defect *class* is to name each
invariant, enumerate its sites once, and add a guard that fails when a new
unguarded site appears. `tests/test_invariants.py` mechanically checks the
site-sets below.

> Status legend: **ENFORCED** = guard + test in place · **PARTIAL** = holds in
> code but not yet mechanically guarded · **OPEN** = known gap, not yet fixed.

---

## INV-1 — Every session write is a locked, disk-truth read-modify-write  · ENFORCED

**Statement.** No code path mutates a persisted session except by (a) acquiring
the **fail-closed** per-session lock and (b) writing back the *freshest on-disk*
`Session` (so a stale in-memory copy can neither clobber a concurrent writer's
events nor resurrect a completed session). The lock **raises `TimeoutError`**
rather than ever proceeding unlocked.

**Single implementation.** `src/trace_mcp/storage/locked.py :: locked_disk_session`.

**Exhaustive site-set (all session writes route through the helper):**
- `src/trace_mcp/tools/session_tools.py :: append_event`
- `src/trace_mcp/tools/session_tools.py :: end_session`
- `src/trace_mcp/tools/decision_tools.py :: resolve_decision`
- `src/trace_mcp/identity_cli.py :: adopt_session` (ADR-006 S6 — `identity adopt`
  re-homes an `auto` session into a real project: stamps `metadata.project_key`
  and appends a `state_change`, append-shaped, under the same locked helper)

**Enforcement.** `JsonFileStorage.lock` writes a `<pid>:<time_ns>` token and
steals a lock only when the holder PID is provably dead (single-host) or, for an
unparseable/legacy token, when older than `steal_after`; it fails closed on
timeout. A live holder's lock is never stolen.

**Tests.** `tests/test_integrity_hardening.py` (fail-closed, token, holder
liveness), `tests/test_v042_storage_concurrency.py` (cross-process no-lost-update
/ no-duplicate-id).

---

## INV-2 — Completed sessions are immutable (one documented exception)  · ENFORCED

**Statement.** Once a session is `completed`, no event may be appended and it may
not be re-ended. Two post-completion mutations are permitted, both
append-shaped and both recorded for audit: (1) resolving a still-`proposed`
decision (the documented cross-session decision lifecycle), which stamps an
audit warning; and (2) `identity adopt` re-homing an `auto`-quarantine session
into a real project (ADR-006 S6), which appends a `state_change` recording
old→new/actor/reason and stamps `project_key`. Adopt is confined to sessions
whose key is reserved (`auto`/`shared`) — it can never relabel a real project's
completed session. The check is made against **disk truth** inside the lock, not
the in-memory copy.

**Site-set:** the INV-1 write paths. `append_event`/`end_session`/
`resolve_decision` each guard `disk.status == "completed"` under the lock;
`identity_cli.py :: adopt_session` is the second sanctioned post-completion
appender, gated instead on the session resolving to a reserved key.

**Tests.** `tests/test_decision_integrity.py` (post-completion resolution +
stale-copy resurrection), `tests/test_integrity_hardening.py`
(`test_end_session_refuses_when_disk_already_completed`).

---

## INV-3 — No `DecisionData` reaches disk without full Pydantic validation  · ENFORCED

**Statement.** A decision's resolution state may never be written by assignment
that bypasses validation (the C1 brick bug: `disposition = "approved"` slipped
past Pydantic and made the file unreadable forever). Every resolution goes
through a `DecisionData.model_validate(...)` round-trip, and the MCP edge types
the parameter as a `Literal`.

**Site:** `src/trace_mcp/tools/decision_tools.py :: resolve_decision`
(`VALID_RESOLUTIONS` guard + `model_validate` round-trip).

**Tests.** `tests/test_decision_integrity.py` (disposition-brick scenario, Literal sweep).

---

## INV-4 — Project scoping matches by canonical key in the core query filters  · ENFORCED

**Statement.** The core query filters (`list_sessions`, `session_brief`) resolve
a project by **canonical key** (`project_identity.session_project_key`), so
drifted display labels resolve to one project: case variants
(`coeqwal`/`COEQWAL`) merge via `canonical_project_key`, and rename aliases
(`TRACE`→`trace-mcp`) merge via the registry alias table. Two predicates are
forbidden — a case-insensitive **substring** match (merges distinct projects)
and **raw case-sensitive label equality** (splits one project across drifted
labels). The adapter hooks are aligned to the same canonical rule in the
surfaces step (ADR-006 S5); until then they still match the raw label (a
narrower pre-migration session set, not a core-tool correctness gap).

**Sites:** `src/trace_mcp/storage/json_file.py` (`list_sessions`,
`session_brief`); `src/trace_mcp/adapters/claude_code/assets/hooks/*.sh`
(aligned in S5).

**Guard:** `tests/test_invariants.py :: test_inv4_project_filter_uses_canonical_key`
fails if the substring idiom or raw-label equality reappears at either core
filter site, and requires the canonical predicate. Behavior pinned by
`tests/test_storage.py`.

**Status.** ENFORCED (core query filters; hooks aligned in S5).

---

## INV-5 — No cloud egress without a pre-call ledger attestation  · ENFORCED

**Statement.** Every OpenAI-SDK network call in the trace-learn extension
(`…completions.create(...)` / `…embeddings.create(...)`) must be preceded, in
the same function, by `attest_egress()` — one appended line in the egress
ledger (`~/.trace/egress.jsonl`, override `TRACE_EGRESS_LOG`) recording the
fact of the call (provider, endpoint, model, purpose, item count,
project/session when known) and never the content. The attestation **fails
closed**: if the ledger cannot be written, the cloud call must not happen —
call sites sit inside the existing strict/permissive LLM handling, so a failed
attestation degrades like a failed provider (strict raises, permissive falls
back to the local path). An unattested call site is unrecorded egress, the
exact failure mode the ledger exists to prevent.

**Sites:** `src/trace_mcp/extensions/learn/extraction.py`
(`extract_from_session_llm`); `src/trace_mcp/extensions/learn/matching.py`
(`_llm_score`); `src/trace_mcp/extensions/learn/embeddings.py`
(`OpenAIEmbeddingProvider.embed_texts`). Writer:
`src/trace_mcp/extensions/learn/egress.py`.

**Guard:** `tests/test_invariants.py :: test_inv5_every_openai_call_site_is_registered`
(AST enumeration — a new `.create` site fails until registered in
`INV5_EGRESS_CALL_SITES`, and stale registrations fail too, with a positive
control against pattern rot) and `:: test_inv5_every_egress_site_attests_first`
(each registered site must call `attest_egress`). Behavior pinned by
`tests/test_egress_ledger.py` (pre-call ordering; no egress when the ledger is
unwritable).

**Status.** ENFORCED.

---

## INV-6 — (project, session) coherence before a learn extraction  · ENFORCED

**Statement.** Every function in `extensions/learn` whose signature carries both
a `project` and a `session_id` (it extracts a session into a project's store)
MUST call `project_identity.validate_project_session` first. Otherwise a session
belonging to project A can be extracted into project B's knowledge store — and,
under a cloud backend, project B's entire store leaves the machine as
de-duplication context alongside project A's events (the worst cross-project
bleed). The check fails closed (`ProjectMismatchError` / `ProjectKeyError`).

**Sites:** `src/trace_mcp/extensions/learn/__init__.py` (`_extract_hook`,
`trace_learn_extract`).

**Guard:** `tests/test_invariants.py :: test_inv6_project_session_sites_validate_coherence`
(AST enumeration — a new `(project, session_id)` learn function that does not
call `validate_project_session` fails, with a positive control against pattern
rot). Behavior pinned by `tests/test_learn_containment.py`.

**Status.** ENFORCED.

---

## INV-8 — The knowledge-store lock is fail-closed and dependency-free  · ENFORCED

**Statement.** `extensions/learn/store.py :: project_lock` acquires the
dependency-free O_EXCL + PID-liveness lock (`project_identity.exclusive_file_lock`),
keyed by the canonical store path, and raises `TimeoutError` rather than
proceeding unlocked. The previous behavior — a silent no-op when the optional
`filelock` package was absent, and proceed-on-timeout — reopened the lost-update
window the lock exists to close, violating the same fail-closed standard as the
session lock (INV-1).

**Sites:** `src/trace_mcp/extensions/learn/store.py` (`project_lock`, keyed by
`_store_path`, the canonical store path).

**Guard:** `tests/test_invariants.py :: test_inv8_knowledge_lock_is_fail_closed_and_dependency_free`
(no `filelock` import; `project_lock` uses `exclusive_file_lock`). Behavior
pinned by `tests/test_learn_containment.py` (lock timeout raises) and, across
real server processes, by `tests/test_concurrency_smoke.py` (interleaved adds
lose nothing and alias no ids; a writer killed mid-add leaves a parseable
store; a dead holder's lock is stolen; a live holder's lock is refused, never
bypassed). A green run of the cross-process module is a precondition for any
consolidation of live knowledge stores; the consolidation runbook adds its own
rehearsal on a cloned home.

**Status.** ENFORCED.

---

## INV-7 — Every project-registry write is fail-closed, locked, and atomic  · ENFORCED

**Statement.** All mutations of the project registry (`~/.trace/projects.json`,
override `TRACE_REGISTRY_PATH`) go through `project_identity.locked_registry`,
which acquires the fail-closed exclusive lock (`O_EXCL` + PID-liveness steal,
raising `TimeoutError` rather than writing unlocked), yields the freshest
disk-truth registry, validates alias uniqueness, and persists atomically
(temp + `os.replace`) only on clean exit. A corrupt or unknown-major registry
raises `RegistryUnavailableError` on load and is **never** overwritten — an
unreadable registry is fail-closed, not silently reset (the same reasoning as
INV-1). The single serializer is `project_identity._atomic_write_registry`.

**Sites:** writer `src/trace_mcp/project_identity.py` (`_atomic_write_registry`,
called only by `locked_registry`).

**Guard:** `tests/test_invariants.py :: test_inv7_registry_write_only_via_locked_registry`
(AST enumeration — a new caller of `_atomic_write_registry` outside
`locked_registry` fails until registered, with a positive control against
pattern rot). Behavior pinned by `tests/test_project_identity.py` (lock timeout
raises, dead-holder steal, corrupt/unknown-major refusal, atomic write leaves no
temp file, a caller exception writes nothing).

**Status.** ENFORCED.

---

## INV-9 — Every learn tool resolves its project label against the pin  · ENFORCED

**Statement.** Each `@mcp.tool` in `extensions/learn` that takes a `project`
parameter calls `_resolve_project` at entry, which delegates to
`project_identity.resolve_scoped_project` — the same scope rule
`trace_start_session` uses. Under a `TRACE_PROJECT` pin, an omitted project
resolves to the pin and a label resolving to any other key errors naming both
keys; unpinned, an explicit non-empty label is required; a reserved-key
(`auto`/`shared`) or degenerate label is rejected in both modes. Combined with
the canonical `_store_path`, a free-form label can neither cross the pin, open
a quarantine store, nor create a mis-keyed one. (Before this rule, learn tools
accepted any foreign label on a pinned server — a cross-project read/write
bypass of the documented fail-closed guarantee.)

**Sites:** `src/trace_mcp/extensions/learn/__init__.py` (`trace_learn_recall`,
`trace_learn_add`, `trace_learn_list`, `trace_learn_forget`,
`trace_learn_extract`); `src/trace_mcp/server.py` (`_resolve_start_project`,
by delegation).

**Guard:** `tests/test_invariants.py :: test_inv9_learn_tools_guard_reserved_projects`
(structural) + `tests/test_learn_pin_scope.py` (behavioral).
(AST enumeration — a new learn tool with a `project` param that does not call
`_reserved_project_error` fails, with a positive control against pattern rot).

**Status.** ENFORCED.

---

## INV-10 — Every restatement of a version agrees with its source of truth  · ENFORCED

**Statement.** Three version numbers each have exactly one source of truth: the
package version (`pyproject.toml :: version`), the spec/wire version
(`schema/session.py :: SCHEMA_VERSION`), and the **released** version (the newest
dated section heading in `CHANGELOG.md`). Every other file that restates one of
them must agree with *that* one. All three are **independent** — a hardening
release may bump the package while the wire format stands still, and `main` may
run ahead of the last release — so nothing asserts that any two are equal.

One cross-rule ties the package and released versions together without equating
them: when they differ, the package version MUST be marked as a pre-release
(`X.Y.Z.devN`, `aN`, `bN`, `rcN`). A *stable* package version that differs from
the last release is the ambiguity this rule exists to prevent — two different
trees both claiming to be the same release, one of them the archived artifact a
DOI resolves to.

**Exhaustive site-set (package version):**
- `src/trace_mcp/__init__.py :: __version__`
- `server.json :: version` and `server.json :: packages[*].version`
- `README.md` — the `**Version:**` banner
- `CLAUDE.md` — the `> **Version**:` banner
- `docs/ONBOARDING.md` — the `package \`<X>\`` half of the state line

**Exhaustive site-set (released version):**
- `CITATION.cff :: version`

**Exhaustive site-set (spec/wire version):**
- `docs/specification.md` — the `## Specification v<X>` heading
- `docs/specification.md` — the §3.1 `trace_version` row's `Default: "<X>"`
- `docs/specification.md` — the Appendix A example's `"trace_version"`
- `schemas/trace-v<major>.<minor>.json` and its `$id`
- `src/trace_mcp/schemas/trace-v<major>.<minor>.json` (the packaged copy)
- `README.md` — the `**Spec:** v<X>` half of the version banner
- `CLAUDE.md` — the `protocol/schema v<X>` half of the version banner
- `docs/ONBOARDING.md` — the `protocol/schema \`<X>\`` half of the state line

**Why this is an integrity invariant, not cosmetics.** `server.json` is the MCP
registry manifest — a stale version there is what an installer *resolves*, not
a typo in prose. It had silently drifted a full minor version behind
`pyproject.toml` because only the `__init__.py` ↔ `pyproject.toml` pair was
ever checked: the same shape as every other defect in this file, an invariant
enforced in one place but not uniformly.

**Guard:** `tests/test_installation_health.py :: TestVersionDeclarationSites`
(plus the pre-existing `TestPyprojectConsistency :: test_version_matches_pyproject`
for the `__init__.py` site). Each site is read and compared to its source of
truth; the schema-file check derives the filename from `SCHEMA_VERSION` so a
wire bump that forgets to rename or regenerate the schema fails.

**The citation exception, 2026-09-06.** `CITATION.cff` was in the package-version
set until `main` first carried work past a tag. A citation file must name a
version that has an archive behind it: pinned to a development package version it
would have GitHub's "Cite this repository" button and every CFF-aware tool emit a
version no DOI resolves to. It is therefore pinned to the newest released version
instead, and the pre-release cross-rule above keeps the package version honest
about being ahead. This is the same reasoning that already keeps the package and
wire versions independent — restating one number in a file that means a different
number is the drift, not the fix.

**Site-set widened 2026-09-03.** The spec/wire set held three sites while five
more prose restatements went unguarded, so a `SCHEMA_VERSION` bump could ship
with the front-door docs and the specification's own producer guidance still
naming the previous wire version. That is the recurring shape this file exists
to close: an invariant enforced in one place but not uniformly. Verified by
bumping `SCHEMA_VERSION` and confirming every added site fails.

**Status.** ENFORCED.

---

## INV-11 — A freshly initialized project is conformance-clean  · ENFORCED

**Extended 2026-09-06 to the instruction block.** The block installed into a
consumer's `CLAUDE.md` now carries a stamp derived from the shipped template's
bytes, the installer refreshes the marked region in place when that stamp
differs, and `docs.block_stamp` reports a block that is not this build's. Before
this, an installed block was never refreshed: a template edit reached only
projects that did not yet have one, so the guidance went stale across the whole
fleet while every check reported those projects clean. The stamp is a content
digest rather than a hand-maintained number so that any edit changes it, closing
the same gap the hook scripts' `[trace-hooks vX.Y]` stamp closes for them.

**Statement.** For every host adapter TRACE actually installs, running
`trace-mcp-init` on an empty directory must produce a deployment that
`trace_mcp.conformance.run_doctor` reports as `ok` — no failing check — using
only this build's own shipped artifacts as the reference. Equivalently: the
installer's OUTPUT and the checker's EXPECTATIONS are never allowed to
disagree.

**Why this is an integrity invariant, not a convenience.** The defects this
closes were all *deployment* defects invisible to the unit suite: a
`settings_template.json` that shipped a PostToolUse matcher naming the bare
tool name `trace_end_session` (which a host never matches, because MCP tools
are namespaced `mcp__<server-key>__<tool>`), so the decision-audit hook was
dead in 15 of 17 deployed projects for several releases; hook copies frozen at
an older release across the whole fleet; launch configs missing the
`--with` extras, which yields a silent 17-tool server instead of the
documented 22. Each one was found by hand, months late. Binding installer to
checker means the next one fails at PR time.

**Exhaustive site-set (what the invariant binds together):**
- Installer output — `src/trace_mcp/init_project.py` (`.mcp.json` entry:
  command, `--from` source, `LEARN_EXTRAS`, refresh flag, `TRACE_PROJECT` pin;
  plus the pin file and the CLAUDE.md pin line) and
  `src/trace_mcp/adapters/claude_code/` with its `assets/` (hook scripts,
  `settings_template.json`, `CLAUDE_BLOCK.md`).
- Checker expectations — `src/trace_mcp/conformance/expectations.py` and the
  probes in `src/trace_mcp/conformance/probes.py`.

**Enforcement.** The expectations are *derived from the shipped artifacts*
rather than restated: required hook filenames and the `[trace-hooks vX.Y]`
stamp are read from `adapters.claude_code.HOOK_ASSETS_DIR`, the decision-audit
matcher is built from `adapters.base.MCP_SERVER_KEY`, the expected served
version from `trace_mcp.__version__`, and the expected tool total is computed
from the declared tool names (themselves pinned against the README table and
against the tools the server registers).

**Guard:** `tests/test_conformance_doctor.py :: test_fresh_init_is_doctor_clean`
— parametrized over the adapter registry, skipping only adapters whose
`install()` is not implemented. A new host adapter therefore starts being
checked the moment it ships, and the doctor must learn that host's layout or
the guard fails. Supporting guards in the same file pin the derivation itself
(a hand-copied hook list, stamp, matcher, or tool count fails) and assert that
breaking exactly one aspect of a fresh install fails exactly one check.

**Status.** ENFORCED.

---

## INV-12 — Learning ids are never reused  · ENFORCED

**Statement.** A learning id is minted from a **persisted monotonic counter**
(`KnowledgeStore.next_id`), never derived from the ids currently present. The
counter is consumed on *call* rather than on append, is initialized on load for
stores written before it existed, and is only ever **raised** — never lowered —
so an id released by `trace_learn_forget` is never reissued to different
content. Complementing that, every **write** path refuses a store whose ids are
already aliased; **reads** stay permissive so an affected store remains
readable, exportable, and repairable.

**Why this is an integrity invariant.** `next_learning_id()` returned
`max(existing) + 1`, so forgetting the highest learning and adding another gave
the new content the old id. Dedup, recall counts, the positionally-aligned
embedding sidecar, and any recorded reference all key on that id, so the two
records become mutually unaddressable — and, unlike a lost update, nothing about
the store looks wrong afterwards. The same defect shape as the rest of this
file: a rule (ids identify one record) enforced by a scheme that quietly breaks
under one operation.

**What "write" means here.** Recall is a read for its caller but persists recall
counts and lazily-computed embeddings. That bookkeeping mints no id, so on an
aliased store it is **dropped with a notice on the response**
(`extensions/learn/__init__.py :: _persist_recall_bookkeeping`) rather than
failing the recall — otherwise the guard would break reads on exactly the stores
it exists to protect, inverting its own guarantee.

**Known limitation (downgrade).** A server built before `next_id` existed loads a
repaired store, drops the field on write, and the counter re-initializes from the
highest id present on the next load. A forget-then-add across that window can
still reissue an id, with nothing on disk showing the counter was lost. Consumers
build from whatever branch is checked out in the shared working tree, so this is
governed by the standing "keep `main` checked out" rule rather than by code.

**Exhaustive site-set:**
- Minting — `src/trace_mcp/extensions/learn/models.py` (`KnowledgeStore.next_id`,
  `next_learning_id`, `_raise_counter_above_existing_ids`).
- Appenders that must mint from the counter —
  `src/trace_mcp/extensions/learn/store.py :: add_learning`;
  `src/trace_mcp/identity_cli.py :: cmd_merge_stores`.
- Write paths that must refuse an aliased store —
  `src/trace_mcp/extensions/learn/store.py :: save_store`, `:: add_learning`
  (single implementation: `_refuse_duplicate_ids`). `cmd_merge_stores` refuses an
  aliased **target** before grafting, so a merge cannot consume the sources into
  premerge backups and leave the operator with a hand reconstruction.
- Detection — `src/trace_mcp/identity_report.py :: find_duplicate_learning_ids`
  (core, filesystem-only, zero `extensions/` imports per ADR-003), consumed by
  `trace-mcp identity check`.
- Repair — `src/trace_mcp/identity_cli.py :: cmd_repair_ids`
  (`trace-mcp identity repair-ids <key>`): snapshot-gated, writer-quiescence
  checked, holds the store lock, keeps the first occurrence and renumbers later
  ones in array order, copies the original to `*.json.prerepair-<date>`, records
  the mapping and before/after digests in `migrations.jsonl`. It **refuses** when
  a duplicated id appears in a *structured* field (`_find_id_references`
  blocking set) rather than guessing which record the reference meant, and merely
  **reports** a mention in free text — prose is resolved by no code path, and
  refusing on it would leave a store permanently unwritable with hand-editing as
  the only escape. When the registry cannot resolve the argument it falls back to
  the canonical store stem, so the stray and quarantine stores `check` flags can
  actually be repaired. Quiescence is checked on the target file only
  (`_preflight_target_quiescent`), so one crashed writer elsewhere cannot wedge
  the only path out of a store that refuses every write.

**Guard:** `tests/test_invariants.py` —
`test_inv12_next_learning_id_reads_and_advances_the_persisted_counter` (fails if
`max()` arithmetic returns, or if the counter is not read and advanced),
`test_inv12_counter_healing_never_lowers_the_counter`,
`test_inv12_every_learning_appender_is_registered` (AST enumeration of every site
that grows a `.learnings` list — `append`/`extend`/`insert`, `+=`, and slice
assignment — with a positive control against pattern rot and a staleness check;
a local alias is beyond a static check of this shape, which the id-minting
assertion and the behavioral tests backstop), `test_inv12_registered_appenders_mint_from_the_counter`, and
`test_inv12_store_writes_refuse_an_aliased_store`. Behavior pinned by
`tests/test_learn_models.py :: TestMonotonicIds`,
`tests/test_learn_store.py :: TestLearningIdReuse` (forget → add → distinct id;
refusal leaves the file byte-identical), and
`tests/test_identity_cli.py :: TestRepairIds`, `:: TestDuplicateIdReporting`,
`:: TestRepairIdsReachesEveryFlaggedStore`, `:: TestRepairIdsReferenceScan`, and
`tests/test_learn_aliased_store.py` (reads keep working and every refused write
names the repair command).

**Status.** ENFORCED.

---

## INV-13 — Session creation never replaces an existing record  · ENFORCED

**Statement.** Creating a session through the storage backend claims its
filename atomically and raises `SessionIdTakenError` if the name is taken,
leaving the existing record untouched. The session-creation tool path treats a
taken id by minting a fresh one, a bounded number of times, and then fails
loudly. It never falls back to a replace. Within the backend, only
`update_session` replaces a session file, and it requires that the file already
exists. The importers' `--output` is out of scope: it writes to a path the
operator names and does not go through the store.

**Why this is an integrity invariant.** Generated ids carry 24 random bits per
day, and external pipelines that share the store mint ids in the same namespace.
When creation ended in `os.replace`, a collision destroyed the earlier session
and nothing on disk showed it had existed.

**Exhaustive site-set:**
- Exclusive write — `src/trace_mcp/storage/json_file.py ::
  JsonFileStorage._write_file_exclusive`, called by `create_session`.
- Fresh-id retry — `src/trace_mcp/tools/session_tools.py :: create_session`, the
  one caller of the backend's `create_session`.
- Routes into it — `tools/session_tools.py :: start_session`,
  `server.py :: _ensure_session`, `server.py :: trace_start_session`.

**Known limitation.** Where `os.link` reports that hard-link publication is
unavailable (the filesystem lacks hard links or a policy denies them), creation
falls back to writing through an `O_CREAT | O_EXCL` descriptor. Any other
`os.link` failure is raised. The fallback's claim is still exclusive, but the
file is visible while it is written, so a concurrent `get_session` can raise
`JSONDecodeError`, and a crash or failed write can leave a partial record. A
failed write deliberately does not unlink the file: another writer may already
have replaced it. Aggregate reads leave a partial file out.

**External writers.** This invariant covers the storage backend. A program that writes
session files directly must follow `docs/integrations/external-writers.md`.

**Guard scope.** The AST guards are syntactic regression checks on the named
functions, not a call-graph proof.

**Tests.** `tests/test_invariants.py :: test_inv13_session_creators_are_registered`,
`:: test_inv13_only_the_retrying_path_calls_the_backend`,
`:: test_inv13_backend_create_is_exclusive`, and
`tests/test_session_create_exclusive.py` (byte-identical first record, concurrent
creators on both publication paths, complete-file publication, a fallback failure
that must not delete a concurrent update, real link failures, a store path that is
not a directory, fresh-id retry, and bounded failure).

**Status.** ENFORCED.

---

## INV-14 — A published name survives a power loss

**Invariant.** A write that creates, replaces, or deletes a session file, the project
registry, or a knowledge store does not return success until the file's bytes and its
parent directory have been synced. Before a process first uses a store directory, it
syncs every ancestor of that directory, because existence does not show that another
process finished making the name durable. `trace_mcp.durability` holds the directory sync
and its platform contract.

**Why this is an integrity invariant.** `fsync` on a file does not make its name durable.
The name lives in the directory, so a power loss could undo a session the caller had been
told was recorded.

**Platform contract.** Where `os.O_DIRECTORY` exists, the directory is synced and a failure
is raised. A filesystem that reports it cannot sync directories is tolerated with one
warning per directory. An ancestor that is read-only or that the process cannot open is
skipped, since this process cannot have just added a name there. Reads never sync. Windows has no directory
sync, so the step is skipped. The guarantee
is whatever `os.fsync` provides on the host; on macOS that excludes a drive-cache flush.

**Exhaustive site-set:**
- Durable publishers — `storage/json_file.py :: _write_file`, `:: _write_file_exclusive`;
  `project_identity.py :: _atomic_write_registry`; `extensions/learn/store.py :: save_store`.
- Durable delete — `storage/json_file.py :: delete_session`.
- Regenerable, exempt — `scratchpad.py :: _atomic_write` and
  `extensions/learn/store.py :: save_embeddings_cache`.
- Operator-run with backups, exempt — `identity_cli.py :: cmd_merge_stores`.

**Known limitation.** A directory-sync failure is reported after the name has changed, so
the caller sees an error while the record stays visible. It is not removed, because another
writer may already depend on it. A process does not re-sync a store's ancestors if one of
them is moved while it runs, or if the store is deleted and recreated at the same path on a
reused inode. Operator-run migrations in `identity_cli.py` keep their
existing backups and are outside this invariant, as are append-only logs.

**Tests.** `tests/test_invariants.py :: test_inv14_every_publisher_is_classified`,
`:: test_inv14_durable_publishers_sync_the_file_and_the_directory`,
`:: test_inv14_session_delete_syncs_the_directory`, and
`tests/test_storage_durability_identity.py :: TestDirectoryDurability`,
`:: TestDirectorySyncPlatformContract`.

**Status.** ENFORCED.

---

## INV-15 — A session id addresses one record

**Invariant.** `get_session`, `update_session`, and `delete_session` treat a file whose
stored id differs from the requested id, but resolves to that same file, as not found.
`locked_disk_session` refuses a file that holds any session other than the one requested,
so an update cannot land on a record whose lock the caller does not hold. `delete_session`
holds the session's lock, so a delete cannot interleave with a locked read-modify-write.

**Why this is an integrity invariant.** `sanitize_name` is lossy and some filesystems
ignore case or Unicode normalization, so distinct ids can share one file. Without this
check, one id could read, replace, or delete another id's record. An unlocked delete
followed by a re-create let an updater that had already loaded the old record overwrite
the new one.

**Compatibility.** A file whose stored id has a file of its own is not an alias and stays
readable under its filename, so no migration is needed, but `locked_disk_session` will not
write it. A corrupt file can still be replaced by `update_session`. A file that cannot be
read raises rather than being treated as unowned.

**Known limitations.** The lock inherits the stale-lock steal race documented on
`JsonFileStorage.lock`: two contenders can both judge a dead holder's lock stale, and one
can then remove the other's new lock. `create_session` takes no lock, so a writer that
loaded nothing from disk and is using its fallback session can still replace a session
created under the same id while it holds the lock.

**Exhaustive site-set:** `storage/json_file.py :: get_session`, `:: update_session`,
`:: delete_session`; `storage/locked.py :: locked_disk_session`.

**Guard scope.** The AST guards are syntactic regression checks on the named methods.

**Tests.** `tests/test_invariants.py :: test_inv15_every_id_addressed_access_refuses_aliases`,
`:: test_inv15_delete_takes_the_session_lock`,
`:: test_inv15_locked_write_refuses_another_record`, and
`tests/test_storage_durability_identity.py :: TestOneRecordPerSessionId`,
`:: TestDeleteTakesTheSessionLock`.

**Status.** ENFORCED.

---

## INV-16 — A pinned server scopes every core read tool

**Invariant.** On a process pinned with `TRACE_PROJECT`, every core read tool is limited to
the pinned project. A read addressed by session id goes through `check_read_scope`. A read
addressed by project goes through `get_read_scope_project`, which resolves an omitted
project to the pin and refuses another project. `TRACE_ALLOW_CROSS_PROJECT_READS=1`
overrides both.

**Why this is an integrity invariant.** The pin is documented to fail closed for
cross-project reads. A tool that skips it exposes another project's sessions and counts
while the documentation says that cannot happen.

**Exhaustive site-set:** `server.py :: trace_get_session`, `:: trace_get_events`,
`:: trace_get_decisions`, `:: trace_get_decision_chain`, `:: trace_search`,
`:: trace_export`, `:: trace_list_sessions`, `:: trace_project_summary`,
`:: trace_health_check`. Learn tools are covered by INV-9. Write tools fail closed
separately.

**Known limitations.** `trace_health_check` still reports store-level diagnostics such as
storage paths and the names of stray knowledge stores. When the project registry is
unreadable it counts no sessions and reports the registry failure.

**Tests.** `tests/test_invariants.py :: test_inv16_every_core_tool_is_classified`,
`:: test_inv16_every_read_tool_applies_the_pin`,
`:: test_inv16_health_check_scopes_both_of_its_addressing_modes`, and
`tests/test_read_scope_pin.py`.

**Status.** ENFORCED.

---

*To add an invariant: give it the next `INV-N`, state it, enumerate the
exhaustive site-set, name the guard + test, and add a check that fails when a
new site violates it — in `tests/test_invariants.py` for the AST/enumeration
guards, or alongside the behavior it protects where that reads better.*
