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
| `model2vec` | Static token→vector (`potion-base-8M`) | `numpy` (no PyTorch) | none | modest upgrade over BM25 |
| `fastembed` | Small ONNX transformer (`arctic-embed-s`) | `onnxruntime` (no PyTorch) | none¹ | **the "local-strong" tier** |
| `openai` | OpenAI embeddings API | `openai` | **content → endpoint** | highest, but off-machine |
| `auto` *(default)* | Prefer local: `fastembed` → `model2vec` → BM25 | — | none | best available **local** |

¹ `fastembed` downloads the model weights once (quantized int8 ONNX) and caches
them; after that it runs fully offline. It never transmits your learning content.

**Local-first `auto` is the default and it never selects OpenAI.** Having an
`OPENAI_API_KEY` in your environment does not route embeddings to the cloud —
that requires an explicit `TRACE_EMBEDDING_BACKEND=openai`.

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

To inspect your machine's egress history:

```bash
cat ~/.trace/egress.jsonl | python3 -m json.tool --json-lines
```

## Switching backends re-embeds your store

Each learning records the model that produced its vector. When you change the
backend/model, TRACE re-embeds affected learnings on the next recall (vectors
from different models are not comparable). The `.npy` sidecar cache tolerates a
partially-migrated store (rows of differing dimension are treated as missing, not
errored) and is rebuilt once every row shares one model.

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
