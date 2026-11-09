"""Requests to OpenAI carry only parameters every configured model accepts.

The default model rejects any ``temperature`` other than its default with HTTP 400,
so a request that sets one fails on every call. The client is the one substitution
here: these tests check the request TRACE builds, not OpenAI's response.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import trace_mcp
from trace_mcp.extensions.learn import extraction
from trace_mcp.extensions.learn.config import LearnConfig
from trace_mcp.extensions.learn.matching import LLMBackend
from trace_mcp.extensions.learn.models import KnowledgeStore, Learning
from trace_mcp.schema import Session
from trace_mcp.schema.events import AnnotationData, TraceEvent
from trace_mcp.schema.session import Actor, SessionMetadata

ACCEPTED_BY_EVERY_MODEL = {"model", "messages", "response_format"}
SRC = Path(trace_mcp.__file__).parent
SESSION_ID = "trace_20260101_abc123"


def _annotation() -> TraceEvent:
    return TraceEvent(
        id="evt_001",
        session_id=SESSION_ID,
        type="annotation",
        actor=Actor(type="ai", id="ai-assistant"),
        annotation=AnnotationData(category="learning", content="A directory fsync makes a new name durable."),
    )


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


async def test_the_matching_request_carries_only_accepted_parameters() -> None:
    backend = LLMBackend(LearnConfig(openai_api_key="test-key"))
    client = _RecordingClient('{"0": 0.5}')
    backend._client = client  # type: ignore[assignment]

    await backend._llm_score([Learning(content="a learning")], "some context", [])

    assert set(client.requests[0]) <= ACCEPTED_BY_EVERY_MODEL


async def test_the_extraction_request_carries_only_accepted_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _RecordingClient('{"learnings": []}')
    monkeypatch.setattr(extraction, "AsyncOpenAI", lambda **_kwargs: client)

    await extraction.extract_from_session_llm(
        KnowledgeStore(project="collab-a"),
        Session(id=SESSION_ID, metadata=SessionMetadata(project="collab-a"), events=[_annotation()]),
        LearnConfig(openai_api_key="test-key"),
    )

    assert set(client.requests[0]) <= ACCEPTED_BY_EVERY_MODEL


def test_no_request_in_the_package_sets_a_temperature() -> None:
    """A new call site must not reintroduce a parameter the default model rejects."""
    offenders = [
        f"{path.relative_to(SRC)}:{node.lineno}"
        for path in sorted(SRC.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path)))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create"
        and any(keyword.arg == "temperature" for keyword in node.keywords)
    ]
    assert not offenders, f"These requests set a temperature the default model rejects: {offenders}"
