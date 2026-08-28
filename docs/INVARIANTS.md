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

**Statement.** The package version has exactly one source of truth
(`pyproject.toml :: version`) and the spec/wire version exactly one
(`schema/session.py :: SCHEMA_VERSION`). Every other file that restates either
number must agree with it. The two are **independent** — a hardening release
may bump the package while the wire format stands still — so nothing asserts
that they equal each other.

**Exhaustive site-set (package version):**
- `src/trace_mcp/__init__.py :: __version__`
- `server.json :: version` and `server.json :: packages[*].version`
- `CITATION.cff :: version`
- `README.md` — the `**Version:**` banner
- `CLAUDE.md` — the `> **Version**:` banner

**Exhaustive site-set (spec/wire version):**
- `docs/specification.md` — the `## Specification v<X>` heading
- `schemas/trace-v<major>.<minor>.json` and its `$id`
- `src/trace_mcp/schemas/trace-v<major>.<minor>.json` (the packaged copy)

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

**Status.** ENFORCED.

---

## INV-11 — A freshly initialized project is conformance-clean  · ENFORCED

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

*To add an invariant: give it the next `INV-N`, state it, enumerate the
exhaustive site-set, name the guard + test, and add a check that fails when a
new site violates it — in `tests/test_invariants.py` for the AST/enumeration
guards, or alongside the behavior it protects where that reads better.*
