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

Auto-selection: Embedding > LLM > BM25.
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

    async def score_batch(
        self,
        learnings: list[Learning],
        context: str,
        context_tags: list[str] | None = None,
    ) -> list[tuple[int, float]]:
        """Score each learning against *context*.

        Returns a list of ``(index, score)`` tuples where *index* is the
        position in *learnings* and *score* is in ``[0, 1]``.
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

    async def score_batch(
        self,
        learnings: list[Learning],
        context: str,
        context_tags: list[str] | None = None,
    ) -> list[tuple[int, float]]:
        return [(i, score_learning(lrn, context, context_tags, self.tag_weight)) for i, lrn in enumerate(learnings)]


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

    async def score_batch(
        self,
        learnings: list[Learning],
        context: str,
        context_tags: list[str] | None = None,
    ) -> list[tuple[int, float]]:
        if not learnings:
            return []
        documents = [_tokenize(lrn.content, stem=True) for lrn in learnings]
        query_tokens = _tokenize(context, stem=True)

        index = _BM25Index(documents, k1=self.k1, b=self.b)

        results: list[tuple[int, float]] = []
        for i, lrn in enumerate(learnings):
            raw_bm25 = index.score(query_tokens, i)
            text_score = _normalize_bm25(raw_bm25)
            tag_score = _tag_overlap(lrn.tags, context_tags)
            combined = (1 - self.tag_weight) * text_score + self.tag_weight * tag_score
            results.append((i, combined))
        return results


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
        self._bm25_fallback = BM25Backend(
            k1=config.bm25_k1,
            b=config.bm25_b,
            tag_weight=config.tag_weight,
        )

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
            from trace_mcp.extensions.learn.config import LLMFallbackError

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

    default_threshold: float = 0.3

    def __init__(
        self,
        provider: EmbeddingProvider,
        tag_weight: float = 0.3,
        bm25_fallback_k1: float = 1.5,
        bm25_fallback_b: float = 0.75,
    ) -> None:
        self._provider = provider
        self._tag_weight = tag_weight
        self._bm25_fallback = BM25Backend(
            k1=bm25_fallback_k1,
            b=bm25_fallback_b,
            tag_weight=tag_weight,
        )

    async def score_batch(
        self,
        learnings: list[Learning],
        context: str,
        context_tags: list[str] | None = None,
    ) -> list[tuple[int, float]]:
        if not learnings:
            return []

        # Partition: learnings with embeddings vs without
        with_emb: list[tuple[int, Learning]] = []
        without_emb: list[tuple[int, Learning]] = []
        for i, lrn in enumerate(learnings):
            if lrn.embedding is not None:
                with_emb.append((i, lrn))
            else:
                without_emb.append((i, lrn))

        results: list[tuple[int, float]] = []

        # Score embedded learnings via cosine similarity
        if with_emb:
            import numpy as np

            from trace_mcp.extensions.learn.embeddings import cosine_similarity_matrix

            query_vecs = await self._provider.embed_texts([context])
            query_vec = query_vecs[0]

            matrix = np.array(
                [lrn.embedding for _, lrn in with_emb],
                dtype=np.float32,
            )
            similarities = cosine_similarity_matrix(query_vec, matrix)

            for j, (orig_idx, lrn) in enumerate(with_emb):
                text_score = max(0.0, min(1.0, float(similarities[j])))
                tag_score = _tag_overlap(lrn.tags, context_tags)
                combined = (1 - self._tag_weight) * text_score + self._tag_weight * tag_score
                results.append((orig_idx, combined))

        # BM25 fallback for learnings without embeddings
        if without_emb:
            bm25_learnings = [lrn for _, lrn in without_emb]
            bm25_scores = await self._bm25_fallback.score_batch(
                bm25_learnings,
                context,
                context_tags,
            )
            for bm25_local_idx, score in bm25_scores:
                orig_idx = without_emb[bm25_local_idx][0]
                results.append((orig_idx, score))

        return results


# ── Backend auto-selection ────────────────────────────────────────────────


def get_default_backend(config: LearnConfig | None = None) -> MatchingBackend:
    """Return the best available matching backend based on *config*.

    Priority: Embedding > LLM > BM25.
    """
    if config is None:
        from trace_mcp.extensions.learn.config import load_config

        config = load_config()

    from trace_mcp.extensions.learn.config import LLMFallbackError

    # Tier 1: Embedding backend (cosine similarity on precomputed vectors)
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
    # If strict mode is ON and the user has an API key, they expected LLM or
    # embedding — refuse to silently degrade to BM25.
    if config.strict_llm and config.openai_api_key:
        logger.error(
            "Strict LLM mode is ON and an OPENAI_API_KEY is set, but neither "
            "Embedding nor LLM backends are available. Refusing to fall back "
            "to BM25. Check that the 'openai' package is installed and the "
            "API key is valid. Set TRACE_STRICT_LLM=false to allow BM25 fallback."
        )
        raise LLMFallbackError(
            "Strict LLM mode is ON but no LLM/Embedding backend is available. "
            "Install 'openai' package and verify OPENAI_API_KEY, or set "
            "TRACE_STRICT_LLM=false to allow BM25 fallback."
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


# ── Main recall function ─────────────────────────────────────────────────


async def recall_learnings(
    learnings: list[Learning],
    context: str,
    context_tags: list[str] | None = None,
    threshold: float | None = None,
    limit: int = 10,
    backend: MatchingBackend | None = None,
    decay_config: DecayParams | None = None,
) -> list[dict]:
    """Find relevant learnings for a given context.

    Returns learnings above *threshold*, sorted by score descending,
    capped at *limit*.  Uses *backend* for scoring; if None, auto-selects
    the best available backend.

    When *threshold* is None, the backend's ``default_threshold`` is used
    (BM25: 0.15, LLM: 0.2, Jaccard: 0.1).

    When *decay_config* is provided and enabled, scores are multiplied by
    a time-based decay factor.  Matched learnings (above threshold) have
    their ``recall_count`` incremented and ``last_surfaced`` set — callers
    should save the store afterward.
    """
    if not learnings:
        return []
    if backend is None:
        backend = get_default_backend()
    if threshold is None:
        threshold = float(getattr(backend, "default_threshold", 0.1))

    scored_pairs = await backend.score_batch(learnings, context, context_tags)

    # Apply decay if configured
    if decay_config is not None and decay_config.enabled:
        now = datetime.now(UTC)
        scored_pairs = [
            (
                idx,
                score
                * compute_decay(
                    learnings[idx],
                    half_life_days=decay_config.half_life_days,
                    evergreen_recall_threshold=decay_config.evergreen_recall_threshold,
                    evergreen_floor=decay_config.evergreen_floor,
                    now=now,
                ),
            )
            for idx, score in scored_pairs
        ]

    # Filter by threshold, then sort + cap to `limit` BEFORE recording recall.
    # Only the learnings actually surfaced (returned) get recall_count++ and
    # last_surfaced reset. Previously every above-threshold match was counted —
    # including those dropped by the limit — which inflated recall_count and
    # reset decay clocks for learnings the caller never saw (corrupting the
    # decay/evergreen signal).
    matched = [(idx, score) for idx, score in scored_pairs if score >= threshold]
    matched.sort(key=lambda x: x[1], reverse=True)
    surfaced = matched[:limit]

    now = datetime.now(UTC)
    results: list[dict] = []
    for idx, score in surfaced:
        learnings[idx].recall_count += 1
        learnings[idx].last_surfaced = now
        results.append(
            {
                "learning": learnings[idx].model_dump(
                    mode="json",
                    exclude={"embedding", "embedding_model"},
                ),
                "score": round(score, 4),
            }
        )
    return results
