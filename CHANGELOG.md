# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] — 2026-04-29

### Added
- Host adapter layer in `src/trace_mcp/adapters/` (`base/`, `claude_code/`, `codex/`).
  Adapters are pure installers — they never import into the MCP server runtime.
- `trace-mcp-init --client {claude-code,codex,none,auto}` and `--dry-run` flags.
- Project-aware Claude Code hooks installed by the adapter:
  - `SessionStart` reminder that only fires when an active session matches the
    current project (CLAUDE.md `TRACE project name: "..."` marker → git basename
    → cwd basename detection order).
  - `UserPromptSubmit` nudge with per-project rate limiting
    (`TRACE_PROMPT_MIN_TURNS`, `TRACE_PROMPT_COOLDOWN_SEC`, runtime state in
    `~/.trace/runtime/<project>.state.json`).
  - `PreToolUse` soft-mode guard for `Edit|Write` operations
    (`TRACE_GUARD={off,soft,strict}`, default `soft`).
  - `PostToolUse` decision-audit hook on `trace_end_session`.
- `docs/adr/001-trace-auto-start.md` — first Architecture Decision Record,
  archiving the auto-start failure analysis that motivated the adapter layer.
- `docs/examples.md` consolidating worked decision/correction/contribution
  examples (migrated out of the deleted `claude-code-skill.md`).

### Changed
- Renamed `trace_mcp.hooks` → `trace_mcp.extension_hooks` to free the "hooks"
  namespace for the host-adapter layer. **Breaking** for any extension that
  imports `trace_mcp.hooks` directly; internal callers updated.
- `init_project.py` rewritten as a thin dispatcher that delegates host-specific
  install logic to the adapter for the chosen `--client`.

### Removed
- `docs/claude-code-skill.md` (stale; superseded by the global `/trace-session`
  Claude Code skill and the `docs/examples.md` consolidated examples).

### Fixed
- `trace-mcp-init` invoked via `uvx` no longer writes a per-machine uvx
  cache path into `.mcp.json`. Resolution order is now: `TRACE_SOURCE_PATH`
  env var → PyPI package name `trace-mcp` (for wheel installs) → repo root
  (for editable installs).

### Removed (repo split — 2026-04-29)
- `manuscript/` (gitignored; ~1.7 GB of paper, lit-review, talks) moved to
  the sibling repo `TRACE-research`. No git history was lost — `manuscript/`
  was never tracked in TRACE.
- 4 tracked literature-download scripts (`scripts/batch_proxy_download.py`,
  `scripts/browser_batch.py`, `scripts/browser_download.py`,
  `scripts/download_fulltext.py`) moved to `TRACE-research`. Their history
  remains accessible in this repo's `git log` for archaeology.
- 15 untracked talk-build / lit-audit / coder-comparison scripts moved.
- Obsolete research-side `.gitignore` entries pruned (`manuscript/`,
  `lit_review/`, talk-summary markdown files, manuscript-side scripts).
- Result: TRACE is now ~7 MB instead of 1.7 GB; the public face of the
  repo is the package and its tests/docs only. Provenance of the split is
  recorded in `TRACE-research/PROVENANCE.md`.

## [0.3.0] — 2026-04-15

### Added
- **Attribution audit** returned by `trace_end_session` for self-review.
- **Scratchpad** auto-generation: session summary appended to
  `.claude/SCRATCHPAD.md` for context restoration in the next session.
- **`conversation_snippet`** field on contributions, annotations, and decisions
  (~200-char user-message excerpt for provenance).
- **Embedding backend** for knowledge recall (OpenAI + model2vec).
- **Decision guards**: `trace_resolve_decision` raises `ValueError` on
  invalid event IDs / dispositions to fail fast.
- **Self-hosting via `uvx`**: `.mcp.json` and consumer-project init switched
  to `uvx --from <path> --refresh-package trace-mcp trace-mcp` — no more
  `.venv` dependency or `bin/trace-mcp-server` launcher.
- "Why decision provenance?" and "Preliminary deployment results" sections
  in README, including motivation and 10-project deployment metrics.
- Tier 2 trace-learn features: decay/staleness, Jaccard content dedup,
  recall tracking, knowledge metrics in `trace_project_summary`.

### Changed
- Test suite made generic — consumer-project paths now passed via
  `TRACE_CONSUMER_PROJECTS` env var rather than hard-coded.

### Removed
- `trace-evolve` extension (replaced entirely by `trace-learn` as default).
- Dead schema fields and the legacy `bin/trace-mcp-server` launcher.
- `TRACE_PINNED_VERSION` env var (no longer needed under `uvx`).

## [0.2.0] — 2026-02-16

### Added
- **Contributions** with direction (who had the idea) vs execution
  (who did the work) attribution.
- **Corrections** via `category="correction"` annotations linking to the
  events being corrected (`corrects_event_ids`).
- **Retry chains** via `retries_event_id` on tool calls.
- **Suggestion types** on decisions: `proactive` / `requested` / `collaborative`.
- **Human intervention metrics** in `trace_project_summary`.
- **trace-learn extension**: cross-session knowledge persistence with
  LLM-primary matching + extraction (BM25 fallback), 3-layer recall, and
  five new MCP tools (`trace_learn_recall`, `trace_learn_add`,
  `trace_learn_list`, `trace_learn_forget`, `trace_learn_extract`).
- Search indexes `corrects_event_ids` and `conversation_snippet`.
- TRACE protocol v0.2 with tiered priority, "no fabrication" absolute rule,
  session-end checklist, and correction-vs-gotcha-vs-decision-rejection
  guidance.

## [0.1.0] — 2026-02-02

### Added
- Initial TRACE MCP server (FastMCP-based).
- Pydantic v2 schemas for `Session`, `TraceEvent`, decisions, annotations,
  tool calls, state changes.
- JSON-file storage backend in `~/.trace/sessions/` with atomic writes.
- Core MCP tools: session lifecycle, decision propose/resolve, annotation,
  tool-call logging, session and event queries.
- Knowledge persistence, behavioral checks, checkpoints.

[Unreleased]: https://github.com/Thru-Echoes/TRACE/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/Thru-Echoes/TRACE/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Thru-Echoes/TRACE/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Thru-Echoes/TRACE/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Thru-Echoes/TRACE/releases/tag/v0.1.0
