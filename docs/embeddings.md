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
| `openai` | OpenAI embeddings API (`text-embedding-3-small`) | `openai` | **content → endpoint** | a small measured edge over `arctic-embed-s` (+0.03 nDCG@10 on judged queries, see below), no model download; 200 ms per recall against 6 ms, no recall at all offline, 4x larger store files, and every learning and query leaves the machine |
| `auto` *(default)* | `fastembed` when installed, otherwise BM25 alone | — | none | best available **local** |

¹ `fastembed` downloads the model weights once (quantized int8 ONNX, about 130 MB for
the default model) into `~/.trace/models` (override with `TRACE_MODEL_CACHE_DIR`, or
fastembed's own `FASTEMBED_CACHE_PATH`); after that it runs fully offline. Left to its own
default fastembed would use the per-user temporary directory, which the operating system
may clear, and the next recall would download again or fail without network. It never
transmits your learning content.
The runtime underneath it carries one telemetry facility of its own, described in
[ONNX Runtime telemetry](#onnx-runtime-telemetry-in-the-fastembed-tier) below.

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

### ONNX Runtime telemetry in the `fastembed` tier

`fastembed` runs its models on Microsoft's ONNX Runtime, and ONNX Runtime ships a
telemetry facility. Its own privacy notice (`Privacy.md`, installed next to the
package) states that telemetry is implemented only for Windows and is **on by
default in the official Windows builds**. There the runtime registers an ETW
TraceLogging provider named `Microsoft.ML.ONNXRuntime` and emits events at session
creation, model load, inference runs, errors, and process facts such as the CPU
model, memory size, and whether a debugger is attached. The library does not
contact the network itself: Windows' diagnostic-data service (Connected User
Experiences and Telemetry) collects those events and may upload them to Microsoft's
diagnostic-data endpoints, the `events.data.microsoft.com` hosts listed in Microsoft's
Windows diagnostic-data documentation. The events describe the runtime, never the
text being embedded. In the official macOS and Linux wheels the facility is a no-op
stub, and none of the 1.27.0 binaries for the three platforms embed a Microsoft
endpoint.

The documented switch is `onnxruntime.disable_telemetry_events()`. It must run
**before the model loads**: the first events fire when the ONNX session is created,
which `fastembed` does while constructing `TextEmbedding(...)`, the call TRACE makes
on the first recall that needs embeddings. TRACE does not make that call yet; a
process that wants it off must call it before that first recall. An ETW controller
on the machine can switch the provider back on afterwards (the runtime honours the
ETW enable callback), so on Windows the airtight options remain a private build or
the operating system's own diagnostic-data settings.

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

A second, larger comparison (fifteen stores, 2091 learnings, 136 real session and decision
queries, each retrieving only from the learnings that existed before its session, relevance
graded 0/1/2 by a language-model judge on the pooled top-10 of every arm, with a second
judge from another vendor agreeing on average) measured the production hybrid with
`arctic-embed-s` at 0.710 nDCG@10 against 0.637 for BM25 alone (+0.074, 90% interval
+0.051 to +0.097), `arctic-embed-m` at 0.708 (no gain), and OpenAI `text-embedding-3-small`
at 0.738 (+0.028 over `arctic-embed-s`, +0.013 to +0.044). The OpenAI edge is real and
consistent across stores and query types, and the items only it surfaced were useful about
twice as often as the items only `arctic-embed-s` surfaced; it stays below the +0.05 bar set
for changing the recommendation, and it costs 200 ms per recall against 6 ms, a hard failure
offline, store files four times larger (15 MB at 482 learnings, loading in 259 ms against
17 ms), vectors that are not bit-identical between calls, and full text egress. Session
descriptions written as keywords matched equally under every arm; the embedding earns its
keep on queries whose wording differs from the learning's. The same run found that the
`matched` label was true for 98 to 100% of items returned for 200 off-topic queries under
every arm, which led to the gate described in the next section.

`auto` no longer selects `model2vec`: a launch config that carries only `model2vec`
runs recall on BM25 alone until `fastembed` is added (the default launch extras now
include it). The first recall with `fastembed` downloads about 130 MB of weights once
and embeds a few hundred learnings (20 to 30 s observed on an Apple Silicon laptop with the weights already on disk). For an explicit
`TRACE_EMBEDDING_BACKEND=model2vec`, the default model is `potion-retrieval-32M` (was
`potion-base-8M`; stores re-embed lazily).

## When the query cannot be embedded, and what `matched` guarantees

**Query-time fallback.** The stored learnings carry vectors, but every recall also has to
embed the query, and that call can fail: no network for the OpenAI path, model files
missing for `fastembed`, a provider error. The ranker then falls back to keyword (BM25)
ranking for that recall, labels the backend `bm25 (query embedding failed under ...)`, and
the response carries a `notice` naming the failure. Under strict mode
(`TRACE_STRICT_LLM=true`, the default when a key is configured) the same failure raises
instead, on the view that a degraded answer is worse than no answer. A refused API key is
reported as such in every mode. Before this, the failure escaped as a bare exception and
the tool answered "Failed to recall learnings" in either mode.

**The absolute gate.** The hybrid ranker marks a result `matched` when it stands out from
the rest of the store for this query (a relative z floor) or has a lexical hit. That ranks
well within a query but says nothing about whether anything in the store resembles the
query at all: on 200 off-topic queries (cooking, hiking, generic web development) against
five real stores, every arm returned ten `matched` items for every query. Recall therefore
also applies a **query-level gate on the best raw similarity**, calibrated per model against
those off-topic queries and 147 real ones:

| model | gate on best similarity | keeps real queries | rejects off-topic queries |
|---|---|---|---|
| `snowflake/snowflake-arctic-embed-s` | 0.625 | 95% | 93% |
| `snowflake/snowflake-arctic-embed-m` | 0.32 | 93% | 90% |
| `text-embedding-3-small` | 0.365 | 98% | 93% |
| keyword ranking (BM25 alone, or the fallback above), stores of 30 or more learnings | 0.76 on the best normalised BM25 | about 90% | about 90% |

A best normalised BM25 of 0.80 or more overrides a failed similarity gate, because an
identifier-shaped query can embed poorly yet match a learning word for word. When the gate
fails, every result is returned as a weak candidate with `matched: false`, the summary says
`gated`, the notice reads "nothing in the store resembles this query", and the decision-warning
path surfaces nothing. Cosine scales differ by model, so a model not in the table has no gate
until it is calibrated the same way (TRACE then behaves as before). `TRACE_RECALL_GATE`
accepts `auto` (the table), `off`, or a number.

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
