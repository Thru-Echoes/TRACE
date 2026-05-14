# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.1] — In progress

> **Audit-driven release.** Targets the five quality issues surfaced by the 2026-05-13 waggle-session audit (`audit_2026-05-13_waggle_session/trace_audit_findings.md`). All changes are additive and backward-compatible with v0.3.x and v0.4.0 wire format. Three rounds of independent verification incorporated; remediation plan and HTML checklist live alongside the audit.

### Added (schema — all optional, default-preserving)
- `AnnotationData.category` accepts `"discovery"` — a non-trivial finding from autonomous or unattended work that carries causal load (distinct from `gotcha` and `correction`). SHOULD be logged at the moment of discovery, not in a post-hoc summary.
- `ToolCallData.host: Literal["mcp","internal","external"] = "mcp"` — distinguishes external MCP servers from host-internal tools (subagent dispatchers) and external non-MCP tools.
- `ToolCallData.parent_event_id: str | None = None` — links a dispatch to the controller event that motivated it. Enables manual dispatch-chain logging on day one.

### Added (server-side audit)
- `AttributionAudit` extended with five new counts: `missing_snippet_contribution_count`, `missing_snippet_correction_count`, `explicit_absence_snippet_count`, `orphan_discovery_hint_count`, `attribution_warning_count`. Surfaced in the session-end audit block in severity order.
- Structural attribution-warning detector: counts decisions where `proposed_by == resolved_by` (same Actor instance) in multi-actor sessions — catches the question→AI-proposal→human-accept self-resolution pattern without regex.
- Orphan-discovery hint: surfaces contributions whose description contains discovery-language (`"discovered"`, `"found a bug"`, `"load-bearing fix"`, `"turned out"`) without a near-in-time discovery/correction/gotcha annotation.

### Added (spec)
- §3.4.1 — normative MUST clause on `conversation_snippet` for `contribution` and `correction`-category `annotation`; absence-marker convention (`<autonomous-stretch>`, `<no recent user message>`).
- §3.5 — generalized Tool Invocation to cover external MCP, external non-MCP (HTTP/CLI), and host-internal tools; documented `host` field and `parent_event_id` for dispatch chains.
- §3.6 — **Proposer Identity Rule** with disambiguation table: `proposed_by` MUST identify the actor who authored the proposal content, not who spoke the directive.
- §3.7 — `discovery` annotation category.
- §3.7.1 (new) — External References in `corrects_event_ids`: URI-form anchors (`external:<uri>`, `jsonl:<path>#L<line>`, etc.) when the corrected item is not a TRACE event.
- §4.4 — split: `corrects_event_ids` MAY use URI-form per §3.7.1.
- §5.2 — rewrite Correction Provenance for three anchor cases (event ID / URI / snippet-only).
- §8.1 — real-time logging guidance + autonomous-window detection recommendation (host-implementation specific).
- §8.2 — recognition table rows for question→AI-proposal pattern and discovery language.
- Appendix A — worked example for question→AI-proposal→accept flow with `suggestion_type="requested"`.

### Changed (PROV-LD export — **breaking for PROV consumers matching on `wasRevisionOf`**)
- Correction events now emit either `prov:wasInvalidatedBy` (event-ID target) or qualified `prov:wasInfluencedBy` with `prov:atLocation` (URI target). Previously all corrections emitted `prov:wasRevisionOf`, which conflated repudiatory corrections with evolutionary revisions. Downstream SPARQL/jq queries matching `?correction prov:wasRevisionOf ?event` must be updated.
- New: `parent_event_id` on `tool_call` emits `prov:wasInformedBy`.

### Changed (validators and warnings)
- `_check_referential_integrity` skips URI-form entries (scheme-prefixed strings) in `corrects_event_ids` — without this, the §3.7.1 URI scheme would hard-fail at `append_event`.
- `FM1` (decision self-resolution) generalized: warns when `proposed_by == resolved_by` for any same-instance pair in a multi-actor session, not just `ai→ai`. Catches the systematic `human→human` attribution pattern surfaced by the audit.
- `FM5` snippet warnings (contribution / correction) sharpened to mention the absence-marker convention.
- `FM17` correction-without-anchor warning relaxed: fires only when both `corrects_event_ids` AND `conversation_snippet` are empty. New co-occurrence warning when `corrects_event_ids: []` but `related_event_ids` non-empty on a correction.
- `FM3` (`related_decision_ids`) warning demoted: only fires when the session has at least one decision event.
- `FM23` exploratory-tool warning made `host`-aware: only fires for typical MCP-side names on `host="mcp"`.

### Changed (single source of truth)
- `Environment.trace_version` removed. Single canonical version lives on `Session.trace_version`. Pre-0.4.1 sessions on disk silently drop the redundant field on next save (Pydantic v2 default `extra="ignore"` permits this).
- `Session.trace_version` default bumped from `"0.3.0"` to `"0.4.1"`.
- `schemas/trace-v0.3.json` regenerated and renamed to `schemas/trace-v0.4.json`. References in `session.py`, `generate_schema.py`, `validate_session.py`, `prov_mapping.py`, README, and tests updated. The PROV namespace URI `https://trace-protocol.org/ns/v0.3#` is kept (additive extensions are valid within the v0.3 namespace).

### Migration notes
- **PROV-LD consumers** must update queries matching `prov:wasRevisionOf` for corrections — see "Changed (PROV-LD export)" above.
- **Consumer projects with installed hooks** should re-run `trace-mcp-init` to refresh `decision-audit.sh`. The server-side FM1 generalization is otherwise invisible to consumers running the v0.4.0 hook.
- **Pinned-version Pydantic consumers** parsing v0.4.1-written sessions through older schemas should set `model_config = ConfigDict(extra="ignore")` on their models to tolerate the new optional fields.

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
