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
not be re-ended. The **only** permitted post-completion mutation is resolving a
still-`proposed` decision (the documented cross-session decision lifecycle),
which stamps an audit warning. The check is made against **disk truth** inside
the lock, not the in-memory copy.

**Site-set:** the same three INV-1 write paths (each guards `disk.status ==
"completed"` under the lock).

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

## INV-4 — Project scoping uses ONE comparison rule across core and hooks  · CLOSED

**Statement.** A project name must resolve to the **same** session set in the
core query layer and in the adapter hooks. The single shared predicate is
**exact, case-sensitive** match (`metadata.project == project`). A
case-insensitive **substring** match must never be used, as it silently merges
distinct projects (e.g. `trace_project_summary("trace")` pulling in `trace-mcp`
and `TRACE-research`).

**Sites:** `src/trace_mcp/storage/json_file.py` (`list_sessions`,
`session_brief`); `src/trace_mcp/adapters/claude_code/assets/hooks/*.sh`.

**Guard:** `tests/test_invariants.py :: test_inv4_project_filter_is_exact_not_substring`
fails if the substring idiom reappears at either core filter site; the exact
semantics are pinned by `tests/test_storage.py :: test_list_filter_by_project`.

**Status.** CLOSED — core (`list_sessions`, `session_brief`) now uses the same
exact-match predicate as the hooks, so both layers resolve one session set.

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

*To add an invariant: give it the next `INV-N`, state it, enumerate the
exhaustive site-set, name the guard + test, and add a check to
`tests/test_invariants.py` that fails when a new site violates it.*
