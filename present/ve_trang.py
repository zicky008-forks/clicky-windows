"""The report as the browser draws it: tables a report script fills in after the page loads.

Many dashboards saved as HTML ship an empty <table> and a block of data, and a script draws the rows when the page
opens. Read as a file, such a report has headers and no figures, so Clicky could only read its notes aloud. This
module opens the report in the same headless browser the screen check uses (Edge on a Windows PC), with every
request outside the file blocked (nothing leaves the PC), lets its scripts draw, and returns the page as drawn.

Elements the reader cannot see when the page opens (a tab not chosen yet) are marked data-clicky-an="1": the talk
does not frame what the Board would not see.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

HIDE = """() => {
  for (const e of document.querySelectorAll('table, div, p, li, section, article, h1, h2, h3, h4, figure'))
    if (!e.getClientRects().length) e.setAttribute('data-clicky-an', '1');
  return '<!doctype html>' + document.documentElement.outerHTML;
}"""


def rendered(path: str, settle_ms: int = 1500, timeout_ms: int = 60000) -> Optional[str]:
    """The page after its scripts ran, or None when no browser can start (then the file as written is used)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    from present import kiem_hinh
    url = Path(path).resolve().as_uri()
    try:
        with sync_playwright() as pw:
            browser = kiem_hinh._launch(pw)
            try:
                page = browser.new_page(viewport={"width": 1366, "height": 768})
                page.route("**/*", lambda r: r.continue_() if r.request.url.startswith("file:") else r.abort())
                page.goto(url, wait_until="load", timeout=timeout_ms)
                page.wait_for_timeout(settle_ms)
                return page.evaluate(HIDE)
            finally:
                browser.close()
    except Exception:  # no browser, or the page would not open: the file as written is still read
        return None
