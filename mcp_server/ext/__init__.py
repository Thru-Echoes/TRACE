"""
TRACE Protocol Extensions

Optional extensions for enhanced functionality:
- claude: Claude-specific features (knowledge_check, checkpoints)
- knowledge: Knowledge management (decisions, learnings, gotchas, ideas)
- reports: Reporting and analysis (trust metrics, publication reports)
"""

from typing import Any

# Extension availability flags
CLAUDE_AVAILABLE = False
KNOWLEDGE_AVAILABLE = False
REPORTS_AVAILABLE = False

# Try to import extensions
try:
    from . import claude

    CLAUDE_AVAILABLE = True
except ImportError:
    claude = None  # type: ignore

try:
    from . import knowledge

    KNOWLEDGE_AVAILABLE = True
except ImportError:
    knowledge = None  # type: ignore

try:
    from . import reports

    REPORTS_AVAILABLE = True
except ImportError:
    reports = None  # type: ignore


def get_available_extensions() -> dict[str, bool]:
    """Get which extensions are available."""
    return {
        "claude": CLAUDE_AVAILABLE,
        "knowledge": KNOWLEDGE_AVAILABLE,
        "reports": REPORTS_AVAILABLE,
    }


def initialize_extensions(trace: dict[str, Any]) -> None:
    """Initialize extension data structures in trace."""
    if "_extensions" not in trace:
        trace["_extensions"] = {}

    if KNOWLEDGE_AVAILABLE and "knowledge" not in trace["_extensions"]:
        trace["_extensions"]["knowledge"] = {
            "decisions": [],
            "learnings": [],
            "gotchas": [],
            "ideas": [],
        }

    if CLAUDE_AVAILABLE and "claude" not in trace["_extensions"]:
        trace["_extensions"]["claude"] = {
            "checkpoints": [],
            "knowledge_checks": [],
        }

    if REPORTS_AVAILABLE and "reports" not in trace["_extensions"]:
        trace["_extensions"]["reports"] = {
            "trust_reports": [],
            "text_analyses": [],
        }


__all__ = [
    "claude",
    "knowledge",
    "reports",
    "CLAUDE_AVAILABLE",
    "KNOWLEDGE_AVAILABLE",
    "REPORTS_AVAILABLE",
    "get_available_extensions",
    "initialize_extensions",
]
