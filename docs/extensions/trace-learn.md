# trace-learn — cross-session knowledge persistence

The `trace-learn` extension is loaded by default. It provides cross-session memory: corrections, gotchas, and learnings from past sessions are automatically surfaced when relevant in future sessions.

## How it works

Knowledge flows through three layers:

1. **Session start** — With `recall_learnings=True`, TRACE recalls past learnings for the session description (or `recall_query` when the description is an identifier or run label) and tags, and says what the recall did.
2. **On-demand search** — At any time, `trace_learn_recall` can search for relevant knowledge.
3. **Decision proposal** — When a decision is proposed, related past learnings are surfaced as warnings (e.g., a past correction about using the wrong conda environment).

At session end, new learnings are automatically extracted from annotations and rejected/revised decisions.

## Matching backends

| Backend | When used | How it works |
|---------|-----------|--------------|
| **Embedding + BM25 hybrid** (default when `fastembed` is installed) | `fastembed` is installed (the default launch extras include it); OpenAI embeddings only on explicit opt-in (measured about 0.03 nDCG@10 better than the local default on judged queries, at 200 ms per recall, no recall offline, and full text egress; see `docs/embeddings.md`); the static `model2vec` tier only on explicit request, since it measured below BM25 alone | Each learning gets a cosine similarity (the query is encoded separately from stored documents) and a BM25 score; the two rank lists are fused by reciprocal rank fusion (k=60). Results sort by fused score, then raw similarity, then decay, then newest creation time: decay scales the displayed score but never reorders candidates whose evidence differs. Tag overlap joins the fusion only when the caller passes tags that some learning shares. |
| **LLM** (opt-in) | `TRACE_LLM_ENABLED=true` + key and no embedding provider | Sends context + candidates to an OpenAI model for relevance scores. Falls back to BM25 on error. |
| **BM25** (default without `fastembed`) | No transformer provider and no LLM | Pure-Python BM25 with stemming. Zero external dependencies. On the benchmark store it matched the best hybrid on ordinary queries. |
| **Jaccard** (legacy) | Backward compatibility only | Token overlap. |

**Auto-selection:** fastembed → LLM → BM25 alone. The
`backend` field of every recall response names what ranked it, for example
`hybrid:embedding:minishlab/potion-retrieval-32M+bm25`.

The `fastembed` tier runs on ONNX Runtime, whose official Windows builds carry
on-by-default telemetry (runtime events through Windows ETW, never learning text);
the macOS and Linux wheels do not. `onnxruntime.disable_telemetry_events()` before
the model loads turns it off. See
[ONNX Runtime telemetry](../embeddings.md#onnx-runtime-telemetry-in-the-fastembed-tier).

## How a recall is scored

Recall ranks, it does not cut. Every result carries `score` (similarity × decay),
`matched`, and `signals` (`similarity`, `z`, `bm25`, `tag_overlap`, `decay`, `fused`),
so the session's model can judge relevance itself.

- **Floor, not cutoff.** Cosine values are not comparable across models, and on the
  project's own 352-learning benchmark store no absolute cutoff separated relevant from
  irrelevant learnings for any model tried. A candidate is `matched` when its similarity
  is at least one standard deviation above the store's mean for this query (`z >= 1`), or
  it has a lexical hit (`bm25 >= 0.15`), or it shares a tag with the query.
- **Gate, so that "matched" means something.** The relative floor always admits the top of
  the store, even for a query about nothing in it (measured: 98 to 100% of items returned
  for off-topic queries were `matched` under every backend). Recall therefore also checks
  the best raw similarity of the whole store against an absolute gate calibrated per model
  (`arctic-embed-s` 0.625, `text-embedding-3-small` 0.365, keyword ranking 0.76 on
  normalised BM25 for stores of 30 or more learnings; see
  [`docs/embeddings.md`](../embeddings.md#when-the-query-cannot-be-embedded-and-what-matched-guarantees)).
  A strong lexical hit (normalised BM25 of 0.80 or more) overrides a failed gate. When the
  gate fails nothing is `matched`, the nearest candidates come back as weak matches, the
  response says `gated` and the notice reads "nothing in the store resembles this query".
  Models not yet calibrated have no gate. `TRACE_RECALL_GATE=auto|off|<number>`.
- **Small stores** (fewer than 8 learnings) skip the z floor, which means nothing on so
  few items, and return everything up to the limit, ranked, ties newest-first, each
  flagged by the remaining signals (the gate still applies to the flags). A chain of
  sessions therefore always sees its own notes.
- **Weak candidates.** When fewer than `limit` learnings are matched, up to three of the
  nearest follow with `matched: false`. They never count as recalled (no `recall_count`
  change) and never appear in decision warnings.
- **The response says what happened.** `trace_learn_recall` reports `matched`, `scored`,
  `floor`, `backend` and the `query` actually scored; a `notice` field explains an empty,
  small-store or cleaned-query recall. The session-start banner carries the same notice.
- **Explicit `threshold`** on `trace_learn_recall` runs the legacy path: the backend's
  combined score (similarity mixed with tag overlap) times decay, filtered by
  `score >= threshold` and ordered by that score, over the candidates the backend scored.
- **Decay.** The displayed `score` is similarity × `2^(-age / half_life)` (default half-life
  365 days, measured from the last time the learning surfaced); learnings surfaced three or
  more times never drop below 0.8. Decay does not reorder candidates with different
  evidence; it only breaks ties after fused score and similarity (so an evergreen learning
  outranks a fresher one of equal evidence). Measured on the benchmark store, letting decay
  scale the fused score cost 0.17 MRR@10 for BM25 alone, because adjacent fused ranks differ
  by a few percent while a 365-day decay spans up to half. The explicit-`threshold` legacy
  path still orders by its decayed combined score. See the configuration table for the knobs.

## Query cleaning

A session description can be a run label plus a slug (`RUN_TOKEN=3b09311f2873 task A
norrell_photonics`), which carries almost no topical signal for any ranker. Before
scoring, recall drops bare hex runs, uuids, long digit runs, upper-case `KEY=value`
labels and assignments whose value is one of those, and splits `snake_case` into words.
Lower-case assignments with topical values stay as words (`ssl_verify=false` becomes
`ssl verify false`). When nothing topical survives, the original text is used. The
cleaned text is reported as `query`, and the notice says when cleaning changed it.

## BM25 stemming

BM25 includes a lightweight suffix-stripping stemmer that handles common English morphological variants without external dependencies:

- **Plurals:** decisions → decision, entries → entry, processes → process
- **Gerunds:** logging → log, implementing → implement (with doubled-consonant handling)
- **Past tense:** logged → log, implemented → implement
- **Multi-step:** learnings → learning → learn

This ensures that "decisions" in a query matches "decision" in a stored learning, and "logging" matches "log" — a class of morphological mismatches that previously caused recall failures.

## Match floors per backend

Without an explicit `threshold`, each non-embedding backend marks a result matched at its
own floor; the embedding backend uses the relative floor described above.

| Backend | Match floor |
|---------|-------------|
| Embedding + BM25 hybrid | `z >= 1`, or `bm25 >= 0.15`, or a shared tag |
| BM25 | 0.15 |
| LLM | 0.20 |
| Jaccard | 0.10 |

Passing `threshold` to `trace_learn_recall` switches to the legacy absolute cutoff.

## Extraction

Learnings are extracted from session events via two backends:

| Backend | When used | What it does |
|---------|-----------|--------------|
| **LLM-enhanced** (opt-in) | `TRACE_LLM_ENABLED=true` + API key | Sends all session events to an OpenAI model which identifies valuable, actionable learnings and generates quality tags. Avoids duplicating existing learnings. |
| **Rule-based** (default) | Otherwise (no key, or LLM not enabled) | Processes annotations with category in {learning, correction, gotcha}, rejected/revised decisions (preserving rationale and revision notes), and collaborative contributions. |

Both backends are **idempotent** — running extraction twice on the same session produces no duplicates.

## LLM configuration

Cloud LLM features are **off by default** (local-first): an API key on the
machine does not, by itself, send any session content to OpenAI. To opt in,
place the key in **this project's own `.env`** and set `TRACE_LLM_ENABLED=true`:

```bash
OPENAI_API_KEY=sk-...
TRACE_LLM_ENABLED=true                   # Opt-in: cloud LLM is OFF by default
TRACE_LLM_MODEL=gpt-6-luna               # Model for matching/scoring
TRACE_LLM_EXTRACTION_MODEL=gpt-6-luna    # Model for extraction (can be different)
TRACE_STRICT_LLM=true                    # Fail loudly on LLM errors (default: true when key set)
```

### Where the key is read from

Highest priority first:

1. an environment variable already exported in the process (CI, containers)
2. **`./.env` — this project's own file**, read from the directory the host launches the server in
3. `~/.trace/.env` — machine-wide defaults

**The key belongs in the project's own file.** Each project having its own
credential means a leaked or exhausted key exposes one project rather than every
project on the machine. `~/.trace/.env` still works as a fallback, and TRACE
names it at session start rather than letting a project quietly borrow it.

A **blank value never overrides a real one** in any source, so copying a
template with a bare `OPENAI_API_KEY=` cannot mask a working key. To stop a
project using an inherited key, set `TRACE_LOCAL_ONLY=true` instead of blanking
the value.

`TRACE_LOCAL_ONLY` is the one setting that does not follow that order: it is a
**restrict-only ratchet**, ORed across sources, so any source may switch the
no-egress kill switch on and none may switch it off.

Configuration is read **once, at server start** — restart the MCP server after
editing a `.env`.

### When the key is missing or refused

- **No key, cloud path requested** — reported in the `trace_start_session`
  banner and in the affected `trace_learn_*` responses (a `warnings` field),
  naming the three places searched and the file to fix. Recall still answers on
  BM25 and says so.
- **Key rejected (401)** — raises `ApiKeyRejectedError` on the matching,
  extraction, and embedding paths *regardless of* `TRACE_STRICT_LLM`, with the
  credential scrubbed from the message. Strict mode governs whether degradation
  is acceptable, not whether you are told your credential was refused. A 403 is
  deliberately excluded — it also covers model access, region, and proxy
  failures — and stays on the strict-mode path.
- **Key borrowed from `~/.trace/.env`** — named at session start.

### Strict vs permissive LLM mode

**Strict mode** (default when `OPENAI_API_KEY` is set) — LLM failures raise `LLMFallbackError` instead of silently degrading to BM25/rule-based. This ensures you know when LLM features aren't working rather than silently getting lower-quality results. Backend selection is logged at `INFO` level at startup so you always know which tier is active.

**Permissive mode** (`TRACE_STRICT_LLM=false`) — LLM failures fall back to BM25/rule-based with a `WARNING` log. Use this in environments where degraded operation is preferable to hard failures (e.g., CI fixtures, or environments without reliable network access).

If `OPENAI_API_KEY` is not set at all, strict mode is disabled automatically and the local embedding tier (or BM25 without one) is used without error — there's nothing to be strict about. A key alone does not change that: strict mode refuses the keyword fallback only when a cloud path was requested (`TRACE_LLM_ENABLED=true` or `TRACE_EMBEDDING_BACKEND=openai`) and cannot be served, or when an explicitly requested local backend is not installed.

The same split applies at query time. Every recall embeds the query, and that call can fail (no network for OpenAI embeddings, model files missing for `fastembed`). Permissive mode ranks that recall by keywords and says so: the `backend` field reads `bm25 (query embedding failed under ...)` and the `notice` names the failure. Strict mode raises instead. A refused key is reported in both modes. Previously this failure produced a bare "Failed to recall learnings".

The `fastembed` model files live under `~/.trace/models` (override `TRACE_MODEL_CACHE_DIR`), not in the temporary directory fastembed would use on its own, so a cleared temp directory no longer costs a 130 MB re-download or an offline failure.

## Storage

- **Path**: `~/.trace/knowledge/{project}.json`
- **Override**: `TRACE_KNOWLEDGE_DIR` environment variable
- **Format**: pretty-printed JSON, one file per project, atomic writes (temp file + `os.replace`)

## Configuration reference

See the main [Configuration table in `README.md`](../../README.md#configuration) for all `TRACE_*` environment variables relevant to trace-learn (decay, deduplication, BM25 parameters, evergreen floor, etc.).
