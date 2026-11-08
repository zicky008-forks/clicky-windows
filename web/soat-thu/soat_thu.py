"""The browser page's one call: two trial-balance files in, a check sheet and the report workbook out.

Runs inside Pyodide (Python in the browser), so the files never leave the user's PC. Uses the same
engine as the desktop app; this module only turns its result into plain data for the page's cards.
Numbers come from the engine; every sentence here is a template filled with them, no AI.
"""
from __future__ import annotations

import io
import json
import tempfile
from decimal import Decimal
from pathlib import Path

from engine import sources
from engine.build import build
from engine.cli import write_workbook

SHEET_CUR, SHEET_PREV = "Goc ky nay", "Goc ky truoc"


def read_any(data: bytes, name: str) -> list:
    """Rows of the first sheet of an .xlsx/.xlsm, or of a .csv."""
    suffix = Path(name).suffix.lower()
    if suffix == ".csv":
        import csv
        text = data.decode("utf-8-sig", errors="replace")
        return [row for row in csv.reader(io.StringIO(text))]
    if suffix == ".xls":
        raise ValueError("xls")
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]


def trieu(amount) -> str:
    """556000000 → '556 triệu'; 5420000000 → '5,42 tỷ'. Vietnamese separators."""
    a = Decimal(amount)
    sign = "-" if a < 0 else ""
    a = abs(a)
    if a >= Decimal(10) ** 9:
        s = f"{a / Decimal(10) ** 9:.2f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"{sign}{s} tỷ"
    if a >= Decimal(10) ** 6:
        s = f"{a / Decimal(10) ** 6:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"{sign}{s} triệu"
    return f"{sign}{int(a):,}".replace(",", ".") + " đồng"


def soat(cur: bytes, cur_name: str, prev: bytes, prev_name: str, out_path: str = "") -> dict:
    """Check this month against last month. Returns plain data; writes the report workbook to out_path."""
    try:
        cur_rows, prev_rows = read_any(cur, cur_name), read_any(prev, prev_name)
    except ValueError as exc:
        if str(exc) == "xls":
            return {"ok": False, "error": "Clicky chưa đọc được file .xls đời cũ. Chị mở file trong Excel, "
                                          "chọn Lưu thành (Save As) dạng .xlsx rồi kéo lại vào đây nhé."}
        raise
    except Exception:
        return {"ok": False, "error": "Clicky chưa mở được file này. Chị kiểm tra lại đây có phải file "
                                      "bảng cân đối tài khoản xuất từ phần mềm kế toán không nhé."}
    try:
        report = build(cur_rows, prev_rows, "Kỳ này", "Kỳ trước")
    except Exception:
        return {"ok": False, "error": "Clicky chưa nhận ra bảng cân đối tài khoản trong file. Chị kiểm tra "
                                      "file có cột số dư đầu kỳ, phát sinh, số dư cuối kỳ không nhé."}

    pat = next(f.value for f in report.figures if f.key == "pat")
    overstated = sources.returns_in_revenue(report.current)
    real = pat - overstated if overstated > 0 else pat
    current_issues = [i for i in report.issues if not i.message.startswith("[")]
    previous_issues = [i for i in report.issues if i.message.startswith("[")]

    if overstated > 0:
        headline = (f"Lợi nhuận đang ghi {trieu(pat)}. Clicky tính đúng là {trieu(real)}.")
        reason = (f"{trieu(overstated)} hàng bán bị trả lại đang ghi thẳng Nợ TK 511. "
                  "Chuyển sang Nợ TK 521 trước khi gửi sếp.")
    elif current_issues:
        headline = f"Clicky thấy {len(current_issues)} điểm cần xem trước khi gửi sếp."
        reason = f"Lợi nhuận sau thuế kỳ này: {trieu(pat)}."
    else:
        headline = "Clicky không thấy lỗi nào trong sổ tháng này."
        reason = f"Lợi nhuận sau thuế kỳ này: {trieu(pat)}. Vẫn nên đọc qua các câu hỏi xác minh."

    if out_path:
        sheets = dict(report.sheets)
        sheets[SHEET_CUR] = cur_rows
        sheets[SHEET_PREV] = prev_rows
        write_workbook(Path(out_path), sheets)

    return {
        "ok": True,
        "headline": headline,
        "reason": reason,
        "pat": int(pat),
        "pat_real": int(real),
        "overstated": int(overstated),
        "issues": [{"level": i.severity, "text": i.message} for i in current_issues],
        "previous_issues": len(previous_issues),
        "questions": [q.question for q in report.questions],
        "summary": report.summary,
    }


def soat_json(cur, cur_name, prev, prev_name, out_path="") -> str:
    """The page calls this one: JS bytes in, a JSON string out (no Python objects cross into JS)."""
    return json.dumps(soat(bytes(cur), cur_name, bytes(prev), prev_name, out_path), ensure_ascii=False)


if __name__ == "__main__":       # python web/soat-thu/soat_thu.py TB_T10.xlsx TB_T09.xlsx
    import sys
    a, b = Path(sys.argv[1]), Path(sys.argv[2])
    out = Path(tempfile.gettempdir()) / "BaoCao_soat_thu.xlsx"
    print(json.dumps(soat(a.read_bytes(), a.name, b.read_bytes(), b.name, str(out)), ensure_ascii=False, indent=1))
