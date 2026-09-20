from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request

import pytest
from mcp.server.fastmcp import FastMCP

import trace_mcp.project_identity as pident
from trace_mcp.extensions.jev import register
from trace_mcp.extensions.jev.client import assess_event, project_event, validate_answers
from trace_mcp.extensions.jev.config import JevConfig, load_config
from trace_mcp.schema import Actor, DecisionConfidence, DecisionData, MeasurementInterval, MeasurementMethod, TraceEvent
from trace_mcp.schema.session import Session, SessionMetadata
from trace_mcp.storage.json_file import JsonFileStorage


def _event() -> TraceEvent:
    return TraceEvent(
        id="evt_001",
        session_id="trace_test",
        type="decision",
        actor=Actor(type="ai", id="agent"),
        decision=DecisionData(
            description="Pin the evaluation split before scoring.",
            rationale="Prevents test leakage.",
            proposed_by=Actor(type="ai", id="agent"),
            confidence=DecisionConfidence(
                statistic="accuracy_delta",
                estimate=0.1,
                direction="higher",
                interval=MeasurementInterval(lower=0.05, upper=0.15, level=0.95),
                method=MeasurementMethod(name="bootstrap"),
                sample_size=100,
            ),
        ),
    )


def _response(choice: str = "durable_candidate", confidence: float = 0.91) -> bytes:
    return json.dumps(
        {
            "model": "jev-1.13.0",
            "answers": {
                "candidate_type": {
                    "type": "choice",
                    "choice": choice,
                    "probabilities": {
                        "durable_candidate": 0.9 if choice == "durable_candidate" else 0.05,
                        "episodic": 0.05 if choice == "durable_candidate" else 0.9,
                        "insufficient": 0.05,
                    },
                    "confidence": confidence,
                },
                "bounded": {"type": "noul", "noul": 0.92},
                "durable": {"type": "noul", "noul": 0.93},
            },
            "usage": {"input_tokens": 21, "output_tokens": 4},
        }
    ).encode()


def test_projection_excludes_trace_measurement() -> None:
    projected = project_event(_event())
    assert projected["content"] == "Pin the evaluation split before scoring."
    assert "confidence" not in projected
    assert "estimate" not in json.dumps(projected)


def test_typed_answers_reject_unknown_fields() -> None:
    raw = json.loads(_response())["answers"]
    raw["invented"] = {"type": "noul", "noul": 1.0}
    with pytest.raises(ValueError, match="unexpected"):
        validate_answers(raw)


async def test_assessment_attests_and_keeps_confidences_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRACE_EGRESS_LOG", str(tmp_path / "egress.jsonl"))

    def transport(request: Request, timeout: float) -> bytes:
        assert request.full_url == "https://api.typesafe.ai/v1/systemone"
        assert timeout == 45.0
        return _response()

    result = await assess_event(
        _event(),
        JevConfig(enabled=True, api_key="test-key", model="jev-latest"),
        project="demo",
        project_key="demo",
        transport=transport,
    )
    assert result["advice"] == "candidate_for_human_review"
    assert result["answers"]["candidate_type"]["distribution_confidence"] == 0.91
    assert "confidence" not in result["source"]
    assert result["request_digest"].startswith("sha256:")
    ledger = json.loads((tmp_path / "egress.jsonl").read_text())
    assert ledger["provider"] == "typesafe-ai"
    assert ledger["project_key"] == "demo"


async def test_missing_key_and_local_only_fail_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRACE_EGRESS_LOG", str(tmp_path / "egress.jsonl"))
    called = False

    def transport(request: Request, timeout: float) -> bytes:
        nonlocal called
        called = True
        return _response()

    for config in (JevConfig(enabled=True), JevConfig(enabled=True, api_key="key", local_only=True)):
        with pytest.raises(ValueError):
            await assess_event(_event(), config, project="demo", project_key="demo", transport=transport)
    assert called is False
    assert not (tmp_path / "egress.jsonl").exists()


def test_key_alone_does_not_enable_jev(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TYPESAFE_AI_API_KEY", "secret-value")
    monkeypatch.delenv("TRACE_JEV_ENABLED", raising=False)
    assert load_config().enabled is False


async def test_registered_tool_reads_existing_event_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRACE_PROJECT", "demo")
    monkeypatch.setenv("TRACE_JEV_ENABLED", "false")
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "missing-registry.json"))
    pident._reset_registry_cache()
    storage = JsonFileStorage(directory=str(tmp_path / "sessions"))
    session = Session(id="trace_test", metadata=SessionMetadata(project="demo", project_key="demo"), events=[_event()])
    await storage.create_session(session)
    before = (tmp_path / "sessions" / "trace_test.json").read_bytes()
    mcp = FastMCP("jev-test")
    register(mcp, storage)
    output = await mcp.call_tool("trace_jev_assess_candidate", {"session_id": "trace_test", "event_id": "evt_001"})
    if isinstance(output, tuple):
        output = output[0]
    payload = json.loads(output[0].text)  # type: ignore[union-attr]
    assert "disabled" in payload["error"]
    assert (tmp_path / "sessions" / "trace_test.json").read_bytes() == before
    pident._reset_registry_cache()
