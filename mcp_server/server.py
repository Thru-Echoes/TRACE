#!/usr/bin/env python3
"""
TRACE MCP Server v3.0 - Transparent Research AI Collaboration Environment

A Model Context Protocol server for tracking AI-human collaboration in research.
Provides a standardized audit schema that is:
- Applied across different scientific domains
- Interoperable between different agent frameworks
- Machine-readable for automated compliance checking
- Aligned with MCP's existing logging hooks

v3.0 Changes (MVP Refactor):
- Formal JSON Schema (urn:trace:schema:core:v3)
- Environment capture for reproducibility
- Evaluation logging for tests/benchmarks
- Modular architecture: core/ + ext/ (claude, knowledge, reports)
- Simplified authorship model with direction/execution separation

v2.1 Changes:
- Smart TRACE Triggers: Behavioral triggers for automatic logging prompts
- trace_knowledge_check tool: Validates events, detects types, checks duplicates

v2.0 Changes:
- Authorship model: human_directed vs ai_suggested vs collaborative
- Git integration for [HUMAN-EDIT] detection
- AI suggestion tracking with accept/reject/modify outcomes
- Multi-content-type support: code (lines), text (lines+words), data (rows)

Usage:
    python server.py

Configuration:
    Set TRACE_PATH environment variable to customize trace file location.
    Default: ./trace.json (same directory as server.py)

Requirements:
    pip install mcp anthropic
"""

import asyncio
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    print("ERROR: MCP package not installed. Run: pip install mcp anthropic")
    exit(1)

# Core Module imports (MVP)
CORE_AVAILABLE = False
EnvironmentCapture = None  # type: ignore
EvaluationLogger = None  # type: ignore
MetricsComputer = None  # type: ignore

try:
    from core import (
        EnvironmentCapture,
        EvaluationLogger,  # noqa: F401
        MetricsComputer,  # noqa: F401
        capture_environment,  # noqa: F401
    )
    from core.environment import generate_environment_id

    CORE_AVAILABLE = True
except ImportError:
    pass

# Extension Module imports
EXT_CLAUDE_AVAILABLE = False
EXT_KNOWLEDGE_AVAILABLE = False
EXT_REPORTS_AVAILABLE = False

try:
    from ext.claude import ClaudeExtension

    EXT_CLAUDE_AVAILABLE = True
except ImportError:
    ClaudeExtension = None  # type: ignore

try:
    from ext.knowledge import KnowledgeManager

    EXT_KNOWLEDGE_AVAILABLE = True
except ImportError:
    KnowledgeManager = None  # type: ignore

try:
    from ext.reports import ReportsExtension

    EXT_REPORTS_AVAILABLE = True
except ImportError:
    ReportsExtension = None  # type: ignore

# V&V Module imports
VV_AVAILABLE = False
SnapshotManager = None  # type: ignore
VerificationEngine = None  # type: ignore
GitReconciler = None  # type: ignore
IntegrityChain = None  # type: ignore
TextAnalyzer = None  # type: ignore
TrustCalculator = None  # type: ignore
ReportGenerator = None  # type: ignore

try:
    from vv import (
        GitReconciler,
        IntegrityChain,
        ReportGenerator,
        SnapshotManager,
        TextAnalyzer,
        TrustCalculator,  # noqa: F401
        VerificationEngine,
    )

    VV_AVAILABLE = True
except ImportError:
    pass


# ============================================================
# Configuration
# ============================================================

TRACE_PATH = Path(os.environ.get("TRACE_PATH", Path(__file__).parent / "trace.json"))
HUMAN_EDIT_TAG = "[HUMAN-EDIT]"
server = Server("trace")

# Content type definitions for different project types
CONTENT_TYPES = {
    "code": {"metrics": ["lines", "functions", "classes", "files", "complexity"], "primary_unit": "lines"},
    "text": {"metrics": ["words", "lines", "paragraphs", "characters", "sections"], "primary_unit": "words"},
    "data": {"metrics": ["rows", "columns", "cells", "schemas", "transformations"], "primary_unit": "rows"},
}


# ============================================================
# TRACE Operations
# ============================================================


def load_trace() -> dict:
    """Load TRACE data from file, creating default if not exists."""
    if TRACE_PATH.exists():
        try:
            with open(TRACE_PATH, encoding="utf-8") as f:
                data = json.load(f)
                # Migrate to latest version
                if data.get("schema_version") == "TRACE-1.0":
                    data = migrate_v1_to_v2(data)
                if data.get("schema_version") == "TRACE-2.0":
                    data = migrate_v2_to_v3(data)
                return data
        except json.JSONDecodeError as e:
            print(f"WARNING: Invalid JSON in TRACE file: {e}")
            return create_default_trace()
    return create_default_trace()


def migrate_v1_to_v2(trace: dict) -> dict:
    """Migrate TRACE v1.0 schema to v2.0."""
    trace["schema_version"] = "TRACE-2.0"

    # Add new collections
    if "ai_suggestions" not in trace:
        trace["ai_suggestions"] = []
    if "human_manual_edits" not in trace:
        trace["human_manual_edits"] = []
    if "audit_log" not in trace:
        trace["audit_log"] = []

    # Migrate code contributions to new authorship model
    for cc in trace.get("code_contributions", []):
        if "direction_source" not in cc:
            cc["direction_source"] = "human_directed"  # Default assumption

        old_authorship = cc.get("authorship", {})
        cc["authorship"] = {
            "human_directed": {
                "ai_executed_lines": old_authorship.get("ai_authored_lines", 0),
                "human_executed_lines": old_authorship.get("human_authored_lines", 0),
            },
            "ai_suggested": {
                "accepted_lines": 0,
                "rejected_lines": 0,
                "modified_lines": old_authorship.get("human_improved_ai_lines", 0),
                "modification_description": None,
                "related_suggestion_id": None,
            },
            "human_manual_edit": {"lines_added": 0, "lines_removed": 0, "lines_modified": 0, "git_commits": []},
            "collaborative": {"lines": old_authorship.get("collaborative_lines", 0), "description": None},
        }

    # Update metrics structure
    if "suggestion_metrics" not in trace.get("metrics_summary", {}):
        trace["metrics_summary"]["suggestion_metrics"] = {
            "total_suggestions": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "modified_count": 0,
            "acceptance_rate": None,
            "rejection_rate": None,
            "modification_rate": None,
            "lines_proposed_total": 0,
            "lines_accepted_as_is": 0,
            "lines_modified_by_human": 0,
            "lines_rejected": 0,
            "by_type": {},
        }

    return trace


def migrate_v2_to_v3(trace: dict) -> dict:
    """Migrate TRACE v2.0 schema to v3.0."""
    trace["schema_version"] = "TRACE-3.0"
    trace["schema_uri"] = "urn:trace:schema:core:v3"

    # Add v3.0 collections if missing
    if "environments" not in trace:
        trace["environments"] = []
    if "evaluations" not in trace:
        trace["evaluations"] = []

    # Create aliases for v3.0 naming (contributions/suggestions)
    # These point to the same data for backwards compatibility
    if "contributions" not in trace:
        trace["contributions"] = trace.get("code_contributions", [])
    if "suggestions" not in trace:
        trace["suggestions"] = trace.get("ai_suggestions", [])

    # Initialize extensions structure
    if "_extensions" not in trace:
        trace["_extensions"] = {}

    # Move knowledge items to extensions
    knowledge_ext = trace["_extensions"].setdefault(
        "knowledge",
        {
            "decisions": [],
            "learnings": [],
            "gotchas": [],
            "ideas": [],
        },
    )

    # Move existing decisions/learnings/gotchas/ideas to extensions
    for key in ["decisions", "learnings", "gotchas", "ideas"]:
        if key in trace and trace[key]:
            knowledge_ext[key] = trace[key]

    # Initialize claude extension
    trace["_extensions"].setdefault(
        "claude",
        {
            "checkpoints": [],
            "knowledge_checks": [],
        },
    )

    # Initialize reports extension
    trace["_extensions"].setdefault(
        "reports",
        {
            "trust_reports": [],
            "text_analyses": [],
        },
    )

    return trace


def create_default_trace() -> dict:
    """Create a default TRACE v3.0 structure."""
    return {
        "schema_version": "TRACE-3.0",
        "schema_uri": "urn:trace:schema:core:v3",
        "schema_name": "Transparent Research AI Collaboration Environment",
        "metadata": {
            "project": "Unknown Project",
            "version": "2.0",
            "created": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "description": "Project TRACE log",
            "maintainers": [],
            "research_protocol": {"enabled": True},
            "environment": {},
            "ai_systems": [],
            "git_conventions": {"human_edit_tag": HUMAN_EDIT_TAG, "auto_detect_manual_edits": True},
        },
        "context": {"goals": "", "current_status": "", "key_technologies": []},
        "sessions": [],
        "ai_suggestions": [],
        "human_manual_edits": [],
        "interactions": [],
        "code_contributions": [],
        "ideas": [],
        "errors": [],
        "decisions": [],
        "learnings": [],
        "gotchas": [],
        "patterns": [],
        "experiments": [],
        "validations": [],
        "interventions": [],
        "attributions": [],
        "audit_log": [],
        "metrics_summary": {
            "last_computed": None,
            "code_metrics": {
                "total_lines": {
                    "human_directed_ai_executed": 0,
                    "human_directed_human_executed": 0,
                    "ai_suggested_accepted": 0,
                    "ai_suggested_modified": 0,
                    "human_manual_edit": 0,
                    "collaborative": 0,
                },
                "total_words": {
                    "human_directed_ai_executed": 0,
                    "human_directed_human_executed": 0,
                    "ai_suggested_accepted": 0,
                    "ai_suggested_modified": 0,
                    "human_manual_edit": 0,
                    "collaborative": 0,
                },
                "total_rows": {
                    "human_directed_ai_executed": 0,
                    "human_directed_human_executed": 0,
                    "ai_suggested_accepted": 0,
                    "ai_suggested_modified": 0,
                    "human_manual_edit": 0,
                    "collaborative": 0,
                },
                "by_source": {
                    "human_direction_percentage": None,
                    "ai_suggestion_percentage": None,
                    "human_manual_percentage": None,
                },
                "by_source_words": {
                    "human_direction_percentage": None,
                    "ai_suggestion_percentage": None,
                    "human_manual_percentage": None,
                },
                "by_source_rows": {
                    "human_direction_percentage": None,
                    "ai_suggestion_percentage": None,
                    "human_manual_percentage": None,
                },
                "by_content_type": {"code": 0, "text": 0, "data": 0},
                "git_integration": {
                    "manual_edit_commits_detected": 0,
                    "manual_edit_lines_added": 0,
                    "manual_edit_lines_removed": 0,
                },
            },
            "suggestion_metrics": {
                "total_suggestions": 0,
                "accepted_count": 0,
                "rejected_count": 0,
                "modified_count": 0,
                "acceptance_rate": None,
                "rejection_rate": None,
                "modification_rate": None,
                "lines_proposed_total": 0,
                "lines_accepted_as_is": 0,
                "lines_modified_by_human": 0,
                "lines_rejected": 0,
                "by_type": {},
            },
            "error_metrics": {},
            "idea_metrics": {},
            "intervention_metrics": {},
            "session_metrics": {},
            "validation_metrics": {},
        },
        # v3.0 additions
        "environments": [],  # Execution environments for reproducibility
        "evaluations": [],  # Test/benchmark results
        "contributions": [],  # Alias for code_contributions (v3.0 naming)
        "suggestions": [],  # Alias for ai_suggestions (v3.0 naming)
        "_extensions": {  # Extension data (not part of core schema)
            "knowledge": {
                "decisions": [],
                "learnings": [],
                "gotchas": [],
                "ideas": [],
            },
            "claude": {
                "checkpoints": [],
                "knowledge_checks": [],
            },
            "reports": {
                "trust_reports": [],
                "text_analyses": [],
            },
        },
    }


def save_trace(trace: dict) -> None:
    """Save TRACE data to file with updated timestamp."""
    trace["metadata"]["last_updated"] = datetime.now().isoformat()
    TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACE_PATH, "w", encoding="utf-8") as f:
        json.dump(trace, f, indent=2, ensure_ascii=False)


def generate_id(prefix: str, existing_items: list) -> str:
    """Generate a unique ID for a new entry."""
    existing_ids = [item.get("id", "") for item in existing_items]
    counter = 1
    while f"{prefix}{counter:03d}" in existing_ids:
        counter += 1
    return f"{prefix}{counter:03d}"


def log_audit(trace: dict, operation: str, arguments_hash: str | None = None) -> None:
    """Log an audit entry for the operation."""
    if "audit_log" not in trace:
        trace["audit_log"] = []

    trace["audit_log"].append(
        {
            "timestamp": datetime.now().isoformat(),
            "operation": operation,
            "arguments_hash": arguments_hash,
            "user": None,
            "trace_version": "2.0",
        }
    )


def hash_arguments(arguments: dict) -> str:
    """Create a SHA256 hash of arguments for audit trail."""
    return hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()[:16]


def search_trace(trace: dict, query: str, categories: list[str] | None = None) -> list[dict]:
    """Search TRACE data for matching entries."""
    query_lower = query.lower()
    results = []
    search_categories = categories or [
        "decisions",
        "learnings",
        "gotchas",
        "patterns",
        "experiments",
        "ideas",
        "errors",
        "code_contributions",
        "ai_suggestions",
        "human_manual_edits",
    ]

    for category in search_categories:
        if category not in trace:
            continue
        for item in trace[category]:
            item_text = json.dumps(item).lower()
            if query_lower in item_text:
                results.append({"category": category, "item": item})
    return results


# ============================================================
# Smart Trigger System (v2.1)
# ============================================================

# Trigger patterns for different entry types
TRIGGER_PATTERNS = {
    "gotcha": {
        "keywords": [
            "unexpected",
            "surprising",
            "weird",
            "strange",
            "gotcha",
            "pitfall",
            "silent",
            "silently",
            "fails",
            "failure",
            "bug",
            "issue",
            "problem",
            "workaround",
            "hack",
            "actually",
            "turns out",
            "realized",
            "discovered",
            "doesn't work",
            "didn't work",
            "won't work",
            "broken",
            "breaking",
            "misleading",
            "confusing",
            "undocumented",
            "documentation says",
            "contrary to",
            "despite",
            "even though",
            "however",
            "but actually",
            "careful",
            "watch out",
            "beware",
            "caution",
            "warning",
            "trap",
            "by default",
        ],
        "patterns": [
            r"(?:does|doesn't|won't|can't|shouldn't|wouldn't).*(?:expect|think|assume)",
            r"(?:doesn't|does not|won't|can't).*(?:raise|throw|check|validate|handle)",
            r"(?:silently|quietly).*(?:fail|drop|ignore|skip)",
            r"(?:docs?|documentation).*(?:wrong|incorrect|outdated|misleading)",
            r"(?:must|need to|have to|should).*(?:before|after|first)",
            r"(?:only|unless|except).*(?:works?|fails?)",
        ],
        "description": "Unexpected behavior, pitfalls, or workarounds",
    },
    "decision": {
        "keywords": [
            "decided",
            "decision",
            "chose",
            "chosen",
            "selected",
            "picked",
            "went with",
            "going with",
            "opted",
            "opting",
            "prefer",
            "preferred",
            "trade-off",
            "tradeoff",
            "instead of",
            "rather than",
            "over",
            "because",
            "since",
            "therefore",
            "approach",
            "strategy",
            "architecture",
            "design",
            "pattern",
            "convention",
            "standard",
        ],
        "patterns": [
            r"(?:chose|decided|selected|picked|opted).*(?:over|instead|rather)",
            r"(?:will|going to).*(?:use|implement|adopt)",
            r"(?:trade-?off|balance|weigh).*(?:between|versus|vs)",
            r"(?:approach|strategy|method).*(?:for|to)",
        ],
        "description": "Choices between approaches, trade-offs, architectural decisions",
    },
    "learning": {
        "keywords": [
            "learned",
            "learning",
            "discovered",
            "found out",
            "realized",
            "understand",
            "understood",
            "figured out",
            "turns out",
            "til",
            "insight",
            "aha",
            "interesting",
            "note to self",
            "remember",
            "important to know",
            "key insight",
            "works because",
            "the reason",
            "explains why",
            "makes sense now",
        ],
        "patterns": [
            r"(?:learned|discovered|realized|found).*(?:that|how|why)",
            r"(?:turns out|it seems|apparently).*(?:that|because)",
            r"(?:now|finally).*(?:understand|know|see)",
            r"(?:the|this).*(?:reason|explanation|cause).*(?:is|was)",
        ],
        "description": "New knowledge, insights, or understanding gained",
    },
    "idea": {
        "keywords": [
            "could",
            "might",
            "should",
            "would be nice",
            "what if",
            "idea",
            "suggestion",
            "proposal",
            "opportunity",
            "potential",
            "improve",
            "improvement",
            "optimize",
            "optimization",
            "enhance",
            "better",
            "alternative",
            "another way",
            "consider",
            "maybe",
            "future",
            "later",
            "eventually",
            "todo",
            "fixme",
            "refactor",
        ],
        "patterns": [
            r"(?:could|might|should).*(?:improve|optimize|enhance|refactor)",
            r"(?:would be|it.d be).*(?:nice|good|better|useful)",
            r"(?:what if|how about|consider).*(?:we|using|adding)",
            r"(?:future|later|eventually).*(?:could|should|might)",
        ],
        "description": "Improvement opportunities, optimizations, or alternative approaches",
    },
    "intervention": {
        "keywords": [
            "changed",
            "modified",
            "corrected",
            "fixed",
            "adjusted",
            "override",
            "overrode",
            "replaced",
            "rewrote",
            "edited",
            "instead",
            "rather",
            "different",
            "not quite",
            "almost",
            "tweaked",
            "refined",
            "improved",
            "simplified",
            "removed",
        ],
        "patterns": [
            r"(?:changed|modified|corrected|fixed).*(?:AI|generated|suggested)",
            r"(?:AI|it).*(?:suggested|generated|produced).*(?:but|however)",
            r"(?:had to|needed to).*(?:change|modify|fix|correct|adjust)",
            r"(?:not quite|almost|close but).*(?:right|correct|what)",
        ],
        "description": "Human modifications to AI-generated output",
    },
    "code": {
        "keywords": [
            "created",
            "implemented",
            "wrote",
            "added",
            "built",
            "function",
            "class",
            "module",
            "file",
            "feature",
            "refactored",
            "optimized",
            "fixed",
            "updated",
            "modified",
            "lines",
            "code",
            "script",
            "program",
        ],
        "patterns": [
            r"(?:created|wrote|implemented|added).*(?:function|class|module|file)",
            r"(?:\d+).*(?:lines|functions|classes)",
            r"(?:new|updated|modified).*(?:file|code|implementation)",
        ],
        "description": "Code contributions or modifications",
    },
    "error": {
        "keywords": [
            "error",
            "exception",
            "crash",
            "bug",
            "failure",
            "traceback",
            "stack trace",
            "raised",
            "thrown",
            "typeerror",
            "valueerror",
            "runtimeerror",
            "attributeerror",
            "null",
            "undefined",
            "nan",
            "infinite loop",
            "timeout",
        ],
        "patterns": [
            r"(?:error|exception|crash|failure).*(?:occurred|happened|raised)",
            r"(?:typeerror|valueerror|runtimeerror|keyerror|attributeerror)",
            r"(?:returned|got|received).*(?:null|none|undefined|nan)",
        ],
        "description": "Errors, exceptions, or failures encountered",
    },
}


def calculate_text_similarity(text1: str, text2: str) -> float:
    """Calculate simple text similarity using word overlap (Jaccard similarity)."""
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())

    if not words1 or not words2:
        return 0.0

    intersection = words1.intersection(words2)
    union = words1.union(words2)

    return len(intersection) / len(union) if union else 0.0


def detect_event_type(context: str) -> list[tuple]:
    """
    Detect likely event types from context.
    Returns list of (event_type, confidence, reasoning) tuples sorted by confidence.
    """
    context_lower = context.lower()
    results = []

    for event_type, patterns in TRIGGER_PATTERNS.items():
        score = 0
        matched_keywords = []
        matched_patterns = []

        # Check keywords
        for keyword in patterns["keywords"]:
            if keyword.lower() in context_lower:
                score += 1
                matched_keywords.append(keyword)

        # Check regex patterns
        for pattern in patterns["patterns"]:
            if re.search(pattern, context_lower):
                score += 2  # Patterns are more specific, weight higher
                matched_patterns.append(pattern)

        if score > 0:
            # Normalize score (cap at 1.0, threshold at 5 matches)
            confidence = min(score / 5, 1.0)

            if confidence >= 0.2:  # Only include if at least 20% confident
                confidence_label = "high" if confidence >= 0.6 else "medium" if confidence >= 0.4 else "low"
                reasoning = f"Matched keywords: {matched_keywords[:3]}" if matched_keywords else ""
                results.append((event_type, confidence_label, reasoning, confidence))

    # Sort by raw confidence score (descending)
    results.sort(key=lambda x: x[3], reverse=True)

    # Return without the raw score
    return [(r[0], r[1], r[2]) for r in results]


def find_similar_entries(trace: dict, context: str, event_type: str, threshold: float = 0.3) -> list[dict]:
    """Find similar existing entries in TRACE data."""
    similar = []

    # Map event types to TRACE categories and their text fields
    category_fields = {
        "gotcha": ("gotchas", ["problem", "solution"]),
        "decision": ("decisions", ["decision", "rationale"]),
        "learning": ("learnings", ["learning", "evidence"]),
        "idea": ("ideas", ["idea", "triggered_by"]),
        "intervention": ("interventions", ["ai_output_summary", "human_action"]),
        "code": ("code_contributions", ["description", "file_path"]),
        "error": ("errors", ["description", "resolution_description"]),
    }

    if event_type not in category_fields:
        return []

    category, fields = category_fields[event_type]
    entries = trace.get(category, [])

    for entry in entries:
        # Build text to compare from relevant fields
        entry_text_parts = []
        for field in fields:
            if field in entry and entry[field]:
                entry_text_parts.append(str(entry[field]))

        entry_text = " ".join(entry_text_parts)

        if entry_text:
            similarity = calculate_text_similarity(context, entry_text)
            if similarity >= threshold:
                similar.append(
                    {
                        "id": entry.get("id", "unknown"),
                        "similarity": round(similarity, 2),
                        "entry_preview": entry_text[:200] + "..." if len(entry_text) > 200 else entry_text,
                        "category": category,
                    }
                )

    # Sort by similarity descending
    similar.sort(key=lambda x: x["similarity"], reverse=True)

    return similar[:5]  # Return top 5 matches


def generate_suggested_fields(context: str, event_type: str) -> dict:
    """Generate suggested field values for logging an entry."""
    suggestions = {}

    # Extract potential tags from context
    common_tech_terms = [
        "python",
        "javascript",
        "typescript",
        "react",
        "node",
        "api",
        "database",
        "sql",
        "nosql",
        "redis",
        "docker",
        "kubernetes",
        "aws",
        "gcp",
        "azure",
        "git",
        "testing",
        "pytest",
        "jest",
        "async",
        "sync",
        "cache",
        "auth",
        "pandas",
        "numpy",
        "tensorflow",
        "pytorch",
        "ml",
        "ai",
        "data",
    ]
    context_lower = context.lower()
    potential_tags = [term for term in common_tech_terms if term in context_lower]

    if event_type == "gotcha":
        # Try to split context into problem and solution
        solution_indicators = ["solution:", "fix:", "workaround:", "instead", "should", "need to", "must"]
        problem_part = context
        solution_part = ""

        for indicator in solution_indicators:
            if indicator in context_lower:
                idx = context_lower.find(indicator)
                problem_part = context[:idx].strip()
                solution_part = context[idx:].strip()
                break

        suggestions = {
            "problem": problem_part if problem_part else context,
            "solution": solution_part if solution_part else "TODO: Add solution",
            "severity": "medium",
            "tags": potential_tags[:5],
        }

    elif event_type == "decision":
        suggestions = {
            "decision": context,
            "rationale": "TODO: Add rationale",
            "proposed_by": "collaborative",
            "tags": potential_tags[:5],
        }

    elif event_type == "learning":
        suggestions = {
            "learning": context,
            "evidence": "TODO: Add evidence",
            "confidence": "medium",
            "discovered_by": "collaborative",
            "tags": potential_tags[:5],
        }

    elif event_type == "idea":
        # Detect idea type
        idea_type = "approach"
        if any(word in context_lower for word in ["optimize", "faster", "performance", "speed"]):
            idea_type = "optimization"
        elif any(word in context_lower for word in ["feature", "add", "new", "implement"]):
            idea_type = "feature"
        elif any(word in context_lower for word in ["refactor", "clean", "simplify"]):
            idea_type = "design"

        suggestions = {"idea": context, "idea_type": idea_type, "source": "collaborative"}

    elif event_type == "intervention":
        suggestions = {
            "intervention_type": "refinement",
            "ai_output_summary": "TODO: Describe AI output",
            "human_action": context,
            "significance": "minor",
        }

    elif event_type == "code":
        suggestions = {
            "file_path": "TODO: Add file path",
            "contribution_type": "modification",
            "description": context,
            "direction_source": "human_directed",
        }

    elif event_type == "error":
        # Try to detect severity
        severity = "medium"
        if any(word in context_lower for word in ["critical", "crash", "data loss", "security"]):
            severity = "critical"
        elif any(word in context_lower for word in ["major", "breaks", "blocking"]):
            severity = "high"
        elif any(word in context_lower for word in ["minor", "cosmetic", "typo"]):
            severity = "low"

        suggestions = {
            "description": context,
            "error_type": "logic",
            "severity": severity,
            "originated_from": "ai",
            "detected_by": "human",
        }

    return suggestions


def knowledge_check(trace: dict, context: str, event_type: str | None = None, check_duplicates: bool = True) -> dict:
    """
    Main knowledge check function.
    Analyzes context, detects event type, checks for duplicates, and returns recommendations.
    """
    result = {
        "should_log": False,
        "recommended_types": [],
        "confidence": "low",
        "reasoning": "",
        "similar_entries": [],
        "suggested_fields": {},
    }

    # Detect event types if not specified
    if event_type and event_type != "auto":
        detected_types = [(event_type, "high", "User specified")]
    else:
        detected_types = detect_event_type(context)

    if not detected_types:
        result["reasoning"] = "No trigger patterns matched. Context may not warrant logging."
        return result

    # Primary recommendation
    primary_type, confidence, reasoning = detected_types[0]
    result["confidence"] = confidence
    result["reasoning"] = reasoning if reasoning else TRIGGER_PATTERNS.get(primary_type, {}).get("description", "")

    # Check for duplicates
    if check_duplicates:
        similar = find_similar_entries(trace, context, primary_type)
        result["similar_entries"] = similar

        # If very similar entry exists, suggest not logging
        if similar and similar[0]["similarity"] >= 0.7:
            result["should_log"] = False
            result["reasoning"] = (
                f"Very similar entry already exists (ID: {similar[0]['id']}, similarity: {similar[0]['similarity']})"
            )
            return result

    # Should log if we detected types and no close duplicates
    result["should_log"] = True
    result["recommended_types"] = [t[0] for t in detected_types[:3]]  # Top 3 types

    # Generate suggested fields for primary type
    result["suggested_fields"] = {primary_type: generate_suggested_fields(context, primary_type)}

    # Also generate for secondary type if different
    if len(detected_types) > 1:
        secondary_type = detected_types[1][0]
        result["suggested_fields"][secondary_type] = generate_suggested_fields(context, secondary_type)

    return result


# ============================================================
# Checkpoint and Knowledge Persistence (v2.1)
# ============================================================


def analyze_session_for_checkpoint(trace: dict, session_id: str, files_touched: list[str] | None = None) -> dict:
    """
    Analyze a session to identify potential unlogged knowledge.
    Returns checkpoint analysis with recommendations.
    """
    session = None
    for s in trace.get("sessions", []):
        if s["id"] == session_id:
            session = s
            break

    if not session:
        return {"error": f"Session {session_id} not found"}

    # Get all entries from this session
    session_learnings = [entry for entry in trace.get("learnings", []) if entry.get("session_id") == session_id]
    session_decisions = [entry for entry in trace.get("decisions", []) if entry.get("session_id") == session_id]
    session_gotchas = [g for g in trace.get("gotchas", []) if g.get("session_id") == session_id]
    session_code = [c for c in trace.get("code_contributions", []) if c.get("session_id") == session_id]
    session_suggestions = [s for s in trace.get("ai_suggestions", []) if s.get("session_id") == session_id]
    session_ideas = [i for i in trace.get("ideas", []) if i.get("session_id") == session_id]
    session_interventions = [i for i in trace.get("interventions", []) if i.get("session_id") == session_id]

    # Find pending suggestions (proposed but not resolved)
    pending_suggestions = [s for s in session_suggestions if s.get("outcome", {}).get("status") == "pending"]

    # Calculate session duration
    started = datetime.fromisoformat(session["started"])
    duration_minutes = int((datetime.now() - started).total_seconds() / 60)

    # Estimate unlogged items based on session activity
    estimated_unlogged = {
        "decisions": max(0, (duration_minutes // 20) - len(session_decisions)),  # Expect ~1 decision per 20 min
        "learnings": max(0, (duration_minutes // 30) - len(session_learnings)),  # Expect ~1 learning per 30 min
        "code_contributions": len(files_touched or []) - len(session_code) if files_touched else 0,
    }

    # Generate prompts based on what's missing
    prompts = []
    recommendations = []

    if pending_suggestions:
        for sug in pending_suggestions:
            prompts.append(f"Pending suggestion: {sug['suggestion']['description'][:60]}...")
            recommendations.append(f"Resolve suggestion {sug['id']} (accept/reject/modify)")

    if estimated_unlogged["decisions"] > 0:
        prompts.append(f"~{estimated_unlogged['decisions']} decisions may be unlogged")
        recommendations.append("Review recent work for architectural or approach decisions")

    if estimated_unlogged["learnings"] > 0:
        prompts.append(f"~{estimated_unlogged['learnings']} learnings may be unlogged")
        recommendations.append("Consider what new knowledge was gained this session")

    if files_touched and estimated_unlogged["code_contributions"] > 0:
        unlogged_files = [f for f in files_touched if not any(c.get("file_path") == f for c in session_code)]
        if unlogged_files:
            prompts.append(f"Files not logged: {', '.join(unlogged_files[:3])}")
            recommendations.append(f"Log code contributions for: {', '.join(unlogged_files[:3])}")

    if duration_minutes > 45 and not session_gotchas:
        prompts.append("No gotchas logged in 45+ minute session")
        recommendations.append("Consider if any unexpected behaviors were encountered")

    return {
        "session_id": session_id,
        "session_duration_minutes": duration_minutes,
        "entries_logged": {
            "learnings": len(session_learnings),
            "decisions": len(session_decisions),
            "gotchas": len(session_gotchas),
            "code_contributions": len(session_code),
            "ideas": len(session_ideas),
            "interventions": len(session_interventions),
        },
        "pending_suggestions": len(pending_suggestions),
        "estimated_unlogged": estimated_unlogged,
        "prompts": prompts,
        "recommendations": recommendations,
    }


def refresh_context_for_topics(
    trace: dict, topics: list[str], include_recent: bool = True, max_items: int = 5, categories: list[str] | None = None
) -> dict:
    """
    Find relevant past knowledge based on topics.
    Returns categorized relevant entries.
    """
    if categories is None:
        categories = ["gotchas", "decisions", "learnings", "patterns"]

    results = {cat: [] for cat in categories}
    topics_lower = [t.lower() for t in topics]

    for category in categories:
        entries = trace.get(category, [])

        for entry in entries:
            # Calculate relevance score
            entry_text = json.dumps(entry).lower()
            score = 0

            # Check topic matches
            for topic in topics_lower:
                if topic in entry_text:
                    score += 2

            # Check tag matches
            entry_tags = [t.lower() for t in entry.get("tags", [])]
            for topic in topics_lower:
                if any(topic in tag for tag in entry_tags):
                    score += 3  # Tag matches are more relevant

            if score > 0:
                results[category].append({"entry": entry, "relevance_score": score})

        # Sort by relevance and limit
        results[category].sort(key=lambda x: x["relevance_score"], reverse=True)
        results[category] = results[category][:max_items]

    # Add recent entries if requested
    if include_recent:
        recent_cutoff = datetime.now().isoformat()[:10]  # Today's date
        for category in categories:
            recent = [
                e
                for e in trace.get(category, [])
                if e.get("timestamp", "")[:10] == recent_cutoff and e not in [r["entry"] for r in results[category]]
            ]
            for entry in recent[:2]:  # Add up to 2 recent per category
                results[category].append({"entry": entry, "relevance_score": 1, "recent": True})
            # Re-apply limit after adding recent entries
            results[category] = results[category][:max_items]

    return results


def consolidate_session_learnings(trace: dict, session_id: str, auto_link: bool = True) -> dict:
    """
    Consolidate and link related entries from a session.
    Returns consolidation summary.
    """
    # Get all entries from this session
    session_entries = {
        "learnings": [entry for entry in trace.get("learnings", []) if entry.get("session_id") == session_id],
        "decisions": [entry for entry in trace.get("decisions", []) if entry.get("session_id") == session_id],
        "gotchas": [g for g in trace.get("gotchas", []) if g.get("session_id") == session_id],
        "ideas": [i for i in trace.get("ideas", []) if i.get("session_id") == session_id],
        "code_contributions": [c for c in trace.get("code_contributions", []) if c.get("session_id") == session_id],
    }

    total_entries = sum(len(v) for v in session_entries.values())
    links_created = 0
    clusters = []

    if auto_link and total_entries > 1:
        # Flatten all entries with their category
        all_entries = []
        for category, entries in session_entries.items():
            for entry in entries:
                all_entries.append({"category": category, "entry": entry, "id": entry.get("id", "unknown")})

        # Find related entries using text similarity
        for i, entry1 in enumerate(all_entries):
            entry1_text = json.dumps(entry1["entry"]).lower()

            for _j, entry2 in enumerate(all_entries[i + 1 :], i + 1):
                entry2_text = json.dumps(entry2["entry"]).lower()

                similarity = calculate_text_similarity(entry1_text, entry2_text)

                if similarity >= 0.25:  # Related threshold
                    # Add link to both entries (in actual data)
                    if "related_to" not in entry1["entry"]:
                        entry1["entry"]["related_to"] = []
                    if entry2["id"] not in entry1["entry"]["related_to"]:
                        entry1["entry"]["related_to"].append(entry2["id"])
                        links_created += 1

                    if "related_to" not in entry2["entry"]:
                        entry2["entry"]["related_to"] = []
                    if entry1["id"] not in entry2["entry"]["related_to"]:
                        entry2["entry"]["related_to"].append(entry1["id"])

        # Identify clusters (groups of related entries)
        visited = set()
        for entry in all_entries:
            if entry["id"] in visited:
                continue

            cluster = [entry["id"]]
            visited.add(entry["id"])

            # Find all related entries
            to_check = entry["entry"].get("related_to", [])[:]
            while to_check:
                related_id = to_check.pop()
                if related_id in visited:
                    continue
                visited.add(related_id)
                cluster.append(related_id)

                # Find the related entry and add its relations
                for e in all_entries:
                    if e["id"] == related_id:
                        to_check.extend(e["entry"].get("related_to", []))
                        break

            if len(cluster) > 1:
                clusters.append(cluster)

    # Generate summary
    summary = {
        "session_id": session_id,
        "total_entries": total_entries,
        "by_category": {k: len(v) for k, v in session_entries.items()},
        "links_created": links_created,
        "clusters_found": len(clusters),
        "clusters": clusters[:5],  # Top 5 clusters
        "tags_used": [],
    }

    # Collect all tags used
    all_tags = set()
    for entries in session_entries.values():
        for entry in entries:
            all_tags.update(entry.get("tags", []))
    summary["tags_used"] = list(all_tags)[:20]

    return summary


def compute_knowledge_metrics(trace: dict) -> dict:
    """Compute knowledge-specific metrics."""
    learnings = trace.get("learnings", [])
    decisions = trace.get("decisions", [])
    gotchas = trace.get("gotchas", [])
    ideas = trace.get("ideas", [])

    # Count totals
    total_entries = {
        "learnings": len(learnings),
        "gotchas": len(gotchas),
        "decisions": len(decisions),
        "ideas": len(ideas),
    }

    # Count by tag
    all_tags = {}
    for entries in [learnings, decisions, gotchas, ideas]:
        for entry in entries:
            for tag in entry.get("tags", []):
                all_tags[tag] = all_tags.get(tag, 0) + 1

    # Sort tags by frequency
    by_tag = dict(sorted(all_tags.items(), key=lambda x: x[1], reverse=True)[:20])

    # Calculate staleness (based on timestamp)
    now = datetime.now()
    fresh_30d = 0
    aging_90d = 0
    stale_180d = 0

    for entries in [learnings, decisions, gotchas, ideas]:
        for entry in entries:
            ts = entry.get("timestamp", "")
            if ts:
                try:
                    entry_date = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    age_days = (now - entry_date.replace(tzinfo=None)).days
                    if age_days <= 30:
                        fresh_30d += 1
                    elif age_days <= 90:
                        aging_90d += 1
                    else:
                        stale_180d += 1
                except (ValueError, TypeError):
                    pass

    # Calculate linkage
    entries_with_links = 0
    total_links = 0
    for entries in [learnings, decisions, gotchas, ideas]:
        for entry in entries:
            links = entry.get("related_to", [])
            if links:
                entries_with_links += 1
                total_links += len(links)

    total_knowledge = sum(total_entries.values())
    orphan_entries = total_knowledge - entries_with_links

    return {
        "total_entries": total_entries,
        "by_tag": by_tag,
        "staleness": {"fresh_30d": fresh_30d, "aging_90d": aging_90d, "stale_180d": stale_180d},
        "linkage": {
            "entries_with_links": entries_with_links,
            "orphan_entries": orphan_entries,
            "total_links": total_links,
            "avg_links_per_entry": round(total_links / total_knowledge, 2) if total_knowledge > 0 else 0,
        },
    }


# ============================================================
# Git Integration
# ============================================================


def scan_git_for_human_edits(since: str = "1 week ago") -> list[dict]:
    """Parse git log for [HUMAN-EDIT] commits."""
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "--oneline",
                f"--since={since}",
                f"--grep={HUMAN_EDIT_TAG}",
                "--numstat",
                "--format=%H|%s|%an|%ai",
            ],
            capture_output=True,
            text=True,
            cwd=TRACE_PATH.parent,
        )

        if result.returncode != 0:
            return []

        commits = []
        current_commit = None
        lines = result.stdout.strip().split("\n")

        for line in lines:
            if not line:
                continue
            if "|" in line and len(line.split("|")) >= 4:
                # New commit line
                if current_commit:
                    commits.append(current_commit)
                parts = line.split("|")
                current_commit = {
                    "hash": parts[0],
                    "message": parts[1],
                    "author": parts[2],
                    "date": parts[3],
                    "files_changed": [],
                    "lines_added": 0,
                    "lines_removed": 0,
                }
            elif current_commit and "\t" in line:
                # File stat line (additions, deletions, filename)
                parts = line.split("\t")
                if len(parts) >= 3:
                    added = int(parts[0]) if parts[0].isdigit() else 0
                    removed = int(parts[1]) if parts[1].isdigit() else 0
                    current_commit["files_changed"].append(parts[2])
                    current_commit["lines_added"] += added
                    current_commit["lines_removed"] += removed

        if current_commit:
            commits.append(current_commit)

        return commits

    except Exception as e:
        print(f"Git scan error: {e}")
        return []


# ============================================================
# Metrics Computation (v2.0)
# ============================================================


def compute_metrics(trace: dict) -> dict:
    """Compute all metrics from TRACE data (v2.1)."""
    metrics = {
        "last_computed": datetime.now().isoformat(),
        "code_metrics": compute_code_metrics_v2(trace),
        "suggestion_metrics": compute_suggestion_metrics(trace),
        "error_metrics": compute_error_metrics(trace),
        "idea_metrics": compute_idea_metrics(trace),
        "intervention_metrics": compute_intervention_metrics(trace),
        "session_metrics": compute_session_metrics(trace),
        "validation_metrics": compute_validation_metrics(trace),
        "knowledge_metrics": compute_knowledge_metrics(trace),
    }
    return metrics


def compute_code_metrics_v2(trace: dict) -> dict:
    """Compute contribution metrics with v2.0 authorship model. Supports code, text, and data content types."""
    contributions = trace.get("code_contributions", [])
    human_edits = trace.get("human_manual_edits", [])

    # Initialize totals for lines (all content types)
    total_human_directed_ai = 0
    total_human_directed_human = 0
    total_ai_suggested_accepted = 0
    total_ai_suggested_modified = 0
    total_human_manual = 0
    total_collaborative = 0

    # Initialize totals for words (text content type)
    total_human_directed_ai_words = 0
    total_human_directed_human_words = 0
    total_ai_suggested_accepted_words = 0
    total_ai_suggested_modified_words = 0
    total_human_manual_words = 0
    total_collaborative_words = 0

    # Initialize totals for rows (data content type)
    total_human_directed_ai_rows = 0
    total_human_directed_human_rows = 0
    total_ai_suggested_accepted_rows = 0
    total_ai_suggested_modified_rows = 0
    total_human_manual_rows = 0
    total_collaborative_rows = 0

    # Counts by content type
    by_content_type = {"code": 0, "text": 0, "data": 0}

    for c in contributions:
        auth = c.get("authorship", {})
        content_type = c.get("content_type", "code")
        by_content_type[content_type] = by_content_type.get(content_type, 0) + 1

        # Human directed (lines)
        hd = auth.get("human_directed", {})
        total_human_directed_ai += hd.get("ai_executed_lines", 0)
        total_human_directed_human += hd.get("human_executed_lines", 0)

        # AI suggested (lines)
        ai_sug = auth.get("ai_suggested", {})
        total_ai_suggested_accepted += ai_sug.get("accepted_lines", 0)
        total_ai_suggested_modified += ai_sug.get("modified_lines", 0)

        # Human manual edit (lines)
        hme = auth.get("human_manual_edit", {})
        total_human_manual += hme.get("lines_added", 0) + hme.get("lines_modified", 0)

        # Collaborative (lines)
        collab = auth.get("collaborative", {})
        total_collaborative += collab.get("lines", 0)

        # Text content type - word tracking
        if content_type == "text":
            total_human_directed_ai_words += hd.get("ai_executed_words", 0)
            total_human_directed_human_words += hd.get("human_executed_words", 0)
            total_ai_suggested_accepted_words += ai_sug.get("accepted_words", 0)
            total_ai_suggested_modified_words += ai_sug.get("modified_words", 0)
            total_human_manual_words += hme.get("words_added", 0)
            total_collaborative_words += collab.get("words", 0)

        # Data content type - row tracking
        if content_type == "data":
            total_human_directed_ai_rows += hd.get("ai_executed_rows", 0)
            total_human_directed_human_rows += hd.get("human_executed_rows", 0)
            total_ai_suggested_accepted_rows += ai_sug.get("accepted_rows", 0)
            total_ai_suggested_modified_rows += ai_sug.get("modified_rows", 0)
            total_human_manual_rows += hme.get("rows_added", 0)
            total_collaborative_rows += collab.get("rows", 0)

    # Add human manual edits from separate collection
    git_lines_added = 0
    git_lines_removed = 0
    for he in human_edits:
        git_lines_added += he.get("lines_added", 0)
        git_lines_removed += he.get("lines_removed", 0)
        total_human_manual += he.get("lines_added", 0)

    # Calculate percentages (lines)
    total_lines = (
        total_human_directed_ai
        + total_human_directed_human
        + total_ai_suggested_accepted
        + total_ai_suggested_modified
        + total_human_manual
        + total_collaborative
    )

    human_direction_total = total_human_directed_ai + total_human_directed_human
    ai_suggestion_total = total_ai_suggested_accepted + total_ai_suggested_modified

    # Calculate word totals
    total_words = (
        total_human_directed_ai_words
        + total_human_directed_human_words
        + total_ai_suggested_accepted_words
        + total_ai_suggested_modified_words
        + total_human_manual_words
        + total_collaborative_words
    )

    human_direction_words = total_human_directed_ai_words + total_human_directed_human_words
    ai_suggestion_words = total_ai_suggested_accepted_words + total_ai_suggested_modified_words

    # Calculate row totals
    total_rows = (
        total_human_directed_ai_rows
        + total_human_directed_human_rows
        + total_ai_suggested_accepted_rows
        + total_ai_suggested_modified_rows
        + total_human_manual_rows
        + total_collaborative_rows
    )

    human_direction_rows = total_human_directed_ai_rows + total_human_directed_human_rows
    ai_suggestion_rows = total_ai_suggested_accepted_rows + total_ai_suggested_modified_rows

    return {
        "total_lines": {
            "human_directed_ai_executed": total_human_directed_ai,
            "human_directed_human_executed": total_human_directed_human,
            "ai_suggested_accepted": total_ai_suggested_accepted,
            "ai_suggested_modified": total_ai_suggested_modified,
            "human_manual_edit": total_human_manual,
            "collaborative": total_collaborative,
        },
        "total_words": {
            "human_directed_ai_executed": total_human_directed_ai_words,
            "human_directed_human_executed": total_human_directed_human_words,
            "ai_suggested_accepted": total_ai_suggested_accepted_words,
            "ai_suggested_modified": total_ai_suggested_modified_words,
            "human_manual_edit": total_human_manual_words,
            "collaborative": total_collaborative_words,
        },
        "total_rows": {
            "human_directed_ai_executed": total_human_directed_ai_rows,
            "human_directed_human_executed": total_human_directed_human_rows,
            "ai_suggested_accepted": total_ai_suggested_accepted_rows,
            "ai_suggested_modified": total_ai_suggested_modified_rows,
            "human_manual_edit": total_human_manual_rows,
            "collaborative": total_collaborative_rows,
        },
        "by_source": {
            "human_direction_percentage": round(human_direction_total / total_lines * 100, 2)
            if total_lines > 0
            else None,
            "ai_suggestion_percentage": round(ai_suggestion_total / total_lines * 100, 2) if total_lines > 0 else None,
            "human_manual_percentage": round(total_human_manual / total_lines * 100, 2) if total_lines > 0 else None,
        },
        "by_source_words": {
            "human_direction_percentage": round(human_direction_words / total_words * 100, 2)
            if total_words > 0
            else None,
            "ai_suggestion_percentage": round(ai_suggestion_words / total_words * 100, 2) if total_words > 0 else None,
            "human_manual_percentage": round(total_human_manual_words / total_words * 100, 2)
            if total_words > 0
            else None,
        },
        "by_source_rows": {
            "human_direction_percentage": round(human_direction_rows / total_rows * 100, 2) if total_rows > 0 else None,
            "ai_suggestion_percentage": round(ai_suggestion_rows / total_rows * 100, 2) if total_rows > 0 else None,
            "human_manual_percentage": round(total_human_manual_rows / total_rows * 100, 2) if total_rows > 0 else None,
        },
        "by_content_type": by_content_type,
        "git_integration": {
            "manual_edit_commits_detected": len(human_edits),
            "manual_edit_lines_added": git_lines_added,
            "manual_edit_lines_removed": git_lines_removed,
        },
    }


def compute_suggestion_metrics(trace: dict) -> dict:
    """Compute AI suggestion metrics."""
    suggestions = trace.get("ai_suggestions", [])

    total = len(suggestions)
    accepted = sum(1 for s in suggestions if s.get("outcome", {}).get("status") == "accepted")
    rejected = sum(1 for s in suggestions if s.get("outcome", {}).get("status") == "rejected")
    modified = sum(1 for s in suggestions if s.get("outcome", {}).get("status") == "modified")

    lines_proposed = sum(s.get("suggestion", {}).get("scope", {}).get("lines_proposed", 0) for s in suggestions)
    lines_accepted = sum(s.get("outcome", {}).get("lines_final", {}).get("accepted_as_is", 0) for s in suggestions)
    lines_modified = sum(s.get("outcome", {}).get("lines_final", {}).get("modified_by_human", 0) for s in suggestions)
    lines_rejected = sum(s.get("outcome", {}).get("lines_final", {}).get("rejected", 0) for s in suggestions)

    # By type breakdown
    by_type = {}
    for s in suggestions:
        stype = s.get("suggestion", {}).get("type", "unknown")
        if stype not in by_type:
            by_type[stype] = {"proposed": 0, "accepted": 0, "rejected": 0, "modified": 0}
        by_type[stype]["proposed"] += 1
        status = s.get("outcome", {}).get("status")
        if status in by_type[stype]:
            by_type[stype][status] += 1

    return {
        "total_suggestions": total,
        "accepted_count": accepted,
        "rejected_count": rejected,
        "modified_count": modified,
        "acceptance_rate": round(accepted / total, 3) if total > 0 else None,
        "rejection_rate": round(rejected / total, 3) if total > 0 else None,
        "modification_rate": round(modified / total, 3) if total > 0 else None,
        "lines_proposed_total": lines_proposed,
        "lines_accepted_as_is": lines_accepted,
        "lines_modified_by_human": lines_modified,
        "lines_rejected": lines_rejected,
        "by_type": by_type,
    }


def compute_error_metrics(trace: dict) -> dict:
    """Compute error detection metrics."""
    errors = trace.get("errors", [])

    ai_errors_human_caught = sum(
        1
        for e in errors
        if e.get("source", {}).get("originated_from") == "ai" and e.get("detection", {}).get("detected_by") == "human"
    )

    human_errors_ai_caught = sum(
        1
        for e in errors
        if e.get("source", {}).get("originated_from") == "human" and e.get("detection", {}).get("detected_by") == "ai"
    )

    ai_errors_total = sum(1 for e in errors if e.get("source", {}).get("originated_from") == "ai")
    human_errors_total = sum(1 for e in errors if e.get("source", {}).get("originated_from") == "human")

    return {
        "ai_errors_caught_by_human": ai_errors_human_caught,
        "human_errors_caught_by_ai": human_errors_ai_caught,
        "total_ai_errors": ai_errors_total,
        "total_human_errors": human_errors_total,
        "ai_error_rate": ai_errors_total / (ai_errors_total + human_errors_total)
        if (ai_errors_total + human_errors_total) > 0
        else None,
        "total_errors": len(errors),
    }


def compute_idea_metrics(trace: dict) -> dict:
    """Compute idea contribution metrics."""
    ideas = trace.get("ideas", [])

    ai_ideas = [i for i in ideas if i.get("origin", {}).get("source") == "ai_suggested"]
    human_ideas = [i for i in ideas if i.get("origin", {}).get("source") == "human"]

    ai_accepted = sum(1 for i in ai_ideas if i.get("outcome", {}).get("adopted") is True)
    ai_rejected = sum(1 for i in ai_ideas if i.get("outcome", {}).get("adopted") is False)
    ai_modified = sum(1 for i in ai_ideas if i.get("outcome", {}).get("modification_description"))

    return {
        "total_ai_ideas": len(ai_ideas),
        "total_human_ideas": len(human_ideas),
        "ai_ideas_accepted": ai_accepted,
        "ai_ideas_rejected": ai_rejected,
        "ai_ideas_modified": ai_modified,
        "ai_idea_acceptance_rate": ai_accepted / len(ai_ideas) if ai_ideas else None,
        "ai_idea_rejection_rate": ai_rejected / len(ai_ideas) if ai_ideas else None,
        "total_ideas": len(ideas),
    }


def compute_intervention_metrics(trace: dict) -> dict:
    """Compute human intervention metrics."""
    interventions = trace.get("interventions", [])

    corrections = sum(1 for i in interventions if i.get("intervention_type") == "correction")
    overrides = sum(1 for i in interventions if i.get("intervention_type") == "override")
    rejections = sum(1 for i in interventions if i.get("intervention_type") == "rejection")

    interactions = trace.get("interactions", [])

    return {
        "total_interventions": len(interventions),
        "corrections": corrections,
        "overrides": overrides,
        "rejections": rejections,
        "intervention_rate": len(interventions) / len(interactions) if interactions else None,
    }


def compute_session_metrics(trace: dict) -> dict:
    """Compute session metrics."""
    sessions = trace.get("sessions", [])
    interactions = trace.get("interactions", [])

    durations = [s.get("duration_minutes") for s in sessions if s.get("duration_minutes")]

    return {
        "total_sessions": len(sessions),
        "total_time_minutes": sum(durations) if durations else 0,
        "avg_session_duration": sum(durations) / len(durations) if durations else None,
        "avg_interactions_per_session": len(interactions) / len(sessions) if sessions else None,
    }


def compute_validation_metrics(trace: dict) -> dict:
    """Compute validation metrics."""
    validations = trace.get("validations", [])

    passed = sum(1 for v in validations if v.get("result") == "passed")
    failed = sum(1 for v in validations if v.get("result") == "failed")

    return {
        "total_validations": len(validations),
        "passed": passed,
        "failed": failed,
        "pass_rate": passed / len(validations) if validations else None,
    }


# ============================================================
# MCP Tool Definitions
# ============================================================


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available TRACE tools."""
    return [
        # Query and Context Tools
        Tool(
            name="trace_query",
            description="Search TRACE data for relevant information. Use this BEFORE starting any task to check for existing context.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Categories to search (default: all)",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="trace_get_context",
            description="Get the full TRACE context including project info, recent entries, and computed metrics.",
            inputSchema={
                "type": "object",
                "properties": {
                    "include_metrics": {"type": "boolean", "description": "Include computed metrics (default: true)"}
                },
            },
        ),
        # Session Management
        Tool(
            name="trace_start_session",
            description="Start a new tracking session. Call at the beginning of each work session.",
            inputSchema={
                "type": "object",
                "properties": {
                    "purpose": {"type": "string", "description": "What will be worked on"},
                    "scientific_stage": {
                        "type": "string",
                        "enum": [
                            "exploration",
                            "hypothesis",
                            "data_collection",
                            "analysis",
                            "interpretation",
                            "validation",
                            "writing",
                        ],
                        "description": "Stage of the scientific method",
                    },
                    "ai_model": {
                        "type": "string",
                        "description": "AI model being used (e.g., claude-opus-4-5-20251101)",
                    },
                },
                "required": ["purpose"],
            },
        ),
        Tool(
            name="trace_end_session",
            description="End the current tracking session with summary and reflection.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session ID to end"},
                    "summary": {"type": "string", "description": "Summary of what was accomplished"},
                    "reflection": {"type": "string", "description": "Reflection on the session"},
                    "ai_helpfulness_rating": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 5,
                        "description": "Rating of AI helpfulness (1-5)",
                    },
                },
                "required": ["session_id"],
            },
        ),
        # v3.0: Environment Capture
        Tool(
            name="trace_capture_environment",
            description="Capture execution environment for reproducibility. Auto-called at session start, but can be called manually.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session to associate environment with"},
                    "agent_name": {"type": "string", "description": "AI agent/model name"},
                    "agent_framework": {"type": "string", "description": "Agent framework (mcp, langchain, etc.)"},
                    "agent_parameters": {
                        "type": "object",
                        "description": "Model parameters (temperature, etc.)",
                    },
                },
            },
        ),
        # v3.0: Evaluation Logging
        Tool(
            name="trace_log_evaluation",
            description="Log test results, benchmarks, or other evaluations. Use after running tests or benchmarks.",
            inputSchema={
                "type": "object",
                "properties": {
                    "evaluation_type": {
                        "type": "string",
                        "enum": [
                            "unit_test",
                            "integration_test",
                            "benchmark",
                            "human_eval",
                            "code_review",
                            "validation",
                        ],
                        "description": "Type of evaluation",
                    },
                    "session_id": {"type": "string", "description": "Session ID"},
                    "target_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Files being evaluated",
                    },
                    "target_description": {"type": "string", "description": "Description of what's being evaluated"},
                    "tests_passed": {"type": "integer", "description": "Number of tests passed"},
                    "tests_failed": {"type": "integer", "description": "Number of tests failed"},
                    "coverage": {"type": "number", "description": "Code coverage (0-1)"},
                    "duration_ms": {"type": "integer", "description": "Duration in milliseconds"},
                    "tool": {"type": "string", "description": "Tool used (pytest, jest, etc.)"},
                    "command": {"type": "string", "description": "Command used to run evaluation"},
                    "output_summary": {"type": "string", "description": "Summary of output"},
                    "passed": {"type": "boolean", "description": "Whether evaluation passed overall"},
                },
                "required": ["evaluation_type"],
            },
        ),
        # AI Suggestion Tracking
        Tool(
            name="trace_log_suggestion",
            description="Log an AI suggestion. Use this when AI proposes something (code, approach, etc.) that the human will decide to accept, reject, or modify.",
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "What AI suggested"},
                    "suggestion_type": {
                        "type": "string",
                        "enum": [
                            "code_change",
                            "architecture",
                            "approach",
                            "bugfix",
                            "optimization",
                            "refactor",
                            "feature",
                        ],
                        "description": "Type of suggestion",
                    },
                    "lines_proposed": {"type": "integer", "description": "Lines of code/change proposed"},
                    "files_affected": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Files that would be affected",
                    },
                    "ai_confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": "How confident AI is in this suggestion",
                    },
                    "what_prompted": {"type": "string", "description": "What prompted this suggestion"},
                    "session_id": {"type": "string", "description": "Current session ID"},
                },
                "required": ["description", "suggestion_type"],
            },
        ),
        Tool(
            name="trace_resolve_suggestion",
            description="Record the outcome of a previously logged AI suggestion (accepted, rejected, or modified).",
            inputSchema={
                "type": "object",
                "properties": {
                    "suggestion_id": {"type": "string", "description": "ID of the suggestion"},
                    "status": {
                        "type": "string",
                        "enum": ["accepted", "rejected", "modified"],
                        "description": "What happened to the suggestion",
                    },
                    "lines_accepted_as_is": {"type": "integer", "description": "Lines accepted without changes"},
                    "lines_modified": {"type": "integer", "description": "Lines accepted but modified by human"},
                    "lines_rejected": {"type": "integer", "description": "Lines not used at all"},
                    "human_rationale": {"type": "string", "description": "Why human made this decision"},
                    "modification_description": {"type": "string", "description": "If modified, what changed"},
                },
                "required": ["suggestion_id", "status"],
            },
        ),
        # Code/Content Contribution Tracking (v2.0)
        Tool(
            name="trace_log_code",
            description="Log a contribution with v2.0 authorship breakdown. Supports multiple content types: 'code' (tracks lines), 'text' (tracks lines AND words), 'data' (tracks rows).",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the file"},
                    "content_type": {
                        "type": "string",
                        "enum": ["code", "text", "data"],
                        "description": "Type of content: 'code' (scripts, programs), 'text' (papers, docs), 'data' (csv, datasets). Default: 'code'",
                    },
                    "contribution_type": {
                        "type": "string",
                        "enum": ["creation", "modification", "refactor", "bugfix", "optimization"],
                        "description": "Type of contribution",
                    },
                    "description": {"type": "string", "description": "What this contribution does"},
                    "direction_source": {
                        "type": "string",
                        "enum": ["human_directed", "ai_suggested", "collaborative"],
                        "description": "Who decided this change should happen",
                    },
                    # Human-directed breakdown (lines - all types)
                    "human_directed_ai_executed_lines": {
                        "type": "integer",
                        "description": "Lines written by AI based on human direction",
                    },
                    "human_directed_human_executed_lines": {
                        "type": "integer",
                        "description": "Lines written by human based on human direction",
                    },
                    # Human-directed breakdown (words - text type)
                    "human_directed_ai_executed_words": {
                        "type": "integer",
                        "description": "(Text only) Words written by AI based on human direction",
                    },
                    "human_directed_human_executed_words": {
                        "type": "integer",
                        "description": "(Text only) Words written by human based on human direction",
                    },
                    # Human-directed breakdown (rows - data type)
                    "human_directed_ai_executed_rows": {
                        "type": "integer",
                        "description": "(Data only) Rows added/modified by AI based on human direction",
                    },
                    "human_directed_human_executed_rows": {
                        "type": "integer",
                        "description": "(Data only) Rows added/modified by human based on human direction",
                    },
                    # AI-suggested breakdown (lines)
                    "ai_suggested_accepted_lines": {
                        "type": "integer",
                        "description": "Lines from AI suggestion accepted as-is",
                    },
                    "ai_suggested_modified_lines": {
                        "type": "integer",
                        "description": "Lines from AI suggestion that human modified",
                    },
                    "ai_suggested_rejected_lines": {
                        "type": "integer",
                        "description": "Lines AI proposed but human rejected",
                    },
                    # AI-suggested breakdown (words - text type)
                    "ai_suggested_accepted_words": {
                        "type": "integer",
                        "description": "(Text only) Words from AI suggestion accepted as-is",
                    },
                    "ai_suggested_modified_words": {
                        "type": "integer",
                        "description": "(Text only) Words from AI suggestion that human modified",
                    },
                    "ai_suggested_rejected_words": {
                        "type": "integer",
                        "description": "(Text only) Words AI proposed but human rejected",
                    },
                    # AI-suggested breakdown (rows - data type)
                    "ai_suggested_accepted_rows": {
                        "type": "integer",
                        "description": "(Data only) Rows from AI suggestion accepted as-is",
                    },
                    "ai_suggested_modified_rows": {
                        "type": "integer",
                        "description": "(Data only) Rows from AI suggestion that human modified",
                    },
                    "ai_suggested_rejected_rows": {
                        "type": "integer",
                        "description": "(Data only) Rows AI proposed but human rejected",
                    },
                    # Human manual edit (lines)
                    "human_manual_edit_lines": {
                        "type": "integer",
                        "description": "Lines human edited directly (outside AI session)",
                    },
                    # Human manual edit (words - text type)
                    "human_manual_edit_words": {
                        "type": "integer",
                        "description": "(Text only) Words human edited directly (outside AI session)",
                    },
                    # Human manual edit (rows - data type)
                    "human_manual_edit_rows": {
                        "type": "integer",
                        "description": "(Data only) Rows human edited directly (outside AI session)",
                    },
                    # Collaborative
                    "collaborative_lines": {
                        "type": "integer",
                        "description": "Lines from back-and-forth collaboration",
                    },
                    "collaborative_words": {
                        "type": "integer",
                        "description": "(Text only) Words from back-and-forth collaboration",
                    },
                    "collaborative_rows": {
                        "type": "integer",
                        "description": "(Data only) Rows from back-and-forth collaboration",
                    },
                    # Git integration
                    "git_commit": {"type": "string", "description": "Git commit hash if available"},
                    "has_human_edit_tag": {
                        "type": "boolean",
                        "description": "Whether commit message contains [HUMAN-EDIT]",
                    },
                    "session_id": {"type": "string", "description": "Current session ID"},
                    "related_suggestion_id": {
                        "type": "string",
                        "description": "If this resulted from an AI suggestion",
                    },
                },
                "required": ["file_path", "contribution_type", "description", "direction_source"],
            },
        ),
        # NEW: Git Integration
        Tool(
            name="trace_scan_git_commits",
            description="Scan git commits for [HUMAN-EDIT] tags and auto-log human manual edits.",
            inputSchema={
                "type": "object",
                "properties": {
                    "since": {"type": "string", "description": "Git date filter (e.g., '1 week ago', '2024-01-01')"},
                    "session_id": {"type": "string", "description": "Session to associate commits with"},
                },
            },
        ),
        # Idea Tracking
        Tool(
            name="trace_log_idea",
            description="Log an idea with its origin (AI/human) and eventual outcome.",
            inputSchema={
                "type": "object",
                "properties": {
                    "idea": {"type": "string", "description": "Description of the idea"},
                    "idea_type": {
                        "type": "string",
                        "enum": ["approach", "hypothesis", "optimization", "feature", "design", "analysis"],
                        "description": "Type of idea",
                    },
                    "source": {
                        "type": "string",
                        "enum": ["ai_suggested", "human", "collaborative"],
                        "description": "Who originated the idea",
                    },
                    "triggered_by": {"type": "string", "description": "What prompted this idea"},
                    "session_id": {"type": "string", "description": "Current session ID"},
                },
                "required": ["idea", "source"],
            },
        ),
        Tool(
            name="trace_evaluate_idea",
            description="Record the evaluation/outcome of a previously logged idea.",
            inputSchema={
                "type": "object",
                "properties": {
                    "idea_id": {"type": "string", "description": "ID of the idea to evaluate"},
                    "adopted": {"type": "boolean", "description": "Whether the idea was adopted"},
                    "rejection_reason": {"type": "string", "description": "If rejected, why"},
                    "modification_description": {"type": "string", "description": "If modified, what changed"},
                    "evaluation_notes": {"type": "string", "description": "Additional evaluation notes"},
                },
                "required": ["idea_id", "adopted"],
            },
        ),
        # Error Tracking
        Tool(
            name="trace_log_error",
            description="Log an error with its source (AI/human) and who caught it.",
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "What the error was"},
                    "error_type": {
                        "type": "string",
                        "enum": ["syntax", "logic", "runtime", "design", "security", "performance"],
                        "description": "Type of error",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                        "description": "Error severity",
                    },
                    "originated_from": {
                        "type": "string",
                        "enum": ["ai", "human"],
                        "description": "Who produced the error",
                    },
                    "detected_by": {
                        "type": "string",
                        "enum": ["ai", "human", "automated_test"],
                        "description": "Who/what caught the error",
                    },
                    "detection_method": {
                        "type": "string",
                        "enum": ["code_review", "testing", "runtime", "static_analysis"],
                        "description": "How it was detected",
                    },
                    "resolution_description": {"type": "string", "description": "How it was fixed"},
                    "session_id": {"type": "string", "description": "Current session ID"},
                    "file_path": {"type": "string", "description": "File where error occurred"},
                },
                "required": ["description", "originated_from", "detected_by"],
            },
        ),
        # Intervention Tracking
        Tool(
            name="trace_log_intervention",
            description="Log when human modifies, corrects, or overrides AI output.",
            inputSchema={
                "type": "object",
                "properties": {
                    "intervention_type": {
                        "type": "string",
                        "enum": ["correction", "override", "rejection", "refinement"],
                        "description": "Type of intervention",
                    },
                    "ai_output_summary": {"type": "string", "description": "What AI produced"},
                    "human_action": {"type": "string", "description": "What human changed"},
                    "rationale": {"type": "string", "description": "Why human made this change"},
                    "expertise_applied": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Types of expertise applied (e.g., domain_knowledge, methodology)",
                    },
                    "significance": {
                        "type": "string",
                        "enum": ["critical", "major", "minor"],
                        "description": "Significance of the intervention",
                    },
                    "lines_affected": {"type": "integer", "description": "Number of lines affected by intervention"},
                    "session_id": {"type": "string", "description": "Current session ID"},
                },
                "required": ["intervention_type", "ai_output_summary", "human_action"],
            },
        ),
        # Standard Knowledge Management
        Tool(
            name="trace_add_decision",
            description="Record a decision with rationale and provenance.",
            inputSchema={
                "type": "object",
                "properties": {
                    "decision": {"type": "string", "description": "What was decided"},
                    "rationale": {"type": "string", "description": "Why this decision was made"},
                    "alternatives_considered": {"type": "string", "description": "Other options considered"},
                    "proposed_by": {
                        "type": "string",
                        "enum": ["human", "ai_suggested", "collaborative"],
                        "description": "Who proposed this decision",
                    },
                    "related_suggestion_id": {
                        "type": "string",
                        "description": "If this decision came from an AI suggestion",
                    },
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization"},
                },
                "required": ["decision", "rationale"],
            },
        ),
        Tool(
            name="trace_add_learning",
            description="Record a learning or finding with evidence.",
            inputSchema={
                "type": "object",
                "properties": {
                    "learning": {"type": "string", "description": "What was learned"},
                    "evidence": {"type": "string", "description": "Evidence supporting this learning"},
                    "discovered_by": {
                        "type": "string",
                        "enum": ["human", "ai_suggested", "collaborative"],
                        "description": "Who discovered this",
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": "Confidence level",
                    },
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags"},
                },
                "required": ["learning"],
            },
        ),
        Tool(
            name="trace_add_gotcha",
            description="Record a pitfall or gotcha with its solution.",
            inputSchema={
                "type": "object",
                "properties": {
                    "problem": {"type": "string", "description": "The pitfall or problem"},
                    "solution": {"type": "string", "description": "How to solve or avoid it"},
                    "discovered_by": {
                        "type": "string",
                        "enum": ["human", "ai", "collaborative"],
                        "description": "Who discovered this",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                        "description": "Severity",
                    },
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags"},
                },
                "required": ["problem", "solution"],
            },
        ),
        # Smart Triggers (v2.1)
        Tool(
            name="trace_knowledge_check",
            description="Check if an event should be logged and find similar existing entries. Use this to validate triggers and avoid duplicates.",
            inputSchema={
                "type": "object",
                "properties": {
                    "context": {"type": "string", "description": "Description of what happened or was discovered"},
                    "event_type": {
                        "type": "string",
                        "enum": ["gotcha", "decision", "learning", "idea", "intervention", "code", "error", "auto"],
                        "description": "Hint about the type of event (default: auto-detect)",
                    },
                    "check_duplicates": {
                        "type": "boolean",
                        "description": "Whether to check for similar existing entries (default: true)",
                    },
                },
                "required": ["context"],
            },
        ),
        # Checkpoints and Knowledge Persistence (v2.1)
        Tool(
            name="trace_checkpoint",
            description="Run a session checkpoint to review progress and identify unlogged knowledge. Call periodically (every 30-45 min) or at milestones.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Current session ID"},
                    "trigger": {
                        "type": "string",
                        "enum": ["time", "milestone", "context_switch", "break", "problem_solved", "session_end"],
                        "description": "What triggered this checkpoint",
                    },
                    "notes": {"type": "string", "description": "Optional notes about current progress"},
                    "files_touched": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Files modified since last checkpoint",
                    },
                },
                "required": ["session_id", "trigger"],
            },
        ),
        Tool(
            name="trace_context_refresh",
            description="Refresh context with relevant past knowledge at session start. Surfaces related gotchas, decisions, learnings based on topics.",
            inputSchema={
                "type": "object",
                "properties": {
                    "topics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Topics/keywords to search for relevant past knowledge",
                    },
                    "include_recent": {
                        "type": "boolean",
                        "description": "Include recent entries regardless of topic match (default: true)",
                    },
                    "max_items": {
                        "type": "integer",
                        "description": "Maximum items to return per category (default: 5)",
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Categories to search (default: gotchas, decisions, learnings, patterns)",
                    },
                },
                "required": ["topics"],
            },
        ),
        Tool(
            name="trace_consolidate_learnings",
            description="Consolidate and link related entries from a session. Call at session end to organize knowledge.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session to consolidate"},
                    "auto_link": {
                        "type": "boolean",
                        "description": "Automatically detect and link related entries (default: true)",
                    },
                    "generate_summary": {
                        "type": "boolean",
                        "description": "Generate a knowledge summary for the session (default: true)",
                    },
                },
                "required": ["session_id"],
            },
        ),
        # Metrics
        Tool(
            name="trace_get_metrics",
            description="Get computed metrics for AI-human collaboration analysis.",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "all",
                            "code",
                            "suggestions",
                            "errors",
                            "ideas",
                            "interventions",
                            "sessions",
                            "knowledge",
                        ],
                        "description": "Which metrics to retrieve (default: all)",
                    }
                },
            },
        ),
        Tool(
            name="trace_compute_metrics",
            description="Recompute all metrics from TRACE data. Call periodically to update summary.",
            inputSchema={"type": "object", "properties": {}},
        ),
        # Attribution
        Tool(
            name="trace_add_attribution",
            description="Add formal attribution for an artifact (for citation/credit purposes).",
            inputSchema={
                "type": "object",
                "properties": {
                    "artifact_description": {"type": "string", "description": "What was created"},
                    "artifact_type": {
                        "type": "string",
                        "enum": ["code", "analysis", "figure", "table", "text", "model"],
                        "description": "Type of artifact",
                    },
                    "ai_contribution_percentage": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                        "description": "Estimated AI contribution %",
                    },
                    "ai_contribution_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Types of AI contribution (e.g., code_generation, suggestions)",
                    },
                    "human_contribution_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Types of human contribution (e.g., design, validation)",
                    },
                    "citation_text": {"type": "string", "description": "Suggested citation text"},
                },
                "required": ["artifact_description", "artifact_type"],
            },
        ),
        # Export
        Tool(
            name="trace_export_report",
            description="Export a summary report of all TRACE data for publication/documentation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["json", "markdown", "summary"],
                        "description": "Export format",
                    }
                },
            },
        ),
        # ============================================================
        # V&V (Verification & Validation) Tools
        # ============================================================
        Tool(
            name="trace_snapshot",
            description="Capture file state for verification. Creates compressed snapshots with SHA-256 hashes. Auto-triggered at session start and before contributions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of file paths to snapshot",
                    },
                    "trigger": {
                        "type": "string",
                        "enum": ["session_start", "pre_contribution", "pre_suggestion", "manual", "checkpoint"],
                        "description": "What triggered this snapshot",
                    },
                    "session_id": {"type": "string", "description": "Current session ID"},
                    "related_entry_id": {
                        "type": "string",
                        "description": "Related TRACE entry ID (e.g., code contribution)",
                    },
                },
                "required": ["files", "trigger"],
            },
        ),
        Tool(
            name="trace_verify",
            description="Verify TRACE claims match actual file changes. Compares logged line/word counts against diffs with configurable tolerance.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entry_id": {"type": "string", "description": "Entry ID to verify (e.g., CC001)"},
                    "session_id": {"type": "string", "description": "Session ID to verify all entries"},
                    "pre_snapshot_id": {"type": "string", "description": "Snapshot ID for pre-change state"},
                    "tolerance_percent": {
                        "type": "number",
                        "description": "Percentage tolerance for counts (default: 5)",
                    },
                    "tolerance_lines": {
                        "type": "integer",
                        "description": "Absolute line tolerance for small counts (default: 2)",
                    },
                },
            },
        ),
        Tool(
            name="trace_git_reconcile",
            description="Cross-validate TRACE logs with git history. Detects unlogged commits, phantom entries, and timestamp mismatches.",
            inputSchema={
                "type": "object",
                "properties": {
                    "since": {"type": "string", "description": "Git date filter (e.g., '1 week ago', '2024-01-01')"},
                    "auto_log_missing": {
                        "type": "boolean",
                        "description": "Generate suggestions for missing entries (default: false)",
                    },
                },
            },
        ),
        Tool(
            name="trace_verify_integrity",
            description="Verify cryptographic hash chain for tamper detection. Each TRACE entry is linked via SHA-256 hashes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entry_id": {"type": "string", "description": "Verify specific entry (optional)"},
                    "rebuild_chain": {
                        "type": "boolean",
                        "description": "Rebuild chain from TRACE data (WARNING: replaces existing chain)",
                    },
                },
            },
        ),
        Tool(
            name="trace_trust_report",
            description="Generate trust metrics and verification report for publication. Computes overall trust score from verification, git sync, integrity, and temporal checks.",
            inputSchema={
                "type": "object",
                "properties": {
                    "period": {"type": "string", "description": "Time period to analyze (e.g., '30 days', '1 week')"},
                    "format": {
                        "type": "string",
                        "enum": ["json", "markdown", "summary"],
                        "description": "Output format (default: markdown)",
                    },
                },
            },
        ),
        Tool(
            name="trace_analyze_text",
            description="Analyze text document (LaTeX, Markdown) for section-level authorship tracking. Returns word counts and fingerprints per section.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the text file"},
                    "include_authorship": {
                        "type": "boolean",
                        "description": "Include authorship info from TRACE (default: true)",
                    },
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="trace_list_snapshots",
            description="List available snapshots. Useful for finding pre-change states for verification.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Filter by session ID"},
                    "trigger": {"type": "string", "description": "Filter by trigger type"},
                    "limit": {"type": "integer", "description": "Maximum results (default: 50)"},
                },
            },
        ),
    ]


# ============================================================
# MCP Tool Handlers
# ============================================================


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:  # type: ignore[return]
    """Handle tool calls."""
    trace = load_trace()

    try:
        # Log audit entry for all operations
        log_audit(trace, name, hash_arguments(arguments))

        # Query and Context
        if name == "trace_query":
            results = search_trace(trace, arguments["query"], arguments.get("categories"))
            if not results:
                return [TextContent(type="text", text=f"No results found for: '{arguments['query']}'")]
            return [
                TextContent(type="text", text=f"Found {len(results)} result(s):\n\n{json.dumps(results, indent=2)}")
            ]

        elif name == "trace_get_context":
            include_metrics = arguments.get("include_metrics", True)
            output = {
                "schema_version": trace.get("schema_version", "unknown"),
                "metadata": trace["metadata"],
                "context": trace["context"],
                "recent_sessions": trace.get("sessions", [])[-3:],
                "recent_decisions": trace.get("decisions", [])[-5:],
                "recent_learnings": trace.get("learnings", [])[-5:],
                "recent_suggestions": trace.get("ai_suggestions", [])[-5:],
                "recent_gotchas": trace.get("gotchas", [])[-5:],
            }
            if include_metrics:
                output["metrics"] = trace.get("metrics_summary", {})
            return [TextContent(type="text", text=json.dumps(output, indent=2))]

        # Session Management
        elif name == "trace_start_session":
            entry = {
                "id": generate_id("S", trace["sessions"]),
                "started": datetime.now().isoformat(),
                "ended": None,
                "duration_minutes": None,
                "ai_system_id": "AI001",
                "model_version": arguments.get("ai_model", "unknown"),
                "purpose": arguments["purpose"],
                "scientific_stage": arguments.get("scientific_stage", "exploration"),
                "human_operator": None,
                "environment_snapshot": {},
                "summary": {
                    "tasks_completed": [],
                    "files_created": [],
                    "files_modified": [],
                    "decisions_made": [],
                    "learnings_recorded": [],
                    "suggestions_proposed": [],
                    "suggestions_accepted": [],
                    "suggestions_rejected": [],
                },
                "reflection": {},
            }
            trace["sessions"].append(entry)

            # v3.0: Auto-capture environment
            env_id = None
            if CORE_AVAILABLE:
                try:
                    capturer = EnvironmentCapture(TRACE_PATH.parent)
                    env_data = capturer.capture(
                        agent_name=arguments.get("ai_model", "unknown"),
                        agent_framework="mcp",
                    )
                    env_id = generate_environment_id(trace.get("environments", []))
                    env_entry = {"id": env_id, **env_data}
                    if "environments" not in trace:
                        trace["environments"] = []
                    trace["environments"].append(env_entry)
                    entry["environment_id"] = env_id
                except Exception:
                    pass  # Environment capture is optional

            save_trace(trace)
            response = f"Session started: {entry['id']}\nPurpose: {entry['purpose']}\nSchema: TRACE-3.0"
            if env_id:
                response += f"\nEnvironment: {env_id}"
            return [TextContent(type="text", text=response)]

        elif name == "trace_end_session":
            session_id = arguments["session_id"]
            for session in trace["sessions"]:
                if session["id"] == session_id:
                    session["ended"] = datetime.now().isoformat()
                    started = datetime.fromisoformat(session["started"])
                    ended = datetime.fromisoformat(session["ended"])
                    session["duration_minutes"] = int((ended - started).total_seconds() / 60)
                    session["reflection"] = {
                        "what_went_well": arguments.get("summary"),
                        "ai_helpfulness_rating": arguments.get("ai_helpfulness_rating"),
                    }
                    save_trace(trace)
                    return [
                        TextContent(
                            type="text",
                            text=f"Session {session_id} ended. Duration: {session['duration_minutes']} minutes",
                        )
                    ]
            return [TextContent(type="text", text=f"Session {session_id} not found")]

        # v3.0: Environment Capture
        elif name == "trace_capture_environment":
            if not CORE_AVAILABLE:
                # Fallback implementation if core module not available
                import platform
                import sys

                env_id = f"ENV{len(trace.get('environments', [])) + 1:03d}"
                env_entry = {
                    "id": env_id,
                    "captured_at": datetime.now().isoformat(),
                    "platform": {
                        "os": platform.system().lower(),
                        "arch": platform.machine(),
                        "version": platform.release(),
                    },
                    "runtime": {
                        "language": "python",
                        "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                    },
                    "agent": {
                        "framework": arguments.get("agent_framework", "mcp"),
                        "name": arguments.get("agent_name", "unknown"),
                        "parameters": arguments.get("agent_parameters", {}),
                    },
                    "mcp": {
                        "spec_version": "2025-06-18",
                        "server_version": "3.0.0",
                    },
                }
            else:
                capturer = EnvironmentCapture(TRACE_PATH.parent)
                env_data = capturer.capture(
                    agent_name=arguments.get("agent_name", "unknown"),
                    agent_framework=arguments.get("agent_framework", "mcp"),
                    agent_parameters=arguments.get("agent_parameters"),
                )
                env_id = generate_environment_id(trace.get("environments", []))
                env_entry = {"id": env_id, **env_data}

            if "environments" not in trace:
                trace["environments"] = []
            trace["environments"].append(env_entry)

            # Associate with session if provided
            session_id = arguments.get("session_id")
            if session_id:
                for session in trace["sessions"]:
                    if session["id"] == session_id:
                        session["environment_id"] = env_id
                        break

            save_trace(trace)
            return [
                TextContent(
                    type="text",
                    text=f"Environment captured: {env_id}\n"
                    f"Platform: {env_entry['platform']['os']}/{env_entry['platform']['arch']}\n"
                    f"Agent: {env_entry['agent']['name']}",
                )
            ]

        # v3.0: Evaluation Logging
        elif name == "trace_log_evaluation":
            eval_id = f"EVAL{len(trace.get('evaluations', [])) + 1:03d}"

            eval_entry = {
                "id": eval_id,
                "timestamp": datetime.now().isoformat(),
                "evaluation_type": arguments["evaluation_type"],
            }

            if arguments.get("session_id"):
                eval_entry["session_id"] = arguments["session_id"]

            # Build target
            target = {}
            if arguments.get("target_files"):
                target["files"] = arguments["target_files"]
            if arguments.get("target_description"):
                target["description"] = arguments["target_description"]
            if target:
                eval_entry["target"] = target

            # Build metrics
            metrics = {}
            if arguments.get("tests_passed") is not None:
                metrics["tests_passed"] = arguments["tests_passed"]
            if arguments.get("tests_failed") is not None:
                metrics["tests_failed"] = arguments["tests_failed"]
            if arguments.get("coverage") is not None:
                metrics["coverage"] = arguments["coverage"]
            if arguments.get("duration_ms") is not None:
                metrics["duration_ms"] = arguments["duration_ms"]
            if metrics:
                eval_entry["metrics"] = metrics

            if arguments.get("tool"):
                eval_entry["tool"] = arguments["tool"]
            if arguments.get("command"):
                eval_entry["command"] = arguments["command"]
            if arguments.get("output_summary"):
                eval_entry["output_summary"] = arguments["output_summary"]
            if arguments.get("passed") is not None:
                eval_entry["passed"] = arguments["passed"]

            if "evaluations" not in trace:
                trace["evaluations"] = []
            trace["evaluations"].append(eval_entry)
            save_trace(trace)

            # Build response
            response_parts = [f"Evaluation logged: {eval_id}", f"Type: {arguments['evaluation_type']}"]
            if metrics.get("tests_passed") is not None or metrics.get("tests_failed") is not None:
                passed = metrics.get("tests_passed", 0)
                failed = metrics.get("tests_failed", 0)
                response_parts.append(f"Tests: {passed} passed, {failed} failed")
            if metrics.get("coverage") is not None:
                response_parts.append(f"Coverage: {metrics['coverage']:.1%}")

            return [TextContent(type="text", text="\n".join(response_parts))]

        # AI Suggestion Tracking
        elif name == "trace_log_suggestion":
            entry = {
                "id": generate_id("SUG", trace.get("ai_suggestions", [])),
                "timestamp": datetime.now().isoformat(),
                "session_id": arguments.get("session_id"),
                "suggestion": {
                    "type": arguments["suggestion_type"],
                    "description": arguments["description"],
                    "scope": {
                        "files_affected": arguments.get("files_affected", []),
                        "lines_proposed": arguments.get("lines_proposed", 0),
                    },
                },
                "ai_confidence": {"level": arguments.get("ai_confidence", "medium"), "reasoning": None},
                "context": {
                    "what_prompted_suggestion": arguments.get("what_prompted"),
                    "human_was_aware_before": False,
                },
                "outcome": {
                    "status": "pending",
                    "decision_timestamp": None,
                    "human_rationale": None,
                    "lines_final": {"accepted_as_is": 0, "modified_by_human": 0, "rejected": 0},
                },
                "resulted_in": {"decisions": [], "code_contributions": [], "errors_discovered": []},
            }
            # V&V: Add to integrity chain if available
            if VV_AVAILABLE:
                try:
                    trace_dir = TRACE_PATH.parent / ".trace"
                    chain = IntegrityChain(trace_dir)
                    integrity_metadata = chain.add_entry(entry["id"], "ai_suggestion", entry)
                    entry["integrity"] = integrity_metadata
                except Exception as e:
                    entry["integrity"] = {"error": str(e)}

            if "ai_suggestions" not in trace:
                trace["ai_suggestions"] = []
            trace["ai_suggestions"].append(entry)
            save_trace(trace)
            return [
                TextContent(
                    type="text",
                    text=f"Suggestion logged ({entry['id']}): {arguments['description'][:80]}...\nType: {arguments['suggestion_type']}, Lines proposed: {arguments.get('lines_proposed', 0)}",
                )
            ]

        elif name == "trace_resolve_suggestion":
            suggestion_id = arguments["suggestion_id"]
            for sug in trace.get("ai_suggestions", []):
                if sug["id"] == suggestion_id:
                    sug["outcome"] = {
                        "status": arguments["status"],
                        "decision_timestamp": datetime.now().isoformat(),
                        "human_rationale": arguments.get("human_rationale"),
                        "lines_final": {
                            "accepted_as_is": arguments.get("lines_accepted_as_is", 0),
                            "modified_by_human": arguments.get("lines_modified", 0),
                            "rejected": arguments.get("lines_rejected", 0),
                        },
                    }
                    if arguments.get("modification_description"):
                        sug["outcome"]["modification_description"] = arguments["modification_description"]
                    save_trace(trace)

                    status = arguments["status"]
                    lines_info = f"Accepted: {arguments.get('lines_accepted_as_is', 0)}, Modified: {arguments.get('lines_modified', 0)}, Rejected: {arguments.get('lines_rejected', 0)}"
                    return [
                        TextContent(type="text", text=f"Suggestion {suggestion_id} resolved: {status}\n{lines_info}")
                    ]
            return [TextContent(type="text", text=f"Suggestion {suggestion_id} not found")]

        # Code/Content Contribution (v2.0)
        elif name == "trace_log_code":
            content_type = arguments.get("content_type", "code")

            entry = {
                "id": generate_id("CC", trace["code_contributions"]),
                "session_id": arguments.get("session_id"),
                "interaction_id": None,
                "timestamp": datetime.now().isoformat(),
                "file_path": arguments["file_path"],
                "content_type": content_type,
                "git_commit": arguments.get("git_commit"),
                "has_human_edit_tag": arguments.get("has_human_edit_tag", False),
                "contribution_type": arguments["contribution_type"],
                "direction_source": arguments["direction_source"],
                "metrics": {
                    "lines_added": 0,
                    "lines_removed": 0,
                    "lines_modified": 0,
                    "functions_added": 0,
                    "classes_added": 0,
                },
                "authorship": {
                    "human_directed": {
                        "ai_executed_lines": arguments.get("human_directed_ai_executed_lines", 0),
                        "human_executed_lines": arguments.get("human_directed_human_executed_lines", 0),
                    },
                    "ai_suggested": {
                        "accepted_lines": arguments.get("ai_suggested_accepted_lines", 0),
                        "rejected_lines": arguments.get("ai_suggested_rejected_lines", 0),
                        "modified_lines": arguments.get("ai_suggested_modified_lines", 0),
                        "modification_description": None,
                        "related_suggestion_id": arguments.get("related_suggestion_id"),
                    },
                    "human_manual_edit": {
                        "lines_added": arguments.get("human_manual_edit_lines", 0),
                        "lines_removed": 0,
                        "lines_modified": 0,
                        "git_commits": [],
                    },
                    "collaborative": {"lines": arguments.get("collaborative_lines", 0), "description": None},
                },
                "quality": {},
                "description": arguments["description"],
            }

            # Add word metrics for text content type
            if content_type == "text":
                entry["authorship"]["human_directed"]["ai_executed_words"] = arguments.get(
                    "human_directed_ai_executed_words", 0
                )
                entry["authorship"]["human_directed"]["human_executed_words"] = arguments.get(
                    "human_directed_human_executed_words", 0
                )
                entry["authorship"]["ai_suggested"]["accepted_words"] = arguments.get("ai_suggested_accepted_words", 0)
                entry["authorship"]["ai_suggested"]["modified_words"] = arguments.get("ai_suggested_modified_words", 0)
                entry["authorship"]["ai_suggested"]["rejected_words"] = arguments.get("ai_suggested_rejected_words", 0)
                entry["authorship"]["human_manual_edit"]["words_added"] = arguments.get("human_manual_edit_words", 0)
                entry["authorship"]["collaborative"]["words"] = arguments.get("collaborative_words", 0)
                entry["metrics"]["words_added"] = (
                    entry["authorship"]["human_directed"]["ai_executed_words"]
                    + entry["authorship"]["human_directed"]["human_executed_words"]
                    + entry["authorship"]["ai_suggested"]["accepted_words"]
                    + entry["authorship"]["ai_suggested"]["modified_words"]
                    + entry["authorship"]["human_manual_edit"]["words_added"]
                    + entry["authorship"]["collaborative"]["words"]
                )

            # Add row metrics for data content type
            if content_type == "data":
                entry["authorship"]["human_directed"]["ai_executed_rows"] = arguments.get(
                    "human_directed_ai_executed_rows", 0
                )
                entry["authorship"]["human_directed"]["human_executed_rows"] = arguments.get(
                    "human_directed_human_executed_rows", 0
                )
                entry["authorship"]["ai_suggested"]["accepted_rows"] = arguments.get("ai_suggested_accepted_rows", 0)
                entry["authorship"]["ai_suggested"]["modified_rows"] = arguments.get("ai_suggested_modified_rows", 0)
                entry["authorship"]["ai_suggested"]["rejected_rows"] = arguments.get("ai_suggested_rejected_rows", 0)
                entry["authorship"]["human_manual_edit"]["rows_added"] = arguments.get("human_manual_edit_rows", 0)
                entry["authorship"]["collaborative"]["rows"] = arguments.get("collaborative_rows", 0)
                entry["metrics"]["rows_added"] = (
                    entry["authorship"]["human_directed"]["ai_executed_rows"]
                    + entry["authorship"]["human_directed"]["human_executed_rows"]
                    + entry["authorship"]["ai_suggested"]["accepted_rows"]
                    + entry["authorship"]["ai_suggested"]["modified_rows"]
                    + entry["authorship"]["human_manual_edit"]["rows_added"]
                    + entry["authorship"]["collaborative"]["rows"]
                )

            # Calculate total lines for metrics (all content types track lines)
            total_lines = (
                entry["authorship"]["human_directed"]["ai_executed_lines"]
                + entry["authorship"]["human_directed"]["human_executed_lines"]
                + entry["authorship"]["ai_suggested"]["accepted_lines"]
                + entry["authorship"]["ai_suggested"]["modified_lines"]
                + entry["authorship"]["human_manual_edit"]["lines_added"]
                + entry["authorship"]["collaborative"]["lines"]
            )
            entry["metrics"]["lines_added"] = total_lines

            # V&V: Add to integrity chain if available
            if VV_AVAILABLE:
                try:
                    trace_dir = TRACE_PATH.parent / ".trace"
                    chain = IntegrityChain(trace_dir)
                    integrity_metadata = chain.add_entry(entry["id"], "code_contribution", entry)
                    entry["integrity"] = integrity_metadata
                except Exception as e:
                    entry["integrity"] = {"error": str(e)}

            trace["code_contributions"].append(entry)
            save_trace(trace)

            # Build summary message
            summary_parts = []
            if entry["authorship"]["human_directed"]["ai_executed_lines"] > 0:
                summary_parts.append(
                    f"Human-directed/AI-executed: {entry['authorship']['human_directed']['ai_executed_lines']} lines"
                )
            if entry["authorship"]["ai_suggested"]["accepted_lines"] > 0:
                summary_parts.append(
                    f"AI-suggested/Accepted: {entry['authorship']['ai_suggested']['accepted_lines']} lines"
                )
            if entry["authorship"]["ai_suggested"]["modified_lines"] > 0:
                summary_parts.append(
                    f"AI-suggested/Modified: {entry['authorship']['ai_suggested']['modified_lines']} lines"
                )
            if entry["authorship"]["human_manual_edit"]["lines_added"] > 0:
                summary_parts.append(f"Human-manual: {entry['authorship']['human_manual_edit']['lines_added']} lines")

            # Add word summary for text type
            if content_type == "text":
                total_words = entry["metrics"].get("words_added", 0)
                if total_words > 0:
                    summary_parts.append(f"Total words: {total_words}")

            # Add row summary for data type
            if content_type == "data":
                total_rows = entry["metrics"].get("rows_added", 0)
                if total_rows > 0:
                    summary_parts.append(f"Total rows: {total_rows}")

            summary = ", ".join(summary_parts) if summary_parts else "No changes logged"
            content_label = {"code": "Code", "text": "Text", "data": "Data"}.get(content_type, "Content")
            return [
                TextContent(
                    type="text",
                    text=f"{content_label} contribution logged ({entry['id']}): {arguments['file_path']}\nContent type: {content_type}, Direction: {arguments['direction_source']}\n{summary}",
                )
            ]

        # Git Integration (NEW)
        elif name == "trace_scan_git_commits":
            since = arguments.get("since", "1 week ago")
            commits = scan_git_for_human_edits(since)

            if not commits:
                return [TextContent(type="text", text=f"No [HUMAN-EDIT] commits found since {since}")]

            # Log each commit as a human manual edit
            if "human_manual_edits" not in trace:
                trace["human_manual_edits"] = []

            logged_count = 0
            for commit in commits:
                # Check if already logged
                existing = [
                    h for h in trace["human_manual_edits"] if h.get("git_commit", {}).get("hash") == commit["hash"]
                ]
                if existing:
                    continue

                entry = {
                    "id": generate_id("HME", trace["human_manual_edits"]),
                    "timestamp": commit["date"],
                    "detected_at": datetime.now().isoformat(),
                    "detection_method": "git_scan",
                    "git_commit": {
                        "hash": commit["hash"],
                        "message": commit["message"],
                        "author": commit["author"],
                        "date": commit["date"],
                    },
                    "files_changed": commit["files_changed"],
                    "lines_added": commit["lines_added"],
                    "lines_removed": commit["lines_removed"],
                    "context": {
                        "why_manual": None,
                        "related_to_ai_session": arguments.get("session_id"),
                        "notes": None,
                    },
                }
                trace["human_manual_edits"].append(entry)
                logged_count += 1

            save_trace(trace)

            total_lines_added = sum(c["lines_added"] for c in commits)
            total_lines_removed = sum(c["lines_removed"] for c in commits)
            return [
                TextContent(
                    type="text",
                    text=f"Git scan complete.\nFound {len(commits)} [HUMAN-EDIT] commits, logged {logged_count} new.\nTotal lines: +{total_lines_added} -{total_lines_removed}",
                )
            ]

        # Idea Tracking
        elif name == "trace_log_idea":
            entry = {
                "id": generate_id("IDEA", trace["ideas"]),
                "timestamp": datetime.now().isoformat(),
                "session_id": arguments.get("session_id"),
                "interaction_id": None,
                "idea": arguments["idea"],
                "idea_type": arguments.get("idea_type", "approach"),
                "domain": "methodology",
                "origin": {
                    "source": arguments["source"],
                    "triggered_by": arguments.get("triggered_by"),
                    "prior_context": None,
                },
                "evaluation": {"status": "pending"},
                "outcome": {"adopted": None},
                "related_to": {},
            }
            trace["ideas"].append(entry)
            save_trace(trace)
            return [
                TextContent(
                    type="text",
                    text=f"Idea logged ({entry['id']}): {arguments['idea'][:80]}...\nSource: {arguments['source']}",
                )
            ]

        elif name == "trace_evaluate_idea":
            idea_id = arguments["idea_id"]
            for idea in trace["ideas"]:
                if idea["id"] == idea_id:
                    idea["evaluation"] = {
                        "status": "evaluated",
                        "evaluated_by": "human",
                        "evaluation_date": datetime.now().isoformat(),
                        "evaluation_notes": arguments.get("evaluation_notes"),
                    }
                    idea["outcome"] = {
                        "adopted": arguments["adopted"],
                        "rejection_reason": arguments.get("rejection_reason"),
                        "modification_description": arguments.get("modification_description"),
                    }
                    save_trace(trace)
                    status = "adopted" if arguments["adopted"] else "rejected"
                    return [TextContent(type="text", text=f"Idea {idea_id} {status}")]
            return [TextContent(type="text", text=f"Idea {idea_id} not found")]

        # Error Tracking
        elif name == "trace_log_error":
            entry = {
                "id": generate_id("ERR", trace["errors"]),
                "timestamp": datetime.now().isoformat(),
                "session_id": arguments.get("session_id"),
                "interaction_id": None,
                "error_type": arguments.get("error_type", "logic"),
                "severity": arguments.get("severity", "medium"),
                "description": arguments["description"],
                "source": {
                    "originated_from": arguments["originated_from"],
                    "file_path": arguments.get("file_path"),
                    "line_numbers": None,
                    "code_snippet": None,
                },
                "detection": {
                    "detected_by": arguments["detected_by"],
                    "detection_method": arguments.get("detection_method", "code_review"),
                    "time_to_detect_minutes": None,
                },
                "resolution": {
                    "resolved": True if arguments.get("resolution_description") else False,
                    "resolved_by": "human",
                    "resolution_description": arguments.get("resolution_description"),
                    "time_to_resolve_minutes": None,
                },
                "impact": {},
            }
            trace["errors"].append(entry)
            save_trace(trace)
            return [
                TextContent(
                    type="text",
                    text=f"Error logged ({entry['id']}): {arguments['description'][:80]}...\nSource: {arguments['originated_from']}, Caught by: {arguments['detected_by']}",
                )
            ]

        # Intervention Tracking
        elif name == "trace_log_intervention":
            entry = {
                "id": generate_id("H", trace["interventions"]),
                "timestamp": datetime.now().isoformat(),
                "session_id": arguments.get("session_id"),
                "interaction_id": None,
                "intervention_type": arguments["intervention_type"],
                "ai_output": {"summary": arguments["ai_output_summary"], "artifact_type": "code"},
                "human_action": {
                    "action": arguments["intervention_type"],
                    "description": arguments["human_action"],
                    "rationale": arguments.get("rationale"),
                    "lines_affected": arguments.get("lines_affected", 0),
                },
                "expertise_applied": arguments.get("expertise_applied", []),
                "impact": {"significance": arguments.get("significance", "minor")},
            }
            trace["interventions"].append(entry)
            save_trace(trace)
            return [
                TextContent(
                    type="text",
                    text=f"Intervention logged ({entry['id']}): {arguments['intervention_type']}\n{arguments['human_action'][:80]}...",
                )
            ]

        # Standard Knowledge Management
        elif name == "trace_add_decision":
            entry = {
                "id": generate_id("D", trace["decisions"]),
                "timestamp": datetime.now().isoformat(),
                "session_id": None,
                "decision": arguments["decision"],
                "rationale": arguments["rationale"],
                "decision_type": "technical",
                "alternatives": [],
                "provenance": {
                    "proposed_by": arguments.get("proposed_by", "human"),
                    "ai_contribution": "none" if arguments.get("proposed_by") == "human" else "suggested",
                    "information_consulted": [],
                    "related_suggestion_id": arguments.get("related_suggestion_id"),
                },
                "confidence": {"initial": 0.7, "current": 0.7, "history": []},
                "validation": {"status": "untested"},
                "status": "active",
                "tags": arguments.get("tags", []),
            }
            trace["decisions"].append(entry)
            save_trace(trace)
            return [
                TextContent(type="text", text=f"Decision recorded ({entry['id']}): {arguments['decision'][:80]}...")
            ]

        elif name == "trace_add_learning":
            entry = {
                "id": generate_id("L", trace["learnings"]),
                "timestamp": datetime.now().isoformat(),
                "session_id": None,
                "learning": arguments["learning"],
                "evidence": arguments.get("evidence", ""),
                "learning_type": "empirical",
                "provenance": {
                    "discovered_by": arguments.get("discovered_by", "human"),
                    "ai_contribution": "none" if arguments.get("discovered_by") == "human" else "contributed",
                    "discovery_method": "experiment",
                    "discovery_context": None,
                },
                "confidence": {
                    "level": arguments.get("confidence", "medium"),
                    "value": {"high": 0.9, "medium": 0.7, "low": 0.5}.get(arguments.get("confidence", "medium"), 0.7),
                },
                "tags": arguments.get("tags", []),
            }
            trace["learnings"].append(entry)
            save_trace(trace)
            return [
                TextContent(type="text", text=f"Learning recorded ({entry['id']}): {arguments['learning'][:80]}...")
            ]

        elif name == "trace_add_gotcha":
            entry = {
                "id": generate_id("G", trace["gotchas"]),
                "timestamp": datetime.now().isoformat(),
                "session_id": None,
                "problem": arguments["problem"],
                "solution": arguments["solution"],
                "severity": arguments.get("severity", "medium"),
                "provenance": {
                    "discovered_by": arguments.get("discovered_by", "human"),
                    "ai_contribution": "none",
                    "discovery_context": None,
                },
                "tags": arguments.get("tags", []),
            }
            trace["gotchas"].append(entry)
            save_trace(trace)
            return [TextContent(type="text", text=f"Gotcha recorded ({entry['id']}): {arguments['problem'][:80]}...")]

        # Metrics
        elif name == "trace_get_metrics":
            category = arguments.get("category", "all")
            metrics = trace.get("metrics_summary", {})

            if category == "all":
                output = metrics
            elif category == "code":
                output = metrics.get("code_metrics", {})
            elif category == "suggestions":
                output = metrics.get("suggestion_metrics", {})
            elif category == "errors":
                output = metrics.get("error_metrics", {})
            elif category == "ideas":
                output = metrics.get("idea_metrics", {})
            elif category == "interventions":
                output = metrics.get("intervention_metrics", {})
            elif category == "sessions":
                output = metrics.get("session_metrics", {})
            elif category == "knowledge":
                output = metrics.get("knowledge_metrics", {})
            else:
                output = metrics

            return [TextContent(type="text", text=json.dumps(output, indent=2))]

        elif name == "trace_compute_metrics":
            metrics = compute_metrics(trace)
            trace["metrics_summary"] = metrics
            save_trace(trace)
            return [TextContent(type="text", text=f"Metrics computed and saved.\n\n{json.dumps(metrics, indent=2)}")]

        # Smart Triggers (v2.1)
        elif name == "trace_knowledge_check":
            context = arguments["context"]
            event_type = arguments.get("event_type", "auto")
            check_duplicates = arguments.get("check_duplicates", True)

            result = knowledge_check(trace, context, event_type, check_duplicates)

            # Format output
            output_lines = []
            output_lines.append(f"Should log: {'Yes' if result['should_log'] else 'No'}")
            output_lines.append(f"Confidence: {result['confidence']}")

            if result["recommended_types"]:
                output_lines.append(f"Recommended types: {', '.join(result['recommended_types'])}")

            if result["reasoning"]:
                output_lines.append(f"Reasoning: {result['reasoning']}")

            if result["similar_entries"]:
                output_lines.append("\nSimilar existing entries:")
                for entry in result["similar_entries"][:3]:
                    output_lines.append(
                        f"  - [{entry['id']}] (similarity: {entry['similarity']}) {entry['entry_preview'][:100]}..."
                    )

            if result["should_log"] and result["suggested_fields"]:
                output_lines.append("\nSuggested fields:")
                output_lines.append(json.dumps(result["suggested_fields"], indent=2))

            return [TextContent(type="text", text="\n".join(output_lines))]

        # Checkpoint (v2.1)
        elif name == "trace_checkpoint":
            session_id = arguments["session_id"]
            trigger = arguments["trigger"]
            notes = arguments.get("notes", "")
            files_touched = arguments.get("files_touched", [])

            # Analyze session
            analysis = analyze_session_for_checkpoint(trace, session_id, files_touched)

            if "error" in analysis:
                return [TextContent(type="text", text=analysis["error"])]

            # Create checkpoint entry
            checkpoint_id = generate_id("CP", trace.get("checkpoints", []))
            checkpoint = {
                "id": checkpoint_id,
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
                "trigger": trigger,
                "notes": notes,
                "analysis": analysis,
            }

            if "checkpoints" not in trace:
                trace["checkpoints"] = []
            trace["checkpoints"].append(checkpoint)
            save_trace(trace)

            # Format output
            output_lines = [
                f"Checkpoint {checkpoint_id} created",
                f"Session: {session_id} ({analysis['session_duration_minutes']} minutes)",
                f"Trigger: {trigger}",
                "",
                "Entries logged this session:",
            ]

            for category, count in analysis["entries_logged"].items():
                if count > 0:
                    output_lines.append(f"  - {category}: {count}")

            if analysis["pending_suggestions"] > 0:
                output_lines.append(f"\nPending suggestions: {analysis['pending_suggestions']}")

            if analysis["prompts"]:
                output_lines.append("\nItems to review:")
                for prompt in analysis["prompts"]:
                    output_lines.append(f"  - {prompt}")

            if analysis["recommendations"]:
                output_lines.append("\nRecommendations:")
                for rec in analysis["recommendations"]:
                    output_lines.append(f"  - {rec}")

            return [TextContent(type="text", text="\n".join(output_lines))]

        # Context Refresh (v2.1)
        elif name == "trace_context_refresh":
            topics = arguments["topics"]
            include_recent = arguments.get("include_recent", True)
            max_items = arguments.get("max_items", 5)
            categories = arguments.get("categories", ["gotchas", "decisions", "learnings", "patterns"])

            results = refresh_context_for_topics(trace, topics, include_recent, max_items, categories)

            # Format output
            output_lines = [f"Context refresh for topics: {', '.join(topics)}", ""]

            total_found = 0
            for category, items in results.items():
                if items:
                    output_lines.append(f"### {category.upper()} ({len(items)} relevant)")
                    for item in items:
                        entry = item["entry"]
                        score = item["relevance_score"]
                        recent_tag = " [RECENT]" if item.get("recent") else ""

                        # Get preview text based on category
                        if category == "gotchas":
                            preview = entry.get("problem", "")[:80]
                        elif category == "decisions":
                            preview = entry.get("decision", "")[:80]
                        elif category == "learnings":
                            preview = entry.get("learning", "")[:80]
                        else:
                            preview = json.dumps(entry)[:80]

                        output_lines.append(f"  [{entry.get('id', '?')}] (relevance: {score}){recent_tag}")
                        output_lines.append(f"      {preview}...")
                        total_found += 1

                    output_lines.append("")

            if total_found == 0:
                output_lines.append("No relevant past knowledge found for these topics.")

            return [TextContent(type="text", text="\n".join(output_lines))]

        # Consolidate Learnings (v2.1)
        elif name == "trace_consolidate_learnings":
            session_id = arguments["session_id"]
            auto_link = arguments.get("auto_link", True)
            _generate_summary = arguments.get("generate_summary", True)  # Reserved for future use

            result = consolidate_session_learnings(trace, session_id, auto_link)

            if auto_link and result["links_created"] > 0:
                save_trace(trace)

            # Format output
            output_lines = [
                f"Consolidation complete for session {session_id}",
                f"Total entries: {result['total_entries']}",
                "",
                "By category:",
            ]

            for category, count in result["by_category"].items():
                if count > 0:
                    output_lines.append(f"  - {category}: {count}")

            if auto_link:
                output_lines.append(f"\nLinks created: {result['links_created']}")
                output_lines.append(f"Clusters found: {result['clusters_found']}")

                if result["clusters"]:
                    output_lines.append("\nRelated entry clusters:")
                    for i, cluster in enumerate(result["clusters"], 1):
                        output_lines.append(f"  Cluster {i}: {' <-> '.join(cluster)}")

            if result["tags_used"]:
                output_lines.append(f"\nTags used: {', '.join(result['tags_used'][:10])}")

            return [TextContent(type="text", text="\n".join(output_lines))]

        # Attribution
        elif name == "trace_add_attribution":
            entry = {
                "id": generate_id("A", trace["attributions"]),
                "artifact_type": arguments["artifact_type"],
                "artifact_id": None,
                "artifact_description": arguments["artifact_description"],
                "ai_contribution": {
                    "percentage_estimate": arguments.get("ai_contribution_percentage", 50),
                    "contribution_types": arguments.get("ai_contribution_types", []),
                    "model_used": "claude-opus-4-5-20251101",
                },
                "human_contribution": {
                    "percentage_estimate": 100 - arguments.get("ai_contribution_percentage", 50),
                    "contribution_types": arguments.get("human_contribution_types", []),
                },
                "citation_text": arguments.get("citation_text", ""),
                "related_session_ids": [],
            }
            trace["attributions"].append(entry)
            save_trace(trace)
            return [
                TextContent(
                    type="text",
                    text=f"Attribution recorded ({entry['id']}): {arguments['artifact_description'][:80]}...",
                )
            ]

        # Export
        elif name == "trace_export_report":
            format_type = arguments.get("format", "summary")

            if format_type == "json":
                return [TextContent(type="text", text=json.dumps(trace, indent=2))]

            elif format_type == "summary":
                metrics = compute_metrics(trace)
                summary = generate_summary_report(trace, metrics)
                return [TextContent(type="text", text=summary)]

            elif format_type == "markdown":
                return [TextContent(type="text", text=generate_markdown_report(trace))]

        # ============================================================
        # V&V (Verification & Validation) Tool Handlers
        # ============================================================

        elif name == "trace_snapshot":
            if not VV_AVAILABLE:
                return [TextContent(type="text", text="V&V module not available. Check installation.")]

            trace_dir = TRACE_PATH.parent / ".trace"
            snapshot_manager = SnapshotManager(trace_dir)

            files = arguments.get("files", [])
            trigger = arguments.get("trigger", "manual")
            session_id = arguments.get("session_id")
            related_entry_id = arguments.get("related_entry_id")

            result = snapshot_manager.create_snapshot(
                files=files, trigger=trigger, session_id=session_id, related_entry_id=related_entry_id
            )

            save_trace(trace)
            return [
                TextContent(
                    type="text",
                    text=f"Snapshot created: {result['snapshot_id']}\n"
                    f"Files: {len(result['files'])}\n"
                    f"Trigger: {result['trigger']}\n"
                    f"Git commit: {result['git_state'].get('commit_hash', 'N/A')[:8]}\n\n"
                    f"{json.dumps(result, indent=2)}",
                )
            ]

        elif name == "trace_verify":
            if not VV_AVAILABLE:
                return [TextContent(type="text", text="V&V module not available. Check installation.")]

            trace_dir = TRACE_PATH.parent / ".trace"

            tolerance_percent = arguments.get("tolerance_percent", 5.0)
            tolerance_lines = arguments.get("tolerance_lines", 2)

            engine = VerificationEngine(trace_dir, tolerance_percent=tolerance_percent, tolerance_lines=tolerance_lines)

            entry_id = arguments.get("entry_id")
            session_id = arguments.get("session_id")
            pre_snapshot_id = arguments.get("pre_snapshot_id")

            if entry_id:
                result = engine.verify_entry(trace, entry_id, pre_snapshot_id)
                status = "VERIFIED" if result.get("verified") else "ISSUES FOUND"
                return [
                    TextContent(
                        type="text",
                        text=f"Verification Result: {status}\n"
                        f"Entry: {entry_id}\n"
                        f"Checks: {result.get('checks_passed', 0)}/{result.get('checks_passed', 0) + result.get('checks_failed', 0)}\n\n"
                        f"{json.dumps(result, indent=2)}",
                    )
                ]

            elif session_id:
                result = engine.verify_session(trace, session_id)
                return [
                    TextContent(
                        type="text",
                        text=f"Session Verification: {session_id}\n"
                        f"Entries: {result.get('entries_verified', 0)}/{result.get('entries_total', 0)} verified\n"
                        f"Verification Rate: {result.get('verification_rate', 0)}%\n\n"
                        f"{json.dumps(result, indent=2)}",
                    )
                ]

            else:
                return [TextContent(type="text", text="Please provide either entry_id or session_id to verify.")]

        elif name == "trace_git_reconcile":
            if not VV_AVAILABLE:
                return [TextContent(type="text", text="V&V module not available. Check installation.")]

            reconciler = GitReconciler(TRACE_PATH.parent)

            since = arguments.get("since", "1 week ago")
            auto_log_missing = arguments.get("auto_log_missing", False)

            result = reconciler.reconcile(trace, since, auto_log_missing)

            if result.get("error"):
                return [TextContent(type="text", text=f"Git reconciliation error: {result['error']}")]

            summary = result.get("summary", {})
            return [
                TextContent(
                    type="text",
                    text=f"Git Reconciliation Report\n"
                    f"========================\n"
                    f"Period: since {since}\n"
                    f"Coverage: {summary.get('coverage_percent', 0)}%\n"
                    f"Total commits: {summary.get('total_commits', 0)}\n"
                    f"Tracked: {summary.get('tracked_commits', 0)}\n"
                    f"Unlogged: {summary.get('unlogged_commits', 0)}\n"
                    f"Human-edit commits: {summary.get('human_edit_commits', 0)}\n"
                    f"Phantom entries: {summary.get('phantom_entries', 0)}\n\n"
                    f"{json.dumps(result, indent=2)}",
                )
            ]

        elif name == "trace_verify_integrity":
            if not VV_AVAILABLE:
                return [TextContent(type="text", text="V&V module not available. Check installation.")]

            trace_dir = TRACE_PATH.parent / ".trace"
            chain = IntegrityChain(trace_dir)

            entry_id = arguments.get("entry_id")
            rebuild = arguments.get("rebuild_chain", False)

            if rebuild:
                result = chain.rebuild_chain(trace)
                # Update entries with integrity metadata
                save_trace(trace)
                return [
                    TextContent(
                        type="text",
                        text=f"Chain Rebuilt\n"
                        f"Old length: {result.get('old_chain_length', 0)}\n"
                        f"New length: {result.get('new_chain_length', 0)}\n"
                        f"Entries added: {result.get('entries_added', 0)}",
                    )
                ]

            elif entry_id:
                # Find entry and verify
                entry = None
                for collection in ["code_contributions", "ai_suggestions", "decisions", "learnings", "gotchas"]:
                    for e in trace.get(collection, []):
                        if e.get("id") == entry_id:
                            entry = e
                            break
                    if entry:
                        break

                if not entry:
                    return [TextContent(type="text", text=f"Entry {entry_id} not found")]

                result = chain.verify_entry(entry, entry_id)
                status = "VERIFIED" if result.get("verified") else "INTEGRITY ISSUE"
                return [TextContent(type="text", text=f"Entry Integrity: {status}\n{json.dumps(result, indent=2)}")]

            else:
                # Verify full chain
                result = chain.verify_chain(trace)
                status = "INTACT" if result.get("verified") else "ISSUES DETECTED"
                return [
                    TextContent(
                        type="text",
                        text=f"Chain Integrity: {status}\n"
                        f"Chain length: {result.get('chain_length', 0)}\n"
                        f"Errors: {len(result.get('errors', []))}\n"
                        f"Warnings: {len(result.get('warnings', []))}\n\n"
                        f"{json.dumps(result, indent=2)}",
                    )
                ]

        elif name == "trace_trust_report":
            if not VV_AVAILABLE:
                return [TextContent(type="text", text="V&V module not available. Check installation.")]

            trace_dir = TRACE_PATH.parent / ".trace"

            period = arguments.get("period", "30 days")
            format_type = arguments.get("format", "markdown")

            generator = ReportGenerator(trace_dir, TRACE_PATH.parent)
            report = generator.generate_trust_report(trace, period, format_type)

            return [TextContent(type="text", text=report)]

        elif name == "trace_analyze_text":
            if not VV_AVAILABLE:
                return [TextContent(type="text", text="V&V module not available. Check installation.")]

            file_path = arguments.get("file_path")
            include_authorship = arguments.get("include_authorship", True)

            if not file_path:
                return [TextContent(type="text", text="file_path is required")]

            analyzer = TextAnalyzer()

            # Make path absolute if needed
            file_path = Path(file_path)
            if not file_path.is_absolute():
                file_path = TRACE_PATH.parent / file_path

            result = analyzer.analyze_file(file_path)

            if "error" in result:
                return [TextContent(type="text", text=f"Analysis error: {result['error']}")]

            if include_authorship:
                contributions = trace.get("code_contributions", [])
                result["sections"] = analyzer.get_section_authorship(result["sections"], contributions, str(file_path))

            return [
                TextContent(
                    type="text",
                    text=f"Text Analysis: {file_path.name}\n"
                    f"Type: {result.get('file_type', 'unknown')}\n"
                    f"Sections: {result.get('total_sections', 0)}\n"
                    f"Words: {result.get('total_words', 0)}\n"
                    f"Lines: {result.get('total_lines', 0)}\n\n"
                    f"{json.dumps(result, indent=2)}",
                )
            ]

        elif name == "trace_list_snapshots":
            if not VV_AVAILABLE:
                return [TextContent(type="text", text="V&V module not available. Check installation.")]

            trace_dir = TRACE_PATH.parent / ".trace"
            snapshot_manager = SnapshotManager(trace_dir)

            session_id = arguments.get("session_id")
            trigger = arguments.get("trigger")
            limit = arguments.get("limit", 50)

            snapshots = snapshot_manager.list_snapshots(session_id=session_id, trigger=trigger, limit=limit)

            if not snapshots:
                return [TextContent(type="text", text="No snapshots found matching criteria.")]

            return [
                TextContent(
                    type="text", text=f"Found {len(snapshots)} snapshot(s):\n\n{json.dumps(snapshots, indent=2)}"
                )
            ]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error executing {name}: {str(e)}")]


def generate_summary_report(trace: dict, metrics: dict) -> str:
    """Generate a summary report."""
    code_metrics = metrics.get("code_metrics", {})
    content_type_counts = code_metrics.get("by_content_type", {})

    # Build word section if text contributions exist
    word_section = ""
    if content_type_counts.get("text", 0) > 0:
        total_words = code_metrics.get("total_words", {})
        word_section = f"""
## Text Authorship (Words)
- Human-directed, AI-executed: {total_words.get("human_directed_ai_executed", 0)} words
- Human-directed, Human-executed: {total_words.get("human_directed_human_executed", 0)} words
- AI-suggested, Accepted: {total_words.get("ai_suggested_accepted", 0)} words
- AI-suggested, Modified: {total_words.get("ai_suggested_modified", 0)} words
- Human Manual Edit: {total_words.get("human_manual_edit", 0)} words
- Collaborative: {total_words.get("collaborative", 0)} words
"""

    # Build row section if data contributions exist
    row_section = ""
    if content_type_counts.get("data", 0) > 0:
        total_rows = code_metrics.get("total_rows", {})
        row_section = f"""
## Data Authorship (Rows)
- Human-directed, AI-executed: {total_rows.get("human_directed_ai_executed", 0)} rows
- Human-directed, Human-executed: {total_rows.get("human_directed_human_executed", 0)} rows
- AI-suggested, Accepted: {total_rows.get("ai_suggested_accepted", 0)} rows
- AI-suggested, Modified: {total_rows.get("ai_suggested_modified", 0)} rows
- Human Manual Edit: {total_rows.get("human_manual_edit", 0)} rows
- Collaborative: {total_rows.get("collaborative", 0)} rows
"""

    return f"""# TRACE Report: {trace["metadata"].get("project", "Unknown Project")}
Generated: {datetime.now().isoformat()}
Schema Version: {trace.get("schema_version", "unknown")}

## Overview
- Sessions: {len(trace.get("sessions", []))}
- Total AI Suggestions: {metrics["suggestion_metrics"].get("total_suggestions", 0)}
- Code Contributions: {len(trace.get("code_contributions", []))}
- Content Types: Code ({content_type_counts.get("code", 0)}), Text ({content_type_counts.get("text", 0)}), Data ({content_type_counts.get("data", 0)})

## Code/Content Authorship (Lines - v2.0 Model)
- Human-directed, AI-executed: {code_metrics["total_lines"].get("human_directed_ai_executed", 0)} lines
- Human-directed, Human-executed: {code_metrics["total_lines"].get("human_directed_human_executed", 0)} lines
- AI-suggested, Accepted: {code_metrics["total_lines"].get("ai_suggested_accepted", 0)} lines
- AI-suggested, Modified: {code_metrics["total_lines"].get("ai_suggested_modified", 0)} lines
- Human Manual Edit: {code_metrics["total_lines"].get("human_manual_edit", 0)} lines
- Collaborative: {code_metrics["total_lines"].get("collaborative", 0)} lines
{word_section}{row_section}
## AI Suggestion Metrics
- Total suggestions: {metrics["suggestion_metrics"].get("total_suggestions", 0)}
- Accepted: {metrics["suggestion_metrics"].get("accepted_count", 0)}
- Rejected: {metrics["suggestion_metrics"].get("rejected_count", 0)}
- Modified: {metrics["suggestion_metrics"].get("modified_count", 0)}
- Acceptance rate: {metrics["suggestion_metrics"].get("acceptance_rate", "N/A")}
- Lines proposed: {metrics["suggestion_metrics"].get("lines_proposed_total", 0)}
- Lines accepted as-is: {metrics["suggestion_metrics"].get("lines_accepted_as_is", 0)}
- Lines modified by human: {metrics["suggestion_metrics"].get("lines_modified_by_human", 0)}

## Git Integration
- Manual edit commits detected: {code_metrics["git_integration"].get("manual_edit_commits_detected", 0)}
- Manual edit lines added: {code_metrics["git_integration"].get("manual_edit_lines_added", 0)}
- Manual edit lines removed: {code_metrics["git_integration"].get("manual_edit_lines_removed", 0)}

## Error Metrics
- AI errors caught by human: {metrics["error_metrics"].get("ai_errors_caught_by_human", 0)}
- Human errors caught by AI: {metrics["error_metrics"].get("human_errors_caught_by_ai", 0)}
- Total errors: {metrics["error_metrics"].get("total_errors", 0)}

## Intervention Metrics
- Total interventions: {metrics["intervention_metrics"].get("total_interventions", 0)}
- Corrections: {metrics["intervention_metrics"].get("corrections", 0)}
- Overrides: {metrics["intervention_metrics"].get("overrides", 0)}
- Rejections: {metrics["intervention_metrics"].get("rejections", 0)}
"""


def generate_markdown_report(trace: dict) -> str:
    """Generate a full markdown report for publication."""
    metrics = compute_metrics(trace)
    code_metrics = metrics.get("code_metrics", {})
    content_type_counts = code_metrics.get("by_content_type", {})

    # Build word section if text contributions exist
    word_section = ""
    if content_type_counts.get("text", 0) > 0:
        total_words = code_metrics.get("total_words", {})
        word_section = f"""
---

## Text Authorship (Words)

For text content (papers, documentation), TRACE also tracks word-level metrics.

| Category | Words |
|----------|-------|
| Human-directed, AI-executed | {total_words.get("human_directed_ai_executed", 0)} |
| Human-directed, Human-executed | {total_words.get("human_directed_human_executed", 0)} |
| AI-suggested, Accepted as-is | {total_words.get("ai_suggested_accepted", 0)} |
| AI-suggested, Modified by human | {total_words.get("ai_suggested_modified", 0)} |
| Human manual edits | {total_words.get("human_manual_edit", 0)} |
| Collaborative | {total_words.get("collaborative", 0)} |

### Word Source Percentages

- Human direction: {code_metrics.get("by_source_words", {}).get("human_direction_percentage", "N/A")}%
- AI suggestion: {code_metrics.get("by_source_words", {}).get("ai_suggestion_percentage", "N/A")}%
- Human manual: {code_metrics.get("by_source_words", {}).get("human_manual_percentage", "N/A")}%
"""

    # Build row section if data contributions exist
    row_section = ""
    if content_type_counts.get("data", 0) > 0:
        total_rows = code_metrics.get("total_rows", {})
        row_section = f"""
---

## Data Authorship (Rows)

For data content (datasets, CSV files), TRACE tracks row-level metrics.

| Category | Rows |
|----------|------|
| Human-directed, AI-executed | {total_rows.get("human_directed_ai_executed", 0)} |
| Human-directed, Human-executed | {total_rows.get("human_directed_human_executed", 0)} |
| AI-suggested, Accepted as-is | {total_rows.get("ai_suggested_accepted", 0)} |
| AI-suggested, Modified by human | {total_rows.get("ai_suggested_modified", 0)} |
| Human manual edits | {total_rows.get("human_manual_edit", 0)} |
| Collaborative | {total_rows.get("collaborative", 0)} |

### Row Source Percentages

- Human direction: {code_metrics.get("by_source_rows", {}).get("human_direction_percentage", "N/A")}%
- AI suggestion: {code_metrics.get("by_source_rows", {}).get("ai_suggestion_percentage", "N/A")}%
- Human manual: {code_metrics.get("by_source_rows", {}).get("human_manual_percentage", "N/A")}%
"""

    report = f"""# TRACE Report: {trace["metadata"].get("project", "Unknown Project")}

**Generated**: {datetime.now().isoformat()}
**Schema Version**: {trace.get("schema_version", "unknown")}

---

## Executive Summary

This report documents the AI-human collaboration for this project using the TRACE
(Transparent Research AI Collaboration Environment) protocol v2.0.

### Key Metrics at a Glance

| Metric | Value |
|--------|-------|
| Total Sessions | {len(trace.get("sessions", []))} |
| Total Contributions | {len(trace.get("code_contributions", []))} |
| Code Contributions | {content_type_counts.get("code", 0)} |
| Text Contributions | {content_type_counts.get("text", 0)} |
| Data Contributions | {content_type_counts.get("data", 0)} |
| Human-Directed Lines (AI executed) | {code_metrics["total_lines"].get("human_directed_ai_executed", 0)} |
| AI-Suggested Lines (Accepted) | {code_metrics["total_lines"].get("ai_suggested_accepted", 0)} |
| AI-Suggested Lines (Modified) | {code_metrics["total_lines"].get("ai_suggested_modified", 0)} |
| Human Manual Edit Lines | {code_metrics["total_lines"].get("human_manual_edit", 0)} |
| Total AI Suggestions | {metrics["suggestion_metrics"].get("total_suggestions", 0)} |
| AI Suggestion Acceptance Rate | {metrics["suggestion_metrics"].get("acceptance_rate", "N/A")} |
| Human Interventions | {metrics["intervention_metrics"].get("total_interventions", 0)} |

---

## AI Suggestion Analysis

### Suggestion Outcomes

| Status | Count | Lines |
|--------|-------|-------|
| Accepted | {metrics["suggestion_metrics"].get("accepted_count", 0)} | {metrics["suggestion_metrics"].get("lines_accepted_as_is", 0)} |
| Modified | {metrics["suggestion_metrics"].get("modified_count", 0)} | {metrics["suggestion_metrics"].get("lines_modified_by_human", 0)} |
| Rejected | {metrics["suggestion_metrics"].get("rejected_count", 0)} | {metrics["suggestion_metrics"].get("lines_rejected", 0)} |

### Acceptance Rates

- **Overall acceptance rate**: {metrics["suggestion_metrics"].get("acceptance_rate", "N/A")}
- **Modification rate**: {metrics["suggestion_metrics"].get("modification_rate", "N/A")}
- **Rejection rate**: {metrics["suggestion_metrics"].get("rejection_rate", "N/A")}

---

## Content Authorship Analysis (v2.0 Model)

### Direction vs Execution

The v2.0 authorship model distinguishes between:
- **Direction**: Who decided the change should happen
- **Execution**: Who wrote the actual content

### Line Breakdown (All Content Types)

| Category | Lines |
|----------|-------|
| Human-directed, AI-executed | {code_metrics["total_lines"].get("human_directed_ai_executed", 0)} |
| Human-directed, Human-executed | {code_metrics["total_lines"].get("human_directed_human_executed", 0)} |
| AI-suggested, Accepted as-is | {code_metrics["total_lines"].get("ai_suggested_accepted", 0)} |
| AI-suggested, Modified by human | {code_metrics["total_lines"].get("ai_suggested_modified", 0)} |
| Human manual edits | {code_metrics["total_lines"].get("human_manual_edit", 0)} |
| Collaborative | {code_metrics["total_lines"].get("collaborative", 0)} |

### Line Source Percentages

- Human direction: {code_metrics["by_source"].get("human_direction_percentage", "N/A")}%
- AI suggestion: {code_metrics["by_source"].get("ai_suggestion_percentage", "N/A")}%
- Human manual: {code_metrics["by_source"].get("human_manual_percentage", "N/A")}%
{word_section}{row_section}
---

## Git Integration

Commits with [HUMAN-EDIT] tag are automatically detected.

| Metric | Value |
|--------|-------|
| Manual edit commits | {code_metrics["git_integration"].get("manual_edit_commits_detected", 0)} |
| Lines added | {code_metrics["git_integration"].get("manual_edit_lines_added", 0)} |
| Lines removed | {code_metrics["git_integration"].get("manual_edit_lines_removed", 0)} |

---

## Methodology

This project used the TRACE (Transparent Research AI Collaboration Environment) protocol v2.0
to document all AI-human collaboration. TRACE v2.0 captures:

1. **Direction source**: Who decided changes should happen (human_directed vs ai_suggested)
2. **Execution**: Who wrote the content (AI vs human)
3. **AI suggestions**: Full tracking of AI proposals with accept/reject/modify outcomes
4. **Git integration**: Automatic detection of [HUMAN-EDIT] commits
5. **Multi-content-type support**: Code (lines), Text (lines + words), Data (lines + rows)
6. **Precise metrics**: Tracking at multiple granularity levels for different content types

For more information about TRACE, see the protocol documentation.
"""

    return report


# ============================================================
# Main Entry Point
# ============================================================


async def main():
    """Run the MCP server."""
    import sys

    print("Starting TRACE MCP Server v2.0", file=sys.stderr)
    print(f"TRACE file path: {TRACE_PATH}", file=sys.stderr)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
