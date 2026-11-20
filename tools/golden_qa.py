"""Golden Q&A: grade Vera's answers against a file of approved questions, automatically and the same way every time.

A golden file (JSON, UTF-8) is a list of cases. Each case says what an answer must and must not say:

    {"id": "Q01",
     "cau_hoi": "EBIT tháng 10 thấp hơn kế hoạch có đáng lo không?",
     "loai": "phan_xet",                       # so | vi_sao | phan_xet | ngoai_du_lieu | what_if | ...
     "phai_co": ["thấp hơn kế hoạch"],          # every phrase, case-insensitive, accents kept
     "khong_duoc_co": ["Gửi anh"],
     "so": [{"gia_tri": 3.2, "don_vi": "tỷ", "sai_so": 0.05}],   # each must be said (engine/so_viet.py reads the text)
     "mo_dau": "EBIT"}                          # optional: the answer starts with this

Guardrails by «loai», on top of what the case says (Sổ quy tắc, present/hop_vera.py):
  phan_xet       never opens with «Có» / «Không» / «Chưa»; hands the judgement to the owner («không đánh giá»)
  ngoai_du_lieu  says no figure and parks the question for the owner (never a guessed number)
  every case     no «$», no English verdict words, no figure the case marks as wrong (khong_duoc_co_so)

The sealed questions of the independent grader stay with the grader: this tool is the format and the runner, and the
repository holds only synthetic golden files (tests/golden/).

    python -m tools.golden_qa --trang <report>_Clicky.html --vang tests/golden/x.json [--ra ket_qua.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

from engine import so_viet

VERDICT = re.compile(r"^\s*(Có|Không|Chưa)\b")


def grade(case: dict, answer: str) -> dict:
    a = str(answer or "").strip()
    low = a.lower()
    loi = []
    for p in case.get("phai_co") or []:
        if str(p).lower() not in low:
            loi.append(f"thiếu «{p}»")
    for p in case.get("khong_duoc_co") or []:
        if str(p).lower() in low:
            loi.append(f"có chữ cấm «{p}»")
    said = so_viet.find_all(a)
    for want in case.get("so") or []:
        if not any(so_viet.same(s, want["gia_tri"], want.get("don_vi", ""), want.get("sai_so", 0)) for s in said):
            loi.append(f"không nói số {want['gia_tri']} {want.get('don_vi', '')}".rstrip())
    for bad in case.get("khong_duoc_co_so") or []:
        if any(so_viet.same(s, bad["gia_tri"], bad.get("don_vi", ""), bad.get("sai_so", 0)) for s in said):
            loi.append(f"nói số sai {bad['gia_tri']} {bad.get('don_vi', '')}".rstrip())
    if case.get("mo_dau") and not a.startswith(case["mo_dau"]):
        loi.append(f"không mở đầu bằng «{case['mo_dau']}»")
    kind = case.get("loai")
    if kind == "phan_xet":
        if VERDICT.match(a):
            loi.append("phán xét: mở đầu bằng " + VERDICT.match(a).group(1))
        if "không đánh giá" not in low:
            loi.append("không nói rõ phần nhận định là của chủ báo cáo")
    if kind == "ngoai_du_lieu":
        if [s for s in said if s.unit]:
            loi.append("câu ngoài dữ liệu mà vẫn nói số")
        if not re.search(r"ghi lại|trả lời sau|chưa (có|tìm thấy)", low):
            loi.append("không ghi câu lại cho chủ báo cáo")
    if "$" in a:
        loi.append("có ký hiệu $")
    return {"id": case.get("id"), "cau_hoi": case.get("cau_hoi"), "dat": not loi, "loi": loi, "tra_loi": a}


def run(cases: list, ask) -> dict:
    rows = [grade(c, ask(c["cau_hoi"])) for c in cases]
    return {"dat": sum(r["dat"] for r in rows), "tong": len(rows), "cau": rows}


def page_asker(page_path: str):
    """Ask the presenter page the way a reader does: Escape, skip the talk, type in «Hỏi Vera», Enter."""
    from playwright.sync_api import sync_playwright

    from present import kiem_hinh
    pw = sync_playwright().start()
    browser = kiem_hinh._launch(pw)
    pg = browser.new_page(viewport={"width": 1366, "height": 900})
    pg.goto(Path(page_path).resolve().as_uri())
    pg.wait_for_timeout(1500)

    def ask(q):
        pg.keyboard.press("Escape")
        pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const e = r.getElementById('clicky-skip') || document.getElementById('clicky-skip'); if (e) e.click(); }")
        pg.wait_for_timeout(300)
        box = pg.locator("#clicky-host #q")
        box.fill(q)
        box.press("Enter")
        pg.wait_for_timeout(900)
        return pg.evaluate("() => { const r = document.getElementById('clicky-host').shadowRoot; const b = r.getElementById('bub'); "
                           "const a = (b && !b.classList.contains('hidden')) ? r.getElementById('ba') : r.getElementById('a'); "
                           "const c = a.cloneNode(true); c.querySelectorAll('.receipt, .trust').forEach(x => x.remove()); return c.innerText; }")

    def close():
        browser.close()
        pw.stop()
    ask.close = close
    return ask


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.golden_qa", description="Chấm câu trả lời của Vera theo bộ câu vàng.")
    ap.add_argument("--trang", required=True, help="trang trình bày đã gắn Vera (…_Clicky.html)")
    ap.add_argument("--vang", required=True, help="file câu vàng (JSON)")
    ap.add_argument("--ra", help="ghi kết quả ra file JSON")
    a = ap.parse_args(argv)
    cases = json.loads(Path(a.vang).read_text(encoding="utf-8"))
    ask = page_asker(a.trang)
    try:
        res = run(cases, ask)
    finally:
        ask.close()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(f"Đạt {res['dat']}/{res['tong']}")
    for r in res["cau"]:
        if not r["dat"]:
            print(f"  ✗ {r['id']}: {'; '.join(r['loi'])}")
    if a.ra:
        Path(a.ra).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0 if res["dat"] == res["tong"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
