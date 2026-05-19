# Round 3 Verification — Amendments to the FINAL Plan

**Date:** 2026-05-18 · 3 independent R3 verifiers (engineering-reality / edge-cases / adoption-downstream).
**Verdict: GO-WITH-FIXES.** The plan's direction is validated by all 9 reviewer-agents across 3 rounds. Apply these amendments to `round_FINAL_plan.md`, then implement. Per the waggle precedent (and all 3 R3 verifiers): **do NOT run Round 4** — marginal value below cost; remaining risk is implementation discovery.

**Readiness:** current ~75–80%, NOT release-ready. Post-plan (all P-items + these amendments): defensible v0.4.1 **~93%**; v1.0-OSS ~70% (gated on PyPI/`gh release` + boundary CI + real tags).

## Amendments (apply before implementing)

- **A-R3-1 (HIGH · P1/FM25 — highest-risk catch).** `decision_tools.py:104` (FM25) is a single block with **no ai/non-ai split** (unlike FM1 at :85-99). The FINAL plan says "gate the non-ai branch (FM1 ~:80-83,104; FM25)" — but FM25 has no branch to attach to. Gating FM25 wholesale would **silently drop the §3-verified-solid `ai→ai` single-actor FM25 warning**, and no existing test catches it (the ungated FM1 ai-branch still emits "AI resolved", masking it). P1 must **explicitly state FM25's scoping**: replicate the ai/non-ai conditional in FM25, gate only the non-ai path, and add a literal-string assertion that `ai→ai` single-actor still emits the FM25 warning.
- **A-R3-2 (HIGH · P9b lock scope).** The lost-update window is the full `load_store → mutate → save_store` span (separate unlocked calls in `extensions/learn/__init__.py`), **not** just `store.py:75-106`. A lock confined to `save_store` does not prevent the race. The lock must wrap the full span, be **per-project** (not whole-dir — 15 projects would starve), declare `filelock` as a real dependency, and note the NFS/`~`-on-network-FS limitation as documented residual.
- **A-R3-3 (HIGH · P1/P6 doc — corroborated by 2 verifiers).** Add `docs/adr/002-v041-protocol-additions.md:32` and `:100` to P1's doc-truth-up list — they state the rule is enforced on "all/any same-instance pairs" / "at log time AND session-end", self-contradictory after P1's guard. (Round-2 M1 flagged this; it was not folded into the FINAL plan.)
- **A-R3-4 (MED · M1 hook scope).** The `decision-audit.sh` change is larger than "line 69": the embedded Python needs a participants/event-actor **type-set pre-pass** to compute the ≥2-actor-types guard, not a one-line edit.
- **A-R3-5 (MED · P1 tests, additive to the correct M2 "exactly 2 bug-tests").** Add: (a) session-end `ai→ai` single-actor asymmetry test (`self_resolution_count==1`, `attribution_warning_count==0`); (b) FM25 `ai→ai` single-actor literal-string assertion; (c) multi-actor different-human-instance no-warn test (decouples id-inequality from the multi-actor gate).
- **A-R3-6 (MED · P6 boundary home + CI).** The "one durable ADR" must be a **NEW ADR** — ADR-002 is the 8 v0.4.1 protocol decisions, not a boundary policy. Also add ADR-002 to the (currently absent) `docs/adr` index. **No P-item wires P2's "delete extensions/learn ⇒ 18 tools" invariant test into CI** — add that; an un-CI'd governance gate does not durably satisfy `evt_002`.
- **A-R3-7 (LOW · P5 dep).** The PROV-O round-trip test needs `rdflib` (or `pyld`) added to `pyproject.toml` `[dev]` (currently absent). Test-only, zero runtime risk.
- **A-R3-8 (LOW · P9c severity).** Torn `*.embeddings.npy` is self-healing via the `store.py:286` size-guard (degrades to a perf hit, not a correctness bug) → reclassify MED, note overlap with a correctly-scoped A-R3-2 lock.

## Resolved by Round 3 (no action / decided)

- **P3 / L9.1 counts:** independently re-derived EXACT — 28 events, 15 missing-snippet contributions, 1 missing-snippet correction, 2 same-instance self-resolutions (`evt_001`, `evt_025`). Gate is stable, not fixture-fragile. P1↔P3 safe (waggle union actor types = {ai,human} = 2 → guard passes → count stays 2).
- **M3 / `evt_016`:** re-derived CORRECT per A1 line 39 ("≥2 unique actor **types**", not instances). Outcome-determining; actor-ID drift harmless under type counting. **USER confirm/override.**
- **M1 `--upgrade`:** NOT needed — `trace-mcp-init` unconditionally overwrites changed hooks (`claude_code/__init__.py:78-84`); a plain re-run (already in `CHANGELOG.md:58`) propagates the fix. L11.10 deferral stands. `decision-audit.sh` is the ONLY adopter asset encoding unguarded FM1.
- **P7 tag policy:** R3 recommends **option (a) real annotated tags** `v0.1.0..v0.4.1` — history fully reconstructable (v0.4.1→`d20be80`, v0.4.0→`0540346`, v0.3.0→`50051ec`, v0.2.0→`568c023`, v0.1.0→`7110528`). Green-gate must run FM7-family E2E **serially, after P9(a), on a quiesced machine** or "green" is non-reproducible under session load. **USER decision.**

## Open USER decisions before/at implementation
1. **M3 / `evt_016`** — confirm "≥2 actor TYPES" (A1- and R3-verified) or override.
2. **P7 tag policy** — real annotated tags (R3-recommended) vs commit-range compare-links.
