"""Fitness scoring: token-based similarity matching for adaptation expression.

Zero external dependencies — uses simple regex tokenization and Jaccard
similarity with optional tag boosting.
"""

from __future__ import annotations

import re

from trace_mcp.extensions.evolve.models import Adaptation

_TOKEN_RE = re.compile(r"\w+")


def _tokenize(text: str) -> set[str]:
    """Extract lowercase word tokens from text."""
    return set(_TOKEN_RE.findall(text.lower()))


def jaccard_similarity(text1: str, text2: str) -> float:
    """Compute Jaccard similarity between two texts (token overlap)."""
    tokens1 = _tokenize(text1)
    tokens2 = _tokenize(text2)
    if not tokens1 or not tokens2:
        return 0.0
    intersection = tokens1 & tokens2
    union = tokens1 | tokens2
    return len(intersection) / len(union)


def fitness_score(
    adaptation: Adaptation,
    context: str,
    context_tags: list[str] | None = None,
) -> float:
    """Score an adaptation's fitness against a context string and optional tags.

    Returns weighted combination: 70% text similarity + 30% tag overlap.
    """
    text_score = jaccard_similarity(adaptation.content, context)

    tag_score = 0.0
    if context_tags and adaptation.tags:
        context_set = {t.lower() for t in context_tags}
        adaptation_set = {t.lower() for t in adaptation.tags}
        intersection = context_set & adaptation_set
        union = context_set | adaptation_set
        tag_score = len(intersection) / len(union) if union else 0.0

    return 0.7 * text_score + 0.3 * tag_score


def express_adaptations(
    adaptations: list[Adaptation],
    context: str,
    context_tags: list[str] | None = None,
    threshold: float = 0.1,
    limit: int = 10,
) -> list[dict]:
    """Express relevant adaptations for a given context (gene expression).

    Returns adaptations above fitness threshold, sorted by score descending, up to limit.
    """
    scored = []
    for adp in adaptations:
        s = fitness_score(adp, context, context_tags)
        if s >= threshold:
            scored.append({"adaptation": adp.model_dump(mode="json"), "score": round(s, 4)})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]
