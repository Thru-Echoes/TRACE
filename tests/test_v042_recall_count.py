"""v0.4.2 Phase 5: recall_count / last_surfaced must reflect only SURFACED learnings.

recall_learnings incremented recall_count and reset last_surfaced for EVERY
above-threshold learning *before* the sort+[:limit] slice. So when more matched
than `limit`, learnings that were never actually returned still had their recall
count inflated and their decay clock reset — corrupting the decay/evergreen
provenance signal (the differentiator the trace-learn feature is marketed on).
The fix mutates only the top-`limit` learnings that are actually surfaced.
"""

from __future__ import annotations

from trace_mcp.extensions.learn.matching import JaccardBackend, recall_learnings
from trace_mcp.extensions.learn.models import KnowledgeStore
from trace_mcp.extensions.learn.store import add_learning


async def test_recall_increments_only_surfaced_learnings():
    ks = KnowledgeStore(project="rc")
    for i in range(5):
        add_learning(ks, content=f"alpha beta gamma extra{i}", tags=["t"])

    results = await recall_learnings(
        ks.learnings,
        context="alpha beta gamma",
        threshold=0.1,
        limit=2,
        backend=JaccardBackend(),
    )

    # Only the 2 surfaced learnings are returned AND recall-counted.
    assert len(results) == 2
    surfaced = [lrn for lrn in ks.learnings if lrn.recall_count > 0]
    assert len(surfaced) == 2
    assert sum(lrn.recall_count for lrn in ks.learnings) == 2
    assert all(lrn.last_surfaced is not None for lrn in surfaced)
    # The 3 matched-but-not-surfaced learnings are untouched.
    not_surfaced = [lrn for lrn in ks.learnings if lrn.recall_count == 0]
    assert len(not_surfaced) == 3
    assert all(lrn.last_surfaced is None for lrn in not_surfaced)
