"""tools/dien_tap/m_agree.py: the PC check that Excel's per-file query reads every order format the way the receipt
does. Its pure parts run here; the Excel part runs on the PC (robot)."""
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))
import m_agree  # noqa: E402


def _as_excel_gives(rows: list, roles: list) -> list:
    """Rows the way Range.Value2 reads them back: dates as serial numbers, amounts as floats."""
    out = []
    for r in rows:
        row = []
        for role, v in zip(roles + ["file"], r):
            if isinstance(v, date):
                row.append(float((datetime(v.year, v.month, v.day) - datetime(1899, 12, 30)).days))
            elif isinstance(v, Decimal):
                row.append(float(v))
            else:
                row.append(v)
        out.append(row)
    return out


@pytest.mark.parametrize("i", range(len(m_agree.formats())))
def test_every_format_agrees_with_itself_read_back_as_excel_gives_it(tmp_path, i):
    label, name, write = m_agree.formats()[i]
    write(tmp_path / name)
    want = m_agree.plan(tmp_path, "ThuMuc1")
    assert want["rows"] and len(want["rows"]) == want["receipt_rows"]
    got = list(reversed(_as_excel_gives(want["rows"], want["roles"])))      # Table.Distinct may reorder
    ok, detail = m_agree.compare(want, want["header"], got)
    assert ok, detail
    assert "Folder.Files(ThuMuc1)" in want["m"]


def test_a_missing_or_changed_row_is_named():
    label, name, write = m_agree.formats()[1]
    import tempfile
    folder = Path(tempfile.mkdtemp())
    write(folder / name)
    want = m_agree.plan(folder, "P")
    got = _as_excel_gives(want["rows"], want["roles"])
    a = want["roles"].index("amount")
    got[0][a] += 1
    ok, detail = m_agree.compare(want, want["header"], got[:-1])
    assert not ok and "thừa" in detail and "thiếu" in detail and "4 dòng" in detail
    ok, detail = m_agree.compare(want, want["header"][::-1], got)
    assert not ok and detail.startswith("cột khác")


@pytest.mark.skipif(sys.platform == "win32", reason="on Windows main() drives the real Excel; CI has none")
def test_it_will_not_run_off_windows():
    assert m_agree.main([]) == 2
