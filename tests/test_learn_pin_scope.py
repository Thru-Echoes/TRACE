"""Learn tools resolve their ``project`` argument against the TRACE_PROJECT pin.

ADR-006 mandates that ``project`` on all ``trace_learn_*`` tools is optional:
under a pin, ``None`` resolves to the pin and any supplied label must resolve
to the pinned key or the call errors naming both keys; unpinned, an explicit
label is required. Before this suite, the learn surface accepted any foreign
label on a pinned server — a cross-project read/write bypass of the documented
fail-closed guarantee.

Tools are exercised through ``FastMCP.call_tool`` so the registered schema
(optional ``project``) and the real dispatch path are what is tested.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

import trace_mcp.project_identity as pident
from trace_mcp.extensions.learn import register
from trace_mcp.storage.json_file import JsonFileStorage


@pytest.fixture()
def learn_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated knowledge/session/registry dirs, offline BM25 backend, no pin."""
    monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setenv("TRACE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    monkeypatch.setenv("TRACE_EMBEDDING_BACKEND", "none")
    monkeypatch.setenv("TRACE_LLM_ENABLED", "false")
    monkeypatch.setenv("TRACE_STRICT_LLM", "false")
    for var in ("TRACE_PROJECT", "OPENAI_API_KEY", "TRACE_LOCAL_ONLY"):
        monkeypatch.delenv(var, raising=False)
    pident._reset_registry_cache()
    mcp = FastMCP("learn-pin-test")
    register(mcp, JsonFileStorage(directory=str(tmp_path / "sessions")))
    yield mcp, tmp_path
    pident._reset_registry_cache()


async def _call(mcp: FastMCP, tool: str, args: dict[str, Any]) -> dict:
    """Call a registered tool and decode its JSON text payload."""
    out = await mcp.call_tool(tool, args)
    if isinstance(out, tuple):  # newer mcp: (content, structured)
        out = out[0]
    text = out[0].text  # type: ignore[union-attr]
    return json.loads(text)


# Minimal valid extra arguments per tool (beyond `project`).
TOOL_MIN_ARGS: dict[str, dict[str, Any]] = {
    "trace_learn_add": {"content": "a learning"},
    "trace_learn_recall": {"context": "anything"},
    "trace_learn_list": {},
    "trace_learn_forget": {"learning_id": "lrn_001"},
    "trace_learn_extract": {},
}


# ── pinned: foreign label fails closed ──────────────────────────────────────


@pytest.mark.parametrize("tool", sorted(TOOL_MIN_ARGS))
async def test_pinned_foreign_label_rejected(learn_env, monkeypatch: pytest.MonkeyPatch, tool: str) -> None:
    mcp, tmp_path = learn_env
    monkeypatch.setenv("TRACE_PROJECT", "proj-a")
    result = await _call(mcp, tool, {"project": "proj-b", **TOOL_MIN_ARGS[tool]})
    assert "error" in result, f"{tool} accepted a foreign label on a pinned server: {result}"
    # The error names both the pinned key and the supplied label (ADR-006).
    blob = json.dumps(result)
    assert "proj-a" in blob and "proj-b" in blob
    # Fail closed: no foreign store was created or touched.
    assert not (tmp_path / "knowledge" / "proj-b.json").exists()


@pytest.mark.parametrize("tool", sorted(TOOL_MIN_ARGS))
async def test_pinned_case_variant_label_is_not_foreign(learn_env, monkeypatch: pytest.MonkeyPatch, tool: str) -> None:
    """A label that canonicalizes to the pinned key is the SAME project, not foreign."""
    mcp, _ = learn_env
    monkeypatch.setenv("TRACE_PROJECT", "proj-a")
    result = await _call(mcp, tool, {"project": "Proj_A", **TOOL_MIN_ARGS[tool]})
    assert "error" not in result or "pinned" not in json.dumps(result), (
        f"{tool} treated a case/separator variant of the pin as foreign: {result}"
    )


# ── pinned: omitted project resolves to the pin ─────────────────────────────


async def test_pinned_omitted_project_resolves_to_pin_on_add(learn_env, monkeypatch: pytest.MonkeyPatch) -> None:
    mcp, tmp_path = learn_env
    monkeypatch.setenv("TRACE_PROJECT", "proj-a")
    result = await _call(mcp, "trace_learn_add", {"content": "pinned default works"})
    assert "added" in result, f"expected success, got: {result}"
    assert (tmp_path / "knowledge" / "proj-a.json").exists()


async def test_pinned_omitted_project_resolves_to_pin_on_list(learn_env, monkeypatch: pytest.MonkeyPatch) -> None:
    mcp, _ = learn_env
    monkeypatch.setenv("TRACE_PROJECT", "proj-a")
    await _call(mcp, "trace_learn_add", {"content": "seed"})
    result = await _call(mcp, "trace_learn_list", {})
    assert result.get("total") == 1, f"expected the pinned store's learning, got: {result}"


async def test_pinned_matching_label_still_works(learn_env, monkeypatch: pytest.MonkeyPatch) -> None:
    mcp, tmp_path = learn_env
    monkeypatch.setenv("TRACE_PROJECT", "proj-a")
    result = await _call(mcp, "trace_learn_add", {"project": "proj-a", "content": "explicit pin label"})
    assert "added" in result
    assert (tmp_path / "knowledge" / "proj-a.json").exists()


# ── unpinned ─────────────────────────────────────────────────────────────────


async def test_unpinned_omitted_project_errors(learn_env) -> None:
    mcp, _ = learn_env
    result = await _call(mcp, "trace_learn_add", {"content": "no project anywhere"})
    assert "error" in result
    assert "pinned" in result["error"] or "project" in result["error"]


async def test_unpinned_explicit_label_still_works(learn_env) -> None:
    mcp, tmp_path = learn_env
    result = await _call(mcp, "trace_learn_add", {"project": "proj-x", "content": "unpinned explicit"})
    assert "added" in result
    assert (tmp_path / "knowledge" / "proj-x.json").exists()


async def test_reserved_key_still_rejected(learn_env) -> None:
    mcp, _ = learn_env
    result = await _call(mcp, "trace_learn_add", {"project": "auto", "content": "nope"})
    assert "error" in result and "reserved" in result["error"]


# ── recall honesty ───────────────────────────────────────────────────────────


async def test_recall_without_context_or_tags_errors(learn_env) -> None:
    """No query → an explicit error pointing at trace_learn_list, never an
    insertion-order listing dressed up as results."""
    mcp, _ = learn_env
    await _call(mcp, "trace_learn_add", {"project": "proj-x", "content": "seed learning"})
    result = await _call(mcp, "trace_learn_recall", {"project": "proj-x"})
    assert "error" in result, f"recall with no query must error, got: {result}"
    assert "trace_learn_list" in json.dumps(result)
    assert "results" not in result


async def test_recall_reports_backend(learn_env) -> None:
    mcp, _ = learn_env
    await _call(mcp, "trace_learn_add", {"project": "proj-x", "content": "the ZANZIBAR-9931 form gate"})
    result = await _call(mcp, "trace_learn_recall", {"project": "proj-x", "context": "ZANZIBAR-9931"})
    assert result.get("backend") == "bm25", f"expected backend name in response, got: {result}"


async def test_recall_bm25_surfaces_verbatim_token(learn_env) -> None:
    mcp, _ = learn_env
    await _seed_corpus(mcp, "proj-x")
    result = await _call(mcp, "trace_learn_recall", {"project": "proj-x", "context": "ZANZIBAR-9931", "limit": 1})
    assert result["total"] == 1
    assert "ZANZIBAR-9931" in result["results"][0]["learning"]["content"]


# ── query alias ──────────────────────────────────────────────────────────────


SEED_CORPUS = (
    "prefer pathlib over os.path in the pipeline",
    "the nightly job needs the warehouse VPN",
    "form ZANZIBAR-9931 must be filed before export",
)
# BM25 note: a single-document store scores ~0 for every query (IDF collapses
# when the term appears in the only document), so alias tests seed a small
# corpus — the behavior under test is query=/context= parity, not tiny-store
# ranking quality.


async def _seed_corpus(mcp: FastMCP, project: str) -> None:
    for text in SEED_CORPUS:
        await _call(mcp, "trace_learn_add", {"project": project, "content": text})


async def test_recall_query_alias_behaves_like_context(learn_env) -> None:
    mcp, _ = learn_env
    await _seed_corpus(mcp, "proj-x")
    result = await _call(mcp, "trace_learn_recall", {"project": "proj-x", "query": "ZANZIBAR-9931"})
    assert result.get("total") == 1, f"query= must rank like context=, got: {result}"
    assert "ZANZIBAR-9931" in result["results"][0]["learning"]["content"]


async def test_recall_query_context_conflict_errors(learn_env) -> None:
    mcp, _ = learn_env
    await _call(mcp, "trace_learn_add", {"project": "proj-x", "content": "seed"})
    result = await _call(
        mcp,
        "trace_learn_recall",
        {"project": "proj-x", "query": "one thing", "context": "another thing"},
    )
    assert "error" in result, f"conflicting query/context must error, got: {result}"


async def test_recall_query_and_identical_context_allowed(learn_env) -> None:
    mcp, _ = learn_env
    await _seed_corpus(mcp, "proj-x")
    result = await _call(
        mcp,
        "trace_learn_recall",
        {"project": "proj-x", "query": "ZANZIBAR-9931", "context": "ZANZIBAR-9931"},
    )
    assert result.get("total") == 1


# ── input normalization ──────────────────────────────────────────────────────


async def test_recall_whitespace_only_context_errors(learn_env) -> None:
    mcp, _ = learn_env
    await _seed_corpus(mcp, "proj-x")
    result = await _call(mcp, "trace_learn_recall", {"project": "proj-x", "context": "   \t"})
    assert "error" in result, f"whitespace-only context must not rank, got: {result}"


async def test_recall_blank_tags_are_no_criteria(learn_env) -> None:
    mcp, _ = learn_env
    await _seed_corpus(mcp, "proj-x")
    result = await _call(mcp, "trace_learn_recall", {"project": "proj-x", "tags": ["", "   "]})
    assert "error" in result, f"blank tag elements must not count as criteria, got: {result}"


async def test_recall_empty_context_with_query_is_not_a_conflict(learn_env) -> None:
    mcp, _ = learn_env
    await _seed_corpus(mcp, "proj-x")
    result = await _call(
        mcp,
        "trace_learn_recall",
        {"project": "proj-x", "context": "", "query": "ZANZIBAR-9931"},
    )
    assert result.get("total") == 1, f"empty-string context is an absent alias, not a conflict: {result}"


# ── registry-alias interactions ──────────────────────────────────────────────


async def test_pinned_accepted_alias_uses_the_pinned_store(learn_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A registry alias whose canonical form differs from the pinned key must
    still land in the PINNED project's store file — otherwise the accepted
    alias silently opens a different store than the one it was authorized
    against (store filenames key on canonical_project_key, which is
    registry-independent)."""
    mcp, tmp_path = learn_env
    with pident.locked_registry() as reg:
        reg.projects["payments-api"] = pident.ProjectEntry(
            key="payments-api", display_label="Payments API", aliases=["Old Payments"]
        )
    pident._reset_registry_cache()
    monkeypatch.setenv("TRACE_PROJECT", "payments-api")

    result = await _call(mcp, "trace_learn_add", {"project": "Old Payments", "content": "alias-added learning"})
    assert "added" in result, f"registry alias of the pin must be accepted: {result}"
    assert (tmp_path / "knowledge" / "payments-api.json").exists()
    assert not (tmp_path / "knowledge" / "old-payments.json").exists()

    listing = await _call(mcp, "trace_learn_list", {})
    assert listing.get("total") == 1, f"the pinned store must see the alias-added learning: {listing}"


async def test_unpinned_alias_to_reserved_key_rejected(learn_env) -> None:
    """A registry alias must not smuggle a benign-looking label into a
    reserved quarantine store."""
    mcp, tmp_path = learn_env
    with pident.locked_registry() as reg:
        reg.projects["auto"] = pident.ProjectEntry(key="auto", display_label="auto", aliases=["Automatic"])
    pident._reset_registry_cache()

    result = await _call(mcp, "trace_learn_add", {"project": "Automatic", "content": "nope"})
    assert "error" in result and "reserved" in result["error"], f"alias to reserved key must be rejected: {result}"
    assert not (tmp_path / "knowledge" / "automatic.json").exists()
    assert not (tmp_path / "knowledge" / "auto.json").exists()
