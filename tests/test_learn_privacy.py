"""Privacy hardening for the learn extension (ADR-006 deferred S4 items).

Covers the egress `project_key` attribution column, the per-project
restrict-only config ratchet, and their fail-closed behavior on registry
damage. All against tmp_path stores and a spy ledger — no cloud calls.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from trace_mcp import project_identity as pident
from trace_mcp.extensions.learn.config import LearnConfig, effective_learn_config
from trace_mcp.extensions.learn.egress import attest_egress, egress_project
from trace_mcp.extensions.learn.matching import BM25Backend, EmbeddingBackend, LLMBackend, get_default_backend


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRACE_EGRESS_LOG", str(tmp_path / "egress.jsonl"))
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    pident._reset_registry_cache()
    return tmp_path


def _ledger_rows(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "egress.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _enroll(key: str, **config: Any) -> None:
    with pident.locked_registry() as registry:
        registry.projects[key] = pident.ProjectEntry(key=key, display_label=key, config=pident.ProjectConfig(**config))
    pident._reset_registry_cache()


def _attest(**overrides: Any) -> None:
    kwargs: dict[str, Any] = {
        "provider": "openai",
        "endpoint": "embeddings",
        "model": "test-model",
        "purpose": "embedding",
        "content_class": "learning-or-query-text",
        "item_count": 1,
    }
    kwargs.update(overrides)
    attest_egress(**kwargs)


# ── project_key attribution ───────────────────────────────────────────────


class TestEgressProjectKey:
    def test_context_attributes_rows_written_inside_it(self, _isolated: Path) -> None:
        """The provider layer knows nothing about projects; the context does."""
        with egress_project("waggle"):
            _attest()
        _attest()  # outside any context — must stay unattributed, not inherit

        rows = _ledger_rows(_isolated)
        assert [r["project_key"] for r in rows] == ["waggle", None]

    def test_explicit_argument_wins_over_the_context(self, _isolated: Path) -> None:
        with egress_project("wrong-project"):
            _attest(project_key="right-project")
        assert _ledger_rows(_isolated)[0]["project_key"] == "right-project"

    def test_context_restores_on_exception(self, _isolated: Path) -> None:
        """A failed span must not leak its attribution into later calls."""
        with pytest.raises(RuntimeError), egress_project("doomed"):
            raise RuntimeError("boom")
        _attest()
        assert _ledger_rows(_isolated)[0]["project_key"] is None

    async def test_context_survives_the_async_call_chain(self, _isolated: Path) -> None:
        """contextvars propagate through await — the mechanism the design rests on."""
        import asyncio

        async def _deep_provider_layer() -> None:
            await asyncio.sleep(0)
            _attest()

        with egress_project("waggle"):
            await _deep_provider_layer()
        assert _ledger_rows(_isolated)[0]["project_key"] == "waggle"

    def test_rows_keep_the_display_label_column(self, _isolated: Path) -> None:
        """`project` (display label) and `project_key` are separate columns."""
        with egress_project("trace-mcp"):
            _attest(project="TRACE", session_id="trace_20260101_x")
        row = _ledger_rows(_isolated)[0]
        assert row["project"] == "TRACE"
        assert row["project_key"] == "trace-mcp"


# ── the restrict-only ratchet ─────────────────────────────────────────────


def _permissive_config() -> LearnConfig:
    return LearnConfig(
        openai_api_key="sk-test-fake",
        llm_enabled=True,
        strict_llm=False,
        local_only=False,
        embedding_backend="openai",
    )


class TestEffectiveLearnConfig:
    def test_no_registry_is_a_no_op(self) -> None:
        base = _permissive_config()
        assert effective_learn_config(base, "waggle") is base

    def test_unenrolled_project_is_a_no_op(self) -> None:
        _enroll("other-project", local_only=True)
        base = _permissive_config()
        assert effective_learn_config(base, "waggle") is base

    def test_unrestrictive_entry_returns_base_identity(self) -> None:
        """`eff is base` is the fast path callers use to keep the global backend."""
        _enroll("waggle")  # default ProjectConfig: no restrictions
        base = _permissive_config()
        assert effective_learn_config(base, "waggle") is base

    def test_local_only_entry_forces_everything_local(self) -> None:
        _enroll("client-proj", local_only=True)
        eff = effective_learn_config(_permissive_config(), "client-proj")
        assert eff.local_only is True
        assert eff.llm_enabled is False
        assert eff.embedding_backend != "openai"

    def test_llm_enabled_false_entry_disables_llm_only(self) -> None:
        _enroll("client-proj", llm_enabled=False)
        eff = effective_learn_config(_permissive_config(), "client-proj")
        assert eff.llm_enabled is False
        assert eff.embedding_backend == "openai", "llm_enabled=False must not touch embeddings"

    def test_embedding_backend_max_local_downgrades_openai(self) -> None:
        _enroll("client-proj", embedding_backend_max="local")
        eff = effective_learn_config(_permissive_config(), "client-proj")
        assert eff.embedding_backend == "auto"
        assert eff.llm_enabled is True, "embedding_backend_max must not touch the LLM switch"

    def test_ratchet_never_loosens_a_global_restriction(self) -> None:
        """A permissive entry against a restrictive global changes nothing."""
        _enroll("open-proj", llm_enabled=None, local_only=False, embedding_backend_max="any")
        base = replace(_permissive_config(), local_only=True, llm_enabled=False, embedding_backend="auto")
        eff = effective_learn_config(base, "open-proj")
        assert eff is base

    def test_alias_resolves_to_the_entry(self) -> None:
        """A drifted display label must reach its project's posture."""
        with pident.locked_registry() as registry:
            registry.projects["trace-mcp"] = pident.ProjectEntry(
                key="trace-mcp",
                display_label="trace-mcp",
                aliases=["TRACE"],
                config=pident.ProjectConfig(local_only=True),
            )
        pident._reset_registry_cache()

        eff = effective_learn_config(_permissive_config(), "TRACE")
        assert eff.local_only is True

    def test_unreadable_registry_fails_closed(self, _isolated: Path) -> None:
        """The posture that forbids the egress may live in the unreadable file."""
        (_isolated / "projects.json").write_text("{not valid json")
        pident._reset_registry_cache()
        with pytest.raises(pident.RegistryUnavailableError):
            effective_learn_config(_permissive_config(), "waggle")

    def test_ratcheted_config_selects_a_local_backend(self) -> None:
        """The backend built from a ratcheted config cannot egress.

        The permissive config would select the LLM/OpenAI tier; the ratcheted
        one must land on a local tier (embedding-local or BM25) instead.
        """
        _enroll("client-proj", local_only=True)
        eff = effective_learn_config(_permissive_config(), "client-proj")
        backend = get_default_backend(eff)
        assert not isinstance(backend, LLMBackend), "local_only project still got the LLM backend"
        if isinstance(backend, EmbeddingBackend):
            provider_name = getattr(backend._provider, "model_name", "")
            assert "text-embedding" not in provider_name, "local_only project still got OpenAI embeddings"
        else:
            assert isinstance(backend, BM25Backend)


# ── fail-closed store load (moved behavior, integration view) ─────────────


class TestStoreFailClosed:
    async def test_tool_surfaces_a_corrupt_store_instead_of_masking_it(self, _isolated: Path) -> None:
        """End-to-end through the real registered tool: corrupt store → clear error,
        file untouched — not "this project has no learnings"."""
        from typing import cast

        from trace_mcp.extension_hooks import clear_hooks
        from trace_mcp.extensions import learn as learn_ext

        class _FakeMCP:
            def __init__(self) -> None:
                self.tools: dict[str, Any] = {}

            def tool(self):
                def deco(fn):
                    self.tools[fn.__name__] = fn
                    return fn

                return deco

        clear_hooks()
        try:
            fake = _FakeMCP()
            learn_ext.register(cast(Any, fake), cast(Any, None))  # storage unused by list

            knowledge = _isolated / "knowledge"
            knowledge.mkdir(exist_ok=True)
            (knowledge / "waggle.json").write_text("{not valid json")

            result = json.loads(await fake.tools["trace_learn_list"]("waggle"))
            assert "error" in result
            assert (knowledge / "waggle.json").read_text() == "{not valid json"
        finally:
            clear_hooks()
