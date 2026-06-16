# TRACE Invariants Registry

This file is the **single source of truth for TRACE's correctness invariants** —
the properties that, if violated at *any* site, corrupt the audit record TRACE
exists to protect. Each invariant lists its exact statement, the **exhaustive
set of sites** where it must hold, the mechanism that enforces it, and the test
that pins it.

**Why this file exists.** The 2026-06-10 multi-agent review found that every
serious defect had the same shape: *an invariant enforced in one place but not
uniformly* (immutability on append/end but not resolve; validation at
construction but bypassed on assignment; locking on the happy path but silently
degrading on timeout). The durable fix for that defect *class* is to name each
invariant, enumerate its sites once, and add a guard that fails when a new
unguarded site appears. `tests/test_invariants.py` (added in the
process-scaffolding work) mechanically checks the site-sets below.

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

**Tests.** `tests/test_decision_integrity.py` (C1 brick scenario, Literal sweep).

---

## INV-4 — Project scoping uses ONE comparison rule across core and hooks  · OPEN

**Statement.** A project name must resolve to the **same** session set in the
core query layer and in the adapter hooks. Today they disagree: core
(`json_file.py :: list_sessions` / `session_brief`) uses case-insensitive
**substring** match (so `trace_project_summary("trace")` silently merges
`trace-mcp` and `TRACE-research`), while the hooks use **exact** match.

**Sites:** `src/trace_mcp/storage/json_file.py` (`list_sessions`,
`session_brief`); `src/trace_mcp/adapters/claude_code/assets/hooks/*.sh`.

**Status.** OPEN — verified still present on `main` (2026-06-16 review). The fix
(make core exact, or an explicit `exact=` flag defaulting to exact) is tracked
as a follow-up; `tests/test_invariants.py` should fail until the two layers
share one predicate.

---

*To add an invariant: give it the next `INV-N`, state it, enumerate the
exhaustive site-set, name the guard + test, and add a check to
`tests/test_invariants.py` that fails when a new site violates it.*
