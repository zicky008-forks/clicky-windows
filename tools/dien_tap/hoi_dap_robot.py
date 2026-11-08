"""Diễn tập hỏi đáp BOD: the robot has the installed Clicky make the month report and the forecast pack on a case
folder, then answer a fixed file of BOD questions from them, and grades every answer against the saved cells and
against figures it works out itself from the case files. Practice (synthetic) data only.

    python tools\\dien_tap\\tao_case_chi_phi.py --out <thu_muc_moi> --du-bao
    python tools\\dien_tap\\hoi_dap_robot.py --thu-muc <thu_muc_moi>

What it does:
 1. refuses to run while Excel has any other file open (real company files stay untouched);
 2. copies the case folder into the output folder (the original stays clean) and writes its own question file;
 3. starts Clicky.exe three times on the copy, each with its own scene log, CLICKY_LOCAL_ONLY=1, CLICKY_OUTLOOK=0
    and never the PC's company configuration: --fpa-report, --forecast, then --hoi-dap with --cau-hoi;
 4. grades, reading the files with its own code:
    - every question of the file has one answer and a status;
    - every cited figure equals its cell in the saved report or pack;
    - no amount or percent in an answer is missing from its citations;
    - the month, year-to-date and full-year operating costs equal, to the dong, the robot's own sums of the case files;
    - a question the report has no figure for (no sales ledger, so no bridge; no last year) says so, with no number;
    - a question not understood says so;
    - the chat path gives the same answers as the file;
    - the report was not changed (its sha256 is the one every answer names), the case files were not changed;
    - the scene log holds no question words; no error;
 5. writes ket-qua-robot.md + ket-qua-robot.json.

Exit code 0 = ĐẠT, 1 = KHÔNG ĐẠT, 2 = could not run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from present_robot import Result, file_hash, read_events, say  # noqa: E402

REPORT_PREFIX, PACK_PREFIX = "Clicky_FPA", "Clicky_Forecast"
JSONL = "hoi-dap.jsonl"
COSTS, VARIANCES = "Chi phi theo bo phan", "Chenh lech"
ANSWERED, NO_FIGURE, NOT_UNDERSTOOD = "trả lời", "chưa có số", "không hiểu câu hỏi"
NUM_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})+(?![\d.])|-?\d+(?:,\d+)?(?=\s*(?:đ|%))")
QUESTIONS = [                                 # (number, question, what the robot expects)
    ("Q1", "Chi phí hoạt động tháng này so với kế hoạch thế nào?", "opex_month"),
    ("Q1.1", "+ còn lũy kế từ đầu năm?", "opex_ytd"),
    ("Q1.2", "+ vì sao?", ANSWERED),
    ("Q2", "Top 3 khoản chi phí chênh lớn nhất?", ANSWERED),
    ("Q3", "Chi phí bộ phận MKT thế nào?", "dept"),
    ("Q4", "Chi phí phòng Pháp chế tháng này?", NO_FIGURE),
    ("Q5", "Ước cả năm chi phí hoạt động bao nhiêu?", "opex_full"),
    ("Q6", "Ảnh hưởng của giá bán lên lợi nhuận gộp?", NO_FIGURE),
    ("Q7", "Doanh thu cùng kỳ năm trước?", NO_FIGURE),
    ("Q8", "Còn điểm nào chưa rõ cần chị xác nhận?", ANSWERED),
    ("Q9", "Thời tiết hôm nay thế nào?", NOT_UNDERSTOOD),
    ("Q10", "Chi phí quản lý tháng này?", "opex_month"),      # 'quản lý' folds to 'quan ly': never 'last year'
]
WORDS = ("chi phí", "thời tiết", "doanh thu", "giá bán", "vì sao")   # none of the question's words in the scene log


def question_file(path: Path) -> Path:
    path.write_text("\n".join(q for _n, q, _e in QUESTIONS) + "\n", encoding="utf-8")
    return path


def robot_env(log: Path) -> dict:
    from robot_env import clean_env
    return clean_env(log, CLICKY_LOCAL_ONLY="1", CLICKY_OUTLOOK="0")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def expected(case: Path) -> dict:
    """The operating costs worked out from the case files alone (forecast_robot's sums, no Clicky code)."""
    import forecast_robot as fr
    month = ytd = Decimal(0)
    for r in fr._rows(case / fr.LEDGER):
        m, y = r.get("Tháng", "").strip(), r.get("Năm", "").strip()
        if not m or not y or not r.get("Số tiền", "").strip() or int(y) != fr.PERIOD[0] or int(m) > fr.PERIOD[1]:
            continue
        sign = 1 if r["TK Nợ"].startswith("6") else -1 if r["TK Có"].startswith("6") else 0
        ytd += sign * fr._amount(r["Số tiền"])
        if int(m) == fr.PERIOD[1]:
            month += sign * fr._amount(r["Số tiền"])
    return {"opex_month": month, "opex_ytd": ytd, "opex_full": fr.expected_full_year(case)}


def _cell(books: dict, path: Path, ref: str):
    from openpyxl import load_workbook
    wb = books.get(path)
    if wb is None:
        wb = books[path] = load_workbook(path, data_only=True, read_only=False)
    sheet, cell = ref.rsplit("!", 1)
    sheet = sheet.strip("'")
    return wb[sheet][cell.replace("$", "")].value if sheet in wb.sheetnames else None


def bad_citations(records: list, report: Path, pack) -> list:
    """Every cited figure or sentence that its cell does not hold: [(number, ref)]."""
    books, bad = {}, []
    for r in records:
        for c in r["so_lieu"]:
            path = Path(pack) if c.get("file") else report
            if c.get("file") and (pack is None or Path(pack).name != c["file"]):
                bad.append((r["so"], c["o"]))
                continue
            got = _cell(books, path, c["o"])
            if "gia_tri" in c:
                ok = got is not None and not isinstance(got, str) and abs(Decimal(str(got)) - Decimal(c["gia_tri"])) < 1
            else:
                ok = got == c.get("van_ban")
            if not ok:
                bad.append((r["so"], c["o"]))
    for wb in books.values():
        wb.close()
    return bad


def unsourced(rec: dict) -> list:
    """The amounts and percents of an answer that none of its citations holds."""
    def fmt(v: Decimal) -> str:
        v = v.quantize(Decimal(1))
        return ("-" if v < 0 else "") + f"{abs(int(v)):,}".replace(",", ".")
    said = set()
    for c in rec["so_lieu"]:
        if "gia_tri" in c:
            v = Decimal(c["gia_tri"])
            said |= {fmt(v), str(v.quantize(Decimal("0.1"))).replace(".", ",")}
        else:
            said |= set(NUM_RE.findall(c.get("van_ban", "")))
    return [n for n in NUM_RE.findall(rec["tra_loi"]) if n not in said]


def grade(records: list, report, pack, events: list, sums: dict, chat: list, report_sha: str,
          unchanged: bool) -> Result:
    res = Result()
    by = {r["so"]: r for r in records}
    want = [n for n, _q, _e in QUESTIONS]
    res.add("Mỗi câu hỏi có một câu trả lời và trạng thái", [r["so"] for r in records] == want and
            all(r.get("trang_thai") in (ANSWERED, NO_FIGURE, NOT_UNDERSTOOD) for r in records),
            f"có {len(records)}/{len(want)} câu" if len(records) != len(want) else "")
    bad = bad_citations(records, Path(report), pack) if report else [("", "không thấy báo cáo")]
    res.add("Mọi số trích đúng ô trong báo cáo đã lưu", not bad, ", ".join(f"{n} {o}" for n, o in bad[:5]))
    unread = [r["so"] for r in records if r["trang_thai"] in (ANSWERED, NO_FIGURE) and
              not r["tra_loi"].startswith("Em hiểu là: ")]
    res.add("Câu trả lời mở đầu bằng cách em hiểu câu hỏi", bool(records) and not unread, ", ".join(unread[:5]))
    loose = [(r["so"], unsourced(r)) for r in records if unsourced(r)]
    res.add("Không có số nào thiếu nguồn", not loose, "; ".join(f"{n}: {', '.join(x[:3])}" for n, x in loose[:5]))
    for number, _q, exp in QUESTIONS:
        r = by.get(number)
        if exp in sums:
            first = Decimal(r["so_lieu"][0]["gia_tri"]) if r and r["so_lieu"] and "gia_tri" in r["so_lieu"][0] else None
            res.add(f"{number}: số đúng từng đồng", first == sums[exp],
                    f"trả lời {first}, robot tự tính {sums[exp]}" if first != sums[exp] else "")
        elif exp == "dept":                       # the department's own lines, never the operating-cost total
            res.add(f"{number}: trả lời bằng các dòng của bộ phận", bool(r) and r["trang_thai"] == ANSWERED and
                    bool(r["so_lieu"]) and all(c["o"].startswith(f"'{COSTS}'!") or c["o"].startswith(f"'{VARIANCES}'!")
                                               for c in r["so_lieu"]), r["trang_thai"] if r else "không có câu trả lời")
        elif exp in (NO_FIGURE, NOT_UNDERSTOOD):
            res.add(f"{number}: nói rõ “{exp}”, không đoán số",
                    bool(r) and r["trang_thai"] == exp and not r["so_lieu"] and not NUM_RE.findall(r["tra_loi"]),
                    r["trang_thai"] if r else "không có câu trả lời")
        else:
            res.add(f"{number}: trả lời bằng số của báo cáo", bool(r) and r["trang_thai"] == ANSWERED,
                    r["trang_thai"] if r else "không có câu trả lời")
    same = [(c["tra_loi"], c["so_lieu"]) for c in chat] == [(r["tra_loi"], r["so_lieu"]) for r in records]
    res.add("Hỏi trong chat ra đúng câu trả lời như file", same, "" if chat else "chưa hỏi được trong chat")
    res.add("Báo cáo không bị sửa", bool(report) and sha256(Path(report)) == report_sha and
            all(r["sha256"] == report_sha for r in records))
    res.add("File của case không bị sửa", unchanged)
    words = json.dumps(events, ensure_ascii=False).lower()
    res.add("Nhật ký không ghi lời câu hỏi", not any(w in words for w in WORDS))
    done = [e for e in events if e.get("event") == "hoi_dap_file"]
    res.add("Mở lại bản hỏi đáp kiểm tra thấy khớp", bool(done) and done[-1].get("verified") is True)
    res.add("Không lỗi", not any(e.get("event") in ("hoi_dap_error", "hoi_dap_stop") for e in events))
    return res


def chat_answers(case: Path) -> list:
    """The same questions through the chat's own function (office.hoi_dap_skill.ask), one after another."""
    from office import hoi_dap_skill, month_close

    class Manager:
        _excel = None
    was = getattr(month_close, "_SOURCE", None)
    month_close._SOURCE = str(case)
    m, out = Manager(), []
    report = hoi_dap_skill.find_report(case)[0]
    jl = report.parent / JSONL
    before = jl.read_bytes() if jl.exists() else None
    try:
        for _n, q, _e in QUESTIONS:
            said = ("hỏi tiếp: " + q.lstrip("+ ")) if q.startswith("+") else "hỏi: " + q
            out.append(hoi_dap_skill.ask(m, said)[1])
    finally:
        month_close._SOURCE = was
        if before is None:
            jl.unlink(missing_ok=True)
        else:
            jl.write_bytes(before)             # the chat's own lines go away: the file's answers are graded
    return out


def _run(exe: str, args: list, log: Path, done, timeout: float) -> list:
    subprocess.run(["taskkill", "/IM", "Clicky.exe", "/F"], capture_output=True)
    time.sleep(1.5)
    subprocess.Popen([exe] + args, env=robot_env(log), cwd=str(Path(exe).parent))
    end = time.monotonic() + timeout
    while time.monotonic() < end and not done(read_events(log)):
        time.sleep(0.5)
    return read_events(log)


def _ended(names: tuple):
    def done(events):
        if any(e.get("event") in names[1:] for e in events):
            return True
        return any(e.get("event") == names[0] for e in events) and any(e.get("event") == "story_end" for e in events)
    return done


def main(argv=None) -> int:
    from present_robot import find_clicky, other_files_open
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--thu-muc", required=True, help="thư mục case GIẢ LẬP (tao_case_chi_phi.py --du-bao)")
    ap.add_argument("--clicky", default="", help="đường dẫn Clicky.exe (mặc định: bản cài mới nhất)")
    ap.add_argument("--out", default="", help="thư mục kết quả (mặc định: dien-tap-hoi-dap-<giờ>)")
    ap.add_argument("--timeout", type=float, default=300, help="giây chờ tối đa mỗi bước")
    a = ap.parse_args(argv)
    exe = a.clicky or find_clicky()
    if not exe or not Path(exe).exists():
        say("Không thấy Clicky.exe. Cài Clicky hoặc truyền --clicky.")
        return 2
    import forecast_robot as fr
    if not (Path(a.thu_muc) / fr.FORECAST).exists():
        say(f"Thư mục case chưa có {fr.FORECAST}. Tạo case bằng: tao_case_chi_phi.py --out <thu_muc_moi> --du-bao")
        return 2
    if other_files_open():
        say("Robot không chạy vì Excel/PowerPoint đang mở file khác (robot không đọc tên). Đóng hết rồi chạy lại.")
        return 2
    out = Path(a.out) if a.out else Path.cwd() / f"dien-tap-hoi-dap-{time.strftime('%Y%m%d-%H%M')}"
    out.mkdir(parents=True, exist_ok=True)
    case = out / "case"
    shutil.copytree(a.thu_muc, case, ignore=shutil.ignore_patterns("Clicky_*", "~$*"))
    sums = expected(case)
    hashes = {p: file_hash(p) for p in case.iterdir() if p.is_file()}
    questions = question_file(out / "cau-hoi-bod.txt")
    say("1/4 Clicky làm báo cáo tháng")
    _run(exe, ["--fpa-report", str(case)], out / "scene-fpa.jsonl", _ended(("fpa_report", "fpa_stop", "fpa_error")),
         a.timeout)
    say("2/4 Clicky làm dự báo cả năm")
    _run(exe, ["--forecast", str(case)], out / "scene-du-bao.jsonl",
         _ended(("forecast", "forecast_stop", "forecast_error")), a.timeout)
    reports = sorted(p for p in case.glob(f"{REPORT_PREFIX}_T*/{REPORT_PREFIX}_T*.xlsx") if "_HoiDap" not in p.stem)
    packs = sorted(case.glob(f"{PACK_PREFIX}_T*/{PACK_PREFIX}_T*.xlsx"))
    if not reports:
        say("Clicky chưa làm ra báo cáo tháng, robot không chấm hỏi đáp được.")
        return 2
    report, pack = reports[-1], (packs[-1] if packs else None)
    report_sha = sha256(report)
    say("3/4 Clicky trả lời file câu hỏi")
    log = out / "scene-hoi-dap.jsonl"
    events = _run(exe, ["--hoi-dap", str(case), "--cau-hoi", str(questions)], log,
                  _ended(("hoi_dap_file", "hoi_dap_stop", "hoi_dap_error")), a.timeout)
    subprocess.run(["taskkill", "/IM", "Clicky.exe", "/F"], capture_output=True)
    jl = report.parent / JSONL
    records = [json.loads(x) for x in jl.read_text(encoding="utf-8").splitlines()] if jl.exists() else []
    say("4/4 Hỏi lại trong chat và chấm")
    try:
        chat = chat_answers(case)
    except Exception as exc:                  # noqa: BLE001 - graded as a failed line
        say(f"Không hỏi được trong chat: {type(exc).__name__}")
        chat = []
    unchanged = all(p.exists() and file_hash(p) == h for p, h in hashes.items())
    res = grade(records, report, pack, events, sums, chat, report_sha, unchanged)
    title = "Diễn tập hỏi đáp BOD"
    (out / "ket-qua-robot.md").write_text(res.markdown(title), encoding="utf-8")
    (out / "ket-qua-robot.json").write_text(json.dumps({"ok": res.ok, "lines": [asdict(l) for l in res.lines],
                                                        "expected": {k: str(v) for k, v in sums.items()}},
                                                       ensure_ascii=False, indent=2), encoding="utf-8")
    say(res.markdown(title))
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
