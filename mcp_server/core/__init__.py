"""
TRACE Protocol Core Module

Framework-agnostic audit schema for AI-human collaboration tracking.
"""

from .environment import EnvironmentCapture, capture_environment
from .evaluation import EvaluationLogger, log_evaluation
from .metrics import MetricsComputer, compute_authorship_metrics, compute_suggestion_metrics
from .types import (
    ContentType,
    ContributionType,
    Direction,
    EvaluationType,
    ScientificStage,
    SuggestionStatus,
    SuggestionType,
)

__all__ = [
    # Types
    "ContentType",
    "ContributionType",
    "Direction",
    "ScientificStage",
    "SuggestionStatus",
    "SuggestionType",
    "EvaluationType",
    # Environment
    "EnvironmentCapture",
    "capture_environment",
    # Evaluation
    "EvaluationLogger",
    "log_evaluation",
    # Metrics
    "MetricsComputer",
    "compute_authorship_metrics",
    "compute_suggestion_metrics",
]

CORE_VERSION = "3.0.0"
SCHEMA_URI = "urn:trace:schema:core:v3"
