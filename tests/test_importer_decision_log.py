"""The decision-log importer, pinned against the producer's own converter.

The producer (`rsi-exam-provenance`) already converts its decision log into a
TRACE document. This importer is a second implementation of that same mapping, so
the committed `expected_session*.json` documents — produced by the producer's
converter, never by this code — are the oracle: on a mismatch, the producer is
right by definition, because its output is what its downstream tools already read.

Two fixtures, because one log cannot exercise both halves of the contract:

- `decision_log_v1/` is a replay-mode log written before the gated-mode keys
  existed; it proves the seven project as explicit nulls.
- `decision_log_profile/` is a gated run under a task profile; it is the only
  fixture with those keys populated, with receipt evidence, and with a
  provisional decision that a replication resolves.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from trace_mcp import __version__
from trace_mcp.importers.decision_log import (
    LINE_KEYS,
    ContractDriftError,
    DecisionLogLine,
    LineageError,
    UnknownSchemaError,
    import_decision_log,
    load_decision_log,
    main,
)
from trace_mcp.schema import SCHEMA_VERSION
from trace_mcp.tools.query_tools import get_decision_chain

FIXTURES = Path(__file__).parent / "fixtures"
LEGACY = FIXTURES / "decision_log_v1"
PROFILE = FIXTURES / "decision_log_profile"

# The arguments each fixture was converted under. `--rollout` is not free for the
# profile fixture: its suite records the rollout it was derived for.
LEGACY_ARGS = dict(
    rollout_id="fixture-rollout", task="game2048_policy_search", harness="claude-code", model="claude-opus-5"
)
PROFILE_ARGS = dict(
    rollout_id="e2e-rollout", task="game2048_policy_search", harness="claude-code", model="claude-opus-5"
)

FIXTURE_CASES = [
    pytest.param(LEGACY, "expected_session_nullfilled.json", LEGACY_ARGS, id="legacy-replay"),
    pytest.param(PROFILE, "expected_session.json", PROFILE_ARGS, id="gated-profile"),
]

GATED_KEYS = (
    "confirm_policy",
    "profile_sha256",
    "look_index",
    "parent_method_tree_sha256",
    "candidate_method_tree_sha256",
    "sizing",
    "suite",
)
HOLDOUT_KEYS = ("estimate", "interval", "sample_size", "verdict", "evidence", "evidence_digests")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(fixture: Path) -> list[dict[str, Any]]:
    """The log's lines as parsed objects, so a mutation test edits data, not text."""
    return [json.loads(raw) for raw in (fixture / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]


def _write_log(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    """Re-serialize parsed rows. Never build a malformed log by string replacement."""
    path = tmp_path / "decisions.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _import(fixture: Path, rows: list[dict[str, Any]] | None = None, **overrides: Any) -> Any:
    args = dict(LEGACY_ARGS if fixture == LEGACY else PROFILE_ARGS)
    project = overrides.pop("project_override", "rsi-exam-provenance")
    args.update(overrides)
    lines = [DecisionLogLine.model_validate(row) for row in (rows if rows is not None else _rows(fixture))]
    return import_decision_log(
        lines,
        project=project,
        recorded_decision_log_sha256=_digest(fixture / "decisions.jsonl"),
        **args,
    )


def _normalize(doc: dict[str, Any]) -> dict[str, Any]:
    """Blank exactly the two fields that legitimately differ from the producer's.

    Both are asserted separately. Blanking two named fields rather than ignoring
    anything that differs is what keeps the parity comparison honest.
    """
    out = json.loads(json.dumps(doc))
    out["trace_version"] = None
    out["metadata"]["custom"]["importer"] = None
    for event in out["events"]:
        event["timestamp"] = _canonical_timestamp(event["timestamp"])
    for key in ("created", "ended"):
        if out.get(key):
            out[key] = _canonical_timestamp(out[key])
    return out


def _canonical_timestamp(value: str) -> str:
    """One spelling for the same instant: the producer writes `+00:00`, TRACE `Z`."""
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()


# ── Fixture integrity ────────────────────────────────────────────────────────


class TestFixtureIntegrity:
    """A fixture that can be edited silently makes every parity test meaningless."""

    @pytest.mark.parametrize("fixture", [LEGACY, PROFILE], ids=["legacy", "profile"])
    def test_manifest_matches_every_file(self, fixture: Path) -> None:
        manifest = (fixture / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
        recorded = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in manifest if line.strip()}

        present = sorted(
            "./" + str(p.relative_to(fixture)).replace("\\", "/")
            for p in fixture.rglob("*")
            if p.is_file() and p.name not in ("MANIFEST.sha256", "README.md")
        )
        assert sorted(recorded) == present, "MANIFEST.sha256 does not list exactly the fixture's files"
        for name, want in recorded.items():
            assert _digest(fixture / name) == want, f"{name} does not match its recorded digest"

    @pytest.mark.parametrize("fixture", [LEGACY, PROFILE], ids=["legacy", "profile"])
    def test_evidence_digests_match_the_result_files(self, fixture: Path) -> None:
        """The log's own digests are checked against the bytes it names."""
        checked = 0
        for row in _rows(fixture):
            for ref in row["evidence"]:
                target = fixture / ref["locator"]
                assert target.exists(), f"evidence locator {ref['locator']} is not in the fixture"
                assert _digest(target) == ref["sha256"], f"{ref['locator']} does not match its recorded digest"
                checked += 1
        assert checked > 0, "positive control failed: no evidence entries were checked"


# ── The oracle ───────────────────────────────────────────────────────────────


class TestGoldenParity:
    """The producer's converter is the oracle; these are its outputs, frozen."""

    @pytest.mark.parametrize(("fixture", "golden", "args"), FIXTURE_CASES)
    def test_importer_reproduces_the_producers_conversion(
        self, fixture: Path, golden: str, args: dict[str, Any]
    ) -> None:
        expected = json.loads((fixture / golden).read_text(encoding="utf-8"))
        produced = _import(fixture).model_dump(mode="json")

        assert _normalize(produced) == _normalize(expected)

    @pytest.mark.parametrize(("fixture", "golden", "args"), FIXTURE_CASES)
    def test_the_two_normalized_fields_are_this_builds_own(
        self, fixture: Path, golden: str, args: dict[str, Any]
    ) -> None:
        produced = _import(fixture).model_dump(mode="json")

        assert produced["trace_version"] == SCHEMA_VERSION
        assert produced["metadata"]["custom"]["importer"] == f"trace-mcp {__version__} schema {SCHEMA_VERSION}"


class TestLegacyLogProjectsSevenNulls:
    """A log written before the gated keys existed must keep importing."""

    def test_every_gated_key_is_present_and_null(self) -> None:
        session = _import(LEGACY)
        blocks = [e.decision.confidence for e in session.events if e.decision]

        assert len(blocks) == 3
        for block in blocks:
            dumped = block.model_dump(mode="json")
            for key in GATED_KEYS:
                assert key in dumped, f"{key} absent rather than null"
                assert dumped[key] is None, f"{key} is not null on a legacy line"

    def test_holdout_is_null_on_every_legacy_event(self) -> None:
        session = _import(LEGACY)
        for event in session.events:
            assert event.decision is not None
            assert event.decision.confidence.model_dump(mode="json")["holdout"] is None


class TestProfileLogCarriesTheGatedKeys:
    """The only fixture that covers populated gated values."""

    def test_all_seven_survive_unchanged(self) -> None:
        rows = _rows(PROFILE)
        session = _import(PROFILE)
        blocks = [e.decision.confidence.model_dump(mode="json") for e in session.events if e.decision]

        assert len(blocks) == 2
        for row, block in zip(rows, blocks, strict=True):
            for key in GATED_KEYS:
                assert block[key] == row[key], f"{key} did not survive the import unchanged"
        assert blocks[0]["sizing"]["size"] == 4
        assert blocks[0]["suite"]["derivation"]["rollout_id"] == "e2e-rollout"
        assert blocks[0]["look_index"] == 1

    def test_receipt_evidence_is_carried(self) -> None:
        session = _import(PROFILE)
        block = session.events[0].decision.confidence.model_dump(mode="json")

        assert [e["role"] for e in block["evidence"]] == ["parent", "candidate", "receipt-parent", "receipt-candidate"]

    def test_a_replication_resolves_and_revises_the_provisional(self) -> None:
        session = _import(PROFILE)
        first, second = session.events[0].decision, session.events[1].decision

        assert first.revision_note == "Resolved by replication evt_002."
        assert first.resolved_by is not None and first.resolved_by.id == "rsi-exam-gate/decide.py"
        assert second.revises_event_id == "evt_001"
        assert second.revision_note == "Replication did not clear the minimum effect."


class TestNormativeFields:
    """Field-by-field, against the legacy golden's own values."""

    def test_event_and_session_identity(self) -> None:
        session = _import(LEGACY)

        assert session.id == "rsiexam_fixture-rollout"
        assert [e.id for e in session.events] == ["evt_001", "evt_002", "evt_003"]
        assert session.status == "completed"
        assert session.created == session.events[0].timestamp
        assert session.ended == session.events[-1].timestamp

    def test_descriptions_and_dispositions(self) -> None:
        session = _import(LEGACY)
        decisions = [e.decision for e in session.events]

        assert decisions[0].description == "Revert v2 (parent v1)"
        assert decisions[0].disposition == "rejected"
        assert decisions[0].revision_note == "Interval entirely below zero."
        assert decisions[1].description == "Keep v3 provisionally (parent v1)"
        assert decisions[2].description == "Replication of v3 on fresh seeds (parent v1)"
        assert decisions[2].revises_event_id == "evt_002"

    def test_rationale_is_the_producers_template(self) -> None:
        session = _import(LEGACY)
        rationale = session.events[0].decision.rationale

        assert rationale is not None
        assert rationale.startswith("Mean paired delta ")
        assert "percent percentile bootstrap interval" in rationale
        assert rationale.endswith(".")

    def test_actors_and_tags(self) -> None:
        session = _import(LEGACY)
        decision = session.events[0].decision

        assert decision.proposed_by.type == "ai"
        assert decision.proposed_by.id == "claude-code:claude-opus-5"
        assert decision.proposed_by.role == "rollout-agent"
        assert decision.resolved_by.type == "system"
        assert decision.resolved_by.role == "decision-gate"
        assert decision.suggestion_type == "proactive"
        assert decision.tags == ["rsi-exam", "decision-gate", "v2"]

    def test_metadata_custom_key_set(self) -> None:
        session = _import(LEGACY)
        custom = session.metadata.custom

        assert set(custom) == {
            "source",
            "importer",
            "rollout_id",
            "task",
            "harness",
            "model",
            "decision_log_sha256",
            "locator_base",
        }
        assert custom["source"] == "rsi-exam-decision-log/v1"
        assert custom["locator_base"] == "artifacts/app/methods"
        assert custom["decision_log_sha256"] == _digest(LEGACY / "decisions.jsonl")
        assert session.metadata.project_key == "rsi-exam-provenance"
        assert session.metadata.experiment_id == "fixture-rollout"

    def test_locator_base_is_metadata_only(self) -> None:
        """It is recorded, never applied to an evidence locator."""
        session = _import(LEGACY, locator_base="somewhere/else")
        block = session.events[0].decision.confidence.model_dump(mode="json")

        assert session.metadata.custom["locator_base"] == "somewhere/else"
        assert not block["evidence"][0]["locator"].startswith("somewhere/else")

    def test_summary_counts_by_line_kind(self) -> None:
        session = _import(LEGACY)

        # The log is: v2 reverted, v3 provisional, v3 replicated (a keep). A
        # replication counts only as replicated, never also as a keep, which is
        # why `kept` is zero here.
        assert session.summary == (
            "RSI-Exam rollout fixture-rollout (game2048_policy_search, claude-code, claude-opus-5): "
            "0 kept, 1 reverted, 1 provisional, 1 replicated."
        )

    def test_the_three_verdict_extras_are_preserved(self) -> None:
        rows = _rows(LEGACY)
        session = _import(LEGACY)

        for row, event in zip(rows, session.events, strict=True):
            block = event.decision.confidence.model_dump(mode="json")
            assert block["verdict"] == row["verdict"]
            assert block["min_effect"] == row["min_effect"]
            assert block["contract"] == "rsi-exam-decision-log/v1"

    def test_decision_chain_links_the_replication_to_its_provisional(self) -> None:
        session = _import(LEGACY)
        chain = get_decision_chain(session, event_id="evt_003")

        assert [e["id"] for e in chain] == ["evt_002", "evt_003"]
        assert all(e["decision"]["confidence"] is not None for e in chain)


class TestHoldoutProjection:
    """A holdout block projects exactly six keys, whatever the line carries."""

    def test_an_extra_key_on_holdout_is_not_projected(self) -> None:
        rows = _rows(LEGACY)
        primary = rows[0]["evidence"][0]
        rows[0]["holdout"] = {
            "estimate": 1.0,
            "interval": {"lower": 0.5, "upper": 1.5, "level": 0.9},
            "sample_size": 4,
            "verdict": "clears",
            "evidence": [{"role": "holdout-parent", "locator": primary["locator"], "sha256": primary["sha256"]}],
            "evidence_digests": {"holdout-parent": "sha256:" + primary["sha256"]},
            "unexpected_seventh": "should not be projected",
        }
        session = _import(LEGACY, rows=rows)
        holdout = session.events[0].decision.confidence.model_dump(mode="json")["holdout"]

        assert set(holdout) == set(HOLDOUT_KEYS)


# ── Refusals ─────────────────────────────────────────────────────────────────


class TestContractDrift:
    """An unknown top-level key is refused, never copied and never dropped."""

    def test_the_loader_names_the_unknown_key(self, tmp_path: Path) -> None:
        rows = _rows(LEGACY)
        rows[0]["look_budget"] = 12
        path = _write_log(tmp_path, rows)

        with pytest.raises(ContractDriftError, match="look_budget"):
            load_decision_log(path)

    def test_a_direct_caller_is_refused_too(self) -> None:
        rows = _rows(LEGACY)
        rows[0]["look_budget"] = 12

        with pytest.raises(ContractDriftError, match="look_budget"):
            _import(LEGACY, rows=rows)

    def test_line_keys_is_the_union_of_required_and_gated(self) -> None:
        """26 keys: the producer's required minimum plus the seven gated ones."""
        assert len(LINE_KEYS) == 26
        assert set(GATED_KEYS) <= set(LINE_KEYS)
        assert {"schema", "line", "timestamp", "version_id", "parent_id", "holdout"} <= set(LINE_KEYS)

    def test_both_fixtures_use_only_known_keys(self) -> None:
        for fixture in (LEGACY, PROFILE):
            for row in _rows(fixture):
                assert set(row) <= set(LINE_KEYS), f"{fixture.name} carries a key outside LINE_KEYS"


class TestLoaderRefusals:
    def test_a_foreign_schema_id_is_refused(self, tmp_path: Path) -> None:
        rows = _rows(LEGACY)
        rows[0]["schema"] = "some-other-log/v1"

        with pytest.raises(UnknownSchemaError):
            load_decision_log(_write_log(tmp_path, rows))

    def test_a_line_number_that_disagrees_with_its_position_is_refused(self, tmp_path: Path) -> None:
        rows = _rows(LEGACY)
        rows[1]["line"] = 99

        with pytest.raises(LineageError):
            load_decision_log(_write_log(tmp_path, rows))

    def test_a_blank_physical_line_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "decisions.jsonl"
        rows = _rows(LEGACY)
        path.write_text(json.dumps(rows[0]) + "\n\n" + json.dumps(rows[1]) + "\n", encoding="utf-8")

        with pytest.raises(ValueError):
            load_decision_log(path)

    def test_a_naive_timestamp_is_refused(self, tmp_path: Path) -> None:
        rows = _rows(LEGACY)
        rows[0]["timestamp"] = "2026-09-03T18:01:00"

        with pytest.raises(ValueError):
            load_decision_log(_write_log(tmp_path, rows))


class TestDirectCallerRefusals:
    def test_a_foreign_schema_id_is_rechecked(self) -> None:
        """A caller that bypasses the loader cannot import a foreign log."""
        rows = _rows(LEGACY)
        rows[0]["schema"] = "some-other-log/v1"

        with pytest.raises(UnknownSchemaError):
            _import(LEGACY, rows=rows)

    def test_non_increasing_line_numbers_are_refused(self) -> None:
        rows = _rows(LEGACY)
        rows[0]["line"], rows[1]["line"] = 2, 1

        with pytest.raises(LineageError):
            _import(LEGACY, rows=rows)

    def test_a_slice_of_a_log_is_accepted(self) -> None:
        """The position check is loader-only, so a slice still imports."""
        session = _import(LEGACY, rows=_rows(LEGACY)[1:])

        assert [e.id for e in session.events] == ["evt_001", "evt_002"]

    def test_an_empty_log_is_refused(self) -> None:
        with pytest.raises(ValueError):
            _import(LEGACY, rows=[])

    def test_a_reserved_project_label_is_refused(self) -> None:
        with pytest.raises(ValueError):
            _import(LEGACY, project_override="shared")

    def test_a_malformed_recorded_digest_is_refused(self) -> None:
        lines = [DecisionLogLine.model_validate(row) for row in _rows(LEGACY)]

        with pytest.raises(ValueError):
            import_decision_log(
                lines,
                project="rsi-exam-provenance",
                recorded_decision_log_sha256="NOTAHASH",
                **LEGACY_ARGS,
            )


class TestLineageRefusals:
    """Only the structural rules the event graph needs; no policy checks."""

    def test_a_replication_without_an_open_provisional_is_refused(self) -> None:
        rows = [_rows(LEGACY)[2]]

        with pytest.raises(LineageError):
            _import(LEGACY, rows=rows)

    def test_a_replication_naming_a_different_version_is_refused(self) -> None:
        rows = _rows(LEGACY)
        rows[2]["replicates"] = "v2"

        with pytest.raises(LineageError):
            _import(LEGACY, rows=rows)

    def test_a_replication_with_a_different_parent_is_refused(self) -> None:
        rows = _rows(LEGACY)
        rows[2]["parent_id"] = "v2"

        with pytest.raises(LineageError):
            _import(LEGACY, rows=rows)

    def test_a_second_open_provisional_is_refused(self) -> None:
        rows = _rows(LEGACY)
        second = json.loads(json.dumps(rows[1]))
        second["line"] = 3
        second["version_id"] = "v4"
        rows = [rows[0], rows[1], second]

        with pytest.raises(LineageError):
            _import(LEGACY, rows=rows)

    def test_building_on_a_version_with_an_open_provisional_is_refused(self) -> None:
        rows = _rows(LEGACY)
        follow_on = json.loads(json.dumps(rows[0]))
        follow_on["line"] = 3
        follow_on["version_id"] = "v4"
        follow_on["parent_id"] = "v3"
        rows = [rows[0], rows[1], follow_on]

        with pytest.raises(LineageError):
            _import(LEGACY, rows=rows)

    def test_a_provisional_replication_is_refused(self) -> None:
        rows = _rows(LEGACY)
        rows[2]["disposition"] = "provisional"

        with pytest.raises(LineageError):
            _import(LEGACY, rows=rows)


# ── Purity ───────────────────────────────────────────────────────────────────


class TestImporterIsPure:
    def test_the_module_never_imports_the_session_store(self) -> None:
        """Structural, not a promise: an import must never write into ~/.trace."""
        from trace_mcp.importers import decision_log

        source = Path(decision_log.__file__).read_text(encoding="utf-8")

        assert "trace_mcp.storage" not in source
        assert "from trace_mcp import storage" not in source


# ── CLI ──────────────────────────────────────────────────────────────────────


class TestCommandLine:
    def test_a_successful_run_writes_a_document(self, tmp_path: Path) -> None:
        out = tmp_path / "session.json"
        code = main(
            [
                str(LEGACY / "decisions.jsonl"),
                "--project",
                "rsi-exam-provenance",
                "--rollout",
                "fixture-rollout",
                "--task",
                "game2048_policy_search",
                "--harness",
                "claude-code",
                "--model",
                "claude-opus-5",
                "--output",
                str(out),
            ]
        )

        assert code == 0
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert len(doc["events"]) == 3

    def test_the_stored_digest_is_computed_over_the_files_bytes(self, tmp_path: Path) -> None:
        out = tmp_path / "session.json"
        log = LEGACY / "decisions.jsonl"
        main(
            [
                str(log),
                "--project",
                "rsi-exam-provenance",
                "--rollout",
                "fixture-rollout",
                "--task",
                "t",
                "--harness",
                "h",
                "--model",
                "m",
                "--output",
                str(out),
            ]
        )
        doc = json.loads(out.read_text(encoding="utf-8"))

        assert doc["metadata"]["custom"]["decision_log_sha256"] == hashlib.sha256(log.read_bytes()).hexdigest()

    def test_a_missing_file_exits_one(self, tmp_path: Path) -> None:
        code = main(
            [
                str(tmp_path / "absent.jsonl"),
                "--project",
                "p",
                "--rollout",
                "r",
                "--task",
                "t",
                "--harness",
                "h",
                "--model",
                "m",
            ]
        )

        assert code == 1

    def test_an_unwritable_output_exits_one(self, tmp_path: Path) -> None:
        code = main(
            [
                str(LEGACY / "decisions.jsonl"),
                "--project",
                "rsi-exam-provenance",
                "--rollout",
                "fixture-rollout",
                "--task",
                "t",
                "--harness",
                "h",
                "--model",
                "m",
                "--output",
                str(tmp_path / "no-such-dir" / "out.json"),
            ]
        )

        assert code == 1

    def test_a_reserved_label_exits_one(self, tmp_path: Path) -> None:
        code = main(
            [
                str(LEGACY / "decisions.jsonl"),
                "--project",
                "shared",
                "--rollout",
                "fixture-rollout",
                "--task",
                "t",
                "--harness",
                "h",
                "--model",
                "m",
                "--output",
                str(tmp_path / "out.json"),
            ]
        )

        assert code == 1

    def test_an_unknown_schema_exits_two(self, tmp_path: Path) -> None:
        rows = _rows(LEGACY)
        rows[0]["schema"] = "some-other-log/v1"
        code = main(
            [
                str(_write_log(tmp_path, rows)),
                "--project",
                "p",
                "--rollout",
                "r",
                "--task",
                "t",
                "--harness",
                "h",
                "--model",
                "m",
            ]
        )

        assert code == 2

    def test_contract_drift_exits_two(self, tmp_path: Path) -> None:
        rows = _rows(LEGACY)
        rows[0]["look_budget"] = 12
        code = main(
            [
                str(_write_log(tmp_path, rows)),
                "--project",
                "p",
                "--rollout",
                "r",
                "--task",
                "t",
                "--harness",
                "h",
                "--model",
                "m",
            ]
        )

        assert code == 2

    def test_a_lineage_violation_exits_two(self, tmp_path: Path) -> None:
        rows = _rows(LEGACY)
        rows[1]["line"] = 99
        code = main(
            [
                str(_write_log(tmp_path, rows)),
                "--project",
                "p",
                "--rollout",
                "r",
                "--task",
                "t",
                "--harness",
                "h",
                "--model",
                "m",
            ]
        )

        assert code == 2


class TestDispatcher:
    """`trace-mcp import decision-log` reaches this importer."""

    def test_the_subcommand_dispatches(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trace_mcp import server

        out = tmp_path / "out.json"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "trace-mcp",
                "import",
                "decision-log",
                str(LEGACY / "decisions.jsonl"),
                "--project",
                "rsi-exam-provenance",
                "--rollout",
                "fixture-rollout",
                "--task",
                "t",
                "--harness",
                "h",
                "--model",
                "m",
                "--output",
                str(out),
            ],
        )

        with pytest.raises(SystemExit) as excinfo:
            server.main()

        assert excinfo.value.code == 0
        assert out.exists()

    def test_an_unknown_source_names_both_importers(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from trace_mcp import server

        monkeypatch.setattr(sys, "argv", ["trace-mcp", "import", "nope"])

        with pytest.raises(SystemExit) as excinfo:
            server.main()

        assert excinfo.value.code == 2
        stderr = capsys.readouterr().err
        assert "gents" in stderr
        assert "decision-log" in stderr


class TestValidatesAgainstThePublishedSchema:
    """An imported document must pass the project's own validator."""

    @pytest.mark.parametrize(("fixture", "golden", "args"), FIXTURE_CASES)
    def test_the_import_validates(self, fixture: Path, golden: str, args: dict[str, Any], tmp_path: Path) -> None:
        out = tmp_path / "out.json"
        code = main(
            [
                str(fixture / "decisions.jsonl"),
                "--project",
                "rsi-exam-provenance",
                "--rollout",
                str(args["rollout_id"]),
                "--task",
                str(args["task"]),
                "--harness",
                str(args["harness"]),
                "--model",
                str(args["model"]),
                "--output",
                str(out),
            ]
        )
        assert code == 0

        result = subprocess.run(
            [sys.executable, "-m", "trace_mcp.validate", str(out)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
