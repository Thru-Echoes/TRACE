# Embedding backends

TRACE's `trace-learn` extension turns each stored *learning* into a vector so
that recall can rank by semantic similarity (with tag boosting and time decay on
top). Which model produces those vectors is configurable, and the default is
**local-first**: TRACE never sends your content to a third party unless you
explicitly ask it to.

## The fallback ladder

From lightest/lowest-quality to heaviest/highest, and from fully-local to cloud:

| Backend | What it is | Dependency | Egress | Retrieval quality |
| --- | --- | --- | --- | --- |
| `none` (BM25) | Lexical ranking only | none | none | lexical baseline (great for identifiers/paths) |
| `model2vec` | Static token→vector (`potion-retrieval-32M`) | `numpy` (no PyTorch) | none | explicit only: on the benchmark below it scored below BM25 alone on ordinary queries, with no significant gain on paraphrases |
| `fastembed` | Small ONNX transformer (`arctic-embed-s`) | `onnxruntime` (no PyTorch) | none¹ | **the local default**: on the benchmark below its point estimate equalled BM25's on ordinary queries and was significantly higher on paraphrased ones; fused with BM25 at recall |
| `openai` | OpenAI embeddings API | `openai` | **content → endpoint** | highest, but off-machine |
| `auto` *(default)* | `fastembed` when installed, otherwise BM25 alone | — | none | best available **local** |

¹ `fastembed` downloads the model weights once (quantized int8 ONNX) and caches
them; after that it runs fully offline. It never transmits your learning content.

**Local-first `auto` is the default and it never selects OpenAI.** Having an
`OPENAI_API_KEY` in your environment does not route embeddings to the cloud —
that requires an explicit `TRACE_EMBEDDING_BACKEND=openai`.

**Queries and documents are encoded separately.** Stored learnings are embedded as
documents (`embed_texts`); the recall query goes through `embed_query`. The arctic
models require the instruction prefix "Represent this sentence for searching relevant
passages: " on the query side, which the fastembed provider adds; documents never get
it. bge models need no prefix. Whatever the model, recall ranks by fusing the embedding
similarity with BM25 (see `docs/extensions/trace-learn.md`), so the model choice tunes
the semantic half of the ranking rather than deciding it alone.

## Choosing a backend

```bash
# Fully local, stronger than the static default (install the extra first):
pip install 'trace-mcp[local-embed]'
export TRACE_EMBEDDING_BACKEND=fastembed          # or leave as auto once installed

# Pick a specific curated model (all permissive-license, no PyTorch):
export TRACE_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
```

### Curated fastembed models (allowlist)

Vetted, permissively-licensed, compact, no-PyTorch. Any other model id is still
accepted, with a warning — its license and embedding dimension are then your
responsibility.

| Model | Dim | License |
| --- | --- | --- |
| `snowflake/snowflake-arctic-embed-s` *(default)* | 384 | Apache-2.0 |
| `snowflake/snowflake-arctic-embed-m` | 768 | Apache-2.0 |
| `BAAI/bge-small-en-v1.5` | 384 | MIT |
| `BAAI/bge-base-en-v1.5` | 768 | MIT |

### Bring your own model, the easy way: a local OpenAI-compatible server

If you already run a local inference server that speaks the OpenAI embeddings API
(Ollama, LM Studio, text-embeddings-inference, vLLM, …), point the `openai`
backend at it. This lets you use *any* embedding model you can host locally while
TRACE stays out of weight/license management:

```bash
export TRACE_EMBEDDING_BACKEND=openai
export OPENAI_BASE_URL=http://localhost:11434/v1   # e.g. Ollama
export TRACE_EMBEDDING_MODEL=nomic-embed-text       # whatever your server serves
export OPENAI_API_KEY=not-needed-but-sdk-wants-one
```

Because the endpoint is local, no content leaves your machine.

## One switch to disable all egress

`auto` is already local-first for *embeddings*, but the LLM extraction and
LLM-matching paths can still use OpenAI when a key is configured. To force
**everything** local in one move — no OpenAI embeddings, no LLM extraction, no
LLM matching — set:

```bash
export TRACE_LOCAL_ONLY=1
```

This is the single, unambiguous kill switch. It overrides an explicit
`TRACE_EMBEDDING_BACKEND=openai` (down to local `auto`) and disables cloud LLM
features regardless of whether a key is present. It closes the "off-switch trap"
where disabling one path (`TRACE_LLM_ENABLED=false` **or**
`TRACE_EMBEDDING_BACKEND=none`) still left the other egressing.

## Egress ledger (egress-as-provenance)

When cloud calls *are* enabled, every one of them is recorded: each LLM
extraction, LLM matching, and OpenAI embedding request appends one JSON line to
`~/.trace/egress.jsonl` (override the path with `TRACE_EGRESS_LOG`) **before**
the request is made. A line records the *fact* of the call — timestamp,
provider, endpoint, model, purpose, item count, and project/session where the
call site knows them — never the content. A `base_url` field on embedding
entries marks calls that target a user-configured OpenAI-compatible endpoint
(e.g. a local Ollama) rather than api.openai.com.

The attestation fails closed: if the ledger cannot be written, the cloud call
does not happen. Under permissive config that degrades to the local path
(BM25 / rule-based / un-embedded learnings) like any provider failure; under
strict config (`TRACE_STRICT_LLM=true`) it raises. This is INV-5 in
[docs/INVARIANTS.md](INVARIANTS.md): an enumeration guard fails CI when a new
OpenAI call site appears without an attestation, so the ledger stays complete
by construction.

**Project attribution (v0.5)**: every row also carries a `project_key` column —
the canonical project key the call was made on behalf of. The matching and
embedding rows were previously unattributable (their call sites sit in layers
that know nothing about projects); the key now travels from the tool boundary
in a context variable, so all three purposes write attributed rows. Rows
written before v0.5 have no `project_key` and are never rewritten — resolve
their `project` display label through the alias registry instead.

To inspect your machine's egress history:

```bash
cat ~/.trace/egress.jsonl | python3 -m json.tool --json-lines
```

To audit one project's egress (joining old label-only rows via the registry is
the consumer's job; for post-v0.5 rows the key column suffices):

```bash
python3 - <<'EOF'
import json, pathlib
key = "my-project"
for line in pathlib.Path.home().joinpath(".trace/egress.jsonl").read_text().splitlines():
    row = json.loads(line)
    if row.get("project_key") == key:
        print(row["ts"], row["purpose"], row["endpoint"], row["item_count"])
EOF
```

**Per-project privacy ratchet (v0.5)**: a project's registry entry
(`~/.trace/projects.json`) can carry `{"local_only": true}`,
`{"llm_enabled": false}`, or `{"embedding_backend_max": "local"}` under its
`config` key. These apply at all three egress decision points as a
**restrict-only** ratchet: an entry can force a confidentiality-bound project
fully local against a permissive machine-global config, but can never loosen a
global restriction. If the registry exists but is unreadable, learn operations
fail closed for every project — the posture that would forbid the egress may
live in the unreadable file.

## Switching backends re-embeds your store

Each learning records the model that produced its vector. When you change the
backend/model, TRACE re-embeds affected learnings on the next recall (vectors
from different models are not comparable). The `.npy` sidecar cache tolerates a
partially-migrated store (rows of differing dimension are treated as missing, not
errored) and is rebuilt once every row shares one model.

## How the tiers were measured

A retrospective measurement, not a session-start trial. One project's knowledge store (352
learnings, the final store) and 100 of its session descriptions as queries, with a learning
counted relevant to the session it was extracted from: every query's targets were in the
candidate set, the labels favour shared vocabulary, and other sessions' learnings count as
irrelevant even when they would help. The shipped ranker, limit 10, decay as tie-breaker, a
fresh copy of the store per query, decay ages taken at run time. MRR@10 is the mean over
queries of the reciprocal rank of the first relevant result among the ten returned (0 when
none); hit rate@5 is the share of queries with at least one relevant result in the top five.
The paraphrased set rewrote each description with a language model that saw only the
descriptions, not the learnings; mean Jaccard between the lower-cased `\w+` token sets of a
paraphrase and its original was 0.04. Intervals are 90% percentile bootstraps over the 100
queries and are exploratory: queries from related sessions are not independent, the methods
were chosen while looking at this store, and nothing was held out.

| Ranking | Ordinary queries MRR@10 (hit rate@5) | Paraphrased MRR@10 (hit rate@5) |
|---|---|---|
| BM25 alone | 0.52 (0.67) | 0.17 (0.20) |
| hybrid, `potion-base-8M` | 0.42 (0.57) | 0.17 (0.25) |
| hybrid, `potion-retrieval-32M` | 0.44 (0.62) | 0.17 (0.27) |
| hybrid, `bge-small-en-v1.5` | 0.49 (0.65) | 0.23 (0.29) |
| hybrid, `arctic-embed-s` (query prefix) | 0.52 (0.68) | 0.21 (0.36) |

Against BM25 alone, the two static models were significantly worse on ordinary queries
(90% intervals −0.150 to −0.051 and −0.133 to −0.028) and not significantly different on
paraphrases; the two transformers were not significantly different on ordinary queries and
significantly better on paraphrases (+0.020 to +0.103 and +0.011 to +0.087). The weights
download is about 130 MB; embedding 352 learnings took 20 to 30 s with the model already on
disk, on an Apple Silicon laptop; both are single observations, not bounds.

`auto` no longer selects `model2vec`: a launch config that carries only `model2vec`
runs recall on BM25 alone until `fastembed` is added (the default launch extras now
include it). The first recall with `fastembed` downloads about 130 MB of weights once
and embeds a few hundred learnings (20 to 30 s observed on an Apple Silicon laptop with the weights already on disk). For an explicit
`TRACE_EMBEDDING_BACKEND=model2vec`, the default model is `potion-retrieval-32M` (was
`potion-base-8M`; stores re-embed lazily).

## Future plan (not yet implemented): first-class arbitrary custom models

Today the two "pick your own" paths are (1) the curated fastembed allowlist and
(2) the local OpenAI-compatible `base_url` passthrough above. A *fully-arbitrary,
TRACE-managed* custom model — where TRACE downloads/exports/quantizes an
arbitrary Hugging Face model and manages its per-model prefixing, pooling, and
license clearance — is **deliberately deferred**. It carries disproportionate
packaging, maintenance, and license-clearance burden relative to its benefit for
the small per-project stores TRACE holds, and the two paths above already cover
the common cases. If you have a concrete need for it, that is the signal to
promote it from this roadmap into a design.
