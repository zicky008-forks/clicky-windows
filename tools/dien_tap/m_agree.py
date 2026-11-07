"""Đối chiếu query đọc từng file với phiếu nhận file (khoảng 1 phút): does Excel's Power Query read every order
format the way Clicky's receipt does, row for row?

    python tools\\dien_tap\\m_agree.py
    python tools\\dien_tap\\m_agree.py --out <thư mục kết quả>

Practice data only: the five synthetic October orders of tests/order_exports.py, written in %TEMP% as a real
Excel 97-2003 .xls, an HTML table saved as .xls (twice: signature date in the date column and beside it),
SpreadsheetML saved as .xls, and an English-header sales CSV, each in its own folder.

For each format, one query from m_code.order_files_query (the one Clicky builds in the month close) is loaded in
a new workbook this script adds and shows; its rows are compared with sources.query_rows (the receipt's own
reading): same number of rows, same total, same values row for row. ĐẠT only when every format agrees.

Safety: it refuses to run while Excel or PowerPoint has any other file open; it never saves the workbook it adds
and closes it at the end (Excel itself is closed only if this script started it); no folder path is printed or
written. Also checked, for information only: whether this Excel has Html.Table and Web.Page (not used by Clicky).

Writes ket-qua-m-agree.md/.json to --out. Exit 0 = ĐẠT, 1 = KHÔNG ĐẠT, 2 = could not run.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from present_robot import Result, say  # noqa: E402

LOAD_SECONDS = 120


def formats() -> list:
    """(label, file name, writer) for every format the per-file query must read."""
    import order_exports as ox
    return [("Excel 97-2003 .xls", "SoChiTiet_T10.xls", ox.write_ole2_xls),
            ("HTML lưu thành .xls (MISA)", "MISA_T10.xls", ox.write_html_xls),
            ("HTML .xls, ngày ký bên cạnh", "MISA_ky_T10.xls", lambda p: ox.write_html_xls(p, date_in_column=False)),
            ("SpreadsheetML lưu thành .xls", "BanHang_T10.xls", ox.write_spreadsheetml),
            ("CSV tiếng Anh có dòng tiêu đề", "Shopee_T10.csv", ox.write_english_csv)]


def plan(folder: Path, param: str) -> dict:
    """What Clicky would build for this folder and what the receipt says the query must return."""
    from engine import m_code, sources
    from office import month_close
    files = sources.find_order_files(folder)
    truth = sources.truth(files)
    roles = sorted({r for f in files for r in f.columns} | {"doc", "date", "amount"})
    params = {f.name: {"order": f.date_order or sources.DAY_FIRST, "encoding": sources.file_encoding(f.path)}
              for f in files}
    dedup = month_close._dedup_roles(files, truth)
    m = m_code.order_files_query(param, params, roles, dedup)
    order = [r for r in m_code.CANON if r in roles]
    return {"m": m, "header": [m_code.CANON[r] for r in order] + [m_code.SOURCE_COLUMN],
            "rows": sources.query_rows(files, roles, None, dedup), "roles": order, "receipt_rows": truth.unique_rows,
            "receipt_total": truth.amount}


def _serial_date(v):
    """Value2 gives a date as its serial number; 1900 date system."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (datetime(1899, 12, 30) + timedelta(days=float(v))).date()
    return v


def _money(v):
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v)).quantize(Decimal("0.01"))
    except Exception:                         # noqa: BLE001 - shown as it came
        return v


def normalise(row: list, roles: list) -> tuple:
    out = []
    for r, v in zip(roles + ["file"], row):
        if r == "date":
            out.append(_serial_date(v) if v not in (None, "") else None)
        elif r in ("amount", "cost"):
            out.append(_money(v))
        else:
            out.append("" if v is None else (str(int(v)) if isinstance(v, float) and v == int(v) else str(v)).strip())
    return tuple(out)


def compare(want: dict, header: list, rows: list) -> tuple:
    """(ok, detail) for one format: header, row count, total, and the rows as a multiset."""
    roles = want["roles"]
    if [str(h) for h in header] != want["header"]:
        return False, f"cột khác: Excel {header} / cần {want['header']}"
    got = sorted((normalise(r, roles) for r in rows), key=repr)
    need = sorted((normalise(r, roles) for r in want["rows"]), key=repr)
    a = roles.index("amount")
    total = sum((r[a] for r in got if isinstance(r[a], Decimal)), Decimal(0))
    need_total = sum((r[a] for r in need if isinstance(r[a], Decimal)), Decimal(0))
    if got == need:
        return True, f"{len(got)} dòng, tổng {int(total):,} đ, khớp phiếu nhận từng dòng"
    extra = [r for r in got if r not in need][:3]
    missing = [r for r in need if r not in got][:3]
    return False, (f"Excel {len(got)} dòng / {int(total):,} đ, phiếu {len(need)} dòng / {int(need_total):,} đ"
                   + (f"; thừa {extra}" if extra else "") + (f"; thiếu {missing}" if missing else ""))


def write_ole2_with_excel(app, path: Path) -> Path:
    """The same print-layout .xls as order_exports.write_ole2_xls, saved by Excel itself (Excel 97-2003, format 56),
    for a PC without xlwt. The workbook is this script's own and is closed at once."""
    import order_exports as ox
    book = app.Workbooks.Add()
    try:
        ws = book.Worksheets(1)
        r = 1
        for line in ox.TITLE:
            ws.Cells(r, 1).Value = line[0]
            r += 1
        r += 1
        for c, h in enumerate(ox.VI_HEADER, 1):
            ws.Cells(r, c).Value = h
        r += 1
        for doc, d, kh, item, qty, amt, cost, _ in ox.ORDERS:
            for c, v in enumerate((doc, None, kh, item, qty, float(amt), float(cost)), 1):
                if v is not None:
                    ws.Cells(r, c).Value = v
            ws.Cells(r, 2).Value = datetime(d.year, d.month, d.day)
            ws.Cells(r, 2).NumberFormat = "dd/mm/yyyy"
            r += 1
        ws.Cells(r, 1).Value = "Tổng cộng"
        ws.Cells(r, 6).Value = float(ox.TOTAL)
        r += 2
        for line in ox.SIGN:
            for c, v in enumerate(line, 1):
                if v:
                    ws.Cells(r, c).Value = v
            r += 1
        book.SaveAs(str(path), 56)
    finally:
        book.Close(SaveChanges=False)
    return path


def missing_readers() -> list:
    out = []
    for mod in ("xlrd",):
        try:
            __import__(mod)
        except ImportError:
            out.append(mod)
    return out


PROBES = {"Html.Table": 'let x = Html.Table("<table><tr><td>1</td></tr></table>", {{"c", "td"}}) in Table.RowCount(x)',
          "Web.Page": 'let x = Web.Page("<table><tr><td>1</td></tr></table>") in Table.RowCount(x)'}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="", help="thư mục kết quả (mặc định: thư mục hiện tại)")
    a = ap.parse_args(argv)
    if sys.platform != "win32":
        say("Bài đối chiếu này chỉ chạy trên Windows có Excel.")
        return 2
    from present_robot import other_files_open
    others = other_files_open()
    if others:
        say(f"Không chạy vì Excel/PowerPoint đang mở {len(others)} file khác. Đóng hết rồi chạy lại.")
        return 2
    import pythoncom
    import win32com.client
    from engine import m_code
    from office.excel_com import ExcelBridge
    from office.power_query import LoadTimeout, PowerQuery

    out = Path(a.out) if a.out else Path.cwd()
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="clicky-m-agree-"))
    res, seconds, info = Result(), {}, {}
    pythoncom.CoInitialize()
    try:
        win32com.client.GetActiveObject("Excel.Application")
        started = False
    except Exception:                         # noqa: BLE001 - not running: this script starts it, and closes it
        started = True
    app = win32com.client.Dispatch("Excel.Application")
    wb = None
    try:
        app.Visible = True
        if missing_readers():
            res.add("Bộ đọc file .xls cũ (xlrd) của Clicky", False,
                    "Python của robot thiếu xlrd nên phiếu nhận không đọc được file .xls thật; các dạng khác vẫn chạy")
        try:
            import xlwt  # noqa: F401 - the practice .xls is written by xlwt when it is there, else by Excel
            excel_writes = False
        except ImportError:
            excel_writes = True
        folders = []
        for i, (label, name, write) in enumerate(formats(), 1):
            folder = work / f"dang{i}"
            folder.mkdir()
            if excel_writes and name.endswith(".xls") and "97-2003" in label:
                write_ole2_with_excel(app, folder / name)
            else:
                write(folder / name)
            folders.append((i, label, folder))
        wb = app.Workbooks.Add()              # owned by this script, never saved
        wb.Activate()
        pq = PowerQuery(ExcelBridge(app=app))
        for i, label, folder in folders:
            param, query = f"ThuMuc{i}", f"DonHang{i}"
            want = plan(folder, param)
            say(f"{i}/{len(formats())} {label}: phiếu nhận {want['receipt_rows']} đơn, {int(want['receipt_total']):,} đ")
            pq.add(param, m_code.parameter_query(str(folder)))
            t0 = time.monotonic()
            got = pq.load(query, timeout=LOAD_SECONDS) if pq.add(query, want["m"]) else "không thêm được query"
            seconds[label] = round(time.monotonic() - t0, 1)
            if isinstance(got, tuple):
                _, header, rows = got
                ok, detail = compare(want, header, rows)
                res.add(f"{i}. {label}", ok, f"{detail} ({seconds[label]} giây)")
            else:
                kind = "quá giờ" if isinstance(got, LoadTimeout) else "lỗi"
                res.add(f"{i}. {label}", False, f"{kind}: {str(got)[:300]} ({seconds[label]} giây)")
        for j, (fn, m) in enumerate(PROBES.items(), 1):
            pq.add(f"Thu{j}", m)
            got = pq.load(f"Thu{j}", timeout=60)
            info[fn] = "có" if isinstance(got, tuple) else f"không ({str(got)[:120]})"
            res.unchecked(f"Thông tin: Excel này có {fn}", info[fn] + "; Clicky không dùng hàm này")
    except Exception as exc:                  # noqa: BLE001 - reported as KHÔNG ĐẠT with the reason
        res.add("Chạy bài đối chiếu", False, f"{type(exc).__name__}: {str(exc)[:300]}")
    finally:
        for _ in range(5):
            try:
                app.DisplayAlerts = False
                if wb is not None:
                    wb.Close(SaveChanges=False)
                if started and app.Workbooks.Count == 0:
                    app.Quit()
                break
            except Exception:                 # noqa: BLE001 - Excel still finishing a refresh
                time.sleep(2)
    title = "Đối chiếu query đọc từng file với phiếu nhận"
    (out / "ket-qua-m-agree.md").write_text(res.markdown(title), encoding="utf-8")
    (out / "ket-qua-m-agree.json").write_text(json.dumps(
        {"ok": res.ok, "lines": [asdict(l) for l in res.lines], "seconds": seconds, "probes": info},
        ensure_ascii=False, indent=2), encoding="utf-8")
    say(res.markdown(title))
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
