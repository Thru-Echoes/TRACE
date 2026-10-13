"""The default OpenAI LLM model id has one source of truth; every restatement agrees with it.

``DEFAULT_LLM_MODEL`` in ``trace_mcp.extensions.learn.config`` is what TRACE sends to
OpenAI for relevance scoring (``TRACE_LLM_MODEL``) and learning extraction
(``TRACE_LLM_EXTRACTION_MODEL``) when the operator sets neither. The README
configuration table and the trace-learn extension doc restate that id, and a reader
trusts them without opening the code, so a model change that reaches the code but
not the docs (or the reverse) fails here instead of shipping as a silent mismatch.
"""

from pathlib import Path

import pytest

from trace_mcp.extensions.learn import config as _cfg
from trace_mcp.extensions.learn.config import DEFAULT_LLM_MODEL, LearnConfig, load_config

TRACE_ROOT = Path(__file__).resolve().parent.parent
MODEL_ENV_VARS = ("TRACE_LLM_MODEL", "TRACE_LLM_EXTRACTION_MODEL")


def test_both_code_defaults_are_the_single_constant() -> None:
    """Scoring and extraction share one default so a change lands in both or neither."""
    config = LearnConfig()
    assert config.llm_model == DEFAULT_LLM_MODEL
    assert config.llm_extraction_model == DEFAULT_LLM_MODEL


def test_loader_falls_back_to_the_constant_when_neither_variable_is_set(monkeypatch, tmp_path) -> None:
    """``load_config`` reads the model ids from the environment and ``.env`` files; with
    none of them set it must land on the same constant the dataclass defaults to.
    """
    for var in (*MODEL_ENV_VARS, "OPENAI_API_KEY", "TRACE_LLM_ENABLED", "TRACE_LOCAL_ONLY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_cfg, "_TRACE_ENV_PATH", Path("/nonexistent/.env"))
    monkeypatch.chdir(tmp_path)

    config = load_config()
    assert config.llm_model == DEFAULT_LLM_MODEL
    assert config.llm_extraction_model == DEFAULT_LLM_MODEL


@pytest.mark.parametrize("env_var", MODEL_ENV_VARS)
def test_readme_configuration_table_states_the_default(env_var: str) -> None:
    """The README's configuration table row for the variable names the code default."""
    rows = [ln for ln in (TRACE_ROOT / "README.md").read_text().split("\n") if ln.startswith(f"| `{env_var}` |")]

    assert len(rows) == 1, f"expected exactly one README table row for {env_var}, found {len(rows)}"
    assert f"`{DEFAULT_LLM_MODEL}`" in rows[0], (
        f"README row for {env_var} does not state {DEFAULT_LLM_MODEL}: {rows[0]!r}"
    )


@pytest.mark.parametrize("env_var", MODEL_ENV_VARS)
def test_trace_learn_doc_env_example_states_the_default(env_var: str) -> None:
    """The extension doc's example ``.env`` block assigns the variable the code default."""
    doc = TRACE_ROOT / "docs" / "extensions" / "trace-learn.md"
    lines = [ln for ln in doc.read_text().split("\n") if ln.startswith(f"{env_var}=")]

    assert len(lines) == 1, f"expected exactly one {env_var}= example line in trace-learn.md, found {len(lines)}"
    value = lines[0].split("=", 1)[1].split()[0]
    assert value == DEFAULT_LLM_MODEL, f"trace-learn.md sets {env_var}={value}, code default is {DEFAULT_LLM_MODEL}"
