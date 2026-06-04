"""Subprocess-coverage bootstrap (test/CI only).

Python automatically imports a module named ``sitecustomize`` at interpreter
startup if one is found on ``sys.path``. The TRACE E2E suite
(``tests/test_e2e_server.py``) spawns the MCP server as a child process via
``python -m trace_mcp.server`` with ``src/`` on ``PYTHONPATH``; this file lets
that child process start measuring coverage so ``server.py`` is not
under-reported (~34% without it, because the parent ``coverage`` run never
sees the child's execution).

``coverage.process_startup()`` is a documented no-op unless the
``COVERAGE_PROCESS_START`` environment variable points at a coverage config
file — so this hook is inert in normal runs and only activates under CI/test
coverage. It is intentionally defensive: any import or runtime failure here
must never break the interpreter (or the server) for ordinary use.

This file lives in ``src/`` (not in the ``trace_mcp`` package) so it is NOT
shipped in the wheel/sdist — it is a dev/CI-only shim. The packaged
distribution never imports it.
"""

from __future__ import annotations

import os

if os.environ.get("COVERAGE_PROCESS_START"):
    try:
        import coverage

        coverage.process_startup()
    except Exception:  # pragma: no cover - coverage absent or misconfigured
        # Coverage is a dev-only dependency; in any environment where it is
        # not installed (or fails to start), silently skip. Never let the
        # coverage shim affect the running program.
        pass
