"""End-to-end tests for `trace-mcp identity` (ADR-006 S6).

Every test runs against an isolated `tmp_path` TRACE home — nothing here touches
the developer's real `~/.trace`. The seven subcommands are exercised through
their real dispatch (`identity_cli.main`), so what is tested is what ships.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from trace_mcp import identity_cli
from trace_mcp import project_identity as pident


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every TRACE path at a throwaway home and reset the registry cache."""
    home = tmp_path / "trace"
    (home / "sessions").mkdir(parents=True)
    (home / "knowledge").mkdir(parents=True)
    monkeypatch.setenv("TRACE_SESSIONS_DIR", str(home / "sessions"))
    monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(home / "knowledge"))
    monkeypatch.setenv("TRACE_REGISTRY_PATH", str(home / "projects.json"))
    monkeypatch.setenv("TRACE_EGRESS_LOG", str(home / "egress.jsonl"))
    monkeypatch.setenv("TRACE_MIGRATIONS_LOG", str(home / "migrations.jsonl"))
    # Disable the merge freshness gate: tests write a store then merge it in the
    # same instant, which the production "was this touched < 5s ago?" writer-liveness
    # check would (racily) treat as an active writer. The lock-file half of the
    # preflight stays exercised by test_refuses_when_a_lock_is_present.
    monkeypatch.setenv("TRACE_MERGE_MIN_AGE_SEC", "0")
    pident._reset_registry_cache()
    return home


def _run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = identity_cli.main(list(argv), out=out)
    return code, out.getvalue()


def _write_session(
    home: Path, session_id: str, project: str, *, project_key: str | None = None, status: str = "completed"
) -> Path:
    meta: dict = {"project": project}
    if project_key is not None:
        meta["project_key"] = project_key
    doc = {
        "context": "https://trace-protocol.org/v0.3",
        "trace_version": "0.5.0",
        "id": session_id,
        "created": "2026-07-23T00:00:00Z",
        "status": status,
        "metadata": meta,
        "events": [],
    }
    path = home / "sessions" / f"{session_id}.json"
    path.write_text(json.dumps(doc, indent=2))
    return path


def _write_store(home: Path, stem: str, *, learnings: list[str], project: str | None = None) -> Path:
    store = {
        "project": project if project is not None else stem,
        "learnings": [{"id": f"lrn_{i:03d}", "content": c, "category": "learning"} for i, c in enumerate(learnings, 1)],
    }
    path = home / "knowledge" / f"{stem}.json"
    path.write_text(json.dumps(store, indent=2))
    return path


def _enroll(key: str, *, display: str | None = None, aliases: list[str] | None = None) -> None:
    with pident.locked_registry() as registry:
        registry.projects[key] = pident.ProjectEntry(key=key, display_label=display or key, aliases=aliases or [])
    pident._reset_registry_cache()


# ── snapshot ───────────────────────────────────────────────────────────────


class TestSnapshot:
    def test_writes_archive_marker_and_counts(self, _isolated_home: Path) -> None:
        _write_session(_isolated_home, "trace_20260101_a", "alpha")
        _write_session(_isolated_home, "trace_20260101_b", "beta")
        _write_store(_isolated_home, "alpha", learnings=["x"])

        code, _ = _run("snapshot")
        assert code == 0
        marker = json.loads((_isolated_home / "backups" / ".snapshot-marker.json").read_text())
        assert marker["counts"] == {"sessions": 2, "knowledge_stores": 1}
        assert Path(marker["archive"]).is_file()

    def test_counts_by_direct_glob_past_the_500_cap(self, _isolated_home: Path) -> None:
        """The 500-session query cap would hide the oldest files; snapshot must not."""
        for i in range(600):
            _write_session(_isolated_home, f"trace_2026010{i % 9}_{i:04d}", "alpha")
        code, _ = _run("snapshot")
        assert code == 0
        marker = json.loads((_isolated_home / "backups" / ".snapshot-marker.json").read_text())
        assert marker["counts"]["sessions"] == 600

    def test_rerun_does_not_overwrite_prior_backup(self, _isolated_home: Path) -> None:
        _write_session(_isolated_home, "trace_20260101_a", "alpha")
        _run("snapshot")
        _run("snapshot")
        archives = list((_isolated_home / "backups").glob("pre-identity-*.tar.gz"))
        assert len(archives) == 2, "a second snapshot silently overwrote the first"

    def test_archive_excludes_the_backups_dir(self, _isolated_home: Path) -> None:
        """The archive must not nest a copy of prior archives."""
        _write_session(_isolated_home, "trace_20260101_a", "alpha")
        _run("snapshot")
        _run("snapshot")
        newest = max((_isolated_home / "backups").glob("*.tar.gz"), key=lambda p: p.stat().st_mtime)
        with tarfile.open(newest) as tar:
            assert not [n for n in tar.getnames() if "backups" in n], "archive contains the backups dir"


# ── scan ───────────────────────────────────────────────────────────────────


class TestScan:
    def test_groups_drifted_labels_by_canonical_key(self, _isolated_home: Path) -> None:
        _write_session(_isolated_home, "trace_20260101_a", "TRACE")
        _write_session(_isolated_home, "trace_20260101_b", "trace")
        _write_session(_isolated_home, "trace_20260101_c", "Other Project")

        code, _ = _run("scan")
        assert code == 0
        plan = json.loads((_isolated_home / f"identity-plan-{identity_cli._today()}.json").read_text())
        by_key = {p["key"]: p for p in plan["projects"]}
        assert set(by_key) == {"trace", "other-project"}
        assert sorted(by_key["trace"]["aliases"]) == ["TRACE", "trace"]
        assert by_key["trace"]["session_counts"] == {"TRACE": 1, "trace": 1}

    def test_writes_no_registry_state(self, _isolated_home: Path) -> None:
        _write_session(_isolated_home, "trace_20260101_a", "alpha")
        _run("scan")
        assert not (_isolated_home / "projects.json").exists(), "scan must not touch the registry"

    def test_includes_knowledge_store_stems(self, _isolated_home: Path) -> None:
        _write_store(_isolated_home, "gamma", learnings=["x"])
        _run("scan", "-o", str(_isolated_home / "plan.json"))
        plan = json.loads((_isolated_home / "plan.json").read_text())
        gamma = next(p for p in plan["projects"] if p["key"] == "gamma")
        assert gamma["knowledge_stores"] == ["gamma.json"]


# ── apply ──────────────────────────────────────────────────────────────────


class TestApply:
    def _plan(self, home: Path, projects: list[dict]) -> Path:
        path = home / "plan.json"
        path.write_text(json.dumps({"projects": projects}))
        return path

    def test_requires_a_snapshot_marker(self, _isolated_home: Path) -> None:
        plan = self._plan(_isolated_home, [{"key": "alpha", "display_label": "Alpha", "aliases": ["ALPHA"]}])
        code, out = _run("apply", "--plan", str(plan))
        assert code == 1
        assert "snapshot" in out.lower()
        assert not (_isolated_home / "projects.json").exists()

    def test_mints_entries_and_is_idempotent(self, _isolated_home: Path) -> None:
        _run("snapshot")
        plan = self._plan(_isolated_home, [{"key": "alpha", "display_label": "Alpha", "aliases": ["ALPHA", "alpha"]}])

        code1, _ = _run("apply", "--plan", str(plan))
        assert code1 == 0
        pident._reset_registry_cache()
        registry = pident.load_registry(required=False)
        assert registry is not None and "alpha" in registry.projects
        assert sorted(registry.projects["alpha"].aliases) == ["ALPHA", "alpha"]

        code2, out2 = _run("apply", "--plan", str(plan))
        assert code2 == 0 and "0 minted" in out2
        pident._reset_registry_cache()
        registry2 = pident.load_registry(required=False)
        assert registry2 is not None
        assert [h.action for h in registry2.history] == ["mint"], "re-apply mutated the registry"

    def test_refuses_reserved_keys(self, _isolated_home: Path) -> None:
        _run("snapshot")
        plan = self._plan(_isolated_home, [{"key": "auto", "display_label": "auto", "aliases": []}])
        code, out = _run("apply", "--plan", str(plan))
        assert code == 0 and "reserved" in out.lower()
        pident._reset_registry_cache()
        registry = pident.load_registry(required=False)
        assert registry is None or "auto" not in registry.projects


# ── check ──────────────────────────────────────────────────────────────────


class TestCheck:
    def test_clean_when_everything_resolves(self, _isolated_home: Path) -> None:
        _enroll("alpha", display="Alpha", aliases=["ALPHA"])
        _write_session(_isolated_home, "trace_20260101_a", "ALPHA")
        _write_store(_isolated_home, "alpha", learnings=["x"], project="alpha")

        code, out = _run("check")
        assert code == 0 and "clean" in out.lower()

    def test_flags_a_stray_store(self, _isolated_home: Path) -> None:
        _enroll("alpha")
        _write_store(_isolated_home, "orphan", learnings=["x"], project="orphan")
        code, out = _run("check")
        assert code == 1
        assert "orphan" in out and "not backed by a registered key" in out

    def test_flags_an_unknown_session_label(self, _isolated_home: Path) -> None:
        _enroll("alpha", aliases=["ALPHA"])
        _write_session(_isolated_home, "trace_20260101_a", "Totally Unenrolled")
        code, out = _run("check")
        assert code == 1
        assert "Totally Unenrolled" in out

    def test_reports_absent_registry(self, _isolated_home: Path) -> None:
        _, out = _run("check")
        assert "absent" in out.lower()

    def test_fails_closed_on_a_corrupt_registry(self, _isolated_home: Path) -> None:
        (_isolated_home / "projects.json").write_text("{not valid json")
        pident._reset_registry_cache()
        code, out = _run("check")
        assert code == 1 and "unavailable" in out.lower()

    def test_ignores_premerge_and_reserved_stores(self, _isolated_home: Path) -> None:
        _enroll("alpha")
        (_isolated_home / "knowledge" / "alpha.json.premerge-2026-07-23").write_text("{}")
        _write_store(_isolated_home, "auto", learnings=["x"], project="auto")
        code, out = _run("check")
        assert code == 0, f"premerge/reserved wrongly flagged as stray: {out}"


# ── merge-stores ───────────────────────────────────────────────────────────


class TestMergeStores:
    def _prepare(self) -> None:
        _run("snapshot")
        _enroll("trace-mcp", display="trace-mcp", aliases=["TRACE"])

    def test_requires_a_snapshot_marker(self, _isolated_home: Path) -> None:
        _enroll("trace-mcp", aliases=["TRACE"])
        code, out = _run("merge-stores", "--key", "trace-mcp")
        assert code == 1 and "snapshot" in out.lower()

    def test_unions_dedupes_and_keeps_premerge_originals(self, _isolated_home: Path) -> None:
        self._prepare()
        _write_store(_isolated_home, "trace-mcp", learnings=["shared learning", "target only"], project="trace-mcp")
        _write_store(_isolated_home, "trace", learnings=["shared learning", "source only"], project="TRACE")

        code, out = _run("merge-stores", "--key", "trace-mcp")
        assert code == 0, out

        merged = json.loads((_isolated_home / "knowledge" / "trace-mcp.json").read_text())
        contents = {lrn["content"] for lrn in merged["learnings"]}
        assert contents == {"shared learning", "target only", "source only"}, "dedup or union wrong"
        assert (_isolated_home / "knowledge").glob("trace.json.premerge-*"), "source not kept as premerge"
        assert not (_isolated_home / "knowledge" / "trace.json").exists(), "source store not renamed"

    def test_refuses_when_a_lock_is_present(self, _isolated_home: Path) -> None:
        self._prepare()
        _write_store(_isolated_home, "trace-mcp", learnings=["a"], project="trace-mcp")
        _write_store(_isolated_home, "trace", learnings=["b"], project="TRACE")
        (_isolated_home / "knowledge" / "trace.json.lock").write_text("held")

        code, out = _run("merge-stores", "--key", "trace-mcp")
        assert code == 1 and "lock" in out.lower()
        assert (_isolated_home / "knowledge" / "trace.json").exists(), "source touched despite the lock"

    def test_never_merges_auto(self, _isolated_home: Path) -> None:
        self._prepare()
        _write_store(_isolated_home, "auto", learnings=["quarantined"], project="auto")
        _run("merge-stores")
        assert (_isolated_home / "knowledge" / "auto.json").exists()
        assert json.loads((_isolated_home / "knowledge" / "auto.json").read_text())["learnings"], "auto was drained"

    def test_idempotent_and_sweeps_a_reminted_source(self, _isolated_home: Path) -> None:
        """Re-running after a laggard re-mints a raw-label store must re-merge it."""
        self._prepare()
        _write_store(_isolated_home, "trace-mcp", learnings=["a"], project="trace-mcp")
        _write_store(_isolated_home, "trace", learnings=["b"], project="TRACE")
        _run("merge-stores", "--key", "trace-mcp")

        # A laggard server re-creates the raw-label store after the merge.
        import time

        time.sleep(0.01)
        _write_store(_isolated_home, "trace", learnings=["c-new"], project="TRACE")
        code, _ = _run("merge-stores", "--key", "trace-mcp")
        assert code == 0
        merged = json.loads((_isolated_home / "knowledge" / "trace-mcp.json").read_text())
        assert "c-new" in {lrn["content"] for lrn in merged["learnings"]}

    def test_logs_hashes_to_migrations(self, _isolated_home: Path) -> None:
        self._prepare()
        _write_store(_isolated_home, "trace-mcp", learnings=["a"], project="trace-mcp")
        _write_store(_isolated_home, "trace", learnings=["b"], project="TRACE")
        _run("merge-stores", "--key", "trace-mcp")
        lines = [json.loads(x) for x in (_isolated_home / "migrations.jsonl").read_text().splitlines()]
        merge = next(r for r in lines if r.get("op") == "store-merge")
        assert merge["sources"][0]["source_sha256"]
        assert merge["target_after"] == 2


# ── adopt ──────────────────────────────────────────────────────────────────


class TestAdopt:
    def test_adopts_an_auto_session_append_shaped(self, _isolated_home: Path) -> None:
        _write_session(_isolated_home, "trace_20260101_x", "auto", project_key="auto", status="completed")
        code, out = _run("adopt", "trace_20260101_x", "--project", "Real Project", "--reason", "belongs to real")
        assert code == 0, out

        doc = json.loads((_isolated_home / "sessions" / "trace_20260101_x.json").read_text())
        assert doc["metadata"]["project_key"] == "real-project"
        # The captured display label is NEVER rewritten (ADR-006 §7 sanctions
        # stamping the additive key + appending the event, nothing more) — the
        # record keeps showing what was originally captured.
        assert doc["metadata"]["project"] == "auto", "adopt rewrote the captured display label"
        # Append-shaped: exactly one new state_change event recording old→new.
        changes = [e for e in doc["events"] if e["type"] == "state_change"]
        assert len(changes) == 1
        assert changes[0]["state_change"]["old_value"] == "auto"
        assert changes[0]["state_change"]["new_value"] == "real-project"
        # And every identity-aware consumer resolves the session to the target.
        assert pident.session_project_key(doc["metadata"]) == "real-project"

    def test_refuses_a_real_project_session(self, _isolated_home: Path) -> None:
        _write_session(_isolated_home, "trace_20260101_y", "waggle", project_key="waggle")
        code, out = _run("adopt", "trace_20260101_y", "--project", "other")
        assert code == 1 and "not the 'auto'" in out

        doc = json.loads((_isolated_home / "sessions" / "trace_20260101_y.json").read_text())
        assert doc["metadata"]["project_key"] == "waggle", "a real session was relabeled"
        assert not [e for e in doc["events"] if e["type"] == "state_change"]

    def test_refuses_a_reserved_target(self, _isolated_home: Path) -> None:
        _write_session(_isolated_home, "trace_20260101_z", "auto", project_key="auto")
        code, out = _run("adopt", "trace_20260101_z", "--project", "auto")
        assert code == 1 and "reserved" in out.lower()

    def test_missing_session_is_reported(self, _isolated_home: Path) -> None:
        code, out = _run("adopt", "trace_nonexistent", "--project", "real")
        assert code == 1 and "not found" in out.lower()


# ── bundle ─────────────────────────────────────────────────────────────────


class TestBundle:
    def test_contains_only_the_target_project(self, _isolated_home: Path) -> None:
        _enroll("waggle", display="Waggle", aliases=["WAGGLE"])
        _write_session(_isolated_home, "trace_20260101_a", "WAGGLE")  # alias of the target
        _write_session(_isolated_home, "trace_20260101_b", "waggle", project_key="waggle")
        _write_session(_isolated_home, "trace_20260101_c", "other-project")  # foreign
        _write_store(_isolated_home, "waggle", learnings=["w"], project="waggle")

        dest = _isolated_home / "waggle-bundle.tar.gz"
        code, out = _run("bundle", "--project", "waggle", "--bundle", str(dest))
        assert code == 0, out

        with tarfile.open(dest) as tar:
            names = tar.getnames()
        session_names = sorted(n for n in names if n.startswith("sessions/"))
        assert session_names == ["sessions/trace_20260101_a.json", "sessions/trace_20260101_b.json"], names
        assert "knowledge/waggle.json" in names
        assert "project.json" in names

    def test_filters_egress_rows_by_project(self, _isolated_home: Path) -> None:
        _enroll("waggle")
        (_isolated_home / "egress.jsonl").write_text(
            "\n".join(
                json.dumps(r)
                for r in [
                    {"project_key": "waggle", "purpose": "extraction"},
                    {"project": "waggle", "purpose": "matching"},
                    {"project_key": "other", "purpose": "extraction"},
                ]
            )
            + "\n"
        )
        dest = _isolated_home / "b.tar.gz"
        _run("bundle", "--project", "waggle", "--bundle", str(dest))
        with tarfile.open(dest) as tar:
            member = tar.extractfile("egress.jsonl")
            assert member is not None
            rows = [json.loads(x) for x in member.read().decode().splitlines() if x.strip()]
        assert len(rows) == 2, f"foreign egress rows leaked into the bundle: {rows}"
        assert all(r.get("project_key") == "waggle" or r.get("project") == "waggle" for r in rows)


# ── dispatch ───────────────────────────────────────────────────────────────


def test_bundle_egress_path_matches_the_writer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """bundle must read the ledger from the same path the extension writes it to.

    Deriving the ledger from TRACE_SESSIONS_DIR (rather than mirroring the
    writer's resolver) would read a different file whenever the sessions dir is
    off the default root, silently omitting every egress row from the bundle.
    """
    from trace_mcp.extensions.learn.egress import egress_log_path

    monkeypatch.delenv("TRACE_EGRESS_LOG", raising=False)
    monkeypatch.setenv("TRACE_SESSIONS_DIR", str(tmp_path / "elsewhere" / "sessions"))
    assert identity_cli._egress_log_path() == egress_log_path()

    monkeypatch.setenv("TRACE_EGRESS_LOG", str(tmp_path / "custom.jsonl"))
    assert identity_cli._egress_log_path() == egress_log_path()


def test_unknown_subcommand_errors() -> None:
    with pytest.raises(SystemExit):
        identity_cli.main(["not-a-command"])


def test_server_dispatches_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """`trace-mcp identity …` must reach the CLI, not fall through to the server."""
    import trace_mcp.server as server

    called: dict = {}

    def _fake_main(argv: list[str]) -> int:
        called["argv"] = argv
        return 0

    monkeypatch.setattr("trace_mcp.identity_cli.main", _fake_main)
    monkeypatch.setattr(server.sys, "argv", ["trace-mcp", "identity", "check"])
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 0
    assert called["argv"] == ["check"]


# ── adversarial-review regression guards ───────────────────────────────────


class TestReservedQuarantineHandling:
    """The auto quarantine is an expected population, not drift.

    Before these guards, scan proposed a plan entry for the reserved `auto` key
    (which apply then re-refused, confusingly), and check flagged every auto
    session as an unknown label — so a store holding ANY auto session could
    never pass the migration runbook's `identity check` exit-0 criterion.
    """

    def test_scan_excludes_reserved_session_labels(self, _isolated_home: Path) -> None:
        _write_session(_isolated_home, "trace_20260101_a", "auto")
        _write_session(_isolated_home, "trace_20260101_b", "waggle")

        _run("scan", "-o", str(_isolated_home / "plan.json"))
        plan = json.loads((_isolated_home / "plan.json").read_text())
        keys = [p["key"] for p in plan["projects"]]
        assert keys == ["waggle"], f"reserved keys leaked into the plan: {keys}"

    def test_check_passes_with_auto_sessions_present(self, _isolated_home: Path) -> None:
        _enroll("waggle")
        _write_session(_isolated_home, "trace_20260101_a", "auto")
        _write_session(_isolated_home, "trace_20260101_b", "waggle")

        code, out = _run("check")
        assert code == 0, f"the auto quarantine was reported as drift: {out}"


class TestApplyAuditAtomicity:
    def test_failed_apply_logs_no_mint_records(self, _isolated_home: Path) -> None:
        """The migration log must record what HAPPENED, not what was attempted.

        An alias-collision plan makes the registry write fail atomically; a mint
        record for an entry that never persisted would make the repair's own
        audit trail lie.
        """
        _run("snapshot")
        plan = _isolated_home / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "projects": [
                        {"key": "alpha", "display_label": "Alpha", "aliases": ["SHARED-NAME"]},
                        {"key": "beta", "display_label": "Beta", "aliases": ["shared-name"]},
                    ]
                }
            )
        )
        code, out = _run("apply", "--plan", str(plan))
        assert code == 1 and "could not apply" in out

        pident._reset_registry_cache()
        assert pident.load_registry(required=False) is None, "a partial registry was persisted"
        lines = [json.loads(x) for x in (_isolated_home / "migrations.jsonl").read_text().splitlines()]
        mints = [r for r in lines if r.get("op") == "mint"]
        assert mints == [], f"the audit log recorded mints that never persisted: {mints}"


class TestMergeTargetSafety:
    def test_merge_refuses_a_corrupt_target(self, _isolated_home: Path) -> None:
        """A corrupt target must abort the merge, not be silently replaced.

        The non-strict loader hands back a fresh store on corruption; saving the
        union over it would destroy the damaged-but-recoverable original — the
        one file merge-stores keeps no premerge copy of.
        """
        _run("snapshot")
        _enroll("trace-mcp", aliases=["TRACE"])
        corrupt = _isolated_home / "knowledge" / "trace-mcp.json"
        corrupt.write_text("{THIS IS NOT JSON")
        _write_store(_isolated_home, "trace", learnings=["from source"], project="TRACE")

        code, out = _run("merge-stores", "--key", "trace-mcp")
        assert code == 1 and "unreadable" in out
        assert corrupt.read_text() == "{THIS IS NOT JSON", "the corrupt target was overwritten"
        assert (_isolated_home / "knowledge" / "trace.json").exists(), "a source was renamed despite the abort"

    def test_merge_key_resolves_an_alias(self, _isolated_home: Path) -> None:
        _run("snapshot")
        _enroll("trace-mcp", aliases=["TRACE"])
        _write_store(_isolated_home, "trace-mcp", learnings=["a"], project="trace-mcp")
        _write_store(_isolated_home, "trace", learnings=["b"], project="TRACE")

        code, out = _run("merge-stores", "--key", "TRACE")
        assert code == 0, out
        assert "resolved to registered key 'trace-mcp'" in out
        merged = json.loads((_isolated_home / "knowledge" / "trace-mcp.json").read_text())
        assert {lrn["content"] for lrn in merged["learnings"]} == {"a", "b"}

    def test_merge_fails_closed_when_the_target_lock_appears_after_preflight(
        self, _isolated_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The preflight is advisory; the store lock is the guarantee.

        Simulates the race by disabling the preflight and pre-holding the
        target's lock: the merge must abort without touching anything.
        """
        _run("snapshot")
        _enroll("trace-mcp", aliases=["TRACE"])
        _write_store(_isolated_home, "trace-mcp", learnings=["a"], project="trace-mcp")
        source = _write_store(_isolated_home, "trace", learnings=["b"], project="TRACE")
        monkeypatch.setattr(identity_cli, "_preflight_merge", lambda paths, out: True)
        monkeypatch.setenv("TRACE_LOCK_TIMEOUT", "0.2")
        # Pre-hold the target's lock as a live writer would.
        (_isolated_home / "knowledge" / "trace-mcp.json.lock").write_text(f"{__import__('os').getpid()}:1")

        code, out = _run("merge-stores", "--key", "trace-mcp")
        assert code == 1 and "store lock" in out
        assert source.exists(), "a source was renamed despite the held lock"
        merged = json.loads((_isolated_home / "knowledge" / "trace-mcp.json").read_text())
        assert {lrn["content"] for lrn in merged["learnings"]} == {"a"}, "the target was written under a held lock"


class TestScanPlanPreservation:
    def test_scan_refuses_to_overwrite_an_existing_plan(self, _isolated_home: Path) -> None:
        """A plan file holds the human's decisions between scan and apply."""
        _write_session(_isolated_home, "trace_20260101_a", "waggle")
        plan = _isolated_home / "plan.json"
        assert _run("scan", "-o", str(plan))[0] == 0
        plan.write_text('{"projects": [], "HUMAN_EDITS": true}')

        code, out = _run("scan", "-o", str(plan))
        assert code == 1 and "refusing to overwrite" in out
        assert "HUMAN_EDITS" in plan.read_text(), "the edited plan was clobbered"


class TestSnapshotExternalKnowledge:
    def test_external_knowledge_dir_is_archived(
        self, _isolated_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The marker green-lights destructive merges; the backup it attests to
        must actually contain the knowledge the merge will destroy."""
        external = tmp_path / "external-knowledge"
        external.mkdir()
        monkeypatch.setenv("TRACE_KNOWLEDGE_DIR", str(external))
        (external / "waggle.json").write_text(json.dumps({"project": "waggle", "learnings": []}))

        code, _ = _run("snapshot")
        assert code == 0
        marker = json.loads((_isolated_home / "backups" / ".snapshot-marker.json").read_text())
        assert marker["knowledge_dir_external"] is True
        assert marker["counts"]["knowledge_stores"] == 1
        with tarfile.open(marker["archive"]) as tar:
            assert any("waggle.json" in n for n in tar.getnames()), (
                "the external knowledge store was counted but not archived"
            )
