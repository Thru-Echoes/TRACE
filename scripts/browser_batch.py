#!/usr/bin/env python3
"""Download PDFs using Playwright browser with Berkeley proxy.

Uses citation_pdf_url meta tag extraction (works across most publishers).
Falls back to publisher-specific PDF URL patterns.

Usage:
    python scripts/browser_batch_v2.py [--limit N]

The browser opens visibly. If proxy session expired, you'll be prompted
to complete CalNet login. After that, downloads proceed automatically.
"""

import asyncio
import csv
import glob
import os
import shutil
import sys
import time
from pathlib import Path

OUTDIR = Path("manuscript/lit_review/papers/fulltext")
PLAYWRIGHT_DL = Path(".playwright-mcp")  # Playwright default download dir
DECISIONS_CSV = Path("manuscript/lit_review/papers/screening_llm_decisions.csv")
PRESCORED_CSV = Path("manuscript/lit_review/papers/screening_prescored.csv")
PROXY = "libproxy.berkeley.edu"


def get_remaining():
    with open(DECISIONS_CSV) as f:
        decisions = {int(r['record_index']): r for r in csv.DictReader(f)}
    with open(PRESCORED_CSV) as f:
        prescored = list(csv.DictReader(f))
    already = {int(f.stem.replace('record_', ''))
               for f in OUTDIR.glob('record_*.pdf')}

    papers = []
    for idx, dec in decisions.items():
        if dec['decision'] in ('include', 'maybe') and idx not in already:
            doi = (prescored[idx].get('doi', '') or '').strip()
            if doi:
                papers.append({'idx': idx, 'doi': doi, 'title': dec['title']})
    return papers


async def main():
    limit = None
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == '--limit' and i < len(sys.argv) - 1:
            limit = int(sys.argv[i + 1])

    papers = get_remaining()
    if limit:
        papers = papers[:limit]
    print(f"Papers to download: {len(papers)}")
    if not papers:
        return

    OUTDIR.mkdir(parents=True, exist_ok=True)

    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(accept_downloads=True)

        # Check proxy session
        page = await context.new_page()
        await page.goto(f"https://doi-org.{PROXY}/10.1038/s41586-020-2649-2",
                        timeout=30000, wait_until='domcontentloaded')
        await page.wait_for_timeout(3000)

        if 'cas/login' in page.url or f'{PROXY}/login' in page.url:
            print("\n⚠️  Proxy session expired. Please log in via CalNet in the browser.")
            print("    Press Enter here once you've completed login...\n")
            await asyncio.get_event_loop().run_in_executor(None, input)
            await page.wait_for_timeout(3000)

        await page.close()
        print("✓ Proxy session active\n")

        ok = 0
        fail = 0

        for i, p in enumerate(papers):
            idx, doi = p['idx'], p['doi']
            outfile = OUTDIR / f"record_{idx}.pdf"
            if outfile.exists():
                continue

            page = await context.new_page()
            success = False

            try:
                # Navigate to DOI through proxy
                url = f"https://doi-org.{PROXY}/{doi}"
                await page.goto(url, timeout=30000, wait_until='domcontentloaded')
                await page.wait_for_timeout(2000)

                # Extract citation_pdf_url
                pdf_url = await page.evaluate('''() => {
                    const m = document.querySelector('meta[name="citation_pdf_url"]');
                    return m ? m.content : null;
                }''')

                if not pdf_url:
                    # Fallback: look for PDF links in the page
                    pdf_url = await page.evaluate('''() => {
                        const links = document.querySelectorAll('a[href]');
                        for (const a of links) {
                            const href = a.href || '';
                            const text = (a.textContent || '').toLowerCase();
                            const title = (a.title || '').toLowerCase();
                            if ((href.includes('/pdf') || href.endsWith('.pdf') ||
                                 href.includes('pdfft') || href.includes('pdfdirect')) &&
                                !href.includes('supplement')) {
                                return href;
                            }
                            if ((text.includes('download pdf') || text.includes('get pdf') ||
                                 title.includes('download pdf')) && href) {
                                return href;
                            }
                        }
                        return null;
                    }''')

                if pdf_url:
                    # Clear any old downloads
                    for old in glob.glob(str(PLAYWRIGHT_DL / "*.pdf")):
                        os.remove(old)

                    # Try to download by navigating to the PDF URL
                    try:
                        async with page.expect_download(timeout=15000) as dl_info:
                            await page.goto(pdf_url, timeout=15000)
                        download = await dl_info.value
                        dl_path = await download.path()
                        if dl_path:
                            shutil.copy2(dl_path, outfile)
                            size = outfile.stat().st_size
                            if size > 1000 and outfile.read_bytes()[:4] == b'%PDF':
                                success = True
                                ok += 1
                                print(f"  OK [{idx}] {size // 1024}KB via download event ({i + 1}/{len(papers)})")
                            else:
                                outfile.unlink(missing_ok=True)
                    except Exception:
                        # Download event didn't fire — maybe it rendered the PDF inline
                        await page.wait_for_timeout(2000)

                    if not success:
                        # Try fetching response body directly
                        try:
                            resp = await page.goto(pdf_url, timeout=15000)
                            if resp:
                                body = await resp.body()
                                if body[:4] == b'%PDF' and len(body) > 1000:
                                    outfile.write_bytes(body)
                                    success = True
                                    ok += 1
                                    print(f"  OK [{idx}] {len(body) // 1024}KB via response body ({i + 1}/{len(papers)})")
                        except Exception:
                            pass

                if not success:
                    fail += 1
                    if fail <= 10 or (i + 1) % 50 == 0:
                        print(f"  FAIL [{idx}] {p['title'][:45]}... ({i + 1}/{len(papers)})")

            except Exception as e:
                fail += 1
                if fail <= 5:
                    print(f"  ERR [{idx}] {str(e)[:40]} ({i + 1}/{len(papers)})")
            finally:
                if not page.is_closed():
                    await page.close()

            await asyncio.sleep(1.0)

            if (i + 1) % 25 == 0:
                total = len(list(OUTDIR.glob('record_*.pdf')))
                print(f"  --- {i + 1}/{len(papers)} | +{ok} new | {fail} fail | {total} total ---")

        await browser.close()

    total = len(list(OUTDIR.glob('record_*.pdf')))
    print(f"\nDone: +{ok} new, {fail} failed. Total PDFs: {total}/547")


if __name__ == '__main__':
    asyncio.run(main())
