#!/usr/bin/env python3
"""Batch download full-text PDFs via Berkeley Library EZProxy.

Usage:
    # Step 1: Login manually (opens browser, you do CalNet auth)
    python scripts/download_fulltext.py login

    # Step 2: Download all paywalled papers (uses saved session)
    python scripts/download_fulltext.py download

    # Step 3: Check status
    python scripts/download_fulltext.py status

Prerequisites:
    pip install playwright
    playwright install chromium
"""

import asyncio
import csv
import json
import os
import sys
import time
from pathlib import Path

OUTDIR = Path("manuscript/lit_review/papers/fulltext")
DECISIONS_CSV = Path("manuscript/lit_review/papers/screening_llm_decisions.csv")
PRESCORED_CSV = Path("manuscript/lit_review/papers/screening_prescored.csv")
UNPAYWALL_CACHE = Path("/tmp/unpaywall_all_547.json")
SESSION_FILE = Path("/tmp/berkeley_proxy_session.json")

# Berkeley EZProxy prefix
EZPROXY_PREFIX = "https://libproxy.berkeley.edu/login?url="


def get_eligible_papers():
    """Get all include/maybe papers with DOIs."""
    with open(DECISIONS_CSV) as f:
        decisions = {int(r['record_index']): r for r in csv.DictReader(f)}
    with open(PRESCORED_CSV) as f:
        prescored = list(csv.DictReader(f))

    papers = []
    for idx, dec in decisions.items():
        if dec['decision'] in ('include', 'maybe'):
            ps = prescored[idx]
            papers.append({
                'idx': idx,
                'doi': (ps.get('doi', '') or '').strip(),
                'title': dec['title'],
                'journal': ps.get('journal', ''),
            })
    return papers


def get_downloaded():
    """Get set of already-downloaded record indices."""
    downloaded = set()
    if OUTDIR.exists():
        for f in OUTDIR.iterdir():
            if f.suffix == '.pdf' and f.stem.startswith('record_'):
                try:
                    downloaded.add(int(f.stem.replace('record_', '')))
                except ValueError:
                    pass
    return downloaded


def get_paywalled():
    """Get papers that need proxy download (not OA, not already downloaded)."""
    downloaded = get_downloaded()
    papers = get_eligible_papers()

    # Load Unpaywall results if available
    oa_indices = set()
    if UNPAYWALL_CACHE.exists():
        with open(UNPAYWALL_CACHE) as f:
            for r in json.load(f):
                if r.get('unpaywall_status') == 'oa':
                    oa_indices.add(r['idx'])

    # Papers that need proxy: have DOI, not already downloaded, not OA (or OA but download failed)
    need_proxy = []
    for p in papers:
        if p['idx'] in downloaded:
            continue
        if not p['doi']:
            continue
        need_proxy.append(p)

    return need_proxy


async def do_login():
    """Open browser for manual CalNet login, save session."""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to a proxied DOI to trigger CalNet login
        test_doi = "10.1038/s41586-020-2649-2"
        proxy_url = f"{EZPROXY_PREFIX}https://doi.org/{test_doi}"
        print(f"Navigating to: {proxy_url}")
        print("Please complete CalNet login in the browser window...")
        await page.goto(proxy_url)

        # Wait for user to complete login (up to 5 minutes)
        print("Waiting for login to complete (up to 5 minutes)...")
        print("After logging in and seeing the paper, press Enter here.")
        await asyncio.get_event_loop().run_in_executor(None, input)

        # Save session cookies
        cookies = await context.cookies()
        with open(SESSION_FILE, 'w') as f:
            json.dump(cookies, f, indent=2)
        print(f"Session saved to {SESSION_FILE} ({len(cookies)} cookies)")

        await browser.close()


async def do_download():
    """Download paywalled PDFs using saved proxy session."""
    from playwright.async_api import async_playwright

    if not SESSION_FILE.exists():
        print("No session found. Run 'login' first.")
        return

    with open(SESSION_FILE) as f:
        cookies = json.load(f)

    papers = get_paywalled()
    print(f"Papers to download: {len(papers)}")

    if not papers:
        print("Nothing to download!")
        return

    OUTDIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            accept_downloads=True,
            extra_http_headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
            }
        )
        await context.add_cookies(cookies)

        downloaded = 0
        failed = 0
        for i, p in enumerate(papers):
            idx = p['idx']
            doi = p['doi']
            outfile = OUTDIR / f"record_{idx}.pdf"

            if outfile.exists():
                continue

            # Try direct DOI resolution through proxy
            proxy_url = f"{EZPROXY_PREFIX}https://doi.org/{doi}"

            try:
                page = await context.new_page()

                # Navigate to the proxied DOI
                response = await page.goto(proxy_url, timeout=30000, wait_until='domcontentloaded')
                await page.wait_for_timeout(2000)  # Let redirects settle

                # Get the final URL after redirects
                final_url = page.url

                # Try to find PDF link on the page
                pdf_url = None

                # Strategy 1: Check if we landed on a PDF directly
                content_type = response.headers.get('content-type', '') if response else ''
                if 'pdf' in content_type:
                    pdf_url = final_url

                # Strategy 2: Look for PDF download links on publisher pages
                if not pdf_url:
                    # Common PDF link selectors across publishers
                    selectors = [
                        'a[href*="/pdf"]',
                        'a[href*=".pdf"]',
                        'a[data-article-pdf]',
                        'a.pdf-download',
                        'a[title*="PDF"]',
                        'a[aria-label*="PDF"]',
                        'a[href*="pdfft"]',  # Elsevier
                        'a[href*="pdfdirect"]',  # Wiley
                        '.article-tools a[href*="pdf"]',
                        '#pdfLink',
                        '.btn-pdf',
                    ]
                    for sel in selectors:
                        try:
                            link = await page.query_selector(sel)
                            if link:
                                href = await link.get_attribute('href')
                                if href:
                                    pdf_url = href if href.startswith('http') else page.url.split('/')[0] + '//' + page.url.split('/')[2] + href
                                    break
                        except Exception:
                            continue

                if pdf_url:
                    # Download the PDF
                    try:
                        pdf_page = await context.new_page()
                        pdf_response = await pdf_page.goto(pdf_url, timeout=30000)
                        if pdf_response:
                            body = await pdf_response.body()
                            if body[:4] == b'%PDF':
                                with open(outfile, 'wb') as f:
                                    f.write(body)
                                downloaded += 1
                                print(f"  OK [{idx}] {len(body)/1024:.0f}KB ({i+1}/{len(papers)})")
                            else:
                                failed += 1
                                print(f"  NOPDF [{idx}] not a PDF ({i+1}/{len(papers)})")
                        await pdf_page.close()
                    except Exception as e:
                        failed += 1
                        print(f"  DLFL [{idx}] {str(e)[:40]} ({i+1}/{len(papers)})")
                else:
                    failed += 1
                    print(f"  NOLINK [{idx}] no PDF link found on {final_url[:50]} ({i+1}/{len(papers)})")

                await page.close()

            except Exception as e:
                failed += 1
                print(f"  ERROR [{idx}] {str(e)[:50]} ({i+1}/{len(papers)})")

            # Rate limit
            await asyncio.sleep(1)

            # Progress every 20
            if (i + 1) % 20 == 0:
                print(f"  --- Progress: {i+1}/{len(papers)}, downloaded: {downloaded}, failed: {failed} ---")

        await browser.close()

    print(f"\nDone: {downloaded} downloaded, {failed} failed")
    print(f"Total PDFs in {OUTDIR}: {len(get_downloaded())}")


def do_status():
    """Print download status."""
    papers = get_eligible_papers()
    downloaded = get_downloaded()

    print(f"Eligible papers: {len(papers)}")
    print(f"Downloaded PDFs: {len(downloaded)}")
    print(f"Remaining: {len(papers) - len(downloaded)}")
    print(f"  With DOI: {sum(1 for p in papers if p['doi'] and p['idx'] not in downloaded)}")
    print(f"  No DOI: {sum(1 for p in papers if not p['doi'] and p['idx'] not in downloaded)}")

    # Check Unpaywall data
    if UNPAYWALL_CACHE.exists():
        with open(UNPAYWALL_CACHE) as f:
            uw = json.load(f)
        from collections import Counter
        statuses = Counter(r['unpaywall_status'] for r in uw)
        print(f"\nUnpaywall breakdown:")
        for s, c in statuses.most_common():
            print(f"  {s}: {c}")
        oa_not_downloaded = sum(1 for r in uw if r['unpaywall_status'] == 'oa' and r['idx'] not in downloaded)
        print(f"  OA but not yet downloaded: {oa_not_downloaded}")


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'

    if cmd == 'login':
        asyncio.run(do_login())
    elif cmd == 'download':
        asyncio.run(do_download())
    elif cmd == 'status':
        do_status()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python scripts/download_fulltext.py [login|download|status]")
