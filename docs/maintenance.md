# Maintenance & conventions

Durable conventions for the static quality gates, commit/PR messages, and
releases, plus the maintenance backlog. Process companion to
[CONTRIBUTING.md](../CONTRIBUTING.md) (contributor how-to) and
[docs/INVARIANTS.md](INVARIANTS.md) (correctness invariants).

## Static quality gates

All three gates run on the **full tree** (`src/`, `tests/`, `scripts/`), are
CI-gated on every PR (`.github/workflows/ci.yml`) and at release
(`.github/workflows/release.yml`), and use the **same commands** documented in
CONTRIBUTING and configured in `pyrightconfig.json`:

| Gate | Command | State |
|------|---------|-------|
| Lint | `uv run ruff check .` | clean |
| Format | `uv run ruff format --check .` | clean |
| Types | `uv run pyright` | 0 errors / 0 warnings |

Run all three plus `uv run pytest` before pushing. The gate commands are kept
**identical** across CI, the release workflow, and the docs so they cannot
silently diverge — documented-but-unenforced drift is the failure mode this
convention exists to prevent. When a gate command changes in CI, change it in
the other two places in the same commit.

## Commit & PR messages

See [CONTRIBUTING.md → "Commit and PR messages"](../CONTRIBUTING.md#commit-and-pr-messages).
In short: self-contained, durable prose; preferred subject shape
`type(scope): summary`; PR bodies follow
[`.github/pull_request_template.md`](../.github/pull_request_template.md).

## Tagging & releases

- **One annotated tag per release**, `vMAJOR.MINOR.PATCH`, matching the
  `pyproject.toml` version and the `CHANGELOG.md` section, created on the
  release commit at release time (not retroactively).
- Versioning follows [SemVer](https://semver.org); the changelog follows
  [Keep a Changelog](https://keepachangelog.com).
- `release.yml` is **manual-only** (`workflow_dispatch`); it has no push or tag
  trigger. Dispatching it with the `confirm` input left blank builds and
  verifies the shipped distribution (a safe dry run); only a dispatch with
  `confirm: PUBLISH` uploads to PyPI. **PyPI publication remains paused**
  pending finalization of the distribution name, so a release is currently
  "built + verified", not "published". Because no tag or GitHub Release can
  trigger a publish, version tags and Releases are safe to create — e.g. for
  Zenodo DOI archival — while the pause is in effect.

## Maintenance backlog

- Keep the CONTRIBUTING test-count headline current as the suite grows (the
  authoritative number is the `uv run pytest` total); the per-area table is
  representative, not exhaustive.
- Periodically confirm each documented gate command still matches CI exactly —
  the recurring drift this file guards against.
