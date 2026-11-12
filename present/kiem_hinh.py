"""Open a "<name>_Clicky.html" file in a headless browser and check what the boss will see.

For every place Clicky frames (each pointed place of the talk, each number card, one figure per table row), with
the question panel closed, open, and with the answer bubble open beside the place, at a laptop screen (1366x768)
and a small one (911x512, a laptop at 150% zoom):
- the frame encloses the place it should;
- the figure is on screen and not under Clicky's own bar, question panel or bubble, nor under the report's own
  fixed header (checked with elementFromPoint);
- the panel and the bubble never cover the figure's row label or column header;
- the nón lá pointer does not sit on any figure or label;
- the answer bubble never covers its own place and stays fully on the screen;
- a lassoed region (the first three rows of a table) is still framed round its cells after the page scrolls;
- Clicky's bar is really on screen and on top (elementFromPoint), also after the report's own scripts ran.
On a phone screen (390x844) the bar, the caption, the question panel and a bubble stay on the screen, and every
button of the bar is at least 44px.
If Clicky's layer never starts on the page, the check says KHÔNG ĐẠT and why: a script error, embedded data that
broke the page, or a report so large that Clicky did not start in time.

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

SIZES = ((1366, 768), (911, 512))
PHONE = (390, 844)
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


BIG = 20000   # table cells: above this a slow start is put down to the report's size


def _why_not(page, errors, blocked=()) -> str:
    """Why Clicky's layer did not start: the page's security policy, a script error, broken embedded data, or a
    report too big to read in time. Each says what it is; none is guessed."""
    if blocked:
        return "trang có chính sách bảo mật (Content-Security-Policy) chặn mã chạy trong trang, nên Clicky không chạy được"
    if errors:
        return "; ".join(errors)
    try:
        d = page.evaluate("""() => { const e = document.getElementById('clicky-data'); let ok = false;
            try { JSON.parse(e.textContent); ok = true; } catch (_) {}
            return {data: !!e, json: ok, cells: document.querySelectorAll('td,th,[role=cell],[role=gridcell]').length}; }""")
    except Exception:
        return "báo cáo quá lớn hoặc quá chậm: trang không trả lời"
    if not d["data"] or not d["json"]:
        return "lớp Clicky không khởi động; dữ liệu nhúng có thể đã làm hỏng thẻ script"
    if d["cells"] >= BIG:
        return (f"báo cáo quá lớn hoặc quá chậm: Clicky chưa sẵn sàng sau 15 giây ({d['cells']:,} ô bảng)"
                .replace(",", "."))
    return "lớp Clicky không khởi động mà trang không báo lỗi; mở file bằng trình duyệt để xem"


def check(packed: str, sizes=SIZES, timeout_ms: int = 60000, questions: Optional[list] = None,
          phone: Optional[tuple] = PHONE) -> dict:
    """Return {"ok", "skipped", "checked", "problems", "sizes", "cau"}; skipped=True when no browser could run.
    questions: the bosses' usual questions; "cau" says for each whether this report can answer it. They are
    passed to the page only inside this check and are never written into the file."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "skipped": True, "reason": "máy chưa có gói playwright", "problems": [], "checked": 0}
    if not Path(packed).is_file():
        return {"ok": False, "skipped": False, "checked": 0, "sizes": [], "cau": [],
                "problems": [f"không thấy file {packed}"]}
    url = Path(packed).resolve().as_uri()
    problems, checked, done, tried = [], 0, [], []
    with sync_playwright() as pw:
        try:
            browser = _launch(pw)
        except RuntimeError as exc:
            return {"ok": False, "skipped": True, "reason": str(exc), "problems": [], "checked": 0}
        try:
            runs = [((w, h), False) for w, h in sizes] + ([(phone, True)] if phone else [])
            for (w, h), mobile in runs:
                ctx = browser.new_context(viewport={"width": w, "height": h},
                                          **({"is_mobile": True, "has_touch": True} if mobile else {}))
                page = ctx.new_page()
                errors, blocked = [], []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e).splitlines()[0][:200]))
                page.on("console", lambda m, blocked=blocked: blocked.append(1)
                        if m.type == "error" and "Content Security Policy" in m.text else None)
                page.set_default_timeout(timeout_ms)
                try:
                    page.goto(url, wait_until="load")
                except Exception:  # the page itself did not finish loading
                    problems.append(f"màn {w}x{h}: báo cáo quá lớn hoặc quá chậm: trình duyệt chưa mở xong trang "
                                    f"sau {timeout_ms // 1000} giây")
                    ctx.close()
                    break
                try:
                    page.wait_for_function("typeof window.__clickyKiemHinh === 'function'",
                                           timeout=min(timeout_ms, 15000))
                except Exception:  # the layer never started: say why, never a traceback
                    problems.append(f"màn {w}x{h}: Clicky không chạy trên trang này ({_why_not(page, errors, blocked)})")
                    ctx.close()
                    break
                if questions and not tried:
                    tried = page.evaluate("qs => window.__clickyThuCau(qs)", questions)
                if mobile:
                    res = page.evaluate("window.__clickyKiemDienThoai()")
                    problems += [f"điện thoại {res['size']}: {p}" for p in res["problems"]]
                    done.append(res["size"] + " (điện thoại)")
                else:
                    res = page.evaluate("window.__clickyKiemHinh()")
                    checked += res["checked"]
                    problems += [f"màn {res['size']}: {p}" for p in res["problems"]]
                    done.append(res["size"])
                problems += [f"màn {w}x{h}: lỗi trên trang: {e}" for e in errors]
                ctx.close()
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
              "không có số, tên dòng hay tiêu đề cột nào bị thanh, bảng hỏi, khung hỏi hay nón lá che; "
              "khung hỏi không che chỗ đang hỏi; vùng khoanh vẫn đúng sau khi cuộn.")
        return 0
    print("KHÔNG ĐẠT:")
    for p in res["problems"][:30]:
        print(" -", p)
    if len(res["problems"]) > 30:
        print(f" - và {len(res['problems']) - 30} chỗ nữa")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
