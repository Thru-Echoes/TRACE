# FINAL Remediation Plan — v0.4.1 status review

**Supersedes** `round1_SYNTHESIS.md` §5. **Incorporates** Round-2 corrections (`round2_correctness.md`, `round2_exhaustiveness.md`, `round2_scope_bloat.md`). **This is the artifact Round 3 verifies (engineering-reality / edge-cases / adoption-downstream).**
**Status:** all 6 findings G1–G6 independently CONFIRMED (R1×3, R2-correctness probes). Honest readiness ~75–80%, NOT release-ready. FM7 is a load-flake, NOT a code regression (do not fix `trace_end_session`).

## What Round 2 changed (deltas folded in below)

P1 was RISKY (shared gate would suppress the correct v0.3 `ai→ai` warning); +M1 (`decision-audit.sh`), +M2 (2nd bug-test), M3 resolved (actor *types*, per A1 / TRACE decision `evt_016`); P2 trimmed (`:340` already guarded); P4 corrected (delete duplicate ⚠️, low-tier already exists); spec §3.6 is CORRECT (do not touch); P9b lock now MANDATED + M5/M6 surfaces.

## FINAL P-items

### P1 — Restore Round-3 A1 scoping *(CRITICAL; the central fix)*
- **Carve-out (R2-correctness):** add the multi-actor guard **ONLY to the generalized non-ai same-instance branch** in `decision_tools.py` (FM1 ~:80-83,104; FM25) and `session_tools.py` (session-end structural detector ~:330-334). The pre-existing v0.3 **`ai→ai` single-actor** self-resolution warning is §3-verified-solid and MUST keep firing unconditionally (A1 line 47).
- **Guard (M3 → TRACE `evt_016`):** fire only when the session has **≥2 unique actor TYPES** (not instances), across `metadata.participants` ∪ event actors; fallback to event actors when participants empty (A7).
- **M1:** mirror the guard in `src/trace_mcp/adapters/claude_code/assets/hooks/decision-audit.sh:69` + add a CHANGELOG callout to re-run `trace-mcp-init`.
- **Tests (TDD, M2 → exactly 2):** rewrite `test_decision_guards.py::test_human_self_resolves_warns` → `test_human_self_resolves_clean` (single-actor human→human ⇒ NO warn) and `test_v041_decision_audit_hook.py::test_hook_handles_session_with_human_self_resolution` (single-actor ⇒ no warn). Add: single-actor `system→system` ⇒ no warn; **`ai→ai` single-actor ⇒ STILL warns** (§3 regression guard); ≥2-actor-type session, same non-ai id ⇒ warns (true evt_025).
- **Doc truth-up (sequenced WITH P1 code):** fix `CHANGELOG.md:24,45` + `decision_tools.py`/`session_tools.py` docstrings. **Do NOT touch spec §3.6 (`specification.md:233`) — already correct.**

### P2 — Restore core/extension boundary *(CRITICAL; governance `evt_002`)*
- Wrap `tools/query_tools.py:158` `import …extensions.learn.store` in `try/except ImportError` fail-open (omit knowledge metrics), mirroring the `extension_hooks.py` / `recall_if_available` precedent. `:340` already guarded — no change.
- Test: all 18 core tools function with `extensions/learn/` absent, simulated **non-destructively** (sys.modules / import hook — NOT moving the directory).
- Do not gold-plate into a metrics-hook refactor.

### P3 — Write the mandated L9.1 gate *(CRITICAL)*
- Load `audit_2026-05-13_waggle_session/trace_session_trace_20260513_446733.json` under v0.4.1; assert JSON-verified counts: **28 events, 15 missing-snippet contributions, 1 missing-snippet correction, 2 same-instance self-resolutions**.
- Audit L9.0/L9.2–L9.7 presence; **L9.7 shipped partial (2 of 5 signals)** — document the 3 missing; v0.4.1 only if low-effort, else explicit spec-noted v1.1 deferral.

### P4 — De-noise orphan-discovery (A8 unmet half) *(HIGH)*
- The low-severity hint tier ALREADY exists (`session_tools.py:175-182`). Fix = **remove the duplicate ⚠️ push at `:378-383`** + tighten the phrase constant. Not "add a tier."

### P5 — PROV-O round-trip test (A4) *(HIGH)*
- Architecture correct; add a test validating emitted JSON-LD against a real PROV-O parser / ontology constraints.

### P6 — Doc truth-up + ONE durable boundary home *(MED)*
- Spec §4.4: document cross-session hard-reject + v1.1 deferral (A9). Add ADR 002 to the adr index; widen CONTRIBUTING core list (`exporters/`, `scratchpad.py`).
- **One new ADR** = canonical home for the core/extension boundary + Tier-3-stays-extension policy (governance `evt_002`); reference from CONTRIBUTING + spec — **do not triplicate prose**.

### P7 — Release process, LAST, green-gated *(HIGH)*
- After P1–P3 land + a **full green `uv run pytest`** (run without session load contention): **USER decision** — tag policy (create `v0.4.1` + backfill prior tags, OR switch CHANGELOG compare-links to commit ranges). Flip `[0.4.1] — In progress` → `2026-05-18`; fix `[Unreleased]` link. Branch + PR; never direct to main.

### P8 — FM7: NOT a code fix *(LOW)*
- Do **not** modify `trace_end_session`. Optionally: make E2E `_send_and_receive` timeout configurable / mark MCP-subprocess E2E tests serial-or-skip-under-load; document FM7 as load-sensitive.

### P9 — Concurrency *(user-raised; HIGH for (a), MED for (b)/(c))*
- **(a)** Lazy-load `model2vec`/embedding (CONFIRMED eager: module-load import + `StaticModel.from_pretrained` at `register()`; ~0.5s/subprocess + N×RAM). Fixes FM7 cold-start root cause + RAM multiplier across concurrent sessions.
- **(b)** Add a **minimal portable cross-process lockfile** (`filelock`/lock-file) around shared `~/.trace/knowledge/` read-modify-write (`store.py:75-106` atomic-but-UNLOCKED → last-writer-wins). **Mandated.** Keep it a minimal lockfile, NOT a concurrency framework.
- **(c)** Make the `*.embeddings.npy` sidecar write atomic (bare `np.save` → temp+`os.replace`). Note `.claude/SCRATCHPAD.md` per-project wholesale-replace + global `~/.trace/scratchpads/SCRATCHPAD.md` — document; minimal mitigation only if cheap, else explicit v1.1.

## Sequencing
P1, P2, P3 (criticals, TDD) → P4, P5, P6 → P9(a) then P9(b)(c) → **P7 LAST, green-gated**. P8 optional/parallel anytime. No protocol/schema version bump (all additive bugfix/doc — R2-scope confirmed).

## Open items for USER
- **M3 / `evt_016`**: resolved to "≥2 actor TYPES" per A1 — confirm or override (outcome-determining).
- **P7 tag policy**: create real git tags vs. commit-range compare-links.

## Scope discipline (unchanged from §6)
Do not touch §3 verified-solid (L1.3, L3.1/A10, L6.x PROV, A6, Layer-11, stdin-EOF). Do not "fix" FM7 as code. No version bump. P9(b) stays a lockfile, not a framework. Doc policy: one durable home, no triplication.
