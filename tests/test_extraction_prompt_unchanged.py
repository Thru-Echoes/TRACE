"""The live extraction prompt is pinned byte for byte.

The provenance-marks shadow arm shares the live extractor's prompt builder and
model call. This characterization test was written against the extractor before
that sharing existed, and its golden file records the exact messages the live
arm sends, so a restructuring cannot change what the live arm asks the model.
The client is the one substitution: nothing leaves the machine.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from trace_mcp.extensions.learn import extraction
from trace_mcp.extensions.learn.config import LearnConfig
from trace_mcp.extensions.learn.models import KnowledgeStore, Learning
from trace_mcp.schema import Session
from trace_mcp.schema.events import AnnotationData, ContributionData, DecisionData, TraceEvent
from trace_mcp.schema.session import Actor, SessionMetadata

GOLDEN = Path(__file__).parent / "fixtures" / "extraction_prompt_golden.json"
SESSION_ID = "trace_20260101_abc123"
T = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


class _RecordingClient:
    """Records the keyword arguments of ``chat.completions.create``."""

    def __init__(self, content: str) -> None:
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._content = content

    async def _create(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(kwargs)
        message = SimpleNamespace(content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def get_fixed_session() -> Session:
    """A session with an AI annotation, a proposed decision with a rationale, and a contribution."""
    ai = Actor(type="ai", id="ai-assistant")
    events = [
        TraceEvent(
            id="evt_001",
            timestamp=T,
            session_id=SESSION_ID,
            type="annotation",
            actor=ai,
            annotation=AnnotationData(category="gotcha", content="The cache key ignores the locale.", tags=["cache"]),
        ),
        TraceEvent(
            id="evt_002",
            timestamp=T.replace(minute=5),
            session_id=SESSION_ID,
            type="decision",
            actor=ai,
            decision=DecisionData(
                description="Key the cache by locale as well as path.",
                rationale="Two locales served each other's pages.",
                proposed_by=ai,
            ),
        ),
        TraceEvent(
            id="evt_003",
            timestamp=T.replace(minute=9),
            session_id=SESSION_ID,
            type="contribution",
            actor=ai,
            contribution=ContributionData(
                description="Added a locale-aware cache key.", artifact="cache.py", direction="human", execution="ai"
            ),
        ),
    ]
    return Session(id=SESSION_ID, created=T, metadata=SessionMetadata(project="collab-a"), events=events)


def get_fixed_store() -> KnowledgeStore:
    store = KnowledgeStore(project="collab-a")
    store.learnings = [
        Learning(id="lrn_001", content="Retry the upload at most three times.", category="decision", created=T),
        Learning(id="lrn_002", content="The staging bucket is read-only.", category="gotcha", created=T),
    ]
    return store


async def test_the_live_extraction_prompt_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _RecordingClient('{"learnings": []}')
    monkeypatch.setattr(extraction, "AsyncOpenAI", lambda **_kw: client)

    await extraction.extract_from_session_llm(
        get_fixed_store(), get_fixed_session(), LearnConfig(openai_api_key="test-key")
    )

    assert len(client.requests) == 1
    assert client.requests[0]["messages"] == json.loads(GOLDEN.read_text())
