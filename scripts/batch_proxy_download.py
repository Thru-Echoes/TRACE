#!/usr/bin/env python3
"""Batch download PDFs through Berkeley Library proxy using saved session cookie.

Usage:
    python scripts/batch_proxy_download.py

Requires: ezproxy cookie saved at /tmp/berkeley_proxy_cookies.json
(Obtained via CalNet login through libproxy.berkeley.edu)
"""

import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

OUTDIR = Path("manuscript/lit_review/papers/fulltext")
COOKIE_FILE = Path("/tmp/berkeley_proxy_cookies.json")
DECISIONS_CSV = Path("manuscript/lit_review/papers/screening_llm_decisions.csv")
PRESCORED_CSV = Path("manuscript/lit_review/papers/screening_prescored.csv")

PROXY_PREFIX = "libproxy.berkeley.edu"


class PDFLinkFinder(HTMLParser):
    """Extract PDF download links from publisher HTML pages."""
    def __init__(self):
        super().__init__()
        self.pdf_links = []
        self.current_attrs = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            d = dict(attrs)
            href = d.get('href', '')
            # Look for PDF links
            if any(p in href.lower() for p in ['/pdf', '.pdf', 'pdfft', 'pdfdirect', 'epdf']):
                self.pdf_links.append(href)
            # Also check aria-label, title, data attributes
            for key in ['aria-label', 'title', 'data-article-pdf']:
                val = d.get(key, '').lower()
                if 'pdf' in val and href:
                    self.pdf_links.append(href)
                    break
        elif tag == 'meta':
            d = dict(attrs)
            # citation_pdf_url meta tag (many publishers use this)
            if d.get('name', '').lower() == 'citation_pdf_url':
                url = d.get('content', '')
                if url:
                    self.pdf_links.append(url)


def get_cookie():
    with open(COOKIE_FILE) as f:
        cookies = json.load(f)
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def proxify_url(url):
    """Convert a regular URL to go through Berkeley proxy."""
    # https://www.nature.com/... -> https://www-nature-com.libproxy.berkeley.edu/...
    if PROXY_PREFIX in url:
        return url  # Already proxied
    url = url.replace('https://', '').replace('http://', '')
    parts = url.split('/', 1)
    host = parts[0].replace('.', '-')
    path = parts[1] if len(parts) > 1 else ''
    return f"https://{host}.{PROXY_PREFIX}/{path}"


def fetch(url, cookie, timeout=30):
    """Fetch URL with proxy cookie."""
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Cookie': cookie,
        'Accept': 'application/pdf,text/html,*/*',
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get('Content-Type', ''), resp.url


def try_download_pdf(doi, cookie):
    """Try multiple strategies to download PDF for a DOI through proxy."""

    # Strategy 1: Direct DOI resolution through proxy
    proxy_doi_url = f"https://doi-org.{PROXY_PREFIX}/{doi}"
    try:
        content, ct, final_url = fetch(proxy_doi_url, cookie)

        # Check if we got PDF directly
        if content[:4] == b'%PDF':
            return content, "direct-doi"

        # Parse HTML for PDF links
        html = content.decode('utf-8', errors='replace')
        parser = PDFLinkFinder()
        parser.feed(html)

        # Try citation_pdf_url first (most reliable)
        for link in parser.pdf_links:
            if 'citation_pdf_url' in str(parser.pdf_links) or link.endswith('.pdf'):
                pass  # all links are candidates

        for link in parser.pdf_links:
            pdf_url = link
            if not pdf_url.startswith('http'):
                # Relative URL — resolve against final URL
                base = final_url.rsplit('/', 1)[0]
                pdf_url = base + '/' + pdf_url.lstrip('/')

            # Proxify the PDF URL
            pdf_url = proxify_url(pdf_url)

            try:
                pdf_content, pdf_ct, _ = fetch(pdf_url, cookie)
                if pdf_content[:4] == b'%PDF':
                    return pdf_content, "html-link"
            except Exception:
                continue

        # Strategy 2: Publisher-specific PDF URL patterns
        pdf_patterns = build_publisher_patterns(doi, final_url)
        for pattern_url in pdf_patterns:
            try:
                pdf_content, pdf_ct, _ = fetch(pattern_url, cookie)
                if pdf_content[:4] == b'%PDF':
                    return pdf_content, "publisher-pattern"
            except Exception:
                continue

    except Exception as e:
        pass

    return None, "failed"


def build_publisher_patterns(doi, final_url):
    """Generate publisher-specific PDF URL patterns."""
    patterns = []

    # Elsevier / ScienceDirect
    if 'sciencedirect' in final_url:
        # /science/article/pii/XXXXX -> /science/article/pii/XXXXX/pdfft
        pii_match = re.search(r'/pii/(S\w+)', final_url)
        if pii_match:
            pii = pii_match.group(1)
            patterns.append(proxify_url(f"https://www.sciencedirect.com/science/article/pii/{pii}/pdfft?isDTMRedir=true&download=true"))

    # Wiley
    if 'wiley' in final_url:
        patterns.append(proxify_url(f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}?download=true"))

    # Taylor & Francis
    if 'tandfonline' in final_url:
        patterns.append(proxify_url(f"https://www.tandfonline.com/doi/pdf/{doi}?download=true"))

    # Springer / Nature
    if 'springer' in final_url or 'nature.com' in final_url:
        patterns.append(proxify_url(f"https://link.springer.com/content/pdf/{doi}.pdf"))

    # IEEE
    if 'ieee' in final_url:
        arnumber_match = re.search(r'/document/(\d+)', final_url)
        if arnumber_match:
            patterns.append(proxify_url(f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?arnumber={arnumber_match.group(1)}"))

    # MDPI
    if 'mdpi.com' in final_url:
        patterns.append(proxify_url(f"https://www.mdpi.com/{doi}/pdf"))

    # Frontiers
    if 'frontiersin.org' in final_url:
        patterns.append(proxify_url(f"https://www.frontiersin.org/articles/{doi}/pdf"))

    # ACM
    if 'acm.org' in final_url:
        patterns.append(proxify_url(f"https://dl.acm.org/doi/pdf/{doi}"))

    return patterns


def main():
    cookie = get_cookie()
    OUTDIR.mkdir(parents=True, exist_ok=True)

    # Get all papers needing download
    with open(DECISIONS_CSV) as f:
        decisions = {int(r['record_index']): r for r in csv.DictReader(f)}
    with open(PRESCORED_CSV) as f:
        prescored = list(csv.DictReader(f))

    already = {int(f.stem.replace('record_', '')) for f in OUTDIR.glob('record_*.pdf')}

    papers = []
    for idx, dec in decisions.items():
        if dec['decision'] in ('include', 'maybe') and idx not in already:
            doi = (prescored[idx].get('doi', '') or '').strip()
            if doi:
                papers.append((idx, doi, dec['title']))

    print(f"Papers to download: {len(papers)} (already have {len(already)})")

    ok = 0
    fail = 0
    for i, (idx, doi, title) in enumerate(papers):
        outfile = OUTDIR / f"record_{idx}.pdf"
        content, method = try_download_pdf(doi, cookie)

        if content:
            outfile.write_bytes(content)
            ok += 1
            print(f"  OK [{idx}] {len(content)//1024}KB via {method} ({i+1}/{len(papers)})")
        else:
            fail += 1
            if (i + 1) % 20 == 0 or fail <= 5:
                print(f"  FAIL [{idx}] {title[:50]}... ({i+1}/{len(papers)})")

        time.sleep(1.0)  # Be polite to publishers

        if (i + 1) % 50 == 0:
            print(f"  --- Progress: {i+1}/{len(papers)}, OK: {ok}, FAIL: {fail} ---")

    total = len(list(OUTDIR.glob('record_*.pdf')))
    print(f"\nDone: {ok} new downloads, {fail} failed")
    print(f"Total PDFs: {total}/547")


if __name__ == '__main__':
    main()
