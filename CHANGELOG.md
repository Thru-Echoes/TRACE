# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **On the 0.4.1 and 0.4.2 sections.** Both were written up here but never
> tagged, so no git tag or GitHub Release exists for either and neither has a
> comparison link. Their changes ship in the v0.5.0 tag, whose comparison link
> therefore spans from v0.4.0.

## [Unreleased]

### Added

- **`trace-mcp doctor [DIR] [--live] [--json]` — deployed-state conformance
  (INV-11).** The unit suite proves the source tree is correct; it cannot prove
  a *deployment* is. The doctor checks one project directory against this
  build's own shipped artifacts: the `.mcp.json` launch entry (uvx command, a
  resolvable `--from` source that is not the unrelated PyPI distribution, the
  three trace-learn `--with` extras, a refresh flag), the host hook deployment
  (all shipped scripts present, executable, carrying this build's
  `[trace-hooks vX.Y]` stamp, registered in `settings.json`, and — the defect
  that left the decision-audit hook dead in most deployed projects — attached
  to the namespaced `mcp__<server-key>__trace_end_session` matcher), and the
  three project-pin sites, which must all canonicalize to one key. `--live`
  additionally spawns the project's own configured command and verifies the
  build it actually serves (version and the 22-tool surface), which is the only
  way to catch a warm package cache serving a stale wheel despite
  `--refresh-package`; the finding carries the remedy. Exits non-zero on any
  failing check, mirroring `trace-mcp identity check`; `--json` emits a
  `DoctorReport` with stable check ids (`0` clean, `1` findings, `2` usage error; diagnostics on stderr).
  A hook is checked under **its own host event** — one registered under the
  wrong event is installed, current, executable, and still never fires — and
  TRACE-stamped scripts this build no longer ships are reported as leftovers.
- **INV-11 — a freshly initialized project is conformance-clean.** A new
  registry row binds the installer's output to the checker's expectations, so
  template rot (a hook matcher that never fires, a stale asset, a launch config
  missing the extras that make a 22-tool server) fails at PR time instead of
  months later on a consumer's machine. The guard is parametrized over the
  adapter registry: a new host adapter is checked the moment it ships.

### Fixed

- **Learn tools now enforce the `TRACE_PROJECT` pin (INV-9, ADR-006).** All
  five `trace_learn_*` tools resolve their `project` argument through the same
  scope rule as `trace_start_session`: under a pin, an omitted `project`
  resolves to the pinned project and a label resolving to any other key errors
  naming both keys; unpinned, an explicit label is required. Previously a
  pinned server accepted any foreign label on the learn surface — a
  cross-project read/write bypass of the documented fail-closed guarantee.
  `project` is now optional on all five tools (it was required), which is
  backwards-compatible for MCP callers, which pass arguments by name. When a
  pinned call is accepted through a registry alias whose canonical form
  differs from the pinned key, the operation is keyed to the pinned project's
  store — an accepted alias can never open a different store file than the
  project it was authorized against.
- **`trace_learn_recall` no longer returns unranked listings dressed as
  results.** A call with neither `context`/`query` nor `tags` previously
  returned the store's first N learnings in insertion order under a
  `"results"` key, with no scores and no warning — indistinguishable from a
  ranked match. It now returns an explicit error pointing at
  `trace_learn_list`; whitespace-only query text and blank tag elements are
  treated as absent rather than ranked against; and every ranked recall
  response carries a `backend` field naming the engine selected for the
  ranking (`bm25`, `embedding:<model>`, `llm:<model>` — the embedding backend
  scores learnings that lack embeddings through its internal BM25 fallback),
  so a degraded backend configuration is visible to the caller. This is a
  protocol-visible behavioral change: a caller that relied on the no-query
  listing must switch to `trace_learn_list`.
- **`trace_learn_recall` accepts `query` as an alias for `context`.** MCP
  argument models silently drop unknown fields, so a client sending
  `query=...` used to have its query text ignored entirely (surfacing as
  insertion-order "results"). Either name works now; passing both with
  different values is an error, never a silent preference.

## [0.5.0] — 2026-07-30

### Added

- **A shipped-launch-path guard.** `TestShippedLaunchPath` installs the built
  wheel into a clean virtualenv with no lockfile — the dependency resolution a
  consumer actually gets — and asserts the server imports and registers its core
  tools. The existing `uvx` check covers the same path but passes
  `--refresh-package trace-mcp`, which refreshes the package and not its
  transitive dependencies, so a machine whose cache already held a working
  environment stayed green while a cold-cache consumer got a dead server. The
  new guard cannot be masked that way, and it asserts that tools *register*
  rather than only that the module imports.
- **A provenance figure generated from real capture data.** `scripts/make_provenance_animation.py`
  renders any session file as a self-contained animated SVG plus a still frame,
  with light and dark styling and no external assets. Event type, category,
  actor, direction/execution, disposition and correction targets are copied
  verbatim; only descriptions are shortened, at a word boundary.

- **Honest bounded session orientation.** `session_brief` scans newest-first up
  to a `read_ceiling` (default 200 files), stopping early at `scan_cap`
  matches, and reports `window_exhausted` when the ceiling was hit with more
  files beyond it. The previous single 25-file window made the session-start
  orientation assert a false absolute — a project whose newest session sat 26
  files back in a busy shared store read as "No prior TRACE sessions". The
  bootstrap now says "no sessions found in the newest ~N (older history not
  scanned)" in that case; the absolute claim survives only when the whole
  store was actually seen. `session_brief` is also promoted onto the
  `TraceStorage` contract with an honest generic default, so a non-file
  backend cannot silently lack orientation.
- **`TRACE_REQUIRE_PIN` closes the auto-create path.** The flag previously
  gated only `trace_start_session`, so on a require-pin fleet an unpinned
  stray process still auto-created quarantine sessions on its first logging
  call — the exact capture the operator opted to fail closed on. Both
  session-creation paths now refuse; using an *existing* session by explicit
  id keeps working, and the flag remains default-off (capture-over-attribution
  stays the default posture).
- **Per-project scratchpad fallback.** The global fallback directory
  (`~/.trace/scratchpads/`) is shared by every project, and the scratchpad is
  most-recent-session-only — so whichever project ended a session last
  silently clobbered another project's context-restoration buffer. Fallback
  files are now named by canonical project key (registry-aware, so an aliased
  legacy label lands in its real project's buffer). A project checkout's
  `.claude/` and an explicit `TRACE_SCRATCHPAD_DIR` keep the stable
  `SCRATCHPAD.md` name — those locations are per-project by construction.
- **Egress ledger rows carry a `project_key` column (all three purposes).** The
  matching and embedding attest sites sit in layers that deliberately know
  nothing about projects, so their rows were unattributable; the canonical key
  now travels from the tool boundary in a context variable (`egress_project`)
  and every row is attributed. The extraction site passes the key explicitly,
  resolved through the alias registry so an aliased legacy label attributes to
  its real project. Rows written before v0.5 carry no key and are never
  rewritten. The ledger stays one global append-only file.
- **Per-project privacy ratchet is now applied** (`effective_learn_config`).
  A project's registry entry (`local_only` / `llm_enabled` /
  `embedding_backend_max`) tightens the machine-global config at all three
  egress decision points — extraction, LLM matching, and embedding-backend
  selection — for that project's calls, pinned or not. Restrict-only: an entry
  can force a confidentiality-bound project fully local against a permissive
  global, never loosen a global restriction. If the registry exists but is
  unreadable, learn tools fail closed (the posture that would forbid the
  egress may live in the unreadable file) while session capture is unaffected;
  the auto-recall/extract hooks skip loudly instead of raising, because they
  run inside capture paths that must complete.
- **Project-wide extraction enumerates the store by direct glob.** It
  previously went through the query layer, whose 500-file scan cap silently
  hid the oldest sessions of a large store — exactly the ones a project-wide
  extraction exists to mine. Matching is by canonical key; storage backends
  without a filesystem location fall back to the query path.

### Changed

- **`load_store` fails closed on a corrupt knowledge store.** A file that
  exists but cannot be parsed or validated now raises `StoreLoadError` instead
  of returning a fresh empty store. The silent fallback meant the next save
  atomically replaced the damaged-but-recoverable original with an empty
  store, and read to callers as "this project has no learnings" when the truth
  was "this project's learnings are unreadable". A missing file still returns
  a fresh store (normal first use). Read aggregates (`project_summary`,
  self-cost) skip-and-report the damage rather than aborting; the learn tools
  surface a clear error with the file untouched.

- **`trace-mcp identity` migration tooling (ADR-006 S6).** Seven subcommands turn
  the identity enforcement machinery into an operator can run against a store that
  predates canonical keys, without ever rewriting a capture record:
  - `snapshot` — back up `~/.trace` (counting by direct glob, so the oldest
    sessions the 500-cap query would hide are included) and write the marker the
    destructive phases require.
  - `scan` — group every drifted label by canonical key into a human-editable
    plan; writes no registry state.
  - `apply` — mint the reviewed plan into the registry (idempotent; snapshot-gated).
  - `check` — report drift (stray knowledge stores, session labels resolving to no
    registered project, registry health); non-zero exit on findings. Shares one
    drift-detection function with the health tool, so the two can never disagree.
  - `merge-stores` — consolidate an alias group's knowledge stores into the
    canonical-key store: Jaccard-deduped union, sources kept as `*.premerge-<date>`,
    sidecar regenerated (never migrated), every merge logged to `migrations.jsonl`
    with content hashes. Idempotent, snapshot-gated, and refuses to run while any
    writer is active. The `auto` quarantine is never merged.
  - `adopt` — re-home an `auto`-quarantine session into a real project, append-shaped
    (records old→new as a `state_change` and stamps `project_key`, never an in-place
    edit); refuses to relabel a real project's session.
  - `bundle` — export one project (sessions, knowledge store, matching egress rows,
    registry entry) as a self-contained tarball, selected by canonical match.
- **Health check reports project identity.** `trace_health_check` gains a
  `pinned` / `bound_project_key` / `registry` / `stray_knowledge_stores` block,
  computed by the same core drift detector as `identity check`.

- **Canonical project identity (spec v0.5.0, schema `trace-v0.5.json`).** A project
  is now identified by a stable canonical key derived from its display label, rather
  than by the label itself. Previously, storage identity and query identity were two
  inconsistent equivalence relations over the same labels: session queries compared
  labels with exact case-sensitive equality, so case variants of one project read as
  separate projects, while the knowledge store's filename derivation on a
  case-insensitive filesystem silently merged some of those same pairs. The canonical
  key is casefolded and free of path separators, so filename identity equals semantic
  identity and neither failure can occur.
  - `metadata.project_key` (optional, spec §3.2.2) carries the key. It is
    authoritative when present and is stamped only by a pinned server process, which
    is the only configuration with an authoritative answer; an unpinned process
    asserts nothing rather than guessing.
  - Sessions written before v0.5 carry no key and resolve through the alias registry
    or the canonical algorithm. **No capture record is rewritten to add the field** —
    retroactively editing capture records is a larger provenance harm than the
    missing field (spec §4.5, documented-divergence pattern).
  - Spec §3.2.2 defines the key algorithm normatively and **independently of any
    implementation's filename sanitization**, so a conforming producer cannot
    accidentally define identity as "whatever my filesystem layer does".
  - §4.5 forbids consumers from splitting one project across equal-key labels or
    merging distinct keys.
- **PROV export carries `trace:projectKey`** beside the untouched `trace:project`
  display literal, plus an optional reified project entity carrying one
  `trace:aliasLabel` per historical label. Both are new terms **within the frozen
  `ns/v0.3#` namespace**, so no `@context` change is required and previously exported
  documents are neither invalidated nor regenerated. `trace:project` keeps its
  display-label meaning permanently — the exported corpus carries drifted labels
  under it, and re-meaning the predicate would silently reinterpret those artifacts.
  The key is resolved through the alias table, so a renamed project exports its real
  key rather than one derived from its old label.
- **Published project-registry interchange schema** (`schemas/trace-projects-v1.json`,
  spec §7.2). Published rather than treated as private state because interpreting a
  historical export depends on the alias table. Its `version` field is independent of
  the session schema version — the two formats change on different cadences.
- Markdown export gains a **Project key** line, shown only when the session actually
  carries one.

### Changed

- `SCHEMA_VERSION` 0.4.1 → **0.5.0** (an on-disk format change), and the schema file
  is renamed `trace-v0.4.json` → **`trace-v0.5.json`** with the `$id` cascade applied
  across the generator, validator, docs, and conformance tests. Per ADR 002 D6 the
  spec URL in `Session.context` and the PROV namespace URI **remain at v0.3** —
  additive extensions are valid within the same namespace.
- Backward and forward compatible in both directions: a v0.4 document validates
  against the v0.5 schema (`project_key` is optional), and a v0.5 document validates
  against the v0.4 schema (`additionalProperties` is permitted throughout).

### Fixed

- **The `mcp` dependency is bounded below 2.0.** `mcp` 2.0.0 removed
  `mcp.server.fastmcp`, which `server.py` imports at module scope, so the
  previously unbounded `mcp>=1.0.0` resolved to a release the server cannot
  import — it died before registering a single tool, which a client reports as
  a total absence of TRACE tools. Consumers launch with `uvx --refresh`, which
  ignores the lockfile and re-resolves on every server start, so the break
  reached every project the day the upstream major was published. Lifting the
  bound requires porting to the `mcp` 2.x API (`mcp.server.mcpserver`).
- **This checkout's own launch arguments match the canonical form.**
  `--refresh` became `--refresh-package trace-mcp`, so a server start rebuilds
  TRACE from the working tree without re-resolving the whole dependency tree,
  and the redundant `--with filelock` was dropped. The relative `--from .` is
  unchanged: it is the local-only guarantee asserted by
  `test_mcp_json_uses_uvx`, since the `trace-mcp` name on PyPI belongs to an
  unrelated project.


- **`identity scan` and stray-store detection canonicalize the store filename
  stem.** A knowledge store written before canonical keying keeps a legacy
  filename — an uppercase or separator-bearing stem such as `REAP.json` — that a
  case-insensitive filesystem still serves as its canonical project's store.
  `scan` grouped stores by the raw stem, minting a phantom `REAP` group distinct
  from the `reap` session group; the two then collided on `apply`'s
  alias-uniqueness check. `find_stray_stores` compared the raw stem to registry
  keys (its docstring said "canonical stem" but the code did not), so such a
  store was reported stray even when its canonical key was registered — which
  would make `identity check` fail its exit-0 verification permanently. Both
  sites now classify by canonical key; the stray report still names the actual
  filename so the operator can find it.
- **`identity apply` no longer logs mints that never happened.** Migration
  records were appended to `migrations.jsonl` inside the registry transaction;
  when the write failed (e.g. an alias-uniqueness violation), the registry
  correctly persisted nothing while the append-only audit log already claimed
  the mints occurred. Records are now buffered and written only after the
  registry commit — a migration log that lies is worse than no log at all.
- **`identity merge-stores` refuses a corrupt target store.** The non-strict
  loader returned a fresh store on corruption, so the merged result silently
  replaced the damaged-but-recoverable original — the one file the merge keeps
  no premerge backup of. The target is now loaded strictly and corruption
  aborts the merge with the original untouched.
- **`identity check` and `identity scan` treat the `auto` quarantine as an
  expected population, not drift.** `check` flagged every `auto` session as an
  unregistered label, so a store holding any auto session could never reach the
  exit-0 verification the migration runbook requires; `scan` proposed a plan
  entry for the reserved key that `apply` then re-refused. Reserved-key labels
  are now excluded from both.
- **`identity adopt` no longer rewrites the captured display label.** It
  stamped `project_key` and also overwrote `metadata.project` — an in-place
  mutation of a captured field beyond what the alias-table-first rule
  sanctions. Only the additive key is stamped now; every identity-aware
  consumer resolves the session through it while the record keeps showing what
  was originally captured.
- **`identity scan` refuses to overwrite an existing plan file.** The plan is
  where the operator's decisions live between `scan` and `apply`; a re-run
  silently clobbered those edits.
- **`identity snapshot` archives a relocated knowledge directory.** With
  `TRACE_KNOWLEDGE_DIR` outside the TRACE home, stores were counted in the
  manifest but absent from the archive — and the marker the snapshot writes is
  what green-lights the destructive merge phase. The external directory is now
  included and the manifest records its location.
- **`identity merge-stores` holds the target store's lock** for the
  load→union→save→rename span (the mtime/lock preflight is advisory and racy),
  and `--key` now resolves aliases and display labels to the registered key
  instead of skipping them.
- **The project registry did not resolve an entry by its own display label.**
  `ProjectRegistry.resolve()` matched a label against an entry's key and aliases but
  not its display label, so an entry whose display label does not canonicalize to its
  key was unreachable by that label — leaving every session recorded under it
  unattributable. Not reachable through enrollment (which derives the key from the
  label) but reachable through a human-authored migration plan, which may pair any
  label with any key. Display labels now participate in resolution and, necessarily,
  in the alias-uniqueness check: an identifier that resolves must also be checked for
  uniqueness, or the registry could hold two entries that one label resolves to
  ambiguously.

### Security

- **`trace-mcp init` no longer writes a dependency-confused `.mcp.json` from
  an installed wheel.** When run from an installed copy (module under
  `site-packages`) with no `TRACE_SOURCE_PATH` set, source resolution
  previously fell back to writing `uvx --from trace-mcp` — but the name
  `trace-mcp` on PyPI belongs to an unrelated package, so the next MCP server
  start would have downloaded and executed third-party code. Resolution now
  **fails closed** (`TraceSourceUnresolvedError`; `trace-mcp init` exits 1,
  including on `--dry-run`) with the remedy in the message: set
  `TRACE_SOURCE_PATH=/abs/path/to/your/TRACE/clone`. Source checkouts and the
  `TRACE_SOURCE_PATH` override behave as before; the server entry is now built
  lazily at write time so importing the module never raises.
  
### Cloud LLM matching/extraction now opt-in (behavior change)

- **`TRACE_LLM_ENABLED` now defaults to `false`.** An `OPENAI_API_KEY` on the
  machine no longer opts sessions into cloud LLM matching and extraction by
  itself — completing the local-first posture already applied to embeddings
  (where `auto` never selects OpenAI). Unset now means rule-based/BM25; cloud
  LLM requires the explicit `TRACE_LLM_ENABLED=true` opt-in. To restore the
  previous behavior everywhere, set `TRACE_LLM_ENABLED=true` once in
  `~/.trace/.env`. When a key is present but the flag is unset, config load
  logs an INFO pointing at the flag (suppressed when the flag is explicitly
  `false` or `TRACE_LOCAL_ONLY` is set). The `LearnConfig.llm_enabled`
  dataclass default flips to `False` to match, so directly-constructed
  configs are safe-by-default too.

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

### Learning creation-path provenance

- **`Learning.extraction_method` and `Learning.generated_by`.** Every learning
  now records how it was created: `"llm"` (cloud extraction — the content is
  model output, so `generated_by` names the generating model), `"rule-based"`
  (local extractor — the content is quoted from session events), or `"manual"`
  (`trace_learn_add`). Records predating the fields load as `None`; tool
  responses (`learning_to_dict`) expose both fields so clients can distinguish
  model-generated from quoted content. This closes the gap where a paraphrased
  LLM extraction was indistinguishable from a verbatim event quote in the
  knowledge store.

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

[Unreleased]: https://github.com/Thru-Echoes/TRACE/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/Thru-Echoes/TRACE/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Thru-Echoes/TRACE/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Thru-Echoes/TRACE/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Thru-Echoes/TRACE/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Thru-Echoes/TRACE/releases/tag/v0.1.0
