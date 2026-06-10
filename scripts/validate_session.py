#!/usr/bin/env python3
"""Validate TRACE session JSON files against the schema (compatibility shim).

The validator now lives in the package at ``trace_mcp.validate`` so that
``trace-mcp validate`` works on installed packages (the schema ships as
package data). This shim keeps the long-documented script invocation working
from a source checkout.

Usage:
    python scripts/validate_session.py ~/.trace/sessions/trace_*.json
    uv run trace-mcp validate ~/.trace/sessions/trace_*.json
"""

import sys
from pathlib import Path

try:
    from trace_mcp.validate import main
except ImportError:  # source checkout without an installed package
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from trace_mcp.validate import main

if __name__ == "__main__":
    sys.exit(main())
