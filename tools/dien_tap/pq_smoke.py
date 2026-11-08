"""Thử nhanh Power Query trên máy thật (khoảng 30 giây): does Clicky's refresh path work on this PC's Excel?
Practice data only: a 3-row made-up CSV in a temporary folder, in a separate Excel instance.

    python tools\\dien_tap\\pq_smoke.py                 (both checks)
    python tools\\dien_tap\\pq_smoke.py --khong-thu-cham (skip the slow-query check, about 10 seconds)

1. Tải được: a new workbook gets one query over the CSV; office.power_query loads it the way the month close
   does (worker thread, COM object marshalled to it, at most --timeout seconds). ĐẠT when the 3 rows and their
   total come back.
2. Dừng đúng hạn: a query that takes 20 s (Function.InvokeAfter) is loaded with a 5 s limit. ĐẠT when Clicky
   stops after about 5 s with LoadTimeout instead of hanging.

Writes ket-qua-pq-smoke.md/.json to --out. Exit 0 = ĐẠT, 1 = KHÔNG ĐẠT, 2 = could not run.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from present_robot import Result, other_files_open, say  # noqa: E402

ROWS = [("PX001", "2026-10-01", 1_200_000), ("PX002", "2026-10-02", 3_400_000), ("PX003", "2026-10-03", 5_600_000)]
SLOW_SECONDS, SLOW_LIMIT = 20, 5


def csv_query(path: Path) -> str:
    p = str(path).replace('"', '""')
    return ('let\n    Source = Csv.Document(File.Contents("' + p + '"), [Delimiter=",", Encoding=65001, '
            'QuoteStyle=QuoteStyle.Csv]),\n    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars=true])\nin\n'
            '    Headers')


def slow_query(seconds: int) -> str:
    return ('let\n    Source = Function.InvokeAfter(() => #table({"x"}, {{1}}), #duration(0, 0, 0, '
            f'{int(seconds)}))\nin\n    Source')


def amounts(rows: list) -> int:
    return sum(int(float(r[2])) for r in rows if len(r) > 2 and r[2] not in (None, ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--timeout", type=float, default=60, help="giây chờ tối đa cho query nhỏ")
    ap.add_argument("--khong-thu-cham", action="store_true", help="bỏ bài thử query chậm")
    ap.add_argument("--out", default="", help="thư mục kết quả (mặc định: thư mục hiện tại)")
    a = ap.parse_args(argv)
    if sys.platform != "win32":
        say("Bài thử này chỉ chạy trên Windows có Excel.")
        return 2
    others = other_files_open()
    if others:
        say(f"Không chạy vì Excel/PowerPoint đang mở file khác: {', '.join(others)}. Đóng hết rồi chạy lại.")
        return 2
    import pythoncom
    import win32com.client
    from office.excel_com import ExcelBridge
    from office.power_query import LoadTimeout, PowerQuery

    out = Path(a.out) if a.out else Path.cwd()
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="clicky-pq-smoke-"))
    data = work / "DonHang_gia_lap.csv"
    data.write_text("So CT,Ngay,Thanh tien\n" + "".join(f"{a_},{b},{c}\n" for a_, b, c in ROWS), encoding="utf-8")
    res, timings = Result(), {}
    pythoncom.CoInitialize()
    app = None
    try:
        app = win32com.client.DispatchEx("Excel.Application")     # its own Excel: no other workbook is touched
        app.Visible = True
        wb = app.Workbooks.Add()
        wb.SaveAs(str(work / "thu_pq.xlsx"), 51)
        pq = PowerQuery(ExcelBridge(app=app))
        steps = []

        def step(event, **data):
            steps.append((event, data))
            say(f"   {event}: {data}")
        say("1/2 Tải query nhỏ qua đường làm mới của Clicky")
        ok_add = pq.add("ThuCSV", csv_query(data))
        t0 = time.monotonic()
        got = pq.load("ThuCSV", timeout=a.timeout, step=step) if ok_add else "không thêm được query"
        timings["tai_duoc"] = round(time.monotonic() - t0, 1)
        if isinstance(got, tuple):
            _, header, rows = got
            right = len(rows) == len(ROWS) and amounts(rows) == sum(r[2] for r in ROWS)
            res.add("1. Tải được query nhỏ", right, f"{len(rows)} dòng, tổng {amounts(rows):,} đ, {timings['tai_duoc']} giây")
        else:
            kind = "quá giờ" if isinstance(got, LoadTimeout) else "lỗi"
            res.add("1. Tải được query nhỏ", False, f"{kind}: {got} ({timings['tai_duoc']} giây)")
        if not a.khong_thu_cham:
            say(f"2/2 Query chậm {SLOW_SECONDS} giây, giới hạn {SLOW_LIMIT} giây: Clicky phải dừng chờ đúng hạn")
            pq.add("ThuCham", slow_query(SLOW_SECONDS))
            t0 = time.monotonic()
            late = pq.load("ThuCham", timeout=SLOW_LIMIT, step=step)
            timings["dung_dung_han"] = round(time.monotonic() - t0, 1)
            on_time = isinstance(late, LoadTimeout) and timings["dung_dung_han"] < SLOW_LIMIT + 10
            res.add("2. Dừng chờ đúng hạn khi query chậm", on_time,
                    f"{'dừng' if isinstance(late, LoadTimeout) else 'không dừng'} sau {timings['dung_dung_han']} giây")
            time.sleep(max(0, SLOW_SECONDS - timings["dung_dung_han"]) + 3)   # let Excel finish before closing
    except Exception as exc:                  # noqa: BLE001 - reported as KHÔNG ĐẠT with the reason
        res.add("Chạy bài thử", False, f"{type(exc).__name__}: {exc}")
    finally:
        if app is not None:
            for _ in range(10):
                try:
                    app.DisplayAlerts = False
                    for i in range(app.Workbooks.Count, 0, -1):
                        app.Workbooks(i).Close(SaveChanges=False)
                    app.Quit()
                    break
                except Exception:             # noqa: BLE001 - Excel still finishing the slow refresh
                    time.sleep(2)
    title = "Thử nhanh Power Query trên máy này"
    (out / "ket-qua-pq-smoke.md").write_text(res.markdown(title), encoding="utf-8")
    (out / "ket-qua-pq-smoke.json").write_text(json.dumps(
        {"ok": res.ok, "lines": [asdict(l) for l in res.lines], "seconds": timings}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    say(res.markdown(title))
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
