"""Cloud LLM matching/extraction is opt-in (local-first default).

Pins the full interaction matrix for the three settings that decide whether
session content may reach a cloud LLM:

    OPENAI_API_KEY  x  TRACE_LLM_ENABLED  x  TRACE_LOCAL_ONLY

The load-bearing rule: an API key on the machine must not, by itself, enable
cloud LLM features. ``TRACE_LLM_ENABLED`` unset means OFF; enabling requires
the explicit ``true`` (and a key). ``TRACE_LOCAL_ONLY`` beats everything.

Any new egress path must add its gating variable to this matrix.
"""

import logging
from pathlib import Path

import pytest

from trace_mcp.extensions.learn import config as _cfg
from trace_mcp.extensions.learn.config import LearnConfig, load_config

NUDGE_FRAGMENT = "OFF by default"


@pytest.fixture(autouse=True)
def _isolated_config_env(monkeypatch, tmp_path):
    """Make load_config() see ONLY what each test sets.

    Neutralizes the developer's real environment: the shell env vars, the
    global ~/.trace/.env, and any ./.env in the checkout (load_config merges
    Path.cwd()/.env).
    """
    for var in (
        "OPENAI_API_KEY",
        "TRACE_LLM_ENABLED",
        "TRACE_LOCAL_ONLY",
        "TRACE_STRICT_LLM",
        "TRACE_EMBEDDING_BACKEND",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_cfg, "_TRACE_ENV_PATH", Path("/nonexistent/.env"))
    monkeypatch.chdir(tmp_path)


class TestLlmOptInMatrix:
    def test_key_present_flag_unset_is_off(self, monkeypatch):
        """THE flip: a mere API key must not enable cloud LLM features."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        cfg = load_config()
        assert cfg.llm_enabled is False
        assert cfg.openai_api_key == "sk-test"  # key still loaded (embeddings opt-in may use it)

    def test_key_present_flag_true_is_on(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("TRACE_LLM_ENABLED", "true")
        assert load_config().llm_enabled is True

    def test_key_present_flag_false_is_off(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("TRACE_LLM_ENABLED", "false")
        assert load_config().llm_enabled is False

    def test_no_key_flag_unset_is_off(self):
        assert load_config().llm_enabled is False

    def test_no_key_flag_true_is_forced_off(self, monkeypatch):
        """Opting in without a key cannot enable anything."""
        monkeypatch.setenv("TRACE_LLM_ENABLED", "true")
        assert load_config().llm_enabled is False

    def test_local_only_beats_explicit_opt_in(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("TRACE_LLM_ENABLED", "true")
        monkeypatch.setenv("TRACE_LOCAL_ONLY", "1")
        cfg = load_config()
        assert cfg.local_only is True
        assert cfg.llm_enabled is False

    def test_dataclass_default_is_off(self):
        """Directly-constructed configs are safe-by-default too — even with a key."""
        assert LearnConfig().llm_enabled is False
        assert LearnConfig(openai_api_key="sk-test").llm_enabled is False


class TestOptInNudge:
    """The one-time INFO nudge: shown only when the user never chose."""

    def test_nudge_when_key_present_and_flag_unset(self, monkeypatch, caplog):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with caplog.at_level(logging.INFO, logger=_cfg.__name__):
            load_config()
        assert any(NUDGE_FRAGMENT in r.message for r in caplog.records)
        assert any("TRACE_LLM_ENABLED=true" in r.message for r in caplog.records)

    def test_no_nudge_when_explicitly_disabled(self, monkeypatch, caplog):
        """A user who opted out must not be nagged to opt in."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("TRACE_LLM_ENABLED", "false")
        with caplog.at_level(logging.INFO, logger=_cfg.__name__):
            load_config()
        assert not any(NUDGE_FRAGMENT in r.message for r in caplog.records)

    def test_no_nudge_when_local_only(self, monkeypatch, caplog):
        """TRACE_LOCAL_ONLY states intent; suggesting cloud opt-in contradicts it."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("TRACE_LOCAL_ONLY", "1")
        with caplog.at_level(logging.INFO, logger=_cfg.__name__):
            load_config()
        assert not any(NUDGE_FRAGMENT in r.message for r in caplog.records)

    def test_no_nudge_without_key(self, caplog):
        """No key -> nothing to nudge about."""
        with caplog.at_level(logging.INFO, logger=_cfg.__name__):
            load_config()
        assert not any(NUDGE_FRAGMENT in r.message for r in caplog.records)
