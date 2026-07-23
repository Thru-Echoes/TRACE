"""``trace-mcp identity`` — provenance-honest project-identity migration tooling (ADR-006 S6).

Core module (stdlib + core identity types; the learn extension is imported lazily
inside ``merge-stores`` only, per ADR-003). Seven subcommands turn the enforcement
machinery of ADR-006 into an operator can actually run against an existing store
that predates canonical identity:

    snapshot      back up ~/.trace and drop a marker later phases require
    scan          propose a label→key plan (writes nothing)
    apply         mint the human-approved plan into the registry
    check         report drift; non-zero exit on findings
    merge-stores  consolidate an alias group's knowledge stores (reversible)
    adopt         re-home an ``auto`` session into a real project (append-shaped)
    bundle        export one project as a self-contained tarball

Two rules run through all of it, both from ADR-006:

* **Capture records are never rewritten.** ``scan``/``apply`` only build the alias
  index; ``adopt`` is the one session-touching command and it is append-shaped
  (it records old→new as a ``state_change`` event, never an in-place edit).
* **The store is enumerated by direct glob, never ``list_sessions``.** The query
  layer caps at 500 files and would hide the oldest sessions — exactly the ones
  most likely to carry drifted labels.

Nothing here runs automatically. Each command is invoked deliberately; the
destructive ones (``apply``, ``merge-stores``) refuse to run without a snapshot
marker, and ``merge-stores`` additionally refuses while any writer is active.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tarfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trace_mcp import identity_report
from trace_mcp import project_identity as pident

# ── Paths ──────────────────────────────────────────────────────────────────


def _trace_home() -> Path:
    """Root of the TRACE data directory (parent of sessions/knowledge/…).

    Derived from ``TRACE_SESSIONS_DIR`` when set (so tests and non-default
    layouts stay consistent), else ``~/.trace``.
    """
    sessions = os.environ.get("TRACE_SESSIONS_DIR")
    if sessions:
        return Path(sessions).expanduser().parent
    return Path("~/.trace").expanduser()


def _sessions_dir() -> Path:
    return Path(os.environ.get("TRACE_SESSIONS_DIR", str(_trace_home() / "sessions"))).expanduser()


def _migrations_log() -> Path:
    """Append-only log of every identity operation (mint / alias / merge / adopt)."""
    override = os.environ.get("TRACE_MIGRATIONS_LOG")
    return Path(override).expanduser() if override else _trace_home() / "migrations.jsonl"


def _snapshot_marker() -> Path:
    return _trace_home() / "backups" / ".snapshot-marker.json"


# ── Small shared helpers ───────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _append_migration(record: dict[str, Any]) -> None:
    """Append one identity-operation record to the append-only migrations log.

    Side effect: writes ``migrations.jsonl``. Never rewrites existing lines —
    the log is the audit trail of the repair itself.
    """
    log = _migrations_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": _now(), **record}) + "\n")


def _iter_session_files() -> Iterable[Path]:
    """Every session JSON by DIRECT GLOB (not list_sessions — its 500-cap hides the oldest)."""
    d = _sessions_dir()
    if not d.is_dir():
        return []
    return sorted(d.glob("trace_*.json"))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _session_label(data: dict[str, Any]) -> str | None:
    meta = data.get("metadata") or {}
    label = meta.get("project")
    return label if isinstance(label, str) and label.strip() else None


def _require_snapshot(out) -> bool:
    """True if a snapshot marker exists; else print guidance and return False."""
    if _snapshot_marker().is_file():
        return True
    print(
        "Error: no snapshot marker found. Run `trace-mcp identity snapshot` first — "
        "the destructive phases refuse to run without a backup.",
        file=out,
    )
    return False


# ── snapshot ───────────────────────────────────────────────────────────────


def cmd_snapshot(args: argparse.Namespace, out) -> int:
    """Back up the whole TRACE home to a dated tarball and record a manifest + marker.

    Counts are obtained by direct glob so the manifest reflects the true store
    size, not the query layer's capped view. The marker gates every later phase.
    """
    home = _trace_home()
    if not home.is_dir():
        print(f"Error: {home} does not exist; nothing to snapshot.", file=out)
        return 1

    backups = home / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = _today()
    archive = backups / f"pre-identity-{stamp}.tar.gz"
    # Distinct suffix if a snapshot already exists today, so a re-run never
    # silently overwrites an earlier backup.
    n = 1
    while archive.exists():
        archive = backups / f"pre-identity-{stamp}-{n}.tar.gz"
        n += 1

    session_files = list(_iter_session_files())
    knowledge = identity_report.knowledge_dir()
    knowledge_files = sorted(knowledge.glob("*.json")) if knowledge.is_dir() else []
    # A relocated knowledge dir (TRACE_KNOWLEDGE_DIR outside the home) must be
    # archived TOO: the marker this snapshot writes is what green-lights the
    # destructive merge phase, and merge-stores destroys knowledge sources — a
    # backup that counted those stores without containing them would be a lie.
    knowledge_external = knowledge.is_dir() and not knowledge.resolve().is_relative_to(home.resolve())

    with tarfile.open(archive, "w:gz") as tar:
        # Exclude the backups dir itself so the archive never contains prior archives.
        tar.add(home, arcname=home.name, filter=lambda ti: None if "/backups/" in ti.name + "/" else ti)
        if knowledge_external:
            tar.add(knowledge, arcname=f"{home.name}-external-knowledge")

    manifest = {
        "created": _now(),
        "archive": str(archive),
        "archive_sha256": _sha256(archive),
        "counts": {
            "sessions": len(session_files),
            "knowledge_stores": len(knowledge_files),
        },
        "trace_home": str(home),
        "knowledge_dir": str(knowledge),
        "knowledge_dir_external": knowledge_external,
    }
    marker = _snapshot_marker()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(manifest, indent=2) + "\n")
    _append_migration({"op": "snapshot", "archive": str(archive), "counts": manifest["counts"]})

    print(f"Snapshot written: {archive}", file=out)
    print(
        f"  {manifest['counts']['sessions']} sessions, {manifest['counts']['knowledge_stores']} knowledge stores",
        file=out,
    )
    print(f"  marker: {marker}", file=out)
    return 0


# ── scan ───────────────────────────────────────────────────────────────────


def _collect_label_groups() -> dict[str, dict[str, Any]]:
    """Group every distinct raw label (sessions + stores) by canonical key.

    Returns ``{key: {"labels": [...], "session_counts": {label: n}, "stores": [...]}}``.
    Pure read; writes nothing.
    """
    groups: dict[str, dict[str, Any]] = {}

    def _group(key: str) -> dict[str, Any]:
        return groups.setdefault(key, {"labels": [], "session_counts": {}, "stores": []})

    for path in _iter_session_files():
        data = _read_json(path)
        if data is None:
            continue
        label = _session_label(data)
        if label is None:
            continue
        try:
            key = pident.canonical_project_key(label)
        except pident.ProjectKeyError:
            continue
        if key in pident.RESERVED_KEYS:
            # The auto quarantine is an expected population, not drift, and it is
            # never enrolled: a plan entry for it would only be re-refused by
            # `apply` and confuse the operator into thinking it needs deciding.
            continue
        g = _group(key)
        if label not in g["labels"]:
            g["labels"].append(label)
        g["session_counts"][label] = g["session_counts"].get(label, 0) + 1

    knowledge = identity_report.knowledge_dir()
    if knowledge.is_dir():
        for store in sorted(knowledge.glob("*.json")):
            stem = store.stem
            if stem in pident.RESERVED_KEYS:
                continue
            _group(stem)["stores"].append(store.name)

    return groups


def cmd_scan(args: argparse.Namespace, out) -> int:
    """Emit a human-editable label→key plan grouped by canonical key. Writes no registry state.

    Cross-key merge candidates (e.g. a rename pair) are NOT decided here — the
    plan lists each canonical group, and a human edits it before ``apply``.
    """
    groups = _collect_label_groups()
    plan = {
        "generated": _now(),
        "note": (
            "Review before `identity apply`. Each entry mints one canonical key with the "
            "listed labels as aliases. To MERGE two keys (a rename), move one entry's labels "
            "into the other's `aliases` and delete the emptied entry — semantically distinct "
            "projects must be merged by hand, never automatically."
        ),
        "projects": [
            {
                "key": key,
                "display_label": g["labels"][0] if g["labels"] else key,
                "aliases": sorted(set(g["labels"])),
                "session_counts": g["session_counts"],
                "knowledge_stores": g["stores"],
            }
            for key, g in sorted(groups.items())
        ],
    }
    dest = Path(args.output).expanduser() if args.output else _trace_home() / f"identity-plan-{_today()}.json"
    if dest.exists():
        # A plan file is where the human's decisions live between scan and apply.
        # Overwriting one on a re-run would silently destroy those edits.
        print(
            f"Error: {dest} already exists — refusing to overwrite a possibly-edited plan. "
            "Move it aside or pass a different --output path.",
            file=out,
        )
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(plan, indent=2) + "\n")
    print(f"Scan plan written: {dest}", file=out)
    print(f"  {len(plan['projects'])} canonical project group(s) found.", file=out)
    print("  Edit it, then run `identity apply --plan <file>`.", file=out)
    return 0


# ── apply ──────────────────────────────────────────────────────────────────


def cmd_apply(args: argparse.Namespace, out) -> int:
    """Mint a signed-off plan into the registry (idempotent). Requires a snapshot marker."""
    if not _require_snapshot(out):
        return 1
    plan_path = Path(args.plan).expanduser()
    plan = _read_json(plan_path)
    if plan is None or "projects" not in plan:
        print(f"Error: {plan_path} is not a readable identity plan (missing 'projects').", file=out)
        return 1

    minted, updated, skipped = 0, 0, 0
    # Migration records are BUFFERED and appended only after the registry write
    # commits. locked_registry persists on clean exit and discards everything on
    # failure (e.g. an alias-uniqueness violation) — logging inside the block
    # would record mints that never happened, and a migration log that lies is
    # worse than no log at all.
    pending_records: list[dict[str, Any]] = []
    try:
        with pident.locked_registry() as registry:
            for entry in plan["projects"]:
                key = entry.get("key")
                if not isinstance(key, str) or not key:
                    continue
                if key in pident.RESERVED_KEYS:
                    print(f"  refusing reserved key '{key}' — skipped.", file=out)
                    skipped += 1
                    continue
                display = entry.get("display_label") or key
                aliases = sorted({a for a in entry.get("aliases", []) if isinstance(a, str)})
                existing = registry.projects.get(key)
                if existing is None:
                    registry.projects[key] = pident.ProjectEntry(
                        key=key, display_label=display, aliases=aliases, enrolled_by="identity apply"
                    )
                    registry.history.append(
                        pident.RegistryChange(
                            actor="identity apply", action="mint", details={"key": key, "aliases": aliases}
                        )
                    )
                    pending_records.append({"op": "mint", "key": key, "display_label": display, "aliases": aliases})
                    minted += 1
                else:
                    new_aliases = sorted(set(existing.aliases) | set(aliases))
                    if new_aliases != sorted(existing.aliases):
                        existing.aliases = new_aliases
                        registry.history.append(
                            pident.RegistryChange(
                                actor="identity apply", action="alias-add", details={"key": key, "aliases": new_aliases}
                            )
                        )
                        pending_records.append({"op": "alias-add", "key": key, "aliases": new_aliases})
                        updated += 1
                    else:
                        skipped += 1
    except pident.RegistryUnavailableError as exc:
        print(f"Error: registry is unreadable ({exc}); refusing to overwrite it.", file=out)
        return 1
    except (TimeoutError, ValueError) as exc:
        print(f"Error: could not apply plan ({exc}); nothing was written.", file=out)
        return 1
    for record in pending_records:
        _append_migration(record)

    print(f"Applied: {minted} minted, {updated} alias-updated, {skipped} unchanged.", file=out)
    return 0


# ── check ──────────────────────────────────────────────────────────────────


def cmd_check(args: argparse.Namespace, out) -> int:
    """Report identity drift. Exit 0 when clean, 1 when findings exist.

    Findings: knowledge stores not backed by a registered key; session labels
    that resolve to no registered project; and the registry's own health.
    """
    status = identity_report.registry_status()
    findings: list[str] = []

    if status == "unavailable":
        print("Registry: UNAVAILABLE (corrupt or unknown version) — fix before migrating.", file=out)
        return 1
    if status == "absent":
        print("Registry: absent (no projects enrolled yet). Run scan → apply to create it.", file=out)

    registry = None
    try:
        registry = pident.get_registry_cached()
    except pident.RegistryUnavailableError:
        pass  # already reported above

    stray = identity_report.find_stray_stores(registry)
    if stray:
        findings.append(f"{len(stray)} knowledge store(s) not backed by a registered key: {', '.join(stray)}")

    if registry is not None:
        unknown_labels: dict[str, int] = {}
        for path in _iter_session_files():
            data = _read_json(path)
            if data is None:
                continue
            label = _session_label(data)
            if label is None:
                continue
            try:
                if pident.canonical_project_key(label) in pident.RESERVED_KEYS:
                    # The auto quarantine is an expected, permanently-unenrolled
                    # population — flagging it would make a store holding ANY auto
                    # session fail `check` forever, and exit-0 is the migration
                    # runbook's verification criterion.
                    continue
            except pident.ProjectKeyError:
                pass  # degenerate label: fall through and report it as unknown
            if registry.resolve(label) is None:
                unknown_labels[label] = unknown_labels.get(label, 0) + 1
        if unknown_labels:
            shown = ", ".join(f"{lbl!r} ({n})" for lbl, n in sorted(unknown_labels.items()))
            findings.append(f"{len(unknown_labels)} session label(s) resolve to no registered project: {shown}")

    if not findings:
        print("identity check: clean — every store and session label resolves to a registered project.", file=out)
        return 0
    print("identity check: findings", file=out)
    for f in findings:
        print(f"  - {f}", file=out)
    return 1


# ── merge-stores ───────────────────────────────────────────────────────────


def _preflight_merge(paths: list[Path], out) -> bool:
    """Refuse to merge while any writer may be active (fail-closed, physical-first).

    A live lock file, or a store/session touched within the freshness threshold,
    means another process may be mid-write; grafting under it could lose an
    update. Returns True only when the store is quiescent.
    """
    knowledge = identity_report.knowledge_dir()
    for lock in knowledge.glob("*.lock"):
        print(f"Error: knowledge lock present ({lock.name}) — a writer may be active. Aborting.", file=out)
        return False
    threshold = float(os.environ.get("TRACE_MERGE_MIN_AGE_SEC", "5"))
    now = datetime.now(UTC).timestamp()
    for p in paths:
        try:
            age = now - p.stat().st_mtime
        except OSError:
            continue
        if age < threshold:
            print(
                f"Error: {p.name} was modified {age:.1f}s ago (< {threshold}s) — a writer may be active. Aborting.",
                file=out,
            )
            return False
    return True


def cmd_merge_stores(args: argparse.Namespace, out) -> int:
    """Consolidate an alias group's knowledge stores into the canonical-key store.

    Idempotent and reversible: sources are renamed to ``*.json.premerge-<date>``
    (kept forever), the union is deduped by the existing Jaccard machinery, the
    embedding sidecar is regenerated (never migrated), and every merge is logged
    with content hashes. The ``auto`` quarantine is never merged. Requires a
    snapshot marker.

    The learn extension is imported HERE, lazily, so this core module never
    imports it at load time (ADR-003).
    """
    if not _require_snapshot(out):
        return 1
    try:
        from trace_mcp.extensions.learn import store as learn_store
        from trace_mcp.extensions.learn.models import KnowledgeStore
    except ImportError as exc:
        print(f"Error: the trace-learn extension is unavailable ({exc}); cannot merge stores.", file=out)
        return 1

    try:
        registry = pident.get_registry_cached()
    except pident.RegistryUnavailableError as exc:
        print(f"Error: registry is unreadable ({exc}); enroll projects before merging.", file=out)
        return 1
    if registry is None:
        print("Error: no registry yet — run scan → apply before merge-stores.", file=out)
        return 1

    knowledge = identity_report.knowledge_dir()
    if not knowledge.is_dir():
        print("Nothing to merge: no knowledge directory.", file=out)
        return 0

    # Which canonical keys to consolidate: a specific one (resolving an alias or
    # display label to its key, so `--key TRACE` reaches the trace-mcp entry
    # instead of being skipped), or every registered key with aliased sources.
    if args.key:
        resolved = registry.resolve(args.key)
        if resolved is None:
            print(f"Error: '{args.key}' resolves to no registered project.", file=out)
            return 1
        if resolved != args.key:
            print(f"  '{args.key}' resolved to registered key '{resolved}'.", file=out)
        target_keys = [resolved]
    else:
        target_keys = sorted(registry.projects)

    merged_any = False
    for key in target_keys:
        if key in pident.RESERVED_KEYS:
            continue
        entry = registry.projects.get(key)
        if entry is None:
            print(f"  '{key}' is not a registered key — skipped.", file=out)
            continue

        # Source stems = every alias/label/key that canonicalizes to a DIFFERENT
        # on-disk stem than the target and actually has a store file.
        source_paths: list[Path] = []
        for ident in {key, entry.display_label, *entry.aliases}:
            try:
                ident_key = pident.canonical_project_key(ident)
            except pident.ProjectKeyError:
                continue
            if ident_key == key or ident_key in pident.RESERVED_KEYS:
                continue
            candidate = knowledge / f"{ident_key}.json"
            if candidate.is_file() and candidate not in source_paths:
                source_paths.append(candidate)

        if not source_paths:
            continue

        target_path = knowledge / f"{key}.json"
        involved = [*source_paths, *([target_path] if target_path.is_file() else [])]
        if not _preflight_merge(involved, out):
            return 1

        # The preflight is advisory (a writer could arrive between it and the
        # write), so the whole load→union→save→rename span additionally holds
        # the target's own store lock — the same lock every learn writer takes.
        # Acquired AFTER the preflight, whose global lock scan would otherwise
        # refuse on our own lock file. A concurrent holder makes this raise
        # (fail closed) rather than merge under them.
        try:
            with learn_store.project_lock(key):
                # Fail closed on a corrupt TARGET: the non-strict loader would
                # hand back a fresh store, and saving the union over it would
                # replace the damaged-but-recoverable original — the one file
                # this command does not keep a premerge copy of.
                try:
                    target_store = learn_store.load_store(key, strict=True)
                except learn_store.StoreLoadError as exc:
                    print(f"Error: target store for '{key}' is unreadable ({exc}). Repair or move it first.", file=out)
                    return 1
                before_target = len(target_store.learnings)
                merge_records: list[dict[str, Any]] = []
                for src in source_paths:
                    raw = _read_json(src)
                    if raw is None:
                        print(f"  skipping unreadable source {src.name}", file=out)
                        continue
                    src_store = KnowledgeStore.model_validate(raw)
                    added, deduped = 0, 0
                    for lrn in src_store.learnings:
                        if learn_store.find_duplicate(target_store, lrn.content) is not None:
                            deduped += 1
                            continue
                        new = lrn.model_copy(update={"id": target_store.next_learning_id()})
                        target_store.learnings.append(new)
                        added += 1
                    merge_records.append(
                        {
                            "source": src.name,
                            "source_sha256": _sha256(src),
                            "learnings": len(src_store.learnings),
                            "added": added,
                            "deduped": deduped,
                        }
                    )

                # Ensure the merged store declares the canonical key, then persist +
                # regenerate the sidecar (save_store rewrites the .npy from inline
                # vectors). Still under the target lock: the save and the source
                # renames are one unit a concurrent writer must not interleave.
                target_store.project = key
                learn_store.save_store(target_store)
                after_target = len(target_store.learnings)

                # Rename sources to premerge backups (kept forever) — reversible by hand.
                premerges = []
                for src in source_paths:
                    backup = src.with_name(f"{src.name}.premerge-{_today()}")
                    n = 1
                    while backup.exists():
                        backup = src.with_name(f"{src.name}.premerge-{_today()}-{n}")
                        n += 1
                    src.rename(backup)
                    premerges.append(backup.name)
                    # The source's sidecar is a derived artifact — drop it
                    # (regenerated for the target).
                    sidecar = src.with_suffix(".embeddings.npy")
                    if sidecar.exists():
                        sidecar.unlink()
        except TimeoutError as exc:
            print(f"Error: could not acquire the store lock for '{key}' ({exc}). Aborting.", file=out)
            return 1

        _append_migration(
            {
                "op": "store-merge",
                "key": key,
                "target_before": before_target,
                "target_after": after_target,
                "target_sha256": _sha256(target_path),
                "sources": merge_records,
                "premerge_files": premerges,
            }
        )
        print(
            f"  merged {len(source_paths)} store(s) into '{key}.json': {before_target} → {after_target} learnings",
            file=out,
        )
        merged_any = True

    if not merged_any:
        print("Nothing to merge: no aliased source stores found for the target key(s).", file=out)
    return 0


# ── adopt ──────────────────────────────────────────────────────────────────


async def adopt_session(storage, session_id: str, target_key: str, reason: str | None) -> str:
    """Re-home an ``auto`` session into *target_key*, append-shaped (INV-1 writer).

    Records the change as a ``state_change`` event and stamps
    ``metadata.project_key`` — it never edits the session's history in place, so
    the operation survives ADR-005 as an ordinary append. Writes through
    ``locked_disk_session`` against disk truth (INV-1); registered in
    docs/INVARIANTS.md.

    Raises ``ValueError`` if the session does not resolve to ``auto`` (real→real
    re-homing is refused — that would be relabeling a captured attribution).
    """
    from trace_mcp.schema import Actor, StateChangeData, TraceEvent
    from trace_mcp.storage.locked import locked_disk_session

    loaded = await storage.get_session(session_id)
    current_key = pident.session_project_key(loaded.metadata)
    if current_key not in pident.RESERVED_KEYS:
        raise ValueError(
            f"session '{session_id}' resolves to project '{current_key}', not the 'auto' quarantine. "
            "adopt only re-homes unattributed sessions; it will not relabel a real project."
        )

    async with locked_disk_session(storage, session_id, fallback=loaded) as write_session:
        old_project = write_session.metadata.project
        old_key = pident.session_project_key(write_session.metadata)
        event = TraceEvent(
            session_id=session_id,
            type="state_change",
            actor=Actor(type="human", id="identity adopt"),
            state_change=StateChangeData(
                description=f"Adopted unattributed session into project '{target_key}'.",
                field="metadata.project_key",
                old_value=old_key,
                new_value=target_key,
                reason=reason or "identity adopt",
            ),
        )
        event.id = write_session.next_event_id()
        write_session.events.append(event)
        # Stamp the ADDITIVE key only. The captured display label (`project`,
        # here the 'auto' sentinel) is left verbatim: ADR-006 §7 sanctions
        # stamping project_key and appending the state_change, nothing more, and
        # rewriting a captured field is exactly what alias-table-first forbids.
        # Every consumer resolves identity through session_project_key, which
        # prefers the stamp — so the session reads as the target project
        # everywhere while its record still shows what was originally captured.
        write_session.metadata.project_key = target_key
        await storage.update_session(write_session)

    _append_migration(
        {
            "op": "adopt",
            "session_id": session_id,
            "old_project": old_project,
            "old_key": old_key,
            "new_key": target_key,
            "reason": reason,
        }
    )
    return f"Adopted session '{session_id}': '{old_key}' → '{target_key}'."


def cmd_adopt(args: argparse.Namespace, out) -> int:
    """CLI wrapper for ``adopt_session``: resolve the target key, then write."""
    from trace_mcp.storage.json_file import JsonFileStorage

    try:
        target_key = pident.canonical_project_key(args.project)
    except pident.ProjectKeyError as exc:
        print(f"Error: {exc}", file=out)
        return 1
    if target_key in pident.RESERVED_KEYS:
        print(f"Error: '{args.project}' is a reserved key, not an adoptable project.", file=out)
        return 1

    storage = JsonFileStorage()
    try:
        message = asyncio.run(adopt_session(storage, args.session_id, target_key, args.reason))
    except FileNotFoundError:
        print(f"Error: session '{args.session_id}' not found.", file=out)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=out)
        return 1
    print(message, file=out)
    return 0


# ── bundle ─────────────────────────────────────────────────────────────────


def cmd_bundle(args: argparse.Namespace, out) -> int:
    """Export one project as a self-contained tarball (sessions + store + egress rows + registry entry).

    The client-handover payoff of ADR-006 without physical per-project storage:
    selection is by canonical match, so a project's whole footprint travels
    together regardless of which drifted label each artifact was written under.
    """
    try:
        key = pident.canonical_project_key(args.project)
    except pident.ProjectKeyError as exc:
        print(f"Error: {exc}", file=out)
        return 1

    dest = Path(args.bundle).expanduser()
    staged: list[tuple[Path, str]] = []

    # Sessions whose canonical key matches — direct glob, not list_sessions.
    session_count = 0
    for path in _iter_session_files():
        data = _read_json(path)
        if data is None:
            continue
        if pident.session_matches_project(data.get("metadata") or {}, key):
            staged.append((path, f"sessions/{path.name}"))
            session_count += 1

    # Knowledge store + its sidecar.
    knowledge = identity_report.knowledge_dir()
    store = knowledge / f"{key}.json"
    if store.is_file():
        staged.append((store, f"knowledge/{store.name}"))
        sidecar = store.with_suffix(".embeddings.npy")
        if sidecar.is_file():
            staged.append((sidecar, f"knowledge/{sidecar.name}"))

    # Egress rows attributable to this project (filtered copy of the global ledger).
    egress_rows = _project_egress_rows(key)

    # Registry entry for the project (so the recipient can resolve its aliases).
    registry_entry = _registry_entry_json(key)

    if not dest.parent.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w:gz") as tar:
        for src, arcname in staged:
            tar.add(src, arcname=arcname)
        _add_bytes(
            tar, "egress.jsonl", ("\n".join(json.dumps(r) for r in egress_rows) + "\n").encode() if egress_rows else b""
        )
        if registry_entry is not None:
            _add_bytes(tar, "project.json", (json.dumps(registry_entry, indent=2) + "\n").encode())

    print(f"Bundled project '{key}' → {dest}", file=out)
    print(
        f"  {session_count} session(s), {'store' if store.is_file() else 'no store'}, {len(egress_rows)} egress row(s)",
        file=out,
    )
    return 0


def _egress_log_path() -> Path:
    """Resolve the egress ledger EXACTLY as the writer (``egress.egress_log_path``) does.

    Mirrored here rather than imported so this core command never pulls the learn
    extension. Kept byte-for-byte identical to that resolver: ``TRACE_EGRESS_LOG``
    override, else ``~/.trace/egress.jsonl`` — deriving it from ``_trace_home``
    instead would read a different file than the writer wrote whenever
    ``TRACE_SESSIONS_DIR`` points off the default root.
    """
    override = os.environ.get("TRACE_EGRESS_LOG")
    return Path(override).expanduser() if override else Path.home() / ".trace" / "egress.jsonl"


def _project_egress_rows(key: str) -> list[dict[str, Any]]:
    """Egress ledger rows whose project (by key or alias) matches *key*. Read-only."""
    path = _egress_log_path()
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        row_key = row.get("project_key")
        label = row.get("project")
        matched = row_key == key or (isinstance(label, str) and pident.key_for_label(label) == key)
        if matched:
            rows.append(row)
    return rows


def _registry_entry_json(key: str) -> dict[str, Any] | None:
    try:
        registry = pident.get_registry_cached()
    except pident.RegistryUnavailableError:
        return None
    if registry is None:
        return None
    entry = registry.projects.get(key)
    return entry.model_dump(mode="json") if entry is not None else None


def _add_bytes(tar: tarfile.TarFile, arcname: str, payload: bytes) -> None:
    import io

    info = tarfile.TarInfo(name=arcname)
    info.size = len(payload)
    tar.addfile(info, io.BytesIO(payload))


# ── Dispatch ───────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trace-mcp identity", description="Project-identity migration tooling (ADR-006)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("snapshot", help="back up ~/.trace and write the marker later phases require")

    p_scan = sub.add_parser("scan", help="propose a label→key plan (writes nothing)")
    p_scan.add_argument(
        "--output", "-o", default=None, help="plan output path (default: ~/.trace/identity-plan-<date>.json)"
    )

    p_apply = sub.add_parser("apply", help="mint a signed-off plan into the registry")
    p_apply.add_argument("--plan", required=True, help="path to the reviewed plan file")

    sub.add_parser("check", help="report identity drift (non-zero exit on findings)")

    p_merge = sub.add_parser("merge-stores", help="consolidate an alias group's knowledge stores (reversible)")
    p_merge.add_argument("--key", default=None, help="a single canonical key to merge (default: every registered key)")

    p_adopt = sub.add_parser("adopt", help="re-home an 'auto' session into a real project")
    p_adopt.add_argument("session_id", help="the auto session to adopt")
    p_adopt.add_argument("--project", required=True, help="target project name")
    p_adopt.add_argument("--reason", default=None, help="why the session is being adopted")

    p_bundle = sub.add_parser("bundle", help="export one project as a self-contained tarball")
    p_bundle.add_argument("--project", required=True, help="project to export")
    p_bundle.add_argument("--bundle", required=True, help="output tarball path")

    return parser


_COMMANDS = {
    "snapshot": cmd_snapshot,
    "scan": cmd_scan,
    "apply": cmd_apply,
    "check": cmd_check,
    "merge-stores": cmd_merge_stores,
    "adopt": cmd_adopt,
    "bundle": cmd_bundle,
}


def main(argv: list[str] | None = None, out=None) -> int:
    """Entry point for ``trace-mcp identity``. Returns a process exit code."""
    out = out if out is not None else sys.stdout
    parser = build_parser()
    args = parser.parse_args(argv)
    return _COMMANDS[args.command](args, out)
