"""Importers that build TRACE sessions from other systems' run records.

Each importer is a pure function from a foreign record to a `Session`: it
reads, maps, and returns, without touching the session store. Persisting the
result is the caller's decision, which keeps an import reviewable before it
lands anywhere.

Exports: `gents` (Gents agent-runtime timelines), `decision_log` (RSI-Exam
decision-gate logs).
"""

from __future__ import annotations

from trace_mcp.importers import decision_log, gents

__all__ = ["decision_log", "gents"]
