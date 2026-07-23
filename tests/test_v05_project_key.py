"""Spec v0.5: the additive `metadata.project_key` field and its consumers.

Covers the three things a format change has to get right: that new documents
carry the field only when the producer actually knows the answer, that old
documents keep validating, and that the exporters derive identity the same way
the rest of the system does.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from trace_mcp import project_identity as pident
from trace_mcp import server
from trace_mcp.exporters.markdown_export import export_markdown
from trace_mcp.exporters.prov_jsonld import export_prov_jsonld
from trace_mcp.schema import SCHEMA_VERSION, Session, SessionMetadata

TRACE_ROOT = Path(__file__).parent.parent
REPO_SCHEMA = TRACE_ROOT / "schemas" / "trace-v0.5.json"
REGISTRY_SCHEMA = TRACE_ROOT / "schemas" / "trace-projects-v1.json"
# A committed copy of the shipped v0.4 schema, kept solely to prove that a v0.5
# document still validates for a consumer that never upgraded.
V04_COMPAT_SCHEMA = TRACE_ROOT / "tests" / "fixtures" / "schema-v0.4-compat.json"


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(tmp_path / "projects.json"))
    pident._reset_registry_cache()


def _enroll(key: str, *, display: str | None = None, aliases: list[str] | None = None) -> None:
    with pident.locked_registry() as registry:
        registry.projects[key] = pident.ProjectEntry(key=key, display_label=display or key, aliases=aliases or [])
    pident._reset_registry_cache()


# ── Schema shape ──────────────────────────────────────────────────────────


class TestSchemaShape:
    def test_schema_version_is_v05(self) -> None:
        assert SCHEMA_VERSION == "0.5.0"

    def test_project_key_is_optional_and_unconstrained(self) -> None:
        """A pattern here would retroactively invalidate live documents."""
        schema = json.loads(REPO_SCHEMA.read_text())
        metadata = schema["$defs"]["SessionMetadata"]
        assert "project_key" in metadata["properties"]
        assert metadata["required"] == ["project"], "project_key must not be required"
        assert "pattern" not in metadata["properties"]["project_key"]
        assert "pattern" not in metadata["properties"]["project"], (
            "constraining `project` would invalidate every drifted label already on disk"
        )

    def test_registry_interchange_schema_is_published_and_valid(self) -> None:
        schema = json.loads(REGISTRY_SCHEMA.read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        assert schema["$id"].endswith("trace-projects-v1.json")

        with pident.locked_registry() as registry:
            registry.projects["demo"] = pident.ProjectEntry(
                key="demo", display_label="Demo", aliases=["DEMO", "demo_project"]
            )
        payload = json.loads(pident.registry_path().read_text())
        jsonschema.validate(payload, schema)

    def test_registry_version_is_independent_of_schema_version(self) -> None:
        """Coupling them would force a session format bump per registry change."""
        assert pident.ProjectRegistry().version != SCHEMA_VERSION


# ── Bidirectional compatibility ───────────────────────────────────────────


class TestBackwardCompatibility:
    def _legacy_doc(self) -> dict[str, Any]:
        return {
            "context": "https://trace-protocol.org/v0.3",
            "trace_version": "0.4.1",
            "id": "trace_20260101_legacy",
            "created": "2026-01-01T00:00:00Z",
            "status": "completed",
            "metadata": {"project": "Legacy Project"},
            "events": [],
        }

    def test_legacy_document_validates_against_v05(self) -> None:
        jsonschema.validate(self._legacy_doc(), json.loads(REPO_SCHEMA.read_text()))

    def test_legacy_document_loads_without_a_key(self) -> None:
        session = Session.model_validate(self._legacy_doc())
        assert session.metadata.project_key is None
        assert session.metadata.project == "Legacy Project"

    def test_legacy_document_round_trips_without_gaining_a_key(self) -> None:
        """Reading and rewriting a pre-v0.5 record must not invent the field.

        Deriving one on the way through would present a guess as a captured
        fact, which is the alteration the alias table exists to avoid.
        """
        session = Session.model_validate(self._legacy_doc())
        dumped = session.model_dump(mode="json", exclude_none=True)
        assert "project_key" not in dumped["metadata"]

    def test_new_document_validates_against_the_v04_schema(self) -> None:
        """Additive means a v0.5 document is still readable by a v0.4 consumer.

        Validated against a committed copy of the real v0.4 schema rather than one
        read out of git history: CI clones shallow, and once this change lands the
        file is no longer at HEAD, so a history lookup would silently skip and read
        as coverage it is not providing.
        """
        v04_schema = json.loads(V04_COMPAT_SCHEMA.read_text())
        assert "project_key" not in v04_schema["$defs"]["SessionMetadata"]["properties"], (
            "fixture is not a pre-v0.5 schema — the compatibility claim would be vacuous"
        )

        doc = self._legacy_doc()
        doc["trace_version"] = SCHEMA_VERSION
        doc["metadata"]["project_key"] = "legacy-project"
        jsonschema.validate(doc, v04_schema)

    def test_v05_added_no_required_field(self) -> None:
        """The structural reason forward compatibility holds."""
        v05 = json.loads(REPO_SCHEMA.read_text())["$defs"]["SessionMetadata"]
        v04 = json.loads(V04_COMPAT_SCHEMA.read_text())["$defs"]["SessionMetadata"]
        assert v05["required"] == v04["required"]
        assert v04.get("additionalProperties") is not False


# ── Server stamping ───────────────────────────────────────────────────────


class TestServerStamping:
    async def test_pinned_start_stamps_the_project_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _enroll("waggle", display="Waggle")
        monkeypatch.setenv("TRACE_PROJECT", "waggle")

        result = await server.trace_start_session(description="d")
        assert "Error" not in result
        session_id = result.split("Session: ")[1].split("\n")[0].strip()

        session = await server.storage.get_session(session_id)
        assert session.metadata.project_key == "waggle"

    async def test_unpinned_start_stamps_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unpinned process has no authoritative key, so it asserts none."""
        monkeypatch.delenv("TRACE_PROJECT", raising=False)

        result = await server.trace_start_session(project="Some Project", description="d")
        assert "Error" not in result
        session_id = result.split("Session: ")[1].split("\n")[0].strip()

        session = await server.storage.get_session(session_id)
        assert session.metadata.project_key is None

        # The serializer writes null rather than omitting the field (as it already
        # does for `doi`), which is equivalent under the schema: `project_key` is
        # `anyOf[string, null]` with a null default. What matters is that no key is
        # ASSERTED — a null claims nothing, so readers still resolve via the label.
        on_disk = json.loads(Path(server.storage.session_location(session_id)).read_text())
        assert on_disk["metadata"]["project_key"] is None

    async def test_pinned_auto_create_stamps_the_project_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The auto-create path is stamped too — it is a real session of the pinned project."""
        _enroll("waggle", display="Waggle")
        monkeypatch.setenv("TRACE_PROJECT", "waggle")
        server._current_session_id = None

        result = await server.trace_log_annotation(category="observation", content="x")
        assert "Error" not in result

        assert server._current_session_id is not None
        session = await server.storage.get_session(server._current_session_id)
        assert session.metadata.project_key == "waggle"

    async def test_unpinned_auto_create_stamps_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRACE_PROJECT", raising=False)
        monkeypatch.delenv("TRACE_DEFAULT_PROJECT", raising=False)
        server._current_session_id = None

        result = await server.trace_log_annotation(category="observation", content="x")
        assert "Error" not in result

        assert server._current_session_id is not None
        session = await server.storage.get_session(server._current_session_id)
        assert session.metadata.project_key is None

    async def test_stamped_key_matches_the_recorded_label(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The stamped key must be the key of the label written beside it."""
        _enroll("waggle", display="Waggle Display Name")
        monkeypatch.setenv("TRACE_PROJECT", "waggle")

        result = await server.trace_start_session(description="d")
        session_id = result.split("Session: ")[1].split("\n")[0].strip()
        session = await server.storage.get_session(session_id)

        assert session.metadata.project == "Waggle Display Name"
        assert session.metadata.project_key == "waggle"
        assert pident.key_for_label(session.metadata.project) == session.metadata.project_key


# ── Exporters ─────────────────────────────────────────────────────────────


def _session(project: str, project_key: str | None = None) -> Session:
    return Session(
        id="trace_20260722_export",
        metadata=SessionMetadata(project=project, project_key=project_key),
    )


class TestProvExport:
    def test_carries_project_key_beside_an_untouched_project_literal(self) -> None:
        graph = json.loads(export_prov_jsonld(_session("Waggle", "waggle")))["@graph"]
        activity = next(n for n in graph if n["@id"] == "trace:session_trace_20260722_export")
        assert activity["trace:project"] == "Waggle", "the display literal must not be canonicalized"
        assert activity["trace:projectKey"] == "waggle"

    def test_derives_the_key_through_the_alias_table(self) -> None:
        """The failure this guards: exporting a renamed project under its old key.

        Bare canonicalization of the label `TRACE` yields `trace`, not the
        `trace-mcp` project it actually belongs to.
        """
        _enroll("trace-mcp", display="trace-mcp", aliases=["TRACE"])

        graph = json.loads(export_prov_jsonld(_session("TRACE")))["@graph"]
        activity = next(n for n in graph if n["@id"] == "trace:session_trace_20260722_export")
        assert activity["trace:projectKey"] == "trace-mcp", (
            "a registered alias must resolve to the project's real key, not the label's"
        )
        assert activity["trace:project"] == "TRACE"

    def test_reified_project_entity_lists_alias_labels(self) -> None:
        _enroll("trace-mcp", display="trace-mcp", aliases=["TRACE", "Trace_MCP"])

        graph = json.loads(export_prov_jsonld(_session("trace-mcp", "trace-mcp")))["@graph"]
        entity = next((n for n in graph if n["@id"] == "trace:project_trace-mcp"), None)
        assert entity is not None, "no reified project entity emitted"
        assert entity["trace:projectKey"] == "trace-mcp"
        assert sorted(entity["trace:aliasLabel"]) == ["TRACE", "Trace_MCP"]

    def test_export_survives_an_unenrolled_project(self) -> None:
        """An export must never fail because identity metadata is missing."""
        graph = json.loads(export_prov_jsonld(_session("Never Enrolled")))["@graph"]
        activity = next(n for n in graph if n["@id"] == "trace:session_trace_20260722_export")
        assert activity["trace:projectKey"] == "never-enrolled"
        assert not [n for n in graph if str(n["@id"]).startswith("trace:project_")]

    def test_identity_terms_round_trip_through_rdflib(self) -> None:
        """The new terms must yield real triples, not just plausible JSON.

        A canonical key may contain dots, so the reified entity's compact IRI is
        worth actually parsing rather than eyeballing — an unparseable @id would
        drop the node silently instead of erroring.
        """
        rdflib = pytest.importorskip("rdflib")
        _enroll("my.proj", display="My.Proj", aliases=["My Proj"])

        graph = rdflib.Graph()
        graph.parse(data=export_prov_jsonld(_session("My.Proj", "my.proj")), format="json-ld")

        ns = "https://trace-protocol.org/ns/v0.3#"
        keys = {str(o) for o in graph.objects(predicate=rdflib.URIRef(ns + "projectKey"))}
        assert "my.proj" in keys, "trace:projectKey produced no triple"

        aliases = {str(o) for o in graph.objects(predicate=rdflib.URIRef(ns + "aliasLabel"))}
        assert aliases == {"My Proj"}, f"trace:aliasLabel produced no usable triple: {aliases}"

        assert (
            rdflib.URIRef(ns + "project_my.proj"),
            rdflib.RDF.type,
            rdflib.URIRef("http://www.w3.org/ns/prov#Entity"),
        ) in graph, "the reified project entity did not survive the round trip"

    def test_prov_namespace_stays_frozen_at_v03(self) -> None:
        """New terms live inside the existing namespace; churning it would
        invalidate every previously exported document."""
        context = json.loads(export_prov_jsonld(_session("waggle", "waggle")))["@context"]
        assert context["trace"] == "https://trace-protocol.org/ns/v0.3#"


class TestMarkdownExport:
    def test_has_project_key_line_when_stamped(self) -> None:
        assert "**Project key**: waggle" in export_markdown(_session("Waggle", "waggle"))

    def test_omits_the_line_for_a_legacy_session(self) -> None:
        out = export_markdown(_session("Waggle"))
        assert "**Project key**" not in out, "a derived key must not be presented as a recorded fact"
        assert "**Project**: Waggle" in out


def test_schema_generator_is_deterministic() -> None:
    """Both shipped copies must match what the models produce right now."""
    result = subprocess.run(
        [sys.executable, str(TRACE_ROOT / "scripts" / "generate_schema.py")],
        cwd=TRACE_ROOT,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(TRACE_ROOT / "src"), "PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    diff = subprocess.run(
        ["git", "diff", "--exit-code", "--", "schemas/", "src/trace_mcp/schemas/"],
        cwd=TRACE_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert diff.returncode == 0, f"generated schemas differ from the committed ones:\n{diff.stdout}"
