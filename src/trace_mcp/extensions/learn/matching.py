"""Matching backends for learning recall.

Four backends, tried in order:

1. **EmbeddingBackend** (primary) — Cosine similarity on precomputed vectors.
   Sub-millisecond, works offline once embeddings are generated.
2. **LLMBackend** — OpenAI semantic matching via chat completions.
   Understands synonyms, abbreviations, conceptual similarity.
3. **BM25Backend** (fallback) — Pure-Python BM25 ranking.  Much better than
   Jaccard for information retrieval; handles term frequency and document
   length normalization.  Zero external dependencies.
4. **JaccardBackend** (legacy) — Simple token-overlap scoring.  Kept for
   backward compatibility and as an absolute fallback.

Auto-selection: Embedding > LLM > BM25. An embedding backend ranks by hybrid
fusion with BM25 and a per-query relative floor (see ``recall_ranked``).
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from trace_mcp.extensions.learn.egress import attest_egress
from trace_mcp.extensions.learn.models import Learning

if TYPE_CHECKING:
    from trace_mcp.extensions.learn.config import LearnConfig
    from trace_mcp.extensions.learn.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)

# ── Tokenization (shared) ─────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"\w+")
_VOWELS = frozenset("aeiou")


def _stem(token: str) -> str:
    """Lightweight English suffix-stripping stemmer for BM25 recall.

    Handles common morphological variants without external dependencies:
    - Plurals: decisions→decision, entries→entry, processes→process
    - Gerunds: logging→log, implementing→implement
    - Past tense: logged→log, implemented→implement

    Applied in sequence (plural strip, then -ed/-ing strip) so that
    "learnings" → "learning" → "learn".
    """
    if len(token) <= 3:
        return token

    # Step 1: Plurals
    if token.endswith("ies") and len(token) > 4:
        token = token[:-3] + "y"
    elif token.endswith("sses"):
        token = token[:-2]
    elif token.endswith(("ches", "shes", "xes", "zes")):
        token = token[:-2]
    elif token.endswith("s") and not token.endswith(("ss", "us", "is")):
        token = token[:-1]

    # Step 2: -ed / -ing (only if stem contains a vowel)
    if token.endswith("ing") and len(token) > 5:
        stem = token[:-3]
        if any(c in _VOWELS for c in stem):
            if len(stem) >= 2 and stem[-1] == stem[-2]:
                token = stem[:-1]  # "logging" → "logg" → "log"
            else:
                token = stem
    elif token.endswith("ed") and len(token) > 4:
        stem = token[:-2]
        if any(c in _VOWELS for c in stem):
            if len(stem) >= 2 and stem[-1] == stem[-2]:
                token = stem[:-1]  # "logged" → "logg" → "log"
            else:
                token = stem

    return token


def _tokenize(text: str, stem: bool = False) -> list[str]:
    """Extract lowercase word tokens from text (preserves duplicates for TF)."""
    tokens = _TOKEN_RE.findall(text.lower())
    if stem:
        tokens = [_stem(t) for t in tokens]
    return tokens


def _tokenize_set(text: str) -> set[str]:
    """Extract unique lowercase word tokens from text."""
    return set(_TOKEN_RE.findall(text.lower()))


_HEX_RUN_RE = re.compile(r"^[0-9a-fA-F]{8,}$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_DIGIT_RUN_RE = re.compile(r"^\d{6,}$")
_ASSIGNMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(\S+)$")
_LABEL_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")  # RUN_TOKEN, SESSION_ID: an environment-style label
_TRAILING_PUNCT = ".,;:!?)"


def _is_identifier(tok: str) -> bool:
    """A bare hex run (8+), a uuid, or a digit run (6+): a label, not a topic."""
    tok = tok.rstrip(_TRAILING_PUNCT)
    return bool(_HEX_RUN_RE.match(tok) or _UUID_RE.match(tok) or _DIGIT_RUN_RE.match(tok))


def _words(tok: str) -> list[str]:
    """Split a snake_case token into words; other tokens pass through unchanged."""
    return [part for part in tok.split("_") if part] if "_" in tok else [tok]


def clean_query(text: str) -> str:
    """Strip identifier-shaped fragments from a recall query and split slugs into words.

    Dropped: bare hex runs, uuids, long digit runs, and ``KEY=value`` fragments
    whose key is an upper-case label (``RUN_TOKEN=...``) or whose value is one
    of those. Kept, as words: every other token, including lower-case
    assignments with topical values (``ssl_verify=false`` becomes
    ``ssl verify false``). A run token and a slug carry almost no topical
    signal for any ranker, so a session description shaped like one is
    useless as a query until it is cleaned. Returns the original text when
    nothing topical survives.
    """
    kept: list[str] = []
    for tok in text.split():
        assignment = _ASSIGNMENT_RE.match(tok)
        if assignment:
            key, value = assignment.group(1), assignment.group(2)
            if _LABEL_KEY_RE.match(key) or _is_identifier(value):
                continue
            kept += _words(key) + _words(value)
            continue
        if _is_identifier(tok):
            continue
        kept += _words(tok)
    cleaned = " ".join(kept).strip()
    return cleaned or text


# ── Tag overlap (shared) ──────────────────────────────────────────────────


def _tag_overlap(learning_tags: list[str], context_tags: list[str] | None) -> float:
    """Jaccard overlap between learning tags and context tags. Returns [0, 1]."""
    if not context_tags or not learning_tags:
        return 0.0
    a = {t.lower() for t in learning_tags}
    b = {t.lower() for t in context_tags}
    intersection = a & b
    union = a | b
    return len(intersection) / len(union) if union else 0.0


# ── Backend protocol ──────────────────────────────────────────────────────


@runtime_checkable
class MatchingBackend(Protocol):
    """Interface that all matching backends implement."""

    default_threshold: float

    async def text_scores(self, learnings: list[Learning], context: str) -> list[float]:
        """The backend's own similarity for every learning, in ``[0, 1]``, aligned to *learnings*.

        No tag term and no decay: this is the primitive the hybrid ranker fuses.
        """
        ...

    async def score_batch(
        self,
        learnings: list[Learning],
        context: str,
        context_tags: list[str] | None = None,
    ) -> list[tuple[int, float]]:
        """Score each learning against *context* (legacy: similarity mixed with tag overlap).

        Returns a list of ``(index, score)`` tuples where *index* is the
        position in *learnings* and *score* is in ``[0, 1]``. Backends that
        mix locally implement it as ``mix_scores(await text_scores(...), ...)``.
        """
        ...


# ── Jaccard backend (legacy) ─────────────────────────────────────────────


def jaccard_similarity(text1: str, text2: str) -> float:
    """Compute Jaccard similarity between two texts (token overlap)."""
    tokens1 = _tokenize_set(text1)
    tokens2 = _tokenize_set(text2)
    if not tokens1 or not tokens2:
        return 0.0
    return len(tokens1 & tokens2) / len(tokens1 | tokens2)


def score_learning(
    learning: Learning,
    context: str,
    context_tags: list[str] | None = None,
    tag_weight: float = 0.3,
) -> float:
    """Score a single learning with Jaccard + tag overlap (legacy API)."""
    text_score = jaccard_similarity(learning.content, context)
    tag_score = _tag_overlap(learning.tags, context_tags)
    return (1 - tag_weight) * text_score + tag_weight * tag_score


class JaccardBackend:
    """Legacy Jaccard token-overlap matching."""

    default_threshold: float = 0.1

    def __init__(self, tag_weight: float = 0.3) -> None:
        self.tag_weight = tag_weight

    async def text_scores(self, learnings: list[Learning], context: str) -> list[float]:
        return [jaccard_similarity(lrn.content, context) for lrn in learnings]

    def mix_scores(
        self, text: list[float], learnings: list[Learning], context_tags: list[str] | None
    ) -> list[tuple[int, float]]:
        """Legacy combined score per learning (pure): similarity mixed with tag overlap, store order."""
        return [
            (i, (1 - self.tag_weight) * t + self.tag_weight * _tag_overlap(lrn.tags, context_tags))
            for i, (lrn, t) in enumerate(zip(learnings, text, strict=True))
        ]

    async def score_batch(
        self,
        learnings: list[Learning],
        context: str,
        context_tags: list[str] | None = None,
    ) -> list[tuple[int, float]]:
        return self.mix_scores(await self.text_scores(learnings, context), learnings, context_tags)


# ── BM25 backend ─────────────────────────────────────────────────────────


class _BM25Index:
    """Minimal BM25 index built from a list of token lists."""

    def __init__(
        self,
        documents: list[list[str]],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.k1 = k1
        self.b = b
        self.n = len(documents)
        self.doc_lens = [len(d) for d in documents]
        self.avgdl = sum(self.doc_lens) / max(self.n, 1)
        self.term_freqs: list[Counter[str]] = [Counter(d) for d in documents]
        self.doc_freqs: Counter[str] = Counter()
        for d in documents:
            for term in set(d):
                self.doc_freqs[term] += 1

    def idf(self, term: str) -> float:
        n_t = self.doc_freqs.get(term, 0)
        return math.log((self.n - n_t + 0.5) / (n_t + 0.5) + 1.0)

    def score(self, query_tokens: list[str], doc_idx: int) -> float:
        tf = self.term_freqs[doc_idx]
        dl = self.doc_lens[doc_idx]
        total = 0.0
        for term in query_tokens:
            freq = tf.get(term, 0)
            if freq == 0:
                continue
            idf_val = self.idf(term)
            numerator = freq * (self.k1 + 1.0)
            denominator = freq + self.k1 * (1.0 - self.b + self.b * dl / self.avgdl)
            total += idf_val * numerator / denominator
        return total


def _normalize_bm25(score: float, k: float = 3.0) -> float:
    """Normalise a raw BM25 score to [0, 1) via saturation function."""
    if score <= 0:
        return 0.0
    return score / (score + k)


class BM25Backend:
    """Pure-Python BM25 matching with tag boosting and stemming."""

    default_threshold: float = 0.15

    def __init__(
        self,
        k1: float = 1.5,
        b: float = 0.75,
        tag_weight: float = 0.3,
    ) -> None:
        self.k1 = k1
        self.b = b
        self.tag_weight = tag_weight

    async def text_scores(self, learnings: list[Learning], context: str) -> list[float]:
        if not learnings:
            return []
        documents = [_tokenize(lrn.content, stem=True) for lrn in learnings]
        query_tokens = _tokenize(context, stem=True)
        index = _BM25Index(documents, k1=self.k1, b=self.b)
        return [_normalize_bm25(index.score(query_tokens, i)) for i in range(len(learnings))]

    def mix_scores(
        self, text: list[float], learnings: list[Learning], context_tags: list[str] | None
    ) -> list[tuple[int, float]]:
        """Legacy combined score per learning (pure): similarity mixed with tag overlap, store order."""
        return [
            (i, (1 - self.tag_weight) * t + self.tag_weight * _tag_overlap(lrn.tags, context_tags))
            for i, (lrn, t) in enumerate(zip(learnings, text, strict=True))
        ]

    async def score_batch(
        self,
        learnings: list[Learning],
        context: str,
        context_tags: list[str] | None = None,
    ) -> list[tuple[int, float]]:
        return self.mix_scores(await self.text_scores(learnings, context), learnings, context_tags)


# ── LLM backend ───────────────────────────────────────────────────────────

try:
    from openai import AsyncOpenAI

    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False


class LLMBackend:
    """OpenAI LLM-based semantic matching.

    Sends the search context and all candidate learnings to the model and
    asks it to return relevance scores.  Falls back to BM25 on any error.
    """

    default_threshold: float = 0.2

    # If the store has more learnings than this, pre-filter with BM25 first
    MAX_DIRECT_CANDIDATES = 50

    def __init__(self, config: LearnConfig) -> None:
        if not _HAS_OPENAI:
            raise RuntimeError(
                "The 'openai' package is required for LLM matching. Install it with: pip install 'trace-mcp[llm]'"
            )
        self._client = AsyncOpenAI(api_key=config.openai_api_key)
        self._model = config.llm_model
        self._tag_weight = config.tag_weight
        self._strict = config.strict_llm
        # Kept so a rejected credential can name the file to edit, and so the
        # credential itself can be scrubbed from any provider message.
        self._key_origin = config.key_origin()
        self._key_search = config.key_search_description()
        self._api_key = config.openai_api_key
        self._bm25_fallback = BM25Backend(
            k1=config.bm25_k1,
            b=config.bm25_b,
            tag_weight=config.tag_weight,
        )

    async def text_scores(self, learnings: list[Learning], context: str) -> list[float]:
        """LLM relevance per learning without tag context; learnings cut by the BM25 pre-filter score 0."""
        dense = [0.0] * len(learnings)
        for idx, score in await self.score_batch(learnings, context, None):
            dense[idx] = max(0.0, min(1.0, score))
        return dense

    async def score_batch(
        self,
        learnings: list[Learning],
        context: str,
        context_tags: list[str] | None = None,
    ) -> list[tuple[int, float]]:
        if not learnings:
            return []

        # Pre-filter with BM25 if too many candidates
        index_map: dict[int, int] = {}  # local idx → original idx
        candidates = learnings
        if len(learnings) > self.MAX_DIRECT_CANDIDATES:
            bm25_scores = await self._bm25_fallback.score_batch(learnings, context, context_tags)
            bm25_scores.sort(key=lambda x: x[1], reverse=True)
            top_indices = [idx for idx, _ in bm25_scores[: self.MAX_DIRECT_CANDIDATES]]
            candidates = [learnings[i] for i in top_indices]
            index_map = {local: orig for local, orig in enumerate(top_indices)}
        else:
            index_map = {i: i for i in range(len(learnings))}

        try:
            scores = await self._llm_score(candidates, context, context_tags)
        except Exception as exc:
            from trace_mcp.extensions.learn.config import (
                ApiKeyRejectedError,
                LLMFallbackError,
                is_auth_error,
                redact_key,
            )

            if is_auth_error(exc):
                # Independent of strict mode: strict governs whether degradation
                # is acceptable, not whether the user is told their credential
                # was refused. Returning keyword results here would hand back
                # plausible output from a broken configuration.
                detail = redact_key(str(exc), self._api_key)
                logger.error("OpenAI rejected the API key during matching (model=%s): %s", self._model, detail)
                raise ApiKeyRejectedError(
                    f"OpenAI REJECTED the API key (model={self._model}): {detail}. "
                    f"The key in use came from {self._key_origin}. Replace it there and restart the MCP server. "
                    f"Searched, in order: {self._key_search}."
                ) from exc
            if self._strict:
                logger.error(
                    "LLM scoring failed in strict mode (model=%s) — "
                    "refusing to silently fall back to BM25. "
                    "Set TRACE_STRICT_LLM=false to allow fallback.",
                    self._model,
                )
                raise LLMFallbackError(
                    f"LLM matching failed (model={self._model}): {exc}. "
                    f"Strict mode is ON — set TRACE_STRICT_LLM=false to "
                    f"allow silent fallback to BM25."
                ) from exc
            logger.warning(
                "LLM scoring failed (model=%s) — falling back to BM25. Strict mode is OFF.",
                self._model,
                exc_info=True,
            )
            return await self._bm25_fallback.score_batch(learnings, context, context_tags)

        return [(index_map[local], score) for local, score in enumerate(scores)]

    async def _llm_score(
        self,
        learnings: list[Learning],
        context: str,
        context_tags: list[str] | None,
    ) -> list[float]:
        """Call the LLM and parse relevance scores."""
        entries: list[str] = []
        for i, lrn in enumerate(learnings):
            tags_str = ", ".join(lrn.tags) if lrn.tags else "none"
            entries.append(f'{i}. [{lrn.id}] "{lrn.content}" (tags: {tags_str})')
        learnings_block = "\n".join(entries)

        tags_str = ", ".join(context_tags) if context_tags else "none"
        user_prompt = (
            f"Search context: {context}\n"
            f"Context tags: {tags_str}\n\n"
            f"Learnings:\n{learnings_block}\n\n"
            "Return a JSON object mapping each learning's index (as a string) "
            "to its relevance score (0.0–1.0)."
        )

        # Egress-as-provenance: record the fact of the cloud call before making
        # it. Raising here is caught by score_batch's strict/permissive
        # handling like any LLM failure — and no content leaves the machine.
        # (project is unknown at this layer; the matcher scores one store's
        # learnings but is constructed from config alone.)
        attest_egress(
            provider="openai",
            endpoint="chat.completions",
            model=self._model,
            purpose="matching",
            content_class="learning-content+recall-query",
            item_count=len(learnings),
        )
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a relevance scoring system for a knowledge base. "
                        "Given a search context and a numbered list of knowledge entries, "
                        "score each entry's relevance to the context on a 0.0 to 1.0 scale. "
                        "Consider semantic meaning, intent, and conceptual similarity — "
                        "not just keyword overlap. "
                        "1.0 = perfectly relevant, 0.0 = completely irrelevant. "
                        "Return ONLY a JSON object mapping the index (as string) to a float score. "
                        'Example: {"0": 0.85, "1": 0.12}'
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )

        raw = response.choices[0].message.content or "{}"
        parsed: dict[str, float] = json.loads(raw)
        return [float(parsed.get(str(i), 0.0)) for i in range(len(learnings))]


# ── Embedding backend ─────────────────────────────────────────────────


class EmbeddingBackend:
    """Cosine-similarity matching using precomputed embeddings.

    Learnings *with* an ``embedding`` field are scored via numpy cosine
    similarity (sub-millisecond).  Learnings *without* embeddings are
    scored via a BM25 fallback so mixed stores work seamlessly.
    """

    # recall_ranked applies no absolute cutoff for this backend; it uses a
    # per-query relative floor (Z_FLOOR). Cosine scales differ by model, and on
    # the benchmark store (session-membership labels) every absolute cutoff
    # performed poorly for every model tried. 0.0 keeps the protocol attribute
    # meaningful: "never filter on this".
    default_threshold: float = 0.0

    def __init__(
        self,
        provider: EmbeddingProvider,
        tag_weight: float = 0.3,
        bm25_fallback_k1: float = 1.5,
        bm25_fallback_b: float = 0.75,
    ) -> None:
        self._provider = provider
        self._tag_weight = tag_weight
        # Lexical partner for hybrid fusion, and the stand-in scorer for
        # learnings that have no vector yet.
        self.bm25 = BM25Backend(
            k1=bm25_fallback_k1,
            b=bm25_fallback_b,
            tag_weight=tag_weight,
        )

    async def text_scores(self, learnings: list[Learning], context: str) -> list[float]:
        """Cosine similarity in [0, 1] per learning; BM25 stand-in for learnings with no vector."""
        if not learnings:
            return []
        scores = [0.0] * len(learnings)
        with_emb = [(i, lrn) for i, lrn in enumerate(learnings) if lrn.embedding is not None]
        without = [i for i, lrn in enumerate(learnings) if lrn.embedding is None]
        if with_emb:
            import numpy as np

            from trace_mcp.extensions.learn.embeddings import cosine_similarity_matrix, embed_query

            # Query-side encoding (instruction prefix where the model needs one);
            # documents were embedded with embed_texts when they were stored.
            query_vec = await embed_query(self._provider, context)
            matrix = np.array([lrn.embedding for _, lrn in with_emb], dtype=np.float32)
            sims = cosine_similarity_matrix(query_vec, matrix)
            for j, (i, _) in enumerate(with_emb):
                scores[i] = max(0.0, min(1.0, float(sims[j])))
        if without:
            standin = await self.bm25.text_scores([learnings[i] for i in without], context)
            for i, sc in zip(without, standin, strict=True):
                scores[i] = sc
        return scores

    def mix_scores(
        self, text: list[float], learnings: list[Learning], context_tags: list[str] | None
    ) -> list[tuple[int, float]]:
        """Legacy combined score per learning (pure), in the legacy order.

        Embedded learnings first, then the BM25 stand-ins, so a stable sort on
        the scores breaks ties the way it always did.
        """
        order = [i for i, lrn in enumerate(learnings) if lrn.embedding is not None]
        order += [i for i, lrn in enumerate(learnings) if lrn.embedding is None]
        return [
            (i, (1 - self._tag_weight) * text[i] + self._tag_weight * _tag_overlap(learnings[i].tags, context_tags))
            for i in order
        ]

    async def score_batch(
        self,
        learnings: list[Learning],
        context: str,
        context_tags: list[str] | None = None,
    ) -> list[tuple[int, float]]:
        return self.mix_scores(await self.text_scores(learnings, context), learnings, context_tags)


# ── Backend auto-selection ────────────────────────────────────────────────


def describe_backend(backend: MatchingBackend) -> str:
    """Short name of the engine behind a ranking, for recall responses.

    A caller must be able to tell WHICH backend produced a ranking — a
    degraded or misconfigured backend has to be visible in the response, not
    silent (the fail-loud posture of docs/INVARIANTS.md).
    """
    if isinstance(backend, EmbeddingBackend):
        return f"hybrid:embedding:{backend._provider.model_name}+bm25"
    if isinstance(backend, LLMBackend):
        return f"llm:{backend._model}"
    if isinstance(backend, BM25Backend):
        return "bm25"
    if isinstance(backend, JaccardBackend):
        return "jaccard"
    return type(backend).__name__


def get_default_backend(
    config: LearnConfig | None = None,
    provider: EmbeddingProvider | None = None,
) -> MatchingBackend:
    """Return the best available matching backend based on *config*.

    Priority: Embedding > LLM > BM25. Pass *provider* to reuse an embedding
    provider the caller already built; otherwise one is selected from *config*.
    Each provider loads its model lazily, so sharing one instance between the
    backend and the caller keeps a static model from being loaded twice.
    """
    if config is None:
        from trace_mcp.extensions.learn.config import load_config

        config = load_config()

    from trace_mcp.extensions.learn.config import LLMFallbackError

    # Tier 1: Embedding backend (cosine similarity on precomputed vectors)
    if provider is None:
        from trace_mcp.extensions.learn.embeddings import get_embedding_provider

        provider = get_embedding_provider(config)
    if provider is not None:
        logger.info("Using Embedding matching backend (provider=%s)", provider.model_name)
        return EmbeddingBackend(
            provider=provider,
            tag_weight=config.tag_weight,
            bm25_fallback_k1=config.bm25_k1,
            bm25_fallback_b=config.bm25_b,
        )

    # Tier 2: LLM backend (sends learnings to GPT for scoring)
    if config.llm_enabled and config.openai_api_key and _HAS_OPENAI:
        logger.info("Using LLM matching backend (model=%s)", config.llm_model)
        return LLMBackend(config)

    # Tier 3: BM25 (always available, zero external deps)
    # Strict mode refuses this fallback only when a CLOUD path was actually asked
    # for (LLM matching enabled, or OpenAI embeddings requested) and cannot be
    # served. A key alone activates nothing in this extension, so a key alone
    # must not turn keyword recall into a refusal; a missing explicitly
    # requested local backend is refused by get_embedding_provider itself.
    cloud_requested = config.llm_enabled or config.embedding_backend == "openai"
    if config.strict_llm and config.openai_api_key and cloud_requested:
        logger.error(
            "Strict LLM mode is ON and a cloud path was requested (TRACE_LLM_ENABLED or "
            "TRACE_EMBEDDING_BACKEND=openai), but neither an embedding nor an LLM backend is "
            "available. Refusing to fall back to BM25. Check that the 'openai' package is "
            "installed and the API key is valid, install 'fastembed' for local embeddings, "
            "or set TRACE_STRICT_LLM=false to allow BM25 fallback."
        )
        raise LLMFallbackError(
            "Strict LLM mode is ON but the requested cloud path has no LLM/Embedding backend "
            "available. Install the 'openai' package and verify OPENAI_API_KEY, install "
            "'fastembed' for local embeddings, or set TRACE_STRICT_LLM=false to allow BM25 fallback."
        )

    logger.warning(
        "Using BM25 matching backend (k1=%s, b=%s) — no LLM or embedding provider available",
        config.bm25_k1,
        config.bm25_b,
    )
    return BM25Backend(
        k1=config.bm25_k1,
        b=config.bm25_b,
        tag_weight=config.tag_weight,
    )


# ── Decay ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecayParams:
    """Configuration for time-based decay of learning scores."""

    enabled: bool = True
    half_life_days: float = 365.0
    evergreen_recall_threshold: int = 3
    evergreen_floor: float = 0.8


def compute_decay(
    learning: Learning,
    *,
    half_life_days: float = 365.0,
    evergreen_recall_threshold: int = 3,
    evergreen_floor: float = 0.8,
    now: datetime | None = None,
) -> float:
    """Compute a decay multiplier in [0, 1] for a learning.

    Uses exponential decay based on time since *last_surfaced* (or *created*
    if never surfaced).  If recall_count >= evergreen_recall_threshold,
    the multiplier is floored at *evergreen_floor*.

    A learning surfaced yesterday decays negligibly.  A never-surfaced
    learning created a year ago gets multiplier ~0.5.  An evergreen
    learning (surfaced 3+ times) never drops below *evergreen_floor*.
    """
    if now is None:
        now = datetime.now(UTC)

    reference = learning.last_surfaced or learning.created
    age_days = max((now - reference).total_seconds() / 86400.0, 0.0)

    # 2^(-age / half_life) — halves every half_life_days
    multiplier = 2.0 ** (-age_days / half_life_days) if half_life_days > 0 else 1.0

    # Evergreen floor: proven learnings don't fade below the floor
    if learning.recall_count >= evergreen_recall_threshold:
        multiplier = max(multiplier, evergreen_floor)

    return multiplier


# ── Hybrid ranking ───────────────────────────────────────────────────────

RRF_K = 60  # reciprocal-rank-fusion constant; the published default
Z_FLOOR = 1.0  # a candidate stands out when its similarity is >= 1 sd above the store's mean for this query
SMALL_STORE = 8  # below this many learnings a floor is statistically meaningless: return everything
UNMATCHED_CAP = 3  # weak candidates appended when fewer than `limit` learnings cleared the floor


@dataclass(frozen=True)
class RecallSummary:
    """What a recall did, for the banner and the tool response."""

    backend: str
    store_size: int
    scored: int
    matched: int
    returned: int
    floor: str
    best_similarity: float | None
    best_z: float | None
    small_store: bool
    query: str


@dataclass
class RecallOutcome:
    results: list[dict]
    summary: RecallSummary


def _competition_ranks(values: list[float]) -> list[int]:
    """1-based ranks, highest value first; ties share the best rank of their group."""
    order = sorted(range(len(values)), key=lambda i: -values[i])
    ranks = [0] * len(values)
    rank = 0
    prev: float | None = None
    for pos, i in enumerate(order):
        if prev is None or values[i] != prev:
            rank = pos + 1
            prev = values[i]
        ranks[i] = rank
    return ranks


def _rrf(lists: list[list[float]], k: int = RRF_K) -> list[float]:
    """Reciprocal rank fusion of several score lists over the same items."""
    fused = [0.0] * len(lists[0])
    for values in lists:
        for i, r in enumerate(_competition_ranks(values)):
            fused[i] += 1.0 / (k + r)
    return fused


def _zscores(values: list[float]) -> list[float]:
    n = len(values)
    if n == 0:
        return []
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / n)
    if sd < 1e-9:
        return [0.0] * n
    return [(v - mean) / sd for v in values]


async def _legacy_scores(
    backend: MatchingBackend,
    learnings: list[Learning],
    context: str,
    context_tags: list[str] | None,
) -> tuple[list[tuple[int, float]], dict[int, float] | None]:
    """The legacy ``score_batch`` pairs plus the raw similarity behind them, or None.

    ``score_batch`` is authoritative (an injected backend may override it). For
    backends that mix locally (they expose ``mix_scores``), the raw text
    similarity is read through ``text_scores`` for the diagnostics; that costs a
    second query embedding on this legacy path only. The LLM backend's scores are
    its own relevance judgement (tags go into its prompt, not into a formula), so
    for it the raw similarity is the returned score and the pairs may be sparse.
    """
    pairs = await backend.score_batch(learnings, context, context_tags)
    if not pairs:
        return pairs, {}
    if getattr(backend, "mix_scores", None) is None:
        return pairs, {i: sc for i, sc in pairs}  # the LLM's score is its own relevance judgement
    try:
        text = await backend.text_scores(learnings, context)
    except Exception:  # noqa: BLE001 - diagnostics must never discard a successful scoring
        logger.warning("Raw similarity unavailable for diagnostics; the result carries no similarity", exc_info=True)
        return pairs, None
    return pairs, dict(enumerate(text))


async def recall_ranked(
    learnings: list[Learning],
    context: str,
    context_tags: list[str] | None = None,
    *,
    threshold: float | None = None,
    limit: int = 10,
    backend: MatchingBackend | None = None,
    decay_config: DecayParams | None = None,
    now: datetime | None = None,
) -> RecallOutcome:
    """Rank *learnings* for *context* and say what happened.

    Default (``threshold`` is None): reciprocal rank fusion of the backend's
    similarity ranks with BM25 ranks (embedding backends) and tag-overlap ranks
    (when tags are given); an evidence list that is zero everywhere is left out
    of the fusion. Decay does not enter the ordering of candidates with
    different evidence: measured on the benchmark store, both multiplying it
    into the fused value and decaying the evidence before ranking let age
    outweigh relevance (MRR@10 0.52 -> 0.35 and 0.26 for BM25-only). Ties break
    by similarity, then by decay (fresher or evergreen first), then newest
    created. ``score`` on each result is similarity x decay. A candidate is
    ``matched`` when it stands >= Z_FLOOR standard deviations above the store
    mean, has a lexical hit (BM25 >= 0.15) or shares a tag; non-embedding
    backends use their own ``default_threshold``. When fewer than ``limit`` are
    matched, up to UNMATCHED_CAP weak candidates follow, flagged
    ``matched: false``. A store smaller than SMALL_STORE skips the z floor
    (meaningless on so few items) and returns everything up to ``limit``, each
    flagged by the remaining signals.

    Explicit ``threshold``: the legacy path. Only the candidates the backend
    scored take part (a pre-filtering backend may return fewer than the store);
    each one's combined score (similarity mixed with tag overlap) times decay is
    filtered by ``score >= threshold`` and the survivors are stable-sorted by
    that score alone, so equal scores keep the backend's order. ``signals``
    report the raw similarity when it can be read, else ``None``.

    Side effects: matched, surfaced learnings get recall_count++ and last_surfaced.
    """
    if not learnings:
        name = describe_backend(backend) if backend is not None else "none"
        return RecallOutcome([], RecallSummary(name, 0, 0, 0, 0, "n/a (empty store)", None, None, False, context))
    if backend is None:
        backend = get_default_backend()
    name = describe_backend(backend)

    n = len(learnings)
    now = now or datetime.now(UTC)
    if decay_config is not None and decay_config.enabled:
        decay = [
            compute_decay(
                lrn,
                half_life_days=decay_config.half_life_days,
                evergreen_recall_threshold=decay_config.evergreen_recall_threshold,
                evergreen_floor=decay_config.evergreen_floor,
                now=now,
            )
            for lrn in learnings
        ]
    else:
        decay = [1.0] * n
    tag_overlap = [_tag_overlap(lrn.tags, context_tags) for lrn in learnings]
    bm25: list[float] | None = None
    small = False

    if threshold is not None:
        pairs, raw = await _legacy_scores(backend, learnings, context, context_tags)
        scored_idx = [i for i, _ in pairs]
        score = {i: sc * decay[i] for i, sc in pairs}
        fused = score
        similarity: dict[int, float | None]
        z: dict[int, float | None]
        if raw is None:  # raw similarity unavailable: say so rather than substitute
            similarity = dict.fromkeys(scored_idx)
            z = dict.fromkeys(scored_idx)
        else:
            similarity = {i: raw.get(i, 0.0) for i in scored_idx}
            zs = _zscores([raw.get(i, 0.0) for i in scored_idx])
            z = dict(zip(scored_idx, zs, strict=True))
        matched = {i: score[i] >= threshold for i in scored_idx}
        floor = f"score>={threshold:g}"
        kept = [i for i in scored_idx if matched[i]]  # backend order ...
        kept.sort(key=lambda i: score[i], reverse=True)  # ... kept for equal scores (stable sort)
        surfaced = kept[:limit]
        scored_count = len(pairs)
    else:
        sims = await backend.text_scores(learnings, context)
        hybrid = isinstance(backend, EmbeddingBackend)
        if hybrid:
            bm25 = await backend.bm25.text_scores(learnings, context)
        # Fusion lists: the similarity always; BM25 and tags only when they carry
        # any evidence, because an all-zero list adds the same constant to every
        # fused score and shifts ties. Decay is NOT in the fusion: it orders ties
        # below and scales the displayed score (see the docstring).
        lists: list[list[float]] = [sims]
        if bm25 is not None and any(v > 0.0 for v in bm25):
            lists.append(bm25)
        if context_tags and any(v > 0.0 for v in tag_overlap):
            lists.append(tag_overlap)
        fused_list = _rrf(lists)
        zs = _zscores(sims)
        similarity = dict(enumerate(sims))
        score = {i: sims[i] * decay[i] for i in range(n)}
        fused = dict(enumerate(fused_list))
        z = dict(enumerate(zs))
        small = n < SMALL_STORE
        if hybrid:
            lexical_floor = BM25Backend.default_threshold
            assert bm25 is not None
            matched = {
                i: (not small and zs[i] >= Z_FLOOR) or bm25[i] >= lexical_floor or tag_overlap[i] > 0.0
                for i in range(n)
            }
            floor = (
                f"small store of {n}: all returned; matched on a lexical hit (bm25>={lexical_floor:g}) or a shared tag"
                if small
                else f"z>={Z_FLOOR:g} or bm25>={lexical_floor:g}"
            )
        else:
            base = float(getattr(backend, "default_threshold", 0.1))
            matched = {i: sims[i] >= base or tag_overlap[i] > 0.0 for i in range(n)}
            floor = f"small store of {n}: all returned; matched at score>={base:g}" if small else f"score>={base:g}"
        order = sorted(range(n), key=lambda i: (fused[i], similarity[i], decay[i], learnings[i].created), reverse=True)
        if small:
            surfaced = order[:limit]
        else:
            surfaced = [i for i in order if matched[i]][:limit]
            if len(surfaced) < limit:
                weak = [i for i in order if not matched[i]]
                surfaced += weak[: min(UNMATCHED_CAP, limit - len(surfaced))]
        scored_count = n

    results: list[dict] = []
    for pos, i in enumerate(surfaced, start=1):
        sim_v, z_v = similarity[i], z[i]  # None on the legacy path when the raw read failed
        if matched[i]:
            learnings[i].recall_count += 1
            learnings[i].last_surfaced = now
        results.append(
            {
                "learning": learnings[i].model_dump(mode="json", exclude={"embedding", "embedding_model"}),
                "score": round(score[i], 4),
                "matched": matched[i],
                "rank": pos,
                "signals": {
                    "similarity": round(sim_v, 4) if sim_v is not None else None,
                    "z": round(z_v, 2) if z_v is not None else None,
                    "bm25": round(bm25[i], 4) if bm25 is not None else None,
                    "tag_overlap": round(tag_overlap[i], 2),
                    "decay": round(decay[i], 3),
                    "fused": round(fused[i], 5),
                },
            }
        )
    summary = RecallSummary(
        backend=name,
        store_size=n,
        scored=scored_count,
        matched=sum(1 for v in matched.values() if v),
        returned=len(results),
        floor=floor,
        best_similarity=round(max(known), 4) if (known := [v for v in similarity.values() if v is not None]) else None,
        best_z=round(max(known_z), 2) if (known_z := [v for v in z.values() if v is not None]) else None,
        small_store=small,
        query=context,
    )
    return RecallOutcome(results, summary)


async def recall_learnings(
    learnings: list[Learning],
    context: str,
    context_tags: list[str] | None = None,
    threshold: float | None = None,
    limit: int = 10,
    backend: MatchingBackend | None = None,
    decay_config: DecayParams | None = None,
) -> list[dict]:
    """Compatibility wrapper: the ranked results of :func:`recall_ranked`."""
    outcome = await recall_ranked(
        learnings,
        context,
        context_tags,
        threshold=threshold,
        limit=limit,
        backend=backend,
        decay_config=decay_config,
    )
    return outcome.results
