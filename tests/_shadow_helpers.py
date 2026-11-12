"""Shared fixtures for the provenance-marks shadow-step tests (not a test module).

Exports:
    ScriptedClient — a stand-in for the OpenAI client that replays scripted replies.
    learn_env — isolate every data path, configure cloud extraction with a fake key,
        register the learn extension, and return the session storage.
    make_session — create and end a session with one annotation and three decisions.
    reply — build the model's JSON reply.
    FakeProvider — a deterministic embedding provider.

Nothing here reads the real ~/.trace or calls a real model.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer

import trace_mcp.project_identity as pident
from trace_mcp.extensions.learn import config as learn_config
from trace_mcp.extensions.learn import register
from trace_mcp.schema import Session
from trace_mcp.storage.json_file import JsonFileStorage
from trace_mcp.tools import decision_tools, logging_tools, session_tools


class ScriptedClient:
    """Replays ``replies[i]`` for the i-th ``chat.completions.create`` call and records each request."""

    def __init__(
        self,
        replies: Sequence[str],
        *,
        usage: tuple[int, int] | None = (120, 30),
        fail_on: set[int] | frozenset[int] = frozenset(),
        delay_on: dict[int, float] | None = None,
    ) -> None:
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._replies = list(replies)
        self._usage = usage
        self._fail_on = set(fail_on)
        self._delay_on = dict(delay_on or {})

    async def _create(self, **kwargs: Any) -> SimpleNamespace:
        index = len(self.requests)
        self.requests.append(kwargs)
        if index in self._delay_on:
            await asyncio.sleep(self._delay_on[index])
        if index in self._fail_on:
            raise RuntimeError("model down")
        content = self._replies[index] if index < len(self._replies) else '{"learnings": []}'
        usage = SimpleNamespace(prompt_tokens=self._usage[0], completion_tokens=self._usage[1]) if self._usage else None
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage)

    def user_message(self, index: int) -> str:
        return next(m["content"] for m in self.requests[index]["messages"] if m["role"] == "user")


def learn_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, local_only: bool = False) -> JsonFileStorage:
    """Point every TRACE data path under ``tmp_path``, enable cloud extraction with a fake key, register learn.

    Side effects: sets environment variables and the working directory for the
    test (undone by ``monkeypatch``); registers the learn extension's hooks.
    """
    monkeypatch.setenv("TRACE_SHADOW_DIR", str(tmp_path / "shadow"))
    monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setenv("TRACE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("TRACE_EGRESS_LOG", str(tmp_path / "egress.jsonl"))
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("TRACE_LLM_ENABLED", "true")
    monkeypatch.setenv("TRACE_STRICT_LLM", "false")
    monkeypatch.setenv("TRACE_EMBEDDING_BACKEND", "none")
    for var in ("TRACE_PROJECT", "TRACE_EMBEDDING_MODEL", "TRACE_DEDUP_THRESHOLD", "TRACE_LOCK_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)
    if local_only:
        monkeypatch.setenv("TRACE_LOCAL_ONLY", "1")
    else:
        monkeypatch.delenv("TRACE_LOCAL_ONLY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(learn_config, "_TRACE_ENV_PATH", tmp_path / "no-such-dir" / ".env")
    pident._reset_registry_cache()
    storage = JsonFileStorage(directory=str(tmp_path / "sessions"))
    register(MCPServer("shadow-test"), storage)
    return storage


async def make_session(
    storage: JsonFileStorage,
    project: str = "collab-a",
    *,
    resolution_quote: str | None = "yes, go with three retries",
) -> Session:
    """Create and end a session: evt_001 AI annotation; evt_002 proposed; evt_003 and evt_004 accepted by alice.

    evt_003 is resolved with ``resolution_quote`` as the person's words; evt_004 with none.
    """
    active: dict[str, Session] = {}
    session = await session_tools.create_session(storage, active, project=project)
    await logging_tools.log_annotation(
        storage, session, category="learning", content="The upload retries forever on a 503."
    )
    for description in ("Cap upload retries at three.", "Use exponential backoff.", "Log every retry."):
        await decision_tools.propose_decision(
            storage, session, description=description, proposed_by_type="ai", proposed_by_id="ai-assistant"
        )
    await decision_tools.resolve_decision(
        storage,
        session,
        event_id="evt_003",
        disposition="accepted",
        resolved_by_type="human",
        resolved_by_id="alice",
        conversation_snippet=resolution_quote,
    )
    await decision_tools.resolve_decision(
        storage, session, event_id="evt_004", disposition="accepted", resolved_by_type="human", resolved_by_id="alice"
    )
    await session_tools.end_session(storage, active, session_id=session.id)
    return await storage.get_session(session.id)


def reply(*pairs: tuple[str, str]) -> str:
    """The model's JSON reply naming ``(content, source_event)`` pairs."""
    return json.dumps(
        {"learnings": [{"content": c, "category": "learning", "tags": [], "source_event": e} for c, e in pairs]}
    )


class FakeProvider:
    """An embedding provider with fixed vectors: the query is ``query_vec``; documents come from ``doc_vec``."""

    model_name: str = "fake"
    dimensions: int = 3

    def __init__(
        self,
        query_vec: Sequence[float] = (1.0, 0.0, 0.0),
        doc_vec: Callable[[str], list[float]] | None = None,
    ) -> None:
        self._query = list(query_vec)
        self._doc = doc_vec or (lambda _text: [0.0, 0.0, 0.0])
        self.embedded: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        return list(self._query)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.embedded.extend(texts)
        return [self._doc(t) for t in texts]
