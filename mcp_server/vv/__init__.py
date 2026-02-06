"""
TRACE Verification & Validation (V&V) System

A comprehensive system to ensure TRACE accurately tracks AI-human collaboration
for scientific work. Provides cryptographic integrity, change verification,
and trust metrics suitable for peer review.

Modules:
- snapshots: Content snapshot system for capturing file states
- verification: Change verification engine
- git_reconcile: Git cross-validation
- integrity: Cryptographic hash chain
- text_analysis: LaTeX/text parsing for section-level tracking
- trust_metrics: Trust score computation
- reports: Report generation for V&V results
"""

from .git_reconcile import GitReconciler, reconcile_with_git
from .integrity import IntegrityChain, compute_entry_hash, verify_chain_integrity
from .reports import ReportGenerator, generate_trust_report
from .snapshots import SnapshotManager, create_snapshot, get_snapshot
from .text_analysis import TextAnalyzer, analyze_text_file, get_section_breakdown
from .trust_metrics import TrustCalculator, compute_trust_score
from .verification import VerificationEngine, verify_entry, verify_session

__version__ = "1.0.0"

__all__ = [
    # Snapshots
    "SnapshotManager",
    "create_snapshot",
    "get_snapshot",
    # Verification
    "VerificationEngine",
    "verify_entry",
    "verify_session",
    # Git reconciliation
    "GitReconciler",
    "reconcile_with_git",
    # Integrity
    "IntegrityChain",
    "verify_chain_integrity",
    "compute_entry_hash",
    # Text analysis
    "TextAnalyzer",
    "analyze_text_file",
    "get_section_breakdown",
    # Trust metrics
    "TrustCalculator",
    "compute_trust_score",
    # Reports
    "ReportGenerator",
    "generate_trust_report",
]
