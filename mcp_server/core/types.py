"""
TRACE Protocol Core Type Definitions

These types are framework-agnostic and form the core vocabulary
for AI-human collaboration tracking.
"""

from enum import Enum
from typing import NotRequired, TypedDict


class ContentType(str, Enum):
    """Type of content being contributed."""

    CODE = "code"  # Source code (tracked by lines)
    TEXT = "text"  # Documents (tracked by lines + words)
    DATA = "data"  # Data files (tracked by lines + rows)


class ContributionType(str, Enum):
    """Nature of the contribution."""

    CREATION = "creation"
    MODIFICATION = "modification"
    REFACTOR = "refactor"
    BUGFIX = "bugfix"
    OPTIMIZATION = "optimization"
    DELETION = "deletion"


class Direction(str, Enum):
    """Who decided this change should happen."""

    HUMAN_DIRECTED = "human_directed"
    AI_SUGGESTED = "ai_suggested"
    COLLABORATIVE = "collaborative"


class ScientificStage(str, Enum):
    """Stage in the scientific method."""

    EXPLORATION = "exploration"
    HYPOTHESIS = "hypothesis"
    DATA_COLLECTION = "data_collection"
    ANALYSIS = "analysis"
    INTERPRETATION = "interpretation"
    VALIDATION = "validation"
    WRITING = "writing"


class SuggestionType(str, Enum):
    """Category of AI suggestion."""

    CODE_CHANGE = "code_change"
    ARCHITECTURE = "architecture"
    APPROACH = "approach"
    BUGFIX = "bugfix"
    OPTIMIZATION = "optimization"
    REFACTOR = "refactor"
    FEATURE = "feature"


class SuggestionStatus(str, Enum):
    """Status of an AI suggestion."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MODIFIED = "modified"


class Confidence(str, Enum):
    """Confidence level."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class InterventionType(str, Enum):
    """Type of human intervention on AI output."""

    CORRECTION = "correction"
    OVERRIDE = "override"
    REJECTION = "rejection"
    REFINEMENT = "refinement"


class ErrorType(str, Enum):
    """Type of error."""

    SYNTAX = "syntax"
    LOGIC = "logic"
    RUNTIME = "runtime"
    DESIGN = "design"
    SECURITY = "security"
    PERFORMANCE = "performance"


class ErrorOrigin(str, Enum):
    """Who produced the error."""

    AI = "ai"
    HUMAN = "human"


class ErrorDetector(str, Enum):
    """Who/what detected the error."""

    AI = "ai"
    HUMAN = "human"
    AUTOMATED_TEST = "automated_test"


class EvaluationType(str, Enum):
    """Type of evaluation."""

    UNIT_TEST = "unit_test"
    INTEGRATION_TEST = "integration_test"
    BENCHMARK = "benchmark"
    HUMAN_EVAL = "human_eval"
    CODE_REVIEW = "code_review"
    VALIDATION = "validation"


class EvaluationScope(str, Enum):
    """Scope of evaluation."""

    FUNCTION = "function"
    MODULE = "module"
    SYSTEM = "system"


# TypedDict definitions for structured data


class Platform(TypedDict):
    """Platform information."""

    os: str
    arch: str
    version: NotRequired[str]


class Runtime(TypedDict):
    """Runtime information."""

    language: NotRequired[str]
    version: NotRequired[str]


class AgentInfo(TypedDict):
    """Agent/model information."""

    name: str
    framework: NotRequired[str]
    version: NotRequired[str]
    parameters: NotRequired[dict]


class MCPInfo(TypedDict):
    """MCP protocol information."""

    spec_version: NotRequired[str]
    server_version: NotRequired[str]


class GitState(TypedDict):
    """Git repository state."""

    commit: NotRequired[str]
    branch: NotRequired[str]
    dirty: NotRequired[bool]


class Environment(TypedDict):
    """Execution environment for reproducibility."""

    id: str
    captured_at: str
    platform: Platform
    runtime: NotRequired[Runtime]
    agent: AgentInfo
    mcp: NotRequired[MCPInfo]
    dependencies_hash: NotRequired[str]
    git_state: NotRequired[GitState]


class Execution(TypedDict):
    """Execution attribution (who wrote the content)."""

    ai_lines: NotRequired[int]
    human_lines: NotRequired[int]
    ai_words: NotRequired[int]
    human_words: NotRequired[int]
    ai_rows: NotRequired[int]
    human_rows: NotRequired[int]


class Authorship(TypedDict):
    """Authorship attribution."""

    direction: str  # Direction enum value
    execution: NotRequired[Execution]


class SuggestionResolution(TypedDict):
    """Resolution of an AI suggestion."""

    resolved_at: str
    lines_accepted: NotRequired[int]
    lines_modified: NotRequired[int]
    lines_rejected: NotRequired[int]
    rationale: NotRequired[str]
    modification_description: NotRequired[str]


class IntegrityMetadata(TypedDict):
    """Cryptographic integrity metadata."""

    entry_hash: str
    previous_hash: str
    chain_position: int


class EvaluationTarget(TypedDict):
    """Target of an evaluation."""

    files: NotRequired[list[str]]
    scope: NotRequired[str]
    description: NotRequired[str]


class EvaluationMetrics(TypedDict):
    """Metrics from an evaluation."""

    tests_passed: NotRequired[int]
    tests_failed: NotRequired[int]
    tests_skipped: NotRequired[int]
    coverage: NotRequired[float]
    duration_ms: NotRequired[int]
    score: NotRequired[float]
