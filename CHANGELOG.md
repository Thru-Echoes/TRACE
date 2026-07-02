# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Egress kill switch + point-of-use disclosure

- **`TRACE_LOCAL_ONLY=1` — one switch, no egress anywhere.** Forces all three
  `trace-learn` cloud paths off in a single flag: OpenAI embeddings, LLM
  extraction, and LLM matching. Enforced at config load (it overrides an explicit
  `TRACE_EMBEDDING_BACKEND=openai` down to local `auto` and disables LLM features
  regardless of key presence), so every downstream reader honors it. This closes
  the "off-switch trap" where disabling one path (`TRACE_LLM_ENABLED=false` or
  `TRACE_EMBEDDING_BACKEND=none`) still left the other egressing content.
- **Point-of-use egress disclosure.** The `trace_learn_extract`,
  `trace_learn_recall`, and `trace_learn_add` tool docstrings now state, at the
  point of use, that content is sent to OpenAI when the OpenAI backend/LLM is
  configured, and how to stay local (`TRACE_LOCAL_ONLY=1`).
- **Egress ledger (egress-as-provenance).** Every cloud call trace-learn makes
  (LLM extraction, LLM matching, OpenAI embeddings) now appends one JSONL line
  to `~/.trace/egress.jsonl` (override: `TRACE_EGRESS_LOG`) — recording the
  FACT of the call (provider, endpoint, model, purpose, item count, and
  project/session where the call site knows them), never the content. The
  attestation is written **before** the request and **fails closed**: if the
  ledger cannot be written, the cloud call does not happen — under permissive
  config the caller falls back to the local path (BM25 / rule-based /
  un-embedded), under strict config it raises. Registered as **INV-5** in
  `docs/INVARIANTS.md` with an AST enumeration guard, so a new OpenAI call
  site cannot merge without attesting. The test suite isolates the ledger the
  same way it isolates the session/knowledge stores (`tests/conftest.py`).

### Local-strong embedding tier + local-first default

- **Fully-local, open-weight embedding option (`fastembed` backend).** Adds a
  `TRACE_EMBEDDING_BACKEND=fastembed` provider (ONNX Runtime, no PyTorch) so
  users can get retrieval markedly stronger than the static `model2vec`/BM25
  floor **without** sending any content to OpenAI. Ships as an optional extra,
  `pip install 'trace-mcp[local-embed]'`. A curated, permissive-license model
  allowlist (`snowflake/snowflake-arctic-embed-s` default, plus `-m`,
  `BAAI/bge-small-en-v1.5`, `BAAI/bge-base-en-v1.5`) is selectable via
  `TRACE_EMBEDDING_MODEL`; any other id is honored with a license/dimension
  warning. See [docs/embeddings.md](docs/embeddings.md).
- **Bring-your-own OpenAI-compatible endpoint (`base_url` passthrough).** The
  `openai` backend now honors `OPENAI_BASE_URL` / `TRACE_OPENAI_BASE_URL`, so it
  can target any local OpenAI-compatible server (Ollama / LM Studio /
  text-embeddings-inference / vLLM) — a fully-local path that keeps TRACE out of
  model-weight and license management.
- **Local-first `auto` selection (behavior change).** `auto` now prefers a local
  backend (`fastembed` → `model2vec` → BM25) and **never** auto-selects OpenAI:
  a mere `OPENAI_API_KEY` on the machine no longer routes embedding content to a
  third party. Cloud embeddings are opt-in via `TRACE_EMBEDDING_BACKEND=openai`.
  Existing stores re-embed to the newly-selected model on next recall (the model
  change is detected per learning); the `.npy` sidecar writer now tolerates
  mixed-dimension rows during that migration instead of erroring.

### Diagnostics

- **Offline self-cost report (`python -m trace_mcp.selfcost`).** A standalone,
  read-only analyzer that estimates TRACE's own token footprint without adding
  any MCP tool (so running it does not enlarge the tool-schema surface it
  measures) and without touching any session record. It reports the tool-schema
  surface under cold-write/warm-read prompt-caching regimes — so the
  always-loaded cost is presented as a write-once/cache-read amount, not
  mis-stated as a full per-turn tax — a per-session estimate of the tokens spent
  authoring `trace_*` calls (scoped to `host="mcp"` events, since
  `host="internal"/"external"` calls are logged by reference and not sizeable
  from the server side), and a trace-learn reuse signal carrying a data-quality
  flag (`recall_count` tracks surfacing, not use, so it is never converted to
  "tokens saved"). Every figure is a labeled `chars/4` estimate, and the report
  points at the host's OpenTelemetry `claude_code.token.usage` metric for the
  authoritative measured number. The carbon/token telemetry direction is
  recorded in [docs/adr/004-telemetry-sidecar.md](docs/adr/004-telemetry-sidecar.md):
  carbon and self-cost are one optional, estimate-provenanced sidecar concern,
  never core.

### Integrity hardening

- **The per-session lock now fails closed.** On lock-acquisition timeout,
  `JsonFileStorage.lock` previously logged to stderr and proceeded **unlocked**,
  silently reopening the lost-update / duplicate-event-id window the lock exists
  to close. It now raises `TimeoutError` (surfaced to the MCP caller as a
  structured error) — a missed lock is visible, never silent. Stale-lock theft
  is keyed on **holder PID liveness** (single-host) with a `<pid>:<time_ns>`
  token re-verified before unlink, so a live long-running holder's lock is never
  stolen and a crashed holder's lock is reclaimed at once. **Behavior change:**
  session writes can now surface a lock-timeout error rather than degrading to an
  unsynchronized write.
- **`append_event` refuses to mint a duplicate event id** instead of silently
  aliasing `revises_event_id` / `parent_event_id` / `corrects_event_ids`
  references when the on-disk record is already in an aliased state.
- **One schema-invalid session file no longer bricks the read aggregates.**
  `trace_project_summary` and `trace_health_check` now catch per-session
  `ValidationError` / `JSONDecodeError`, log and skip the bad record, and return
  the skipped ids in a new **`skipped_sessions`** list — instead of aborting the
  whole aggregate on one bad file. **Behavior change:** both tools' return dicts
  gain a `skipped_sessions: list[str]` key.
- **Forward-compatible schema (no silent field-stripping).** Schema models now
  preserve unknown fields (`extra="allow"` via a shared `TraceModel` base), so an
  older server that loads a newer-schema session and rewrites it no longer
  durably deletes fields it did not recognize; `get_session` logs a version-skew
  warning. `Environment` intentionally stays closed (legacy-field drop). The
  generated JSON Schema now carries `additionalProperties: true` on those models.
- **Consolidated the three session write paths** (`append_event`, `end_session`,
  `resolve_decision`) onto a single `locked_disk_session` helper — the lock +
  disk-reload + immutability sequence was previously hand-copied across all three
  — so "write under the fail-closed lock against disk truth" is one
  implementation. Registered as **INV-1** in the new `docs/INVARIANTS.md`.

### Fixed

- **Auto-created sessions no longer inherit a foreign project.** `_infer_project`
  fell back to the most-recent session across the *shared* store, so when the
  newest session on disk belonged to a different project, a pointer-less
  `trace_log_*` call auto-created a session — and routed any learnings later
  extracted from it — under that unrelated project. With no
  `TRACE_DEFAULT_PROJECT` it now returns the stable `"auto"` sentinel rather than
  a foreign project name.
- **Corrected the OpenAI-key precedence docstring.** `config.py` described
  resolution as "first match wins" and labeled `./.env` a "project-level
  override", but the real behavior is a merge where the global `~/.trace/.env`
  OVERRIDES a project-local `./.env` (env var > global > project). The docstring
  now states this so a project's `./.env` is not mistaken for an override of the
  shared key.
- **Wheels built from the sdist were missing 7 runtime files** (`py.typed` and
  all six `adapters/claude_code/assets/` files: the settings template, the
  CLAUDE block, and the four hook scripts). The sdist allowlist's only src
  pattern was `src/trace_mcp/**/*.py`, and `uv build` constructs the wheel
  *from the sdist* — so a clean-venv install of the built wheel installed
  zero hooks silently, then `trace-mcp-init` crashed with `FileNotFoundError`.
  Local `uvx --from <path>` launches build direct-from-tree and were never
  affected. New **positive packaging guards**
  (`tests/test_packaging_artifacts.py`) build via `uv build` and assert
  required files are *present* in both artifacts (the release leak-guard only
  ever checked that private files were *absent*), including a clean-venv
  wheel-install end-to-end test.
- **`trace-mcp validate` crashed on any installed package**: it loaded
  `scripts/validate_session.py` via a repo-relative path that only exists in a
  source checkout, and the JSON Schema wasn't shipped. The validator now lives
  in the package (`trace_mcp.validate`) and loads `schemas/trace-v0.4.json` as
  package data via `importlib.resources`. `scripts/validate_session.py`
  remains as a compatibility shim. `scripts/generate_schema.py` writes both
  byte-identical schema copies; freshness and identity are test-guarded.
- **README now renders correctly on PyPI**: all relative links and image srcs
  (which 404 without repo context) are absolute GitHub / raw.githubusercontent
  URLs, enforced by a regression test (`tests/test_readme_pypi.py`).

### Added

- `validate` optional extra (`pip install "trace-mcp[validate]"`) for the
  `jsonschema` dependency backing `trace-mcp validate`; also included in
  `[all]`. A missing dependency now produces an install hint instead of a
  stack trace.

### Changed

- **Test-suite hygiene: the default suite no longer reads or writes the
  developer's real `~/.trace` data.** A root conftest points
  `TRACE_KNOWLEDGE_DIR`, `TRACE_SESSIONS_DIR`, and `TRACE_SCRATCHPAD_DIR` at
  per-run temp directories, set in `pytest_configure` so even module-level
  imports during collection resolve the isolated paths. Previously, suite
  runs deposited `e2e-*`/`fm-test`/`guard-e2e`/`export-test` stores into the
  real knowledge dir and **overwrote the developer's real
  `.claude/SCRATCHPAD.md` on every run** (session-end scratchpads from e2e
  server subprocesses). All four real-data test classes
  (`TestRealDataEmbeddings`, `TestRealDataE2E`, `TestRealSessionDataIntegrity`,
  `TestCrossProjectConsistency`) are opt-in via `TRACE_REAL_DATA_TESTS=1` — the
  registered `real_data` marker is load-bearing (a conftest collection hook
  skips marked tests unless opted in, so a class can't forget its gate), and
  their recall expectations are derived from store content at runtime instead
  of pinned learning IDs, so personal-store drift can't fail them. Config
  tests that need key-absence scrub the env var, `~/.trace/.env`, **and** the
  checkout's `./.env`; the real-LLM integration tests intentionally still
  read the developer's real key. `LearnConfig.openai_api_key` is `repr=False`
  so the key cannot leak into pytest output or logs; the uvx installation
  check (`test_mcp_json_command_resolves`) skips instead of failing when uv
  isn't installed.

## [0.4.2] — 2026-06-01

> **Crash-surface + publication-hardening release.** Reduces TRACE's contribution to a Claude Code extended-thinking API-400 (a *client-side* signed-thinking-block re-serialization bug that TRACE cannot fix — only avoid triggering), fixes a critical storage data-loss bug, caps query payloads, and makes the package safe and ready to publish. The upstream client report is in `docs/upstream-claude-code-thinking-block-400.md`.

### Fixed
- **CRITICAL — storage lost-update / event-ID collision.** `append_event` did an unsynchronized read-modify-write with positional `evt_{len+1}` IDs, so a second writer (another process, or a stale in-memory `Session`) clobbered the first writer's event and both were assigned the same id — silent provenance loss, contradicting TRACE's core guarantee. It now reloads the authoritative on-disk events under a portable per-session lock before appending, and `_write_file` fsyncs before `os.replace`. No new dependency (cross-platform `O_CREAT|O_EXCL` lockfile; core stays mcp + pydantic).
- **trace-learn recall accounting.** `recall_learnings` incremented `recall_count` / `last_surfaced` for *every* above-threshold match before the `[:limit]` slice, inflating recall counts and resetting decay clocks for learnings that were never surfaced. It now mutates only the surfaced top-`limit`.

### Changed
- **`trace_start_session` is now a cheap, quiet bootstrap.** `recall_learnings` defaults to **False** (was True); the response carries a bounded prior-session orientation plus a sequential-cadence note so the model need not fan out into `trace_list_sessions` / `trace_get_events` / `trace_health_check` at session start — the opening MCP fan-out that inflated a single interleaved-thinking turn. Removed the start-time double-recall.
- **Hard payload caps on query tools** (context-bloat guard — caps clamp rather than honour an over-large request): `trace_search` was UNBOUNDED → default 25 / max 100 and now returns an object `{query, total_matched, returned, truncated, results}` (**breaking**: was a bare list); `trace_get_events` default 100 → 25 (max 200); `trace_health_check` / `trace_project_summary` read ≤ 500 session files (was 10000 / 1000) with a `scan_truncated` flag.
- **Query/retrieval tools emit compact JSON** (no indent) — their output lands in the model context where indentation is ~20-30% token waste. `trace_export` keeps pretty (indented) JSON for the human/artifact path and gains a `pretty=False` toggle for compact artifacts.

### Added
- `JsonFileStorage.session_brief()` (bounded orientation), `JsonFileStorage.lock()`, `session_tools.format_bootstrap_message()`, `export_session(pretty=...)`.
- PyPI metadata (`readme`, authors, keywords, classifiers, `[project.urls]`) — `twine check` now passes; `NOTICE`, `SECURITY.md`, `server.json` (MCP registry manifest).
- `.github/workflows/release.yml` — tag-triggered build + leak guard + `twine check` + PyPI Trusted Publishing (OIDC).
- Regression suites: `test_v042_cheap_bootstrap`, `test_v042_payload_caps`, `test_v042_storage_concurrency`, `test_v042_recall_count`; plus `docs/upstream-claude-code-thinking-block-400.md`.

### Security / packaging
- **Stopped the sdist/wheel from shipping private + cruft files.** The v0.4.1 sdist included `notes/` (confidential IP/legal material marked "do not share externally") and a ~4 MB crash-handoff tree; the wheel installed a macOS-duplicate `extension_status 2.py`. Deleted 7 duplicates, hardened `.gitignore` (default-deny `notes/`, `*-handoff-*/`, `* 2.*`), and added an explicit hatch sdist include-allowlist + global exclude. The release workflow's leak guard fails the build if any slip through.

### Test infrastructure
- pytest `pythonpath = ["src"]` so collection no longer depends on a fragile editable install (`uv run` re-syncs were dropping it and silently breaking `uv run pytest`); e2e server tests inject `src` on the subprocess `PYTHONPATH` and force offline BM25 so they no longer block on a model2vec cold-load. The model2vec-dependent matching test is `importorskip`-guarded.

### Docs
- Corrected stale counts: **22 tools (17 core + 5 trace-learn)** (was "23 / 18 core"), test count "322+" → "880+". Added the "≤1–2 trace calls per turn, never batch, don't fan out at session start" cadence guidance to the global protocol and the `trace-session` skill (maintained out-of-repo).

### Migration notes
- **`trace_search` response shape changed (breaking).** It now returns an object
  `{query, total_matched, returned, truncated, results}` instead of a bare list.
  Consumers that indexed the result directly should read the `results` array
  (`resp["results"]`), and may check `truncated` / `total_matched` for capped queries.
- **Versioning:** shipped as **0.4.2** under SemVer §4 (pre-1.0, `0.y.z`): the
  LLM-facing breaking changes above (`trace_search` shape, `recall_learnings`
  default) are permitted within this bump, and the on-disk wire format is
  unchanged (still schema v0.4.1).

### Deferred
- The `.npy` embedding sidecar is redundant (embeddings already persist in the JSON store) but is an intentional, tested feature; removing it would break the embeddings tests, and the correct fix (exclude embeddings from JSON and load from the sidecar) is an architectural change with migration cost — deferred to a future release.

## [0.4.1] — 2026-05-18

> **Audit-driven release.** Targets the five quality issues surfaced by the 2026-05-13 waggle-session audit (`audit_2026-05-13_waggle_session/trace_audit_findings.md`). All changes are additive and backward-compatible with v0.3.x and v0.4.0 wire format. Three rounds of independent verification incorporated; remediation plan and HTML checklist live alongside the audit.

### Added (schema — all optional, default-preserving)
- `AnnotationData.category` accepts `"discovery"` — a non-trivial finding from autonomous or unattended work that carries causal load (distinct from `gotcha` and `correction`). SHOULD be logged at the moment of discovery, not in a post-hoc summary.
- `ToolCallData.host: Literal["mcp","internal","external"] = "mcp"` — distinguishes external MCP servers from host-internal tools (subagent dispatchers) and external non-MCP tools.
- `ToolCallData.parent_event_id: str | None = None` — links a dispatch to the controller event that motivated it. Enables manual dispatch-chain logging on day one.

### Added (MCP wrapper)
- `trace_log_tool_call` in `server.py` now exposes the `host` and `parent_event_id` parameters so the v0.4.1 schema fields are reachable through the public MCP interface (previously only the internal `logging_tools.log_tool_call` function accepted them — a release-gate verifier caught the gap). Defaults preserve v0.3.0 / v0.4.0 semantics (`host="mcp"`, `parent_event_id=None`). Six new E2E tests in `tests/test_v041_tool_call_wrapper.py` verify the wrapper passes both fields through, that invalid `host` values are rejected by Pydantic, and that dangling `parent_event_id` surfaces a referential-integrity warning.

### Added (server-side audit)
- `AttributionAudit` extended with five new counts: `missing_snippet_contribution_count`, `missing_snippet_correction_count`, `explicit_absence_snippet_count`, `orphan_discovery_hint_count`, `attribution_warning_count`. Surfaced in the session-end audit block in severity order.
- Structural attribution-warning detector: counts decisions where `proposed_by == resolved_by` (same Actor instance) in multi-actor sessions — catches the question→AI-proposal→human-accept self-resolution pattern without regex.
- Orphan-discovery hint: surfaces, as a low-severity hint (not a warning), contributions whose description contains discovery-language (`"discovered"`, `"found a bug"`, `"load-bearing fix"`) without a near-in-time discovery/correction/gotcha annotation.

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
- `schemas/trace-v0.3.json` renamed to `schemas/trace-v0.4.json` and regenerated to include v0.4.1 fields (`discovery` category, `host`, `parent_event_id`). The `$id` inside the schema is updated to `https://trace-protocol.org/schemas/trace-v0.4.json`. References updated in `scripts/generate_schema.py`, `scripts/validate_session.py`, `README.md`, `CONTRIBUTING.md`, `docs/specification.md`, and `tests/test_specification_conformance.py`. Per ADR 002 D6, the spec URL `https://trace-protocol.org/v0.3` in `Session.context` and the PROV namespace URI `https://trace-protocol.org/ns/v0.3#` in `prov_mapping.py` remain at v0.3 — additive extensions are valid within the same namespace.

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

[Unreleased]: https://github.com/Thru-Echoes/TRACE/compare/v0.4.2...HEAD
[0.4.2]: https://github.com/Thru-Echoes/TRACE/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/Thru-Echoes/TRACE/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/Thru-Echoes/TRACE/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Thru-Echoes/TRACE/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Thru-Echoes/TRACE/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Thru-Echoes/TRACE/releases/tag/v0.1.0
