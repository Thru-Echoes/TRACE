"""``trace-mcp jev`` commands: install the Gateway bridge and review a session.

Review runs only when a person starts it. Nothing in TRACE calls it automatically.

Exports: ``get_session_review``, ``main``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from trace_mcp import project_identity as pident
from trace_mcp.extensions.jev.bridge import install_bridge
from trace_mcp.extensions.jev.client import (
    EventNotAssessableError,
    Transport,
    _post,
    assess_and_record,
    is_assessable,
)
from trace_mcp.extensions.jev.config import JevConfig, effective_config, load_config
from trace_mcp.extensions.jev.results import get_assessed_event_ids
from trace_mcp.storage.base import TraceStorage
from trace_mcp.storage.json_file import JsonFileStorage

DEFAULT_MAX_EVENTS = 50
_PROJECT_MARKER = Path(".claude") / "trace.project"


def get_directory_project() -> str:
    """Return the project this directory belongs to.

    A key lives in ``.env`` per project, so a review must run from the directory of
    the session's own project. ``TRACE_PROJECT`` takes precedence over the marker file
    that ``trace-mcp-init`` writes.

    Raises:
        ValueError: neither names a project.

    Side effects: reads the environment and the marker file.
    """
    pinned = os.environ.get("TRACE_PROJECT", "").strip()
    if pinned:
        return pinned
    try:
        marked = (Path.cwd() / _PROJECT_MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        marked = ""
    if not marked:
        raise ValueError(f"Run this from a TRACE project directory: no TRACE_PROJECT and no {_PROJECT_MARKER}")
    return marked


def _get_blocked_reason(config: JevConfig) -> str | None:
    """Return why a real run would send nothing, or None if it could send."""
    if config.local_only:
        return "TRACE_LOCAL_ONLY or the project privacy posture forbids Jev egress"
    if not config.enabled:
        return "Jev advisory is disabled; set TRACE_JEV_ENABLED=true"
    if not config.api_key:
        return "Jev advisory is enabled but no provider key is configured"
    return None


async def get_session_review(
    storage: TraceStorage,
    session_id: str,
    config: JevConfig,
    *,
    project: str,
    max_events: int = DEFAULT_MAX_EVENTS,
    dry_run: bool = False,
    transport: Transport = _post,
) -> dict[str, Any]:
    """Assess a session's eligible events that have no saved result for this model.

    An event that can never be sent is skipped and reported. Any other failure stops
    the review, and the summary still reports what was completed.

    Args:
        storage: the session store, read only.
        session_id: the session to review.
        config: Jev configuration, narrowed here by the project's privacy setting.
        project: the project the session must belong to.
        max_events: the most events to send in this run.
        dry_run: report what would be sent without contacting any service.
        transport: the HTTP sender for the direct route.

    Returns:
        A summary with ``eligible``, ``already_assessed``, ``selected``,
        ``truncated``, ``skipped_unresolved_decisions``, ``unsendable``, ``blocked``,
        ``advice_counts``, ``candidates``, ``assessed``, ``failed``, ``saved_to``.

    Raises:
        ValueError: ``max_events`` is not positive.
        FileNotFoundError: the session does not exist.
        ProjectKeyError: ``project`` conflicts with this process's pin.
        ProjectMismatchError: the session belongs to another project.

    Side effects: unless ``dry_run``, sends each selected event's text to the
    configured provider and appends each result to the project's results file.
    """
    if max_events < 1:
        raise ValueError("max_events must be at least 1")
    label = pident.resolve_scoped_project(project)
    project_key = await pident.validate_project_session(storage, label, session_id)
    session = await storage.get_session(session_id)
    effective = effective_config(config, label)
    done = get_assessed_event_ids(project_key, session_id, effective.model)
    eligible = [event for event in session.events if is_assessable(event)]
    pending = [event for event in eligible if event.id not in done]
    selected = pending[:max_events]
    summary: dict[str, Any] = {
        "session_id": session_id,
        "dry_run": dry_run,
        "eligible": len(eligible),
        "already_assessed": len(eligible) - len(pending),
        "selected": len(selected),
        "truncated": len(pending) > len(selected),
        "skipped_unresolved_decisions": sum(
            1 for event in session.events if event.type == "decision" and not is_assessable(event)
        ),
        "unsendable": [],
        "blocked": _get_blocked_reason(effective),
        "advice_counts": {},
        "candidates": [],
        "assessed": 0,
        "failed": None,
        "saved_to": None,
    }
    if dry_run:
        return summary
    advice: Counter[str] = Counter()
    for event in selected:
        try:
            result = await assess_and_record(
                event, effective, project=label, project_key=project_key, transport=transport
            )
        except EventNotAssessableError as exc:
            summary["unsendable"].append({"event_id": event.id, "error": str(exc)})
            continue
        except (ValueError, RuntimeError) as exc:
            summary["failed"] = {"event_id": event.id, "error": str(exc)}
            break
        summary["assessed"] += 1
        advice[result["advice"]] += 1
        summary["saved_to"] = result["saved_to"] or summary["saved_to"]
        if result["advice"] == "candidate_for_human_review":
            summary["candidates"].append(event.id)
    summary["advice_counts"] = dict(advice)
    return summary


def main(argv: list[str]) -> int:
    """Run a ``trace-mcp jev`` command.

    Args:
        argv: arguments after ``jev``.

    Returns:
        0 on success, 1 on a failure, which is reported on stderr.

    Side effects: see ``install_bridge`` and ``get_session_review``; prints JSON.
    """
    parser = argparse.ArgumentParser(prog="trace-mcp jev", description="Experimental Jev advisory commands.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="Install the Node bridge used by the Vercel AI Gateway route.")
    review = commands.add_parser("review", help="Assess a session's logged events. Run from the project directory.")
    review.add_argument("session_id")
    review.add_argument("--max-events", type=int, default=DEFAULT_MAX_EVENTS)
    review.add_argument("--dry-run", action="store_true", help="Report what would be sent and send nothing.")
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            print(json.dumps({"bridge": str(install_bridge())}))
            return 0
        summary = asyncio.run(
            get_session_review(
                JsonFileStorage(),
                args.session_id,
                load_config(),
                project=get_directory_project(),
                max_events=args.max_events,
                dry_run=args.dry_run,
            )
        )
    except (OSError, ValueError, RuntimeError, pident.ProjectMismatchError, pident.RegistryUnavailableError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    if summary["failed"] is not None:
        print(f"Error: stopped at {summary['failed']['event_id']}: {summary['failed']['error']}", file=sys.stderr)
        return 1
    return 0
