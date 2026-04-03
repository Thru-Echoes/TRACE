#!/usr/bin/env python3
"""Download remaining PDFs using Playwright browser automation through Berkeley proxy.

This handles publishers that block programmatic downloads (Elsevier, IEEE, MDPI)
by using a real browser with JavaScript rendering.

Usage:
    # Run from project root. Assumes you're already logged in via proxy.
    python scripts/browser_download.py [--limit N] [--publisher elsevier|ieee|mdpi|all]

Prerequisites:
    pip install playwright
    playwright install chromium
"""

import asyncio
import csv
import os
import sys
from pathlib import Path

OUTDIR = Path("manuscript/lit_review/papers/fulltext")
DECISIONS_CSV = Path("manuscript/lit_review/papers/screening_llm_decisions.csv")
PRESCORED_CSV = Path("manuscript/lit_review/papers/screening_prescored.csv")
PROXY_DOMAIN = "libproxy.berkeley.edu"


def get_remaining_papers(publisher_filter=None):
    """Get papers not yet downloaded, optionally filtered by publisher."""
    with open(DECISIONS_CSV) as f:
        decisions = {int(r['record_index']): r for r in csv.DictReader(f)}
    with open(PRESCORED_CSV) as f:
        prescored = list(csv.DictReader(f))

    already = {int(f.stem.replace('record_', ''))
               for f in OUTDIR.glob('record_*.pdf')}

    papers = []
    for idx, dec in decisions.items():
        if dec['decision'] not in ('include', 'maybe'):
            continue
        if idx in already:
            continue
        doi = (prescored[idx].get('doi', '') or '').strip()
        if not doi:
            continue

        pub = None
        if doi.startswith('10.1016'):
            pub = 'elsevier'
        elif doi.startswith('10.1109'):
            pub = 'ieee'
        elif doi.startswith('10.3390'):
            pub = 'mdpi'
        else:
            pub = 'other'

        if publisher_filter and publisher_filter != 'all' and pub != publisher_filter:
            continue

        papers.append({'idx': idx, 'doi': doi, 'title': dec['title'], 'publisher': pub})

    return papers


def proxify(url):
    """Convert URL to Berkeley proxy version."""
    if PROXY_DOMAIN in url:
        return url
    url = url.replace('https://', '').replace('http://', '')
    host, *path_parts = url.split('/', 1)
    path = path_parts[0] if path_parts else ''
    proxy_host = host.replace('.', '-')
    return f"https://{proxy_host}.{PROXY_DOMAIN}/{path}"


async def download_paper(context, idx, doi, publisher):
    """Try to download a single paper PDF via browser."""
    page = await context.new_page()
    outfile = OUTDIR / f"record_{idx}.pdf"

    try:
        # Navigate to DOI through proxy
        url = f"https://doi-org.{PROXY_DOMAIN}/{doi}"
        await page.goto(url, timeout=30000, wait_until='domcontentloaded')
        await page.wait_for_timeout(3000)

        # Strategy 1: Check for citation_pdf_url meta tag
        pdf_url = await page.evaluate('''() => {
            const meta = document.querySelector('meta[name="citation_pdf_url"]');
            return meta ? meta.content : null;
        }''')

        if not pdf_url:
            # Strategy 2: Publisher-specific selectors
            selectors = {
                'elsevier': [
                    'a[id="pdfLink"]',
                    'a.pdf-download',
                    'a[href*="pdfft"]',
                    'a[data-t="download-pdf"]',
                    '.PdfDownloadButton a',
                ],
                'ieee': [
                    'a[href*="stamp"]',
                    'a.pdf-btn-link',
                    'a[href*="/stampPDF/"]',
                    '.document-ft a',
                ],
                'mdpi': [
                    'a[href$="/pdf"]',
                    'a.download-pdf',
                    'a[href*="/pdf?version="]',
                ],
                'other': [
                    'a[href*="/pdf"]',
                    'a[href*=".pdf"]',
                    'a[data-article-pdf]',
                    'a[aria-label*="PDF"]',
                    'a[title*="PDF"]',
                    'a[title*="pdf"]',
                    '.pdf-download a',
                ],
            }

            for sel in selectors.get(publisher, selectors['other']):
                try:
                    link = await page.query_selector(sel)
                    if link:
                        href = await link.get_attribute('href')
                        if href:
                            pdf_url = href
                            break
                except Exception:
                    continue

        if not pdf_url:
            # Strategy 3: Try publisher-specific direct PDF URLs
            final_url = page.url
            if publisher == 'elsevier':
                pii = await page.evaluate('''() => {
                    const meta = document.querySelector('meta[name="citation_pii_id"]');
                    return meta ? meta.content : null;
                }''')
                if pii:
                    pdf_url = f"https://www-sciencedirect-com.{PROXY_DOMAIN}/science/article/pii/{pii}/pdfft?isDTMRedir=true&download=true"
            elif publisher == 'ieee':
                arnumber = await page.evaluate('''() => {
                    const m = window.location.href.match(/document\\/(\\d+)/);
                    return m ? m[1] : null;
                }''')
                if arnumber:
                    pdf_url = f"https://ieeexplore-ieee-org.{PROXY_DOMAIN}/stampPDF/getPDF.jsp?arnumber={arnumber}"

        if pdf_url:
            # Ensure URL is proxified
            if not PROXY_DOMAIN in pdf_url:
                if pdf_url.startswith('/'):
                    # Relative URL
                    base_url = page.url.split('/', 3)
                    pdf_url = '/'.join(base_url[:3]) + pdf_url
                pdf_url = proxify(pdf_url)

            # Navigate to PDF URL and download
            pdf_page = await context.new_page()
            try:
                response = await pdf_page.goto(pdf_url, timeout=30000)
                if response:
                    body = await response.body()
                    if body[:4] == b'%PDF':
                        outfile.write_bytes(body)
                        await pdf_page.close()
                        await page.close()
                        return True, len(body)
            except Exception:
                pass
            finally:
                if not pdf_page.is_closed():
                    await pdf_page.close()

        await page.close()
        return False, 0

    except Exception as e:
        if not page.is_closed():
            await page.close()
        return False, 0


async def main():
    limit = None
    publisher_filter = 'all'

    for arg in sys.argv[1:]:
        if arg.startswith('--limit'):
            limit = int(sys.argv[sys.argv.index(arg) + 1])
        elif arg.startswith('--publisher'):
            publisher_filter = sys.argv[sys.argv.index(arg) + 1]

    papers = get_remaining_papers(publisher_filter)
    if limit:
        papers = papers[:limit]

    print(f"Papers to download: {len(papers)} (filter: {publisher_filter})")
    if not papers:
        print("Nothing to download!")
        return

    OUTDIR.mkdir(parents=True, exist_ok=True)

    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        # Launch visible browser so proxy auth cookies persist
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(
            accept_downloads=True,
            java_script_enabled=True,
        )

        # First: navigate to proxy to establish session
        page = await context.new_page()
        await page.goto(f"https://doi-org.{PROXY_DOMAIN}/10.1038/s41586-020-2649-2", timeout=30000)
        await page.wait_for_timeout(2000)

        # Check if we need to log in
        if 'cas/login' in page.url or 'libproxy.berkeley.edu/login' in page.url:
            print("⚠ Proxy session expired. Please log in via CalNet in the browser window.")
            print("Press Enter here once logged in...")
            await asyncio.get_event_loop().run_in_executor(None, input)

        await page.close()
        print("Proxy session active. Starting downloads...\n")

        ok = 0
        fail = 0
        for i, p in enumerate(papers):
            success, size = await download_paper(context, p['idx'], p['doi'], p['publisher'])
            if success:
                ok += 1
                print(f"  OK [{p['idx']}] {size//1024}KB - {p['publisher']} ({i+1}/{len(papers)})")
            else:
                fail += 1

            if (i + 1) % 25 == 0:
                total = len(list(OUTDIR.glob('record_*.pdf')))
                print(f"  --- Progress: {i+1}/{len(papers)} | OK: {ok} | FAIL: {fail} | Total PDFs: {total} ---")

            await asyncio.sleep(1.5)

        await browser.close()

    total = len(list(OUTDIR.glob('record_*.pdf')))
    print(f"\nDone: {ok} new, {fail} failed. Total PDFs: {total}/547")


if __name__ == '__main__':
    asyncio.run(main())
