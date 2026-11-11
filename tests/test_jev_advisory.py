from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from urllib.request import Request

import pytest
from _mcp_helpers import call_tool_json
from mcp.server.mcpserver import MCPServer

import trace_mcp.extensions.jev.config as jev_config
import trace_mcp.project_identity as pident
from trace_mcp.extensions.jev import bridge, register
from trace_mcp.extensions.jev.cli import get_directory_project, get_session_review
from trace_mcp.extensions.jev.client import (
    assess_and_record,
    assess_event,
    is_assessable,
    project_event,
    validate_answers,
)
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
            disposition="accepted",
            resolved_by=Actor(type="human", id="reviewer"),
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


async def test_gateway_uses_fixed_model_boolean_wire_and_explicit_transport_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRACE_EGRESS_LOG", str(tmp_path / "egress.jsonl"))

    def gateway_runner(body: bytes, api_key: str) -> bytes:
        assert api_key == "gateway-test-key"
        request = json.loads(body)
        assert request["model"] == "typesafe-ai/jev"
        assert request["questions"]["candidate_type"]["type"] == "choice"
        assert request["questions"]["bounded"]["type"] == "boolean"
        assert request["questions"]["durable"]["type"] == "boolean"
        assert request["providerOptions"] == {"gateway": {"zeroDataRetention": True}}
        return _response().replace(b'"jev-1.13.0"', b'"typesafe-ai/jev"')

    result = await assess_event(
        _event(),
        JevConfig(
            enabled=True,
            api_key="gateway-test-key",
            provider="vercel_gateway",
            model="typesafe-ai/jev",
            gateway_zero_data_retention=True,
        ),
        project="demo",
        project_key="demo",
        gateway_runner=gateway_runner,
    )
    assert result["advice"] == "candidate_for_human_review"
    assert result["response_model"] == "typesafe-ai/jev"
    assert result["transport"] == {
        "provider": "vercel-ai-gateway",
        "sdk": "ai/7.0.105;@ai-sdk/gateway/4.0.85",
        "response_model_source": "gateway-route",
        "zero_data_retention": True,
        "answer_normalization": "boolean-to-noul;typesafe-confidence-by-question/v1",
    }
    ledger = json.loads((tmp_path / "egress.jsonl").read_text())
    assert ledger["provider"] == "vercel-ai-gateway"
    assert ledger["endpoint"] == "v4/ai/evaluation-model"


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


async def test_gateway_wrong_model_fails_before_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACE_EGRESS_LOG", str(tmp_path / "egress.jsonl"))
    called = False

    def gateway_runner(body: bytes, api_key: str) -> bytes:
        nonlocal called
        called = True
        return _response()

    with pytest.raises(ValueError, match="typesafe-ai/jev"):
        await assess_event(
            _event(),
            JevConfig(enabled=True, api_key="key", provider="vercel_gateway", model="jev-latest"),
            project="demo",
            project_key="demo",
            gateway_runner=gateway_runner,
        )
    assert called is False
    assert not (tmp_path / "egress.jsonl").exists()


def test_key_alone_does_not_enable_jev(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TYPESAFE_AI_API_KEY", "secret-value")
    monkeypatch.delenv("TRACE_JEV_ENABLED", raising=False)
    assert load_config().enabled is False


def test_gateway_config_selects_only_gateway_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRACE_JEV_ENABLED", "true")
    monkeypatch.setenv("TRACE_JEV_PROVIDER", "vercel_gateway")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gateway-key")
    monkeypatch.setenv("TYPESAFE_AI_API_KEY", "direct-key")
    monkeypatch.delenv("TRACE_JEV_MODEL", raising=False)
    config = load_config()
    assert config.provider == "vercel_gateway"
    assert config.model == "typesafe-ai/jev"
    assert config.api_key == "gateway-key"
    assert "gateway-key" not in repr(config)
    assert "direct-key" not in repr(config)


@pytest.mark.parametrize(
    ("provider", "present_key", "missing_key"),
    [
        ("vercel_gateway", "TYPESAFE_AI_API_KEY", "AI_GATEWAY_API_KEY"),
        ("typesafe", "AI_GATEWAY_API_KEY", "TYPESAFE_AI_API_KEY"),
    ],
)
def test_provider_credentials_never_fall_across(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
    present_key: str,
    missing_key: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(jev_config, "_GLOBAL_ENV", tmp_path / "missing-global.env")
    monkeypatch.setenv("TRACE_JEV_PROVIDER", provider)
    monkeypatch.setenv(present_key, "wrong-provider-key")
    monkeypatch.delenv(missing_key, raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert load_config().api_key is None


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
    mcp = MCPServer("jev-test")
    register(mcp, storage)
    payload = await call_tool_json(
        mcp, "trace_jev_assess_candidate", {"session_id": "trace_test", "event_id": "evt_001"}
    )
    assert "disabled" in payload["error"]
    assert (tmp_path / "sessions" / "trace_test.json").read_bytes() == before
    pident._reset_registry_cache()


def _resolved_session() -> Session:
    proposed = _event().model_copy(deep=True)
    proposed.id = "evt_002"
    assert proposed.decision is not None
    proposed.decision.disposition = "proposed"
    proposed.decision.resolved_by = None
    return Session(
        id="trace_test", metadata=SessionMetadata(project="demo", project_key="demo"), events=[_event(), proposed]
    )


@pytest.fixture
def jev_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("TRACE_EGRESS_LOG", str(tmp_path / "egress.jsonl"))
    monkeypatch.setenv("TRACE_JEV_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "missing-registry.json"))
    monkeypatch.setenv("TRACE_PROJECT", "demo")
    pident._reset_registry_cache()
    yield tmp_path
    pident._reset_registry_cache()


ENABLED = JevConfig(enabled=True, api_key="test-key")


def test_the_direct_route_defaults_to_a_pinned_model() -> None:
    assert JevConfig().model == "jev-1.13.0"


def test_an_unresolved_decision_is_refused() -> None:
    session = _resolved_session()
    with pytest.raises(ValueError, match="resolved"):
        project_event(session.events[1])


def test_a_decision_resolved_by_an_ai_is_refused() -> None:
    event = _event().model_copy(deep=True)
    assert event.decision is not None
    event.decision.resolved_by = Actor(type="ai", id="agent")
    assert not is_assessable(event)


async def test_a_result_is_saved_without_event_text(jev_home: Path) -> None:
    result = await assess_and_record(
        _event(), ENABLED, project="demo", project_key="demo", transport=lambda _r, _t: _response()
    )
    saved = (jev_home / "results" / "demo.jsonl").read_text().splitlines()
    assert result["saved_to"] == str(jev_home / "results" / "demo.jsonl")
    assert len(saved) == 1
    assert json.loads(saved[0])["source"]["event_id"] == _event().id
    assert result["status"] == "experimental"
    assert "Pin the evaluation split" not in saved[0]


async def test_a_failed_save_still_returns_the_answer_and_says_so(
    jev_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (jev_home / "results").write_text("a file where the directory belongs")
    result = await assess_and_record(
        _event(), ENABLED, project="demo", project_key="demo", transport=lambda _r, _t: _response()
    )
    assert result["saved_to"] is None
    assert any("not saved" in warning for warning in result["warnings"])


async def test_an_unpinned_model_is_warned_about(jev_home: Path) -> None:
    pinned = await assess_event(
        _event(), ENABLED, project="demo", project_key="demo", transport=lambda _r, _t: _response()
    )
    floating = await assess_event(
        _event(),
        JevConfig(enabled=True, api_key="test-key", model="jev-latest"),
        project="demo",
        project_key="demo",
        transport=lambda _r, _t: _response(),
    )
    assert not any("not a pinned" in warning for warning in pinned["warnings"])
    assert any("not a pinned" in warning for warning in floating["warnings"])


class TestSessionReview:
    @pytest.fixture
    async def storage(self, tmp_path: Path) -> JsonFileStorage:
        storage = JsonFileStorage(directory=str(tmp_path / "sessions"))
        await storage.create_session(_resolved_session())
        return storage

    async def test_a_dry_run_sends_nothing(self, storage: JsonFileStorage, jev_home: Path) -> None:
        def must_not_send(_request: Request, _timeout: float) -> bytes:
            pytest.fail("a dry run contacted the provider")

        summary = await get_session_review(
            storage, "trace_test", ENABLED, project="demo", dry_run=True, transport=must_not_send
        )
        assert (summary["eligible"], summary["skipped_unresolved_decisions"], summary["assessed"]) == (1, 1, 0)
        assert not (jev_home / "egress.jsonl").exists()

    async def test_only_resolved_events_are_sent_and_saved(self, storage: JsonFileStorage, jev_home: Path) -> None:
        sent: list[bytes] = []

        def transport(request: Request, _timeout: float) -> bytes:
            assert isinstance(request.data, bytes)
            sent.append(request.data)
            return _response()

        before = (jev_home / "sessions" / "trace_test.json").read_bytes()
        summary = await get_session_review(storage, "trace_test", ENABLED, project="demo", transport=transport)
        assert summary["assessed"] == 1 and len(sent) == 1
        assert summary["candidates"] == [_event().id]
        assert summary["advice_counts"] == {"candidate_for_human_review": 1}
        assert len((jev_home / "results" / "demo.jsonl").read_text().splitlines()) == 1
        assert (jev_home / "sessions" / "trace_test.json").read_bytes() == before

    async def test_the_bound_is_applied_and_reported(self, tmp_path: Path, jev_home: Path) -> None:
        storage = JsonFileStorage(directory=str(tmp_path / "bounded"))
        events = []
        for index in range(3):
            event = _event().model_copy(deep=True)
            event.id = f"evt_{index:03d}"
            events.append(event)
        await storage.create_session(
            Session(id="trace_many", metadata=SessionMetadata(project="demo", project_key="demo"), events=events)
        )
        summary = await get_session_review(
            storage, "trace_many", ENABLED, project="demo", max_events=2, transport=lambda _r, _t: _response()
        )
        assert (summary["eligible"], summary["selected"], summary["truncated"], summary["assessed"]) == (3, 2, True, 2)

    async def test_a_failure_stops_the_review_and_is_reported(self, storage: JsonFileStorage, jev_home: Path) -> None:
        def down(_request: Request, _timeout: float) -> bytes:
            raise OSError("connection refused")

        summary = await get_session_review(storage, "trace_test", ENABLED, project="demo", transport=down)
        assert summary["assessed"] == 0
        assert summary["failed"]["event_id"] == _event().id

    async def test_nothing_more_is_sent_after_a_failure(self, tmp_path: Path, jev_home: Path) -> None:
        storage = JsonFileStorage(directory=str(tmp_path / "failing"))
        events = []
        for index in range(3):
            event = _event().model_copy(deep=True)
            event.id = f"evt_{index:03d}"
            events.append(event)
        await storage.create_session(
            Session(id="trace_many", metadata=SessionMetadata(project="demo", project_key="demo"), events=events)
        )
        attempts: list[object] = []

        def down(request: Request, _timeout: float) -> bytes:
            attempts.append(request)
            raise OSError("connection refused")

        summary = await get_session_review(storage, "trace_many", ENABLED, project="demo", transport=down)
        assert len(attempts) == 1
        assert summary["failed"]["event_id"] == "evt_000"

    async def test_a_session_outside_the_pin_is_refused(
        self, storage: JsonFileStorage, jev_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRACE_PROJECT", "other-project")
        pident._reset_registry_cache()
        with pytest.raises(pident.ProjectKeyError):
            await get_session_review(storage, "trace_test", ENABLED, project="demo", dry_run=True)

    async def test_another_projects_session_is_refused(
        self, storage: JsonFileStorage, jev_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TRACE_PROJECT")
        pident._reset_registry_cache()
        with pytest.raises(pident.ProjectMismatchError):
            await get_session_review(storage, "trace_test", ENABLED, project="other-project", dry_run=True)

    def test_the_directory_decides_the_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRACE_PROJECT", raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="project directory"):
            get_directory_project()
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "trace.project").write_text("demo\n")
        assert get_directory_project() == "demo"

    async def test_a_dry_run_reports_a_block(self, storage: JsonFileStorage, jev_home: Path) -> None:
        off = await get_session_review(storage, "trace_test", JevConfig(), project="demo", dry_run=True)
        assert "disabled" in off["blocked"]
        on = await get_session_review(storage, "trace_test", ENABLED, project="demo", dry_run=True)
        assert on["blocked"] is None

    async def test_a_second_run_does_not_resend(self, storage: JsonFileStorage, jev_home: Path) -> None:
        sent: list[object] = []

        def transport(request: Request, _timeout: float) -> bytes:
            sent.append(request)
            return _response()

        await get_session_review(storage, "trace_test", ENABLED, project="demo", transport=transport)
        again = await get_session_review(storage, "trace_test", ENABLED, project="demo", transport=transport)
        assert len(sent) == 1
        assert (again["already_assessed"], again["selected"]) == (1, 0)

    async def test_another_model_is_a_new_instrument(self, storage: JsonFileStorage, jev_home: Path) -> None:
        await get_session_review(storage, "trace_test", ENABLED, project="demo", transport=lambda _r, _t: _response())
        other = JevConfig(enabled=True, api_key="test-key", model="jev-2.0.0")
        summary = await get_session_review(storage, "trace_test", other, project="demo", dry_run=True)
        assert (summary["already_assessed"], summary["selected"]) == (0, 1)

    async def test_an_unsendable_event_does_not_stop_the_review(self, tmp_path: Path, jev_home: Path) -> None:
        storage = JsonFileStorage(directory=str(tmp_path / "oversized"))
        huge = _event().model_copy(deep=True)
        huge.id = "evt_huge"
        assert huge.decision is not None
        huge.decision.description = "x" * 70_000
        await storage.create_session(
            Session(
                id="trace_many", metadata=SessionMetadata(project="demo", project_key="demo"), events=[huge, _event()]
            )
        )
        summary = await get_session_review(
            storage, "trace_many", ENABLED, project="demo", transport=lambda _r, _t: _response()
        )
        assert [item["event_id"] for item in summary["unsendable"]] == ["evt_huge"]
        assert summary["assessed"] == 1 and summary["failed"] is None

    async def test_an_egress_ledger_failure_keeps_the_summary(
        self, storage: JsonFileStorage, jev_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRACE_EGRESS_LOG", str(jev_home / "no-such-dir-file" / "x"))
        (jev_home / "no-such-dir-file").write_text("a file where the directory belongs")
        summary = await get_session_review(
            storage, "trace_test", ENABLED, project="demo", transport=lambda _r, _t: _response()
        )
        assert summary["failed"]["event_id"] == _event().id

    async def test_a_disabled_config_fails_visibly(self, storage: JsonFileStorage, jev_home: Path) -> None:
        summary = await get_session_review(
            storage, "trace_test", JevConfig(), project="demo", transport=lambda _r, _t: _response()
        )
        assert "disabled" in summary["failed"]["error"]


class TestBridge:
    @pytest.fixture
    def bridge_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        path = tmp_path / "bridge"
        monkeypatch.setenv("TRACE_JEV_BRIDGE_DIR", str(path))
        monkeypatch.setattr(bridge.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(bridge, "_get_node_major", lambda _node: 22)
        return path

    def _install_files(self, directory: Path) -> None:
        (directory / "node_modules" / "ai").mkdir(parents=True)
        (directory / "node_modules" / "ai" / "package.json").write_text("{}")
        for name in bridge._BRIDGE_FILES:
            shutil.copyfile(bridge._PACKAGED_DIR / name, directory / name)

    def test_a_missing_bridge_names_the_setup_command(self, bridge_dir: Path) -> None:
        with pytest.raises(ValueError, match="trace-mcp jev setup"):
            bridge.get_installed_bridge()

    def test_an_installed_bridge_is_found(self, bridge_dir: Path) -> None:
        self._install_files(bridge_dir)
        assert bridge.get_installed_bridge() == bridge_dir / "gateway_bridge.mjs"

    def test_an_out_of_date_bridge_is_refused(self, bridge_dir: Path) -> None:
        self._install_files(bridge_dir)
        (bridge_dir / "gateway_bridge.mjs").write_text("// an older version")
        with pytest.raises(ValueError, match="out of date"):
            bridge.get_installed_bridge()

    def test_an_old_node_is_refused(self, bridge_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._install_files(bridge_dir)
        monkeypatch.setattr(bridge, "_get_node_major", lambda _node: 18)
        with pytest.raises(ValueError, match="22 or newer"):
            bridge.get_installed_bridge()

    def test_a_failed_install_leaves_the_existing_bridge(
        self, bridge_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install_files(bridge_dir)
        (bridge_dir / "marker").write_text("existing")

        def failing_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 1, "", "registry unavailable")

        monkeypatch.setattr(bridge.subprocess, "run", failing_run)
        with pytest.raises(RuntimeError, match="registry unavailable"):
            bridge.install_bridge()
        assert (bridge_dir / "marker").read_text() == "existing"
        assert bridge.get_installed_bridge() == bridge_dir / "gateway_bridge.mjs"

    def test_missing_node_is_reported(self, bridge_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._install_files(bridge_dir)
        monkeypatch.setattr(bridge.shutil, "which", lambda _name: None)
        with pytest.raises(ValueError, match="Node"):
            bridge.get_installed_bridge()

    async def test_a_missing_bridge_stops_before_any_egress_is_recorded(self, bridge_dir: Path, jev_home: Path) -> None:
        config = JevConfig(enabled=True, api_key="k", provider="vercel_gateway", model="typesafe-ai/jev")
        with pytest.raises(ValueError, match="trace-mcp jev setup"):
            await assess_event(_event(), config, project="demo", project_key="demo")
        assert not (jev_home / "egress.jsonl").exists()
