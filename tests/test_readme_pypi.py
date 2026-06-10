"""README must render correctly on PyPI.

PyPI renders the README (pyproject ``readme = "README.md"``) without any repo
context: relative markdown links 404 on the project page and relative image
srcs simply don't load. Every file link must be an absolute GitHub URL and
every image an absolute raw.githubusercontent.com URL. In-page anchors (``#…``)
and mailto links are fine.
"""

from __future__ import annotations

import re
from pathlib import Path

README = Path(__file__).parent.parent / "README.md"

_ALLOWED_LINK_PREFIXES = ("http://", "https://", "#", "mailto:")


def get_relative_md_links(text: str) -> list[str]:
    """Return markdown inline-link targets that would break on PyPI."""
    targets = re.findall(r"\]\(([^)\s]+)\)", text)
    return [t for t in targets if not t.startswith(_ALLOWED_LINK_PREFIXES)]


def get_relative_img_srcs(text: str) -> list[str]:
    """Return HTML <img> src values that would break on PyPI."""
    srcs = re.findall(r'<img[^>]*\bsrc="([^"]+)"', text)
    return [s for s in srcs if not s.startswith(("http://", "https://"))]


class TestReadmeRendersOnPyPI:
    def test_no_relative_markdown_links(self) -> None:
        rel = get_relative_md_links(README.read_text(encoding="utf-8"))
        assert not rel, (
            f"README.md contains relative links that 404 on the PyPI project page; "
            f"use absolute https://github.com/Thru-Echoes/TRACE/blob/main/ URLs: {sorted(set(rel))}"
        )

    def test_no_relative_image_srcs(self) -> None:
        rel = get_relative_img_srcs(README.read_text(encoding="utf-8"))
        assert not rel, (
            f"README.md contains relative <img> srcs that don't load on PyPI; "
            f"use absolute https://raw.githubusercontent.com/Thru-Echoes/TRACE/main/ URLs: {sorted(set(rel))}"
        )
