"""The browser page's Python glue and its build: same engine, plain-data result, nothing to fetch from elsewhere."""
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web" / "soat-thu"))
sys.path.insert(0, str(ROOT / "tools" / "web"))

import soat_thu  # noqa: E402
import build_soat_thu  # noqa: E402

SAMPLE = ROOT / "assets" / "mau" / "Mau_SongHong_T10"


def _sample(out=""):
    cur, prev = SAMPLE / "TB_T10.xlsx", SAMPLE / "TB_T09.xlsx"
    return soat_thu.soat(cur.read_bytes(), cur.name, prev.read_bytes(), prev.name, out)


def test_sample_finds_the_returns_trap(tmp_path):
    out = tmp_path / "BaoCao.xlsx"
    r = _sample(str(out))
    assert r["ok"]
    assert (r["pat"], r["pat_real"], r["overstated"]) == (556_000_000, 436_000_000, 120_000_000)
    assert r["headline"] == "Lợi nhuận đang ghi 556 triệu. Clicky tính đúng là 436 triệu."
    assert "TK 521" in r["reason"]
    assert len(r["issues"]) == 3 and r["questions"]
    from openpyxl import load_workbook
    assert "BC thang" in load_workbook(out).sheetnames


def test_json_is_plain_text_for_the_page():
    cur, prev = SAMPLE / "TB_T10.xlsx", SAMPLE / "TB_T09.xlsx"
    text = soat_thu.soat_json(cur.read_bytes(), cur.name, prev.read_bytes(), prev.name)
    assert json.loads(text)["pat_real"] == 436_000_000
    assert "Lợi nhuận" in text                     # Vietnamese kept, not \\u escapes


def test_wrong_files_get_plain_words():
    r = soat_thu.soat(b"not excel", "a.xlsx", b"x", "b.xlsx")
    assert not r["ok"] and "Clicky" in r["error"]
    r = soat_thu.soat(b"", "old.xls", b"", "b.xls")
    assert not r["ok"] and ".xlsx" in r["error"]


def test_money_in_words():
    assert soat_thu.trieu(556_000_000) == "556 triệu"
    assert soat_thu.trieu(5_420_000_000) == "5,42 tỷ"
    assert soat_thu.trieu(-120_000_000) == "-120 triệu"


def test_build_without_pyodide(tmp_path):
    out = build_soat_thu.build(tmp_path / "site", with_pyodide=False)
    for name in ("index.html", "app.js", "worker.js", "_headers", "app.zip", "mau/TB_T10.xlsx", "mau/TB_T09.xlsx"):
        assert (out / name).is_file(), name
    names = zipfile.ZipFile(out / "app.zip").namelist()
    assert "soat_thu.py" in names and "engine/build.py" in names and "openpyxl/__init__.py" in names
    # Privacy: the page may only talk to its own origin, and loads no script from a CDN.
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "connect-src 'self'" in html and "connect-src 'self'" in (out / "_headers").read_text()
    for page in ("index.html", "app.js", "worker.js"):
        text = (out / page).read_text(encoding="utf-8")
        assert "cdn." not in text and "importScripts(\"http" not in text and '<script src="http' not in text, page
