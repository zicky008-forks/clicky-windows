"""Diễn tập dự báo cả năm gửi HO: the robot starts the installed Clicky on a case folder with --forecast, waits for
the run to end, and grades it against a figure it works out itself from the case files. Practice (synthetic) data
only.

    python tools\\dien_tap\\tao_case_chi_phi.py --out <thu_muc_moi> --du-bao
    python tools\\dien_tap\\forecast_robot.py --thu-muc <thu_muc_moi>

What it does:
 1. refuses to run while Excel has any other file open (real company files stay untouched);
 2. copies the case folder into the output folder (the original stays clean);
 3. starts Clicky.exe --forecast on the copy with the scene log on, CLICKY_LOCAL_ONLY=1 and CLICKY_OUTLOOK=0, and
    never with the PC's company configuration;
 4. grades: the pack was reopened and checked; its full-year operating costs equal, to the dong, the robot's own sum
    of the case files (the ledger to October, then November and December from the forecast, the line the forecast
    lacks from the annual plan); that line is said in the receipt; the case files were not changed; no error;
 5. writes ket-qua-robot.md + ket-qua-robot.json.

Exit code 0 = ĐẠT, 1 = KHÔNG ĐẠT, 2 = could not run.
"""
from __future__ import annotations

import argparse
import csv
import json
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

from present_robot import Result, file_hash, find_clicky, other_files_open, read_events, say  # noqa: E402

OUT_PREFIX = "Clicky_Forecast"
PNL_SHEET, RECEIPT_SHEET = "Du bao KQKD", "Phieu nhan file"
PERIOD = (2026, 10)                          # the case's month: the ledger runs to October 2026, the year is January
LEDGER, PLAN, FORECAST = "SoChiTietChiPhi_2026.csv", "AOP_2026.csv", "LE_T09_2026.csv"


def robot_env(log: Path) -> dict:
    """Local only, never Outlook, never the PC's company configuration."""
    from robot_env import clean_env
    return clean_env(log, CLICKY_LOCAL_ONLY="1", CLICKY_OUTLOOK="0")


def finished(events: list) -> bool:
    if any(e.get("event") in ("forecast_stop", "forecast_error") for e in events):
        return True
    return any(e.get("event") == "forecast" for e in events) and any(e.get("event") == "story_end" for e in events)


def _rows(path: Path) -> list:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _amount(text) -> Decimal:
    return Decimal(str(text).replace(",", "")) if str(text or "").strip() else Decimal(0)


def expected_full_year(case: Path) -> Decimal:
    """The full-year operating costs worked out from the case files alone, with no Clicky code: every cost posting
    of January to October (Nợ 6xx adds, Có 6xx takes off; a line with no month is not in any month), then November
    and December from the forecast, each department x cost element line it lacks from the annual plan."""
    ytd = Decimal(0)
    for r in _rows(case / LEDGER):
        month, year = r.get("Tháng", "").strip(), r.get("Năm", "").strip()
        if not month or not year or not r.get("Số tiền", "").strip():
            continue
        if (int(year), int(month)) > PERIOD or int(year) != PERIOD[0]:
            continue
        if r["TK Nợ"].startswith("6"):
            ytd += _amount(r["Số tiền"])
        elif r["TK Có"].startswith("6"):
            ytd -= _amount(r["Số tiền"])

    def by_line(path: Path) -> dict:
        out = {}
        for r in _rows(path):
            m = r.get("Tháng", "").strip()
            if not m or int(m) <= PERIOD[1]:
                continue
            k = (int(m), r["Mã phòng ban"], r["Mã khoản mục"])
            out[k] = out.get(k, Decimal(0)) + _amount(r["Số tiền"])
        return out
    plan, forecast = by_line(case / PLAN), by_line(case / FORECAST)
    rest = sum(forecast.values(), Decimal(0)) + sum((v for k, v in plan.items() if k not in forecast), Decimal(0))
    return ytd + rest


def figures(xlsx: Path) -> tuple:
    """(full-year operating costs on the pack, the receipt's text)."""
    from openpyxl import load_workbook
    wb = load_workbook(xlsx, read_only=True, data_only=True)
    try:
        rows = list(wb[PNL_SHEET].iter_rows(values_only=True))
        head = list(rows[2])
        col = head.index("Cả năm ước")
        full = next((r[col] for r in rows[3:] if r and r[0] == "Chi phí hoạt động"), None)
        text = " ".join(str(v) for r in wb[RECEIPT_SHEET].iter_rows(values_only=True) for v in r if v is not None)
    finally:
        wb.close()
    return (Decimal(str(full)) if full is not None else None), text


def grade(events: list, unchanged: bool, result_dir, expected: Decimal) -> Result:
    res = Result()
    done = [e for e in events if e.get("event") == "forecast"]
    stop = [e for e in events if e.get("event") in ("forecast_stop", "forecast_error")]
    last = done[-1] if done else {}
    res.add("Làm xong dự báo, mở lại kiểm tra thấy khớp", bool(done) and last.get("verified") is True and not stop,
            ("Clicky dừng: " + stop[-1]["event"]) if stop else "" if done else "không thấy kết quả dự báo")
    books = sorted(Path(result_dir).glob(OUT_PREFIX + "*.xlsx")) if result_dir else []
    full, text = figures(books[-1]) if books else (None, "")
    res.add("Chi phí cả năm ước đúng từng đồng", full is not None and full == expected,
            f"trên file {full}, robot tự tính {expected}" if full != expected else "")
    res.add("Nói rõ dòng bản dự báo thiếu lấy theo kế hoạch năm",
            last.get("filled", 0) >= 1 and "không có 1 dòng chi phí (bộ phận × yếu tố)" in text,
            "" if text else "không đọc được Phiếu nhận file")
    res.add("File của case không bị sửa", unchanged)
    res.add("Không lỗi", not any(e.get("event") == "forecast_error" for e in events))
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--thu-muc", required=True, help="thư mục case GIẢ LẬP (tao_case_chi_phi.py --du-bao)")
    ap.add_argument("--clicky", default="", help="đường dẫn Clicky.exe (mặc định: bản cài mới nhất)")
    ap.add_argument("--out", default="", help="thư mục kết quả (mặc định: dien-tap-du-bao-<giờ> trong thư mục hiện tại)")
    ap.add_argument("--timeout", type=float, default=300, help="giây chờ tối đa")
    a = ap.parse_args(argv)
    exe = a.clicky or find_clicky()
    if not exe or not Path(exe).exists():
        say("Không thấy Clicky.exe. Cài Clicky hoặc truyền --clicky.")
        return 2
    if not (Path(a.thu_muc) / FORECAST).exists():
        say(f"Thư mục case chưa có {FORECAST}. Tạo case bằng: tao_case_chi_phi.py --out <thu_muc_moi> --du-bao")
        return 2
    if other_files_open():
        say("Robot không chạy vì Excel/PowerPoint đang mở file khác (robot không đọc tên). Đóng hết rồi chạy lại.")
        return 2
    out = Path(a.out) if a.out else Path.cwd() / f"dien-tap-du-bao-{time.strftime('%Y%m%d-%H%M')}"
    out.mkdir(parents=True, exist_ok=True)
    case = out / "case"
    shutil.copytree(a.thu_muc, case, ignore=shutil.ignore_patterns(OUT_PREFIX + "*", "~$*"))
    expected = expected_full_year(case)
    hashes = {p: file_hash(p) for p in case.iterdir() if p.is_file()}
    log = out / "scene.jsonl"
    say(f"1/2 Mở Clicky làm dự báo: {exe} --forecast {case}")
    subprocess.Popen([exe, "--forecast", str(case)], env=robot_env(log), cwd=str(Path(exe).parent))
    end = time.monotonic() + a.timeout
    while time.monotonic() < end and not finished(read_events(log)):
        time.sleep(0.5)
    say("2/2 Chấm")
    unchanged = all(p.exists() and file_hash(p) == h for p, h in hashes.items())
    result_dirs = sorted(case.glob(OUT_PREFIX + "*"))
    res = grade(read_events(log), unchanged, result_dirs[-1] if result_dirs else None, expected)
    title = "Diễn tập dự báo cả năm gửi HO"
    (out / "ket-qua-robot.md").write_text(res.markdown(title), encoding="utf-8")
    (out / "ket-qua-robot.json").write_text(json.dumps({"ok": res.ok, "lines": [asdict(l) for l in res.lines],
                                                        "expected": str(expected)}, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    say(res.markdown(title))
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
