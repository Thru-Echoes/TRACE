"""
TRACE V&V Text/Writing Analysis

Specialized analysis for manuscripts (LaTeX, Markdown, etc.):
- Section-level tracking
- Paragraph-level fingerprints
- Word-level verification
"""

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any


class TextAnalyzer:
    """Analyzes text documents for section-level authorship tracking."""

    # LaTeX section commands in order of hierarchy
    LATEX_SECTIONS = [
        (r"\\chapter\{([^}]+)\}", "chapter"),
        (r"\\section\{([^}]+)\}", "section"),
        (r"\\subsection\{([^}]+)\}", "subsection"),
        (r"\\subsubsection\{([^}]+)\}", "subsubsection"),
        (r"\\paragraph\{([^}]+)\}", "paragraph"),
    ]

    # Markdown heading patterns
    MARKDOWN_HEADINGS = [
        (r"^#{1}\s+(.+)$", "h1"),
        (r"^#{2}\s+(.+)$", "h2"),
        (r"^#{3}\s+(.+)$", "h3"),
        (r"^#{4}\s+(.+)$", "h4"),
        (r"^#{5}\s+(.+)$", "h5"),
        (r"^#{6}\s+(.+)$", "h6"),
    ]

    def __init__(self):
        """Initialize the text analyzer."""
        pass

    def _detect_file_type(self, file_path: Path) -> str:
        """Detect the type of text file."""
        suffix = file_path.suffix.lower()
        if suffix in [".tex", ".latex"]:
            return "latex"
        elif suffix in [".md", ".markdown"]:
            return "markdown"
        elif suffix in [".rst"]:
            return "rst"
        elif suffix in [".txt"]:
            return "plaintext"
        else:
            return "unknown"

    def _compute_fingerprint(self, text: str) -> str:
        """Compute a fingerprint for text content."""
        # Normalize whitespace
        normalized = " ".join(text.split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def _count_words(self, text: str) -> int:
        """Count words in text."""
        # Remove LaTeX commands for more accurate word count
        cleaned = re.sub(r"\\[a-zA-Z]+\{[^}]*\}", "", text)
        cleaned = re.sub(r"\\[a-zA-Z]+", "", cleaned)
        cleaned = re.sub(r"[{}\\$%&]", "", cleaned)
        return len(cleaned.split())

    def _count_sentences(self, text: str) -> int:
        """Count sentences in text."""
        # Simple sentence detection
        sentences = re.split(r"[.!?]+", text)
        return len([s for s in sentences if s.strip()])

    def _extract_latex_sections(self, content: str) -> list[dict[str, Any]]:
        """Extract sections from LaTeX content."""
        sections = []
        lines = content.split("\n")

        current_section = {
            "title": "Preamble",
            "level": "preamble",
            "start_line": 1,
            "content_lines": [],
            "line_numbers": [],
        }

        for i, line in enumerate(lines, 1):
            matched = False

            for pattern, level in self.LATEX_SECTIONS:
                match = re.search(pattern, line)
                if match:
                    # Save previous section
                    if current_section["content_lines"]:
                        current_section["end_line"] = i - 1
                        sections.append(current_section)

                    # Start new section
                    current_section = {
                        "title": match.group(1),
                        "level": level,
                        "start_line": i,
                        "content_lines": [],
                        "line_numbers": [],
                    }
                    matched = True
                    break

            if not matched:
                current_section["content_lines"].append(line)
                current_section["line_numbers"].append(i)

        # Add final section
        if current_section["content_lines"]:
            current_section["end_line"] = len(lines)
            sections.append(current_section)

        return sections

    def _extract_markdown_sections(self, content: str) -> list[dict[str, Any]]:
        """Extract sections from Markdown content."""
        sections = []
        lines = content.split("\n")

        current_section = {
            "title": "Introduction",
            "level": "h0",
            "start_line": 1,
            "content_lines": [],
            "line_numbers": [],
        }

        for i, line in enumerate(lines, 1):
            matched = False

            for pattern, level in self.MARKDOWN_HEADINGS:
                match = re.match(pattern, line, re.MULTILINE)
                if match:
                    # Save previous section
                    if current_section["content_lines"]:
                        current_section["end_line"] = i - 1
                        sections.append(current_section)

                    # Start new section
                    current_section = {
                        "title": match.group(1).strip(),
                        "level": level,
                        "start_line": i,
                        "content_lines": [],
                        "line_numbers": [],
                    }
                    matched = True
                    break

            if not matched:
                current_section["content_lines"].append(line)
                current_section["line_numbers"].append(i)

        # Add final section
        if current_section["content_lines"]:
            current_section["end_line"] = len(lines)
            sections.append(current_section)

        return sections

    def analyze_file(self, file_path: Path) -> dict[str, Any]:
        """
        Analyze a text file for section-level breakdown.

        Args:
            file_path: Path to the text file

        Returns:
            Analysis result with sections and metrics
        """
        file_path = Path(file_path)

        if not file_path.exists():
            return {"error": f"File not found: {file_path}"}

        with open(file_path, encoding="utf-8", errors="replace") as f:
            content = f.read()

        file_type = self._detect_file_type(file_path)

        # Extract sections based on file type
        if file_type == "latex":
            sections = self._extract_latex_sections(content)
        elif file_type == "markdown":
            sections = self._extract_markdown_sections(content)
        else:
            # Treat as single section for unknown types
            sections = [
                {
                    "title": "Content",
                    "level": "document",
                    "start_line": 1,
                    "end_line": len(content.split("\n")),
                    "content_lines": content.split("\n"),
                    "line_numbers": list(range(1, len(content.split("\n")) + 1)),
                }
            ]

        # Compute metrics for each section
        section_analyses = []
        total_words = 0
        total_lines = 0

        for section in sections:
            section_content = "\n".join(section["content_lines"])
            word_count = self._count_words(section_content)
            line_count = len(section["content_lines"])
            sentence_count = self._count_sentences(section_content)

            total_words += word_count
            total_lines += line_count

            section_analyses.append(
                {
                    "title": section["title"],
                    "level": section["level"],
                    "start_line": section["start_line"],
                    "end_line": section.get("end_line", section["start_line"] + line_count),
                    "line_count": line_count,
                    "word_count": word_count,
                    "sentence_count": sentence_count,
                    "fingerprint": self._compute_fingerprint(section_content),
                    "avg_sentence_length": round(word_count / sentence_count, 1) if sentence_count > 0 else 0,
                }
            )

        return {
            "file_path": str(file_path),
            "file_type": file_type,
            "timestamp": datetime.now().isoformat(),
            "total_lines": total_lines,
            "total_words": total_words,
            "total_sections": len(section_analyses),
            "document_fingerprint": self._compute_fingerprint(content),
            "sections": section_analyses,
        }

    def compare_versions(self, before_content: str, after_content: str, file_type: str = "latex") -> dict[str, Any]:
        """
        Compare two versions of a document.

        Args:
            before_content: Content before changes
            after_content: Content after changes
            file_type: Type of document (latex, markdown, etc.)

        Returns:
            Comparison result with section-level diff
        """
        # Analyze both versions
        if file_type == "latex":
            before_sections = self._extract_latex_sections(before_content)
            after_sections = self._extract_latex_sections(after_content)
        elif file_type == "markdown":
            before_sections = self._extract_markdown_sections(before_content)
            after_sections = self._extract_markdown_sections(after_content)
        else:
            before_sections = [{"title": "Content", "content_lines": before_content.split("\n")}]
            after_sections = [{"title": "Content", "content_lines": after_content.split("\n")}]

        # Build maps by title
        before_map = {s["title"]: s for s in before_sections}
        after_map = {s["title"]: s for s in after_sections}

        # Compare sections
        _section_changes = []  # Reserved for detailed change tracking
        new_sections = []
        removed_sections = []
        modified_sections = []

        # Check for new and modified sections
        for title, after_sec in after_map.items():
            if title not in before_map:
                new_sections.append(
                    {
                        "title": title,
                        "line_count": len(after_sec["content_lines"]),
                        "word_count": self._count_words("\n".join(after_sec["content_lines"])),
                    }
                )
            else:
                before_sec = before_map[title]
                before_fp = self._compute_fingerprint("\n".join(before_sec["content_lines"]))
                after_fp = self._compute_fingerprint("\n".join(after_sec["content_lines"]))

                if before_fp != after_fp:
                    before_words = self._count_words("\n".join(before_sec["content_lines"]))
                    after_words = self._count_words("\n".join(after_sec["content_lines"]))

                    modified_sections.append(
                        {
                            "title": title,
                            "before_lines": len(before_sec["content_lines"]),
                            "after_lines": len(after_sec["content_lines"]),
                            "before_words": before_words,
                            "after_words": after_words,
                            "word_change": after_words - before_words,
                            "line_change": len(after_sec["content_lines"]) - len(before_sec["content_lines"]),
                        }
                    )

        # Check for removed sections
        for title in before_map:
            if title not in after_map:
                before_sec = before_map[title]
                removed_sections.append(
                    {
                        "title": title,
                        "line_count": len(before_sec["content_lines"]),
                        "word_count": self._count_words("\n".join(before_sec["content_lines"])),
                    }
                )

        # Compute totals
        total_words_added = sum(s["word_count"] for s in new_sections)
        total_words_added += sum(max(0, s["word_change"]) for s in modified_sections)

        total_words_removed = sum(s["word_count"] for s in removed_sections)
        total_words_removed += sum(abs(min(0, s["word_change"])) for s in modified_sections)

        return {
            "file_type": file_type,
            "timestamp": datetime.now().isoformat(),
            "before_document_fingerprint": self._compute_fingerprint(before_content),
            "after_document_fingerprint": self._compute_fingerprint(after_content),
            "summary": {
                "new_sections": len(new_sections),
                "removed_sections": len(removed_sections),
                "modified_sections": len(modified_sections),
                "unchanged_sections": len(after_map) - len(new_sections) - len(modified_sections),
                "total_words_added": total_words_added,
                "total_words_removed": total_words_removed,
                "net_word_change": total_words_added - total_words_removed,
            },
            "new_sections": new_sections,
            "removed_sections": removed_sections,
            "modified_sections": modified_sections,
        }

    def get_section_authorship(
        self, sections: list[dict[str, Any]], trace_contributions: list[dict[str, Any]], file_path: str
    ) -> list[dict[str, Any]]:
        """
        Map TRACE contributions to document sections.

        Args:
            sections: List of sections from analyze_file
            trace_contributions: TRACE code_contributions for this file
            file_path: Path to the file

        Returns:
            Sections with authorship information
        """
        # Filter contributions for this file
        file_contributions = [c for c in trace_contributions if c.get("file_path") == file_path]

        # For each section, try to determine authorship
        sections_with_authorship = []

        for section in sections:
            section_start = section.get("start_line", 1)
            section_end = section.get("end_line", section_start + section.get("line_count", 0))

            # Find contributions that might affect this section
            # This is an approximation since contributions may not have line ranges
            relevant_contributions = []
            for contrib in file_contributions:
                # If contribution has line range, check overlap
                contrib_start = contrib.get("line_start")
                contrib_end = contrib.get("line_end")

                if contrib_start and contrib_end:
                    # Check for overlap
                    if contrib_start <= section_end and contrib_end >= section_start:
                        relevant_contributions.append(contrib)
                else:
                    # No line range, assume it could be relevant
                    relevant_contributions.append(contrib)

            # Compute authorship summary
            authorship_summary = {
                "human_directed_ai_executed": 0,
                "human_directed_human_executed": 0,
                "ai_suggested_accepted": 0,
                "ai_suggested_modified": 0,
                "human_manual_edit": 0,
                "collaborative": 0,
            }

            for contrib in relevant_contributions:
                auth = contrib.get("authorship", {})

                # Human directed
                hd = auth.get("human_directed", {})
                authorship_summary["human_directed_ai_executed"] += hd.get("ai_executed_lines", 0)
                authorship_summary["human_directed_human_executed"] += hd.get("human_executed_lines", 0)

                # AI suggested
                ais = auth.get("ai_suggested", {})
                authorship_summary["ai_suggested_accepted"] += ais.get("accepted_lines", 0)
                authorship_summary["ai_suggested_modified"] += ais.get("modified_lines", 0)

                # Human manual
                hm = auth.get("human_manual_edit", {})
                authorship_summary["human_manual_edit"] += hm.get("lines_added", 0) + hm.get("lines_modified", 0)

                # Collaborative
                coll = auth.get("collaborative", {})
                authorship_summary["collaborative"] += coll.get("lines", 0)

            sections_with_authorship.append(
                {
                    **section,
                    "authorship": authorship_summary,
                    "contribution_ids": [c.get("id") for c in relevant_contributions],
                }
            )

        return sections_with_authorship


# Module-level convenience functions
def analyze_text_file(file_path: Path) -> dict[str, Any]:
    """Analyze a text file using a new analyzer instance."""
    analyzer = TextAnalyzer()
    return analyzer.analyze_file(file_path)


def get_section_breakdown(file_path: Path, trace_contributions: list[dict[str, Any]]) -> dict[str, Any]:
    """Get section breakdown with authorship using a new analyzer instance."""
    analyzer = TextAnalyzer()
    analysis = analyzer.analyze_file(file_path)

    if "error" in analysis:
        return analysis

    sections_with_auth = analyzer.get_section_authorship(analysis["sections"], trace_contributions, str(file_path))

    analysis["sections"] = sections_with_auth
    return analysis
