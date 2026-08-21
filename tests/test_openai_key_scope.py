"""Per-project OpenAI keys: `./.env` is the key's home, and its absence is loud.

Two properties are pinned here.

**Scope.** A project's own `.env` is the place a key lives, so it overrides the
machine-global `~/.trace/.env` (shell environment still wins over both). The
previous order was inverted — the global file shadowed every project file — so a
key placed in a project was silently ignored, which is indistinguishable from
having no key at all. One exception survives the flip: `TRACE_LOCAL_ONLY` is a
restrict-only ratchet, ORed across every source, so a project `.env` can turn
the kill switch ON but can never turn a machine-global one OFF.

**Loudness.** A cloud call attempted with no key, or with a key the provider
rejects, must be visible to the person running the session. Silence here is the
worst outcome: recall still returns results, they are just keyword-ranked, and
nothing says the semantic path was skipped. Per-project keys make "this project
has no key yet" routine, so the quiet path had to go.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

import trace_mcp.extensions.learn.config as _cfg
import trace_mcp.project_identity as pident
from trace_mcp.extensions.learn.config import (
    ApiKeyRejectedError,
    is_auth_error,
    load_config,
)
from trace_mcp.storage.json_file import JsonFileStorage

_KEY_VARS = (
    "OPENAI_API_KEY",
    "TRACE_LLM_ENABLED",
    "TRACE_LOCAL_ONLY",
    "TRACE_LLM_MODEL",
    "TRACE_EMBEDDING_BACKEND",
    "TRACE_STRICT_LLM",
)


@pytest.fixture()
def env_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated project + global .env files, with the real environment scrubbed.

    Returns (write_project, write_global): each takes a dict and writes that
    file. The working directory is the project, which is what a host gives an
    MCP server it launches.
    """
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    global_env = tmp_path / "home" / ".trace" / ".env"
    global_env.parent.mkdir(parents=True)
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(_cfg, "_TRACE_ENV_PATH", global_env)
    for var in _KEY_VARS:
        monkeypatch.delenv(var, raising=False)

    def write(path: Path):
        def _write(values: dict[str, str]) -> None:
            path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))

        return _write

    return write(project_dir / ".env"), write(global_env)


# ── scope: which file supplies the key ──────────────────────────────────────


def test_project_env_overrides_the_global_file(env_files) -> None:
    """The defect this closes: a key placed in a project was silently ignored."""
    write_project, write_global = env_files
    write_global({"OPENAI_API_KEY": "global-key"})
    write_project({"OPENAI_API_KEY": "project-key"})

    config = load_config()
    assert config.openai_api_key == "project-key"
    assert config.key_source == "project"


def test_environment_variable_still_wins_over_both(env_files, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicitly exported key is a deliberate override (CI, containers)."""
    write_project, write_global = env_files
    write_global({"OPENAI_API_KEY": "global-key"})
    write_project({"OPENAI_API_KEY": "project-key"})
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")

    config = load_config()
    assert config.openai_api_key == "env-key"
    assert config.key_source == "environment"


def test_global_file_is_the_fallback_and_says_so(env_files) -> None:
    """A project with no key of its own borrows the shared one — visibly."""
    _write_project, write_global = env_files
    write_global({"OPENAI_API_KEY": "global-key"})

    config = load_config()
    assert config.openai_api_key == "global-key"
    assert config.key_source == "global"
    assert config.key_source_path is not None and config.key_source_path.endswith(".env")


def test_no_key_anywhere_reports_no_source(env_files) -> None:
    config = load_config()
    assert config.openai_api_key is None
    assert config.key_source is None


def test_project_env_overrides_ordinary_settings_too(env_files) -> None:
    """The flip is a general precedence rule, not a special case for the key."""
    write_project, write_global = env_files
    write_global({"TRACE_LLM_MODEL": "global-model"})
    write_project({"TRACE_LLM_MODEL": "project-model"})
    assert load_config().llm_model == "project-model"


# ── the one exception: TRACE_LOCAL_ONLY ratchets, never loosens ─────────────


def test_project_can_turn_the_kill_switch_on(env_files) -> None:
    write_project, _write_global = env_files
    write_project({"TRACE_LOCAL_ONLY": "true"})
    assert load_config().local_only is True


def test_project_cannot_turn_a_global_kill_switch_off(env_files) -> None:
    """Otherwise the precedence flip would hand every project a way to opt out
    of a machine-wide no-egress policy."""
    write_project, write_global = env_files
    write_global({"TRACE_LOCAL_ONLY": "true"})
    write_project({"TRACE_LOCAL_ONLY": "false", "OPENAI_API_KEY": "project-key"})

    config = load_config()
    assert config.local_only is True
    assert config.llm_enabled is False


def test_environment_variable_cannot_turn_a_global_kill_switch_off(env_files, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_project, write_global = env_files
    write_global({"TRACE_LOCAL_ONLY": "1"})
    monkeypatch.setenv("TRACE_LOCAL_ONLY", "false")
    assert load_config().local_only is True


# ── loudness: a cloud call with no usable key ───────────────────────────────


def test_llm_requested_without_a_key_is_flagged_not_swallowed(env_files) -> None:
    """Previously this logged at INFO and disabled LLM features in silence."""
    write_project, _write_global = env_files
    write_project({"TRACE_LLM_ENABLED": "true"})

    config = load_config()
    assert config.missing_key_for_cloud is True
    assert config.llm_enabled is False, "nothing may attempt a cloud call without a key"


def test_openai_embedding_backend_without_a_key_is_flagged(env_files) -> None:
    write_project, _write_global = env_files
    write_project({"TRACE_EMBEDDING_BACKEND": "openai"})
    assert load_config().missing_key_for_cloud is True


def test_local_only_is_not_a_missing_key(env_files) -> None:
    """Deliberately offline is not a misconfiguration — it must not warn."""
    write_project, _write_global = env_files
    write_project({"TRACE_LOCAL_ONLY": "true", "TRACE_LLM_ENABLED": "true"})
    assert load_config().missing_key_for_cloud is False


def test_key_search_path_names_every_place_that_was_checked(env_files) -> None:
    """A 'no key found' message that does not say where it looked is not actionable."""
    config = load_config()
    searched = config.key_search_description()
    assert ".env" in searched
    assert str(Path.cwd()) in searched


# ── loudness: a key the provider rejects ────────────────────────────────────


class _FakeAuthError(Exception):
    """Shaped like an OpenAI 401 without depending on the SDK's constructors."""

    status_code = 401


def test_only_a_401_counts_as_a_rejected_credential() -> None:
    """403 is deliberately excluded.

    The provider returns 403 for a model the account cannot access, an
    unsupported region, and a proxy in front of a custom base_url. Calling
    those "your API key was rejected" misdiagnoses them, and raising
    unconditionally would turn an error that degraded gracefully into an
    outage. 403 stays on the strict-mode path, which is loud by default.
    """
    assert is_auth_error(_FakeAuthError())
    assert is_auth_error(type("AuthenticationError", (Exception,), {})())
    assert not is_auth_error(type("PermissionDeniedError", (Exception,), {})())
    assert not is_auth_error(type("Whatever", (Exception,), {"status_code": 403})())
    assert not is_auth_error(TimeoutError("network hiccup"))


async def test_rejected_key_raises_even_when_strict_mode_is_off(env_files, monkeypatch) -> None:
    """A rejected key is a configuration failure, not a transient one.

    Strict mode governs whether *degradation* is acceptable; it must not govern
    whether the user is told their key was refused. Falling back to keyword
    results here would hand back plausible output from a broken setup.
    """
    pytest.importorskip("openai")
    from trace_mcp.extensions.learn.matching import LLMBackend
    from trace_mcp.extensions.learn.models import Learning

    write_project, _write_global = env_files
    write_project({"OPENAI_API_KEY": "sk-rejected", "TRACE_LLM_ENABLED": "true", "TRACE_STRICT_LLM": "false"})
    config = load_config()
    assert config.strict_llm is False

    backend = LLMBackend(config)

    async def _reject(*_args: Any, **_kwargs: Any) -> list[float]:
        raise _FakeAuthError("Incorrect API key provided")

    monkeypatch.setattr(backend, "_llm_score", _reject)
    learning = Learning(id="lrn_001", content="something", category="learning")

    with pytest.raises(ApiKeyRejectedError) as exc:
        await backend.score_batch([learning], "context")
    assert "sk-rejected" not in str(exc.value), "an error message must never echo the key"
    assert ".env" in str(exc.value), "the message must point at the file to fix"


def test_a_blank_value_never_overrides_a_real_one(env_files) -> None:
    """Copying a template with a bare `OPENAI_API_KEY=` must not mask a real key.

    The template also leaves the cloud flags off, so nothing would have tripped
    the missing-key warning either: the project would simply have stopped using
    the key it had, silently. That is the failure this whole change exists to
    prevent, arriving through the documentation for the change.
    """
    write_project, write_global = env_files
    write_global({"OPENAI_API_KEY": "real-global-key"})
    write_project({"OPENAI_API_KEY": "", "TRACE_LLM_ENABLED": "false"})

    config = load_config()
    assert config.openai_api_key == "real-global-key"
    assert config.key_source == "global"


def test_an_exported_but_empty_variable_does_not_mask_a_key(env_files, monkeypatch: pytest.MonkeyPatch) -> None:
    """`docker run -e OPENAI_API_KEY` exports the name with an empty value."""
    write_project, _write_global = env_files
    write_project({"OPENAI_API_KEY": "project-key"})
    monkeypatch.setenv("OPENAI_API_KEY", "")

    config = load_config()
    assert config.openai_api_key == "project-key"
    assert config.key_source == "project"


def test_the_shipped_example_file_cannot_disable_a_working_key(env_files) -> None:
    """Belt and braces: the committed template itself, verbatim."""
    _write_project, write_global = env_files
    write_global({"OPENAI_API_KEY": "real-global-key", "TRACE_LLM_ENABLED": "true"})
    example = Path(__file__).parent.parent / ".env.example"
    (Path.cwd() / ".env").write_text(example.read_text())

    config = load_config()
    assert config.openai_api_key == "real-global-key"


# ── loudness: what the user actually sees ───────────────────────────────────


def test_session_start_banner_warns_when_a_cloud_call_has_no_key(env_files) -> None:
    """The banner is surfaced in the trace_start_session response — the one place
    every session looks."""
    write_project, _write_global = env_files
    write_project({"TRACE_LLM_ENABLED": "true"})

    from trace_mcp.extension_status import get_extension_status

    banner = get_extension_status().lower()
    assert "no openai_api_key found" in banner
    assert ".env" in banner


def test_session_start_banner_flags_a_borrowed_global_key(env_files) -> None:
    """A project silently using the machine-wide key is the thing per-project
    keys exist to prevent, so it is stated rather than assumed acceptable."""
    _write_project, write_global = env_files
    write_global({"OPENAI_API_KEY": "global-key"})

    from trace_mcp.extension_status import get_extension_status

    assert "machine-global" in get_extension_status().lower()


def test_session_start_banner_is_quiet_when_the_project_has_its_own_key(env_files) -> None:
    write_project, _write_global = env_files
    write_project({"OPENAI_API_KEY": "project-key"})

    from trace_mcp.extension_status import get_extension_status

    banner = get_extension_status().lower()
    assert "no openai_api_key found" not in banner
    assert "machine-global" not in banner


def test_a_project_ratcheted_offline_is_not_warned_about_a_missing_key(env_files) -> None:
    """A registry entry that forces local-only means the project asks for no
    cloud path at all — warning it about a key it does not want is noise, and
    noise is how a real warning stops being read."""
    from dataclasses import replace as dc_replace

    from trace_mcp.extensions.learn.config import _clear_cloud_expectation

    write_project, _write_global = env_files
    write_project({"TRACE_LLM_ENABLED": "true"})
    base = load_config()
    assert base.missing_key_for_cloud is True

    offline = _clear_cloud_expectation(dc_replace(base, local_only=True, llm_enabled=False))
    assert offline.missing_key_for_cloud is False

    from trace_mcp.extension_status import key_warnings

    assert key_warnings(offline) == []


# ── loudness: the learn tools' own responses ────────────────────────────────


@pytest.fixture()
def learn_mcp(env_files, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A registered learn extension over isolated dirs, honouring env_files."""
    monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setenv("TRACE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    monkeypatch.setenv("TRACE_PROJECT", "keyscope")
    pident._reset_registry_cache()
    yield env_files
    pident._reset_registry_cache()


async def _call(mcp: FastMCP, tool: str, args: dict[str, Any]) -> dict:
    out = await mcp.call_tool(tool, args)
    if isinstance(out, tuple):
        out = out[0]
    return json.loads(out[0].text)  # type: ignore[union-attr]


async def test_recall_response_carries_the_missing_key_warning(learn_mcp, tmp_path: Path) -> None:
    """The warning rides along with the results, so the answer that gets used
    carries the caveat that produced it."""
    write_project, _write_global = learn_mcp
    write_project({"TRACE_LLM_ENABLED": "true"})

    from trace_mcp.extensions.learn import register

    mcp = FastMCP("key-scope-test")
    register(mcp, JsonFileStorage(directory=str(tmp_path / "sessions")))

    await _call(mcp, "trace_learn_add", {"content": "a learning about widgets"})
    result = await _call(mcp, "trace_learn_recall", {"context": "widgets"})

    warnings = " ".join(result.get("warnings", []))
    assert "OPENAI_API_KEY" in warnings
    assert ".env" in warnings


async def test_a_rejected_key_surfaces_as_its_own_error_not_a_strict_mode_one(
    learn_mcp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Covers the two paths a rejected credential could have escaped through:
    the embed step swallowing it with strict mode off, and the handler labelling
    it "strict mode blocked fallback" — advice that sends the reader to a knob
    which cannot fix it."""
    import trace_mcp.extensions.learn as learn_pkg

    class _RejectingProvider:
        model_name = "text-embedding-3-small"

        async def embed_texts(self, texts: list[str]) -> list[list[float]]:
            raise ApiKeyRejectedError("OpenAI REJECTED the API key. The key came from this project's .env.")

    write_project, _write_global = learn_mcp
    write_project({"OPENAI_API_KEY": "sk-rejected", "TRACE_STRICT_LLM": "false"})
    monkeypatch.setattr(learn_pkg, "get_embedding_provider", lambda _config: _RejectingProvider())

    mcp = FastMCP("key-scope-test")
    learn_pkg.register(mcp, JsonFileStorage(directory=str(tmp_path / "sessions")))

    result = await _call(mcp, "trace_learn_add", {"content": "a learning about widgets"})
    assert result.get("error") == "OpenAI API key rejected", result
    assert "strict" not in result["error"].lower()


async def test_recall_on_an_empty_store_still_carries_the_warning(learn_mcp, tmp_path: Path) -> None:
    """A first-run project is where a missing key most needs saying out loud."""
    write_project, _write_global = learn_mcp
    write_project({"TRACE_LLM_ENABLED": "true"})

    from trace_mcp.extensions.learn import register

    mcp = FastMCP("key-scope-test")
    register(mcp, JsonFileStorage(directory=str(tmp_path / "sessions")))

    result = await _call(mcp, "trace_learn_recall", {"context": "anything"})
    assert result["total"] == 0
    assert "OPENAI_API_KEY" in " ".join(result.get("warnings", []))


async def test_recall_has_no_warnings_when_the_key_is_present(learn_mcp, tmp_path: Path) -> None:
    write_project, _write_global = learn_mcp
    # Strict mode off so keyword matching is a legitimate backend here; with it
    # on and no embedding provider, a warning is the CORRECT output and the
    # sibling test above covers that.
    write_project({"OPENAI_API_KEY": "project-key", "TRACE_EMBEDDING_BACKEND": "none", "TRACE_STRICT_LLM": "false"})

    from trace_mcp.extensions.learn import register

    mcp = FastMCP("key-scope-test")
    register(mcp, JsonFileStorage(directory=str(tmp_path / "sessions")))

    await _call(mcp, "trace_learn_add", {"content": "a learning about widgets"})
    result = await _call(mcp, "trace_learn_recall", {"context": "widgets"})
    assert not result.get("warnings")
