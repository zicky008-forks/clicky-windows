"""Open a "<name>_Clicky.html" file in a headless browser and check what the boss will see.

For every place Clicky frames (each pointed place of the talk, each number card, one figure per table row), with
the question panel closed and open, at two laptop screen sizes:
- the frame encloses the place it should;
- the figure is on screen and not under Clicky's own bar or question panel;
- the nón lá pointer does not sit on any figure.

The geometry is measured by the page itself (window.__clickyKiemHinh in clicky_html_layer.html), so the check
measures the same code the boss runs. Nothing leaves the PC: the browser opens the local file and no screenshot
is taken or sent.

Browser: Edge on a Windows PC (always installed), else Chrome, else Playwright's own Chromium, else the path in
CLICKY_BROWSER. If none can start, the check says so and is skipped; it never blocks a build silently.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from present import tri_nho

SIZES = ((1366, 768), (1920, 1080))
SKIPPED = 3


def _launch(pw):
    tries = []
    if os.environ.get("CLICKY_BROWSER"):
        tries.append({"executable_path": os.environ["CLICKY_BROWSER"]})
    tries += [{"channel": "msedge"}, {"channel": "chrome"}, {}]
    if Path("/opt/pw-browsers/chromium").exists():
        tries.append({"executable_path": "/opt/pw-browsers/chromium"})
    last = None
    for kw in tries:
        try:
            return pw.chromium.launch(headless=True, args=["--no-sandbox"], **kw)
        except Exception as exc:  # the next browser on the list may work
            last = exc
    raise RuntimeError(f"không mở được trình duyệt ({last})")


def check(packed: str, sizes=SIZES, timeout_ms: int = 60000, questions: Optional[list] = None) -> dict:
    """Return {"ok", "skipped", "checked", "problems", "sizes", "cau"}; skipped=True when no browser could run.
    questions: the bosses' usual questions; "cau" says for each whether this report can answer it. They are
    passed to the page only inside this check and are never written into the file."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "skipped": True, "reason": "máy chưa có gói playwright", "problems": [], "checked": 0}
    url = Path(packed).resolve().as_uri()
    problems, checked, done, tried = [], 0, [], []
    with sync_playwright() as pw:
        try:
            browser = _launch(pw)
        except RuntimeError as exc:
            return {"ok": False, "skipped": True, "reason": str(exc), "problems": [], "checked": 0}
        try:
            for w, h in sizes:
                page = browser.new_page(viewport={"width": w, "height": h})
                page.set_default_timeout(timeout_ms)
                page.goto(url, wait_until="load")
                page.wait_for_function("typeof window.__clickyKiemHinh === 'function'")
                if questions and not tried:
                    tried = page.evaluate("qs => window.__clickyThuCau(qs)", questions)
                res = page.evaluate("window.__clickyKiemHinh()")
                checked += res["checked"]
                problems += [f"màn {res['size']}: {p}" for p in res["problems"]]
                done.append(res["size"])
                page.close()
        finally:
            browser.close()
    return {"ok": not problems and checked > 0, "skipped": False, "checked": checked, "problems": problems,
            "sizes": done, "cau": tried}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Kiểm bằng trình duyệt: Clicky không che số, khoanh đúng chỗ.")
    ap.add_argument("file", help="file <tên>_Clicky.html")
    ap.add_argument("--tri-nho", default=os.environ.get("CLICKY_TRI_NHO") or None,
                    help="thư mục trí nhớ: thử luôn các câu các sếp hay hỏi (SEP_HAY_HOI.md)")
    a = ap.parse_args(argv)
    bosses = tri_nho.load(a.tri_nho).bosses if a.tri_nho else {}
    asked = [(boss, q) for boss, qs in bosses.items() for q in qs]
    res = check(a.file, questions=[q for _, q in asked])
    if res.get("cau"):
        ok = sum(1 for r in res["cau"] if r["ok"])
        print(f"Câu các sếp hay hỏi: báo cáo này trả lời được {ok}/{len(res['cau'])}.")
        for (boss, _), r in zip(asked, res["cau"]):
            mark = "✓" if r["ok"] else "~" if r.get("partial") else "✗"
            print(f"  {mark} {boss}: {r['q']}  →  {r['label'] or 'chưa có trong báo cáo'}")
    if res["skipped"]:
        print(f"BỎ QUA kiểm hình: {res['reason']}. Mở file và xem bằng mắt vài chỗ khoanh trước khi gửi.")
        return SKIPPED
    if res["ok"]:
        print(f"ĐẠT. {res['checked']} lần khoanh trên màn {', '.join(res['sizes'])}: khung đúng chỗ, "
              "không có số nào bị thanh, bảng hỏi hay nón lá che.")
        return 0
    print("KHÔNG ĐẠT:")
    for p in res["problems"][:30]:
        print(" -", p)
    if len(res["problems"]) > 30:
        print(f" - và {len(res['problems']) - 30} chỗ nữa")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
