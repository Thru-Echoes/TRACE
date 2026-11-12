"""The extraction model call reports its usage and is attested for each purpose.

Both arms of the provenance-marks comparison call the model through one
function, which reads token counts from the provider's response and times the
request. The client is the one substitution: nothing leaves the machine.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_extraction_prompt_unchanged import get_fixed_session, get_fixed_store

from trace_mcp.extensions.learn import extraction
from trace_mcp.extensions.learn.config import LearnConfig
from trace_mcp.extensions.learn.extraction import (
    PROPOSAL_WORDING_INSTRUCTION,
    ExtractionUsage,
    _call_extraction_model,
    _format_learnings,
    build_extraction_prompt,
)


class _UsageClient:
    def __init__(self, content: str, usage: Any = None, *, fail: bool = False) -> None:
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._content = content
        self._usage = usage
        self._fail = fail

    async def _create(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(kwargs)
        if self._fail:
            raise RuntimeError("provider unavailable")
        message = SimpleNamespace(content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=self._usage)


async def test_the_sink_receives_the_provider_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _UsageClient('{"learnings": []}', SimpleNamespace(prompt_tokens=120, completion_tokens=30))
    monkeypatch.setattr(extraction, "AsyncOpenAI", lambda **_kw: client)
    store = get_fixed_store()
    seen: list[ExtractionUsage] = []

    await extraction.extract_from_session_llm(
        store, get_fixed_session(), LearnConfig(openai_api_key="test-key"), usage_sink=seen.append
    )

    assert len(seen) == 1
    usage = seen[0]
    assert usage.path == "llm"
    assert usage.prompt_tokens == 120
    assert usage.completion_tokens == 30
    assert usage.seconds is not None and usage.seconds >= 0
    assert usage.existing_chars == len(_format_learnings(store.learnings))


async def test_a_response_without_usage_reports_none(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _UsageClient('{"learnings": []}', None)
    monkeypatch.setattr(extraction, "AsyncOpenAI", lambda **_kw: client)
    seen: list[ExtractionUsage] = []

    await extraction.extract_from_session_llm(
        get_fixed_store(), get_fixed_session(), LearnConfig(openai_api_key="test-key"), usage_sink=seen.append
    )

    assert seen[0].path == "llm"
    assert seen[0].prompt_tokens is None and seen[0].completion_tokens is None


async def test_a_fallback_reports_the_rule_based_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _UsageClient("", fail=True)
    monkeypatch.setattr(extraction, "AsyncOpenAI", lambda **_kw: client)
    seen: list[ExtractionUsage] = []

    await extraction.extract_from_session_llm(
        get_fixed_store(),
        get_fixed_session(),
        LearnConfig(openai_api_key="test-key", strict_llm=False),
        usage_sink=seen.append,
    )

    assert [u.path for u in seen] == ["rule-based"]


async def test_auto_reports_the_rule_based_branch() -> None:
    seen: list[ExtractionUsage] = []
    await extraction.extract_from_session_auto(
        get_fixed_store(), get_fixed_session(), LearnConfig(openai_api_key=None), usage_sink=seen.append
    )
    assert [u.path for u in seen] == ["rule-based"]


def test_proposal_wording_appends_one_instruction() -> None:
    session = get_fixed_session()
    text = _format_learnings(get_fixed_store().learnings)
    assert (
        build_extraction_prompt(session, text, proposal_wording=True)
        == build_extraction_prompt(session, text) + "\n\n" + PROPOSAL_WORDING_INSTRUCTION
    )
    assert PROPOSAL_WORDING_INSTRUCTION == (
        'A decision whose disposition is "proposed" has not been accepted by anyone. If a learning\'s '
        "source_event is such a decision, word the learning as a proposal (for example "
        '"It was proposed that ..."), never as a rule or an instruction.'
    )


async def test_the_shadow_purpose_is_attested(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _UsageClient('{"learnings": [{"content": "x"}]}', SimpleNamespace(prompt_tokens=5, completion_tokens=1))
    monkeypatch.setattr(extraction, "AsyncOpenAI", lambda **_kw: client)
    ledger = Path(os.environ["TRACE_EGRESS_LOG"])
    before = ledger.read_text().splitlines() if ledger.exists() else []

    items, usage = await _call_extraction_model(
        LearnConfig(openai_api_key="test-key"),
        "a prompt",
        session=get_fixed_session(),
        purpose="shadow-extraction",
        content_class="session-events+nearest-learnings",
        existing_chars=42,
    )

    after = ledger.read_text().splitlines()
    added = [json.loads(line) for line in after[len(before) :]]
    assert len(added) == 1
    assert added[0]["purpose"] == "shadow-extraction"
    assert added[0]["content_class"] == "session-events+nearest-learnings"
    assert items == [{"content": "x"}]
    assert usage.existing_chars == 42
    assert set(client.requests[0]) == {"model", "messages", "response_format"}
