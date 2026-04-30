# trace-learn — cross-session knowledge persistence

The `trace-learn` extension is loaded by default. It provides cross-session memory: corrections, gotchas, and learnings from past sessions are automatically surfaced when relevant in future sessions.

## How it works

Knowledge flows through three layers:

1. **Session start** — When a new session starts, TRACE auto-recalls the most relevant past learnings based on the session description and tags.
2. **On-demand search** — At any time, `trace_learn_recall` can search for relevant knowledge.
3. **Decision proposal** — When a decision is proposed, related past learnings are surfaced as warnings (e.g., a past correction about using the wrong conda environment).

At session end, new learnings are automatically extracted from annotations and rejected/revised decisions.

## Matching backends

trace-learn uses a tiered matching system for finding relevant learnings:

| Backend | When used | How it works |
|---------|-----------|--------------|
| **LLM** (primary) | When `openai` is installed and `OPENAI_API_KEY` is configured | Sends context + candidate learnings to an OpenAI model for semantic relevance scoring. Understands synonyms, abbreviations, and conceptual similarity. Falls back to BM25 on any error. |
| **BM25** (fallback) | When no API key is available, or as pre-filter for LLM | Pure-Python BM25 ranking with stemming and tag boosting. Handles term frequency and document length normalization. Zero external dependencies. |
| **Jaccard** (legacy) | Backward compatibility only | Simple token-overlap scoring. Kept as absolute fallback. |

**Auto-selection:** LLM if the `openai` package is installed and an API key is configured, otherwise BM25.

## BM25 stemming

BM25 includes a lightweight suffix-stripping stemmer that handles common English morphological variants without external dependencies:

- **Plurals:** decisions → decision, entries → entry, processes → process
- **Gerunds:** logging → log, implementing → implement (with doubled-consonant handling)
- **Past tense:** logged → log, implemented → implement
- **Multi-step:** learnings → learning → learn

This ensures that "decisions" in a query matches "decision" in a stored learning, and "logging" matches "log" — a class of morphological mismatches that previously caused recall failures.

## Per-backend thresholds

Each backend has a tuned default threshold to balance recall against false positives:

| Backend | Default threshold | Rationale |
|---------|-------------------|-----------|
| BM25 | 0.15 | Higher than naive 0.1 to filter keyword-overlap noise |
| LLM | 0.20 | LLM scores are more semantically meaningful |
| Jaccard | 0.10 | Legacy, more permissive |

Thresholds can be overridden per-call via the `threshold` parameter on `trace_learn_recall`.

## Extraction

Learnings are extracted from session events via two backends:

| Backend | When used | What it does |
|---------|-----------|--------------|
| **LLM-enhanced** (primary) | When configured | Sends all session events to an OpenAI model which identifies valuable, actionable learnings and generates quality tags. Avoids duplicating existing learnings. |
| **Rule-based** (fallback) | When no API key | Processes annotations with category in {learning, correction, gotcha}, rejected/revised decisions (preserving rationale and revision notes), and collaborative contributions. |

Both backends are **idempotent** — running extraction twice on the same session produces no duplicates.

## LLM configuration

Place your OpenAI API key in `~/.trace/.env` (shared across all TRACE projects):

```bash
OPENAI_API_KEY=sk-...
TRACE_LLM_MODEL=gpt-5.4-mini             # Model for matching/scoring
TRACE_LLM_EXTRACTION_MODEL=gpt-5.4-mini  # Model for extraction (can be different)
TRACE_LLM_ENABLED=true                   # Set false to force BM25-only
TRACE_STRICT_LLM=true                    # Fail loudly on LLM errors (default: true when key set)
```

Environment variables take precedence over `.env` file values for CI/container use.

### Strict vs permissive LLM mode

**Strict mode** (default when `OPENAI_API_KEY` is set) — LLM failures raise `LLMFallbackError` instead of silently degrading to BM25/rule-based. This ensures you know when LLM features aren't working rather than silently getting lower-quality results. Backend selection is logged at `INFO` level at startup so you always know which tier is active.

**Permissive mode** (`TRACE_STRICT_LLM=false`) — LLM failures fall back to BM25/rule-based with a `WARNING` log. Use this in environments where degraded operation is preferable to hard failures (e.g., CI fixtures, or environments without reliable network access).

If `OPENAI_API_KEY` is not set at all, strict mode is disabled automatically and BM25 is used without error — there's nothing to be strict about.

## Storage

- **Path**: `~/.trace/knowledge/{project}.json`
- **Override**: `TRACE_KNOWLEDGE_DIR` environment variable
- **Format**: pretty-printed JSON, one file per project, atomic writes (temp file + `os.replace`)

## Configuration reference

See the main [Configuration table in `README.md`](../../README.md#configuration) for all `TRACE_*` environment variables relevant to trace-learn (decay, deduplication, BM25 parameters, evergreen floor, etc.).
