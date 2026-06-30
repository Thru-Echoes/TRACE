<!--
Keep this body self-contained: a reader with no access to the authoring context
must understand it. No conversational narrative, AI-attribution footers, internal
plan labels, agent/review counts, or bare finding codes. See CONTRIBUTING.md →
"Commit and PR messages". Keep it proportional — a tiny PR may use only Summary
and Verification.
-->

## Summary

- <what this PR does, in durable terms>

## Why

<the problem / failure mode / need this addresses>

## Verification

- [ ] `uv run ruff check .` and `uv run ruff format --check .`
- [ ] `uv run pyright`
- [ ] `uv run pytest`
- [ ] Built & verified the shipped artifact, if packaging/release changed
      (`uv build && uv run pytest tests/test_packaging_artifacts.py`)
- [ ] Ran the invariant guard, if a write/read path or invariant changed
      (`uv run pytest tests/test_invariants.py`)
- [ ] Regenerated the JSON Schema, if schema models changed
      (`python scripts/generate_schema.py`)

<!-- Optional sections — include only when relevant:

## Design / ADR
<link or summarize the decision; reference docs/adr/NNN>

## Risks / rollback
<compatibility / data / migration risk, or why risk is low; how to roll back>

## References
- path/to/file.py
- docs/adr/NNN-title.md
- Closes #NNN
-->
