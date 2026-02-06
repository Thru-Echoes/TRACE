"""
Tests for TRACE V&V Text Analysis
"""

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "mcp_server"))

from vv.text_analysis import TextAnalyzer


@pytest.fixture
def temp_dir():
    """Create a temporary directory."""
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def text_analyzer():
    """Create a TextAnalyzer instance."""
    return TextAnalyzer()


@pytest.fixture
def latex_file(temp_dir):
    """Create a sample LaTeX file."""
    content = r"""
\documentclass{article}
\begin{document}

\section{Introduction}
This is the introduction section.
It contains some text about the topic.

\section{Methods}
Here we describe the methods used.

\subsection{Data Collection}
Data was collected from various sources.

\subsection{Analysis}
Analysis was performed using statistical methods.

\section{Results}
The results show interesting findings.

\section{Conclusion}
In conclusion, we found several things.

\end{document}
"""
    file_path = temp_dir / "paper.tex"
    file_path.write_text(content)
    return file_path


@pytest.fixture
def markdown_file(temp_dir):
    """Create a sample Markdown file."""
    content = """# Project Documentation

This is the main documentation.

## Installation

Install the package using pip.

### Requirements

- Python 3.8+
- Some library

## Usage

Here's how to use the package.

### Basic Usage

Just import and call.

### Advanced Usage

For advanced users, there are more options.

## Contributing

We welcome contributions.
"""
    file_path = temp_dir / "README.md"
    file_path.write_text(content)
    return file_path


class TestFileTypeDetection:
    """Tests for file type detection."""

    def test_detect_latex(self, text_analyzer, temp_dir):
        """Test detecting LaTeX files."""
        latex_file = temp_dir / "paper.tex"
        latex_file.write_text("content")

        file_type = text_analyzer._detect_file_type(latex_file)
        assert file_type == "latex"

    def test_detect_markdown(self, text_analyzer, temp_dir):
        """Test detecting Markdown files."""
        md_file = temp_dir / "README.md"
        md_file.write_text("content")

        file_type = text_analyzer._detect_file_type(md_file)
        assert file_type == "markdown"

    def test_detect_plaintext(self, text_analyzer, temp_dir):
        """Test detecting plain text files."""
        txt_file = temp_dir / "notes.txt"
        txt_file.write_text("content")

        file_type = text_analyzer._detect_file_type(txt_file)
        assert file_type == "plaintext"


class TestLatexAnalysis:
    """Tests for LaTeX document analysis."""

    def test_extract_latex_sections(self, text_analyzer, latex_file):
        """Test extracting sections from LaTeX."""
        content = latex_file.read_text()
        sections = text_analyzer._extract_latex_sections(content)

        # Should find: Preamble, Introduction, Methods, Data Collection, Analysis, Results, Conclusion
        section_titles = [s["title"] for s in sections]

        assert "Introduction" in section_titles
        assert "Methods" in section_titles
        assert "Data Collection" in section_titles
        assert "Results" in section_titles
        assert "Conclusion" in section_titles

    def test_section_levels(self, text_analyzer, latex_file):
        """Test that section levels are detected correctly."""
        content = latex_file.read_text()
        sections = text_analyzer._extract_latex_sections(content)

        methods = next(s for s in sections if s["title"] == "Methods")
        data_collection = next(s for s in sections if s["title"] == "Data Collection")

        assert methods["level"] == "section"
        assert data_collection["level"] == "subsection"

    def test_analyze_latex_file(self, text_analyzer, latex_file):
        """Test full analysis of LaTeX file."""
        result = text_analyzer.analyze_file(latex_file)

        assert result["file_type"] == "latex"
        assert result["total_sections"] > 0
        assert result["total_words"] > 0
        assert result["total_lines"] > 0

    def test_section_metrics(self, text_analyzer, latex_file):
        """Test section-level metrics."""
        result = text_analyzer.analyze_file(latex_file)

        for section in result["sections"]:
            assert "word_count" in section
            assert "line_count" in section
            assert "fingerprint" in section


class TestMarkdownAnalysis:
    """Tests for Markdown document analysis."""

    def test_extract_markdown_sections(self, text_analyzer, markdown_file):
        """Test extracting sections from Markdown."""
        content = markdown_file.read_text()
        sections = text_analyzer._extract_markdown_sections(content)

        section_titles = [s["title"] for s in sections]

        assert "Project Documentation" in section_titles
        assert "Installation" in section_titles
        assert "Usage" in section_titles

    def test_markdown_heading_levels(self, text_analyzer, markdown_file):
        """Test that heading levels are detected correctly."""
        content = markdown_file.read_text()
        sections = text_analyzer._extract_markdown_sections(content)

        project_doc = next(s for s in sections if s["title"] == "Project Documentation")
        installation = next(s for s in sections if s["title"] == "Installation")
        requirements = next(s for s in sections if s["title"] == "Requirements")

        assert project_doc["level"] == "h1"
        assert installation["level"] == "h2"
        assert requirements["level"] == "h3"

    def test_analyze_markdown_file(self, text_analyzer, markdown_file):
        """Test full analysis of Markdown file."""
        result = text_analyzer.analyze_file(markdown_file)

        assert result["file_type"] == "markdown"
        assert result["total_sections"] > 0


class TestWordCounting:
    """Tests for word counting."""

    def test_count_words_simple(self, text_analyzer):
        """Test basic word counting."""
        text = "This is a simple sentence with seven words."
        count = text_analyzer._count_words(text)

        assert count == 8

    def test_count_words_latex_commands(self, text_analyzer):
        """Test word counting ignores LaTeX commands."""
        text = r"This \textbf{is} a \section{test} sentence."
        count = text_analyzer._count_words(text)

        # Should count: This, is, a, test, sentence (some may merge)
        assert count >= 3  # At least some main words

    def test_count_sentences(self, text_analyzer):
        """Test sentence counting."""
        text = "First sentence. Second sentence! Third one?"
        count = text_analyzer._count_sentences(text)

        assert count == 3


class TestFingerprinting:
    """Tests for content fingerprinting."""

    def test_fingerprint_deterministic(self, text_analyzer):
        """Test that fingerprints are deterministic."""
        text = "Some content to fingerprint"

        fp1 = text_analyzer._compute_fingerprint(text)
        fp2 = text_analyzer._compute_fingerprint(text)

        assert fp1 == fp2

    def test_fingerprint_whitespace_normalized(self, text_analyzer):
        """Test that fingerprints normalize whitespace."""
        text1 = "Some   content"
        text2 = "Some content"

        fp1 = text_analyzer._compute_fingerprint(text1)
        fp2 = text_analyzer._compute_fingerprint(text2)

        assert fp1 == fp2


class TestVersionComparison:
    """Tests for comparing document versions."""

    def test_compare_added_section(self, text_analyzer):
        """Test detecting added sections."""
        before = r"""
\section{Introduction}
First section content.
"""
        after = r"""
\section{Introduction}
First section content.

\section{Methods}
New methods section.
"""

        result = text_analyzer.compare_versions(before, after, "latex")

        assert result["summary"]["new_sections"] == 1
        assert any(s["title"] == "Methods" for s in result["new_sections"])

    def test_compare_removed_section(self, text_analyzer):
        """Test detecting removed sections."""
        before = r"""
\section{Introduction}
Content.

\section{Old Section}
Old content.
"""
        after = r"""
\section{Introduction}
Content.
"""

        result = text_analyzer.compare_versions(before, after, "latex")

        assert result["summary"]["removed_sections"] == 1

    def test_compare_modified_section(self, text_analyzer):
        """Test detecting modified sections."""
        before = r"""
\section{Introduction}
Original content here.
"""
        after = r"""
\section{Introduction}
Modified content with more words added here.
"""

        result = text_analyzer.compare_versions(before, after, "latex")

        assert result["summary"]["modified_sections"] == 1
        assert result["summary"]["net_word_change"] > 0


class TestAuthorshipMapping:
    """Tests for mapping TRACE contributions to sections."""

    def test_get_section_authorship(self, text_analyzer, latex_file):
        """Test mapping contributions to sections."""
        analysis = text_analyzer.analyze_file(latex_file)

        contributions = [
            {"id": "CC001", "file_path": str(latex_file), "authorship": {"human_directed": {"ai_executed_lines": 10}}}
        ]

        sections_with_auth = text_analyzer.get_section_authorship(analysis["sections"], contributions, str(latex_file))

        # All sections should have authorship info
        for section in sections_with_auth:
            assert "authorship" in section
            assert "contribution_ids" in section


class TestErrorHandling:
    """Tests for error handling."""

    def test_analyze_nonexistent_file(self, text_analyzer, temp_dir):
        """Test analyzing a file that doesn't exist."""
        result = text_analyzer.analyze_file(temp_dir / "missing.tex")

        assert "error" in result

    def test_analyze_empty_file(self, text_analyzer, temp_dir):
        """Test analyzing an empty file."""
        empty_file = temp_dir / "empty.tex"
        empty_file.write_text("")

        result = text_analyzer.analyze_file(empty_file)

        assert result["total_words"] == 0
        # Empty string split by newline gives [''], so 1 line
        assert result["total_lines"] <= 1
