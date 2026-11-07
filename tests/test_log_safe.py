"""clicky.log never holds a customer's file or folder name; the error kind and reason stay readable."""
import logging

from engine.log_safe import scrub, shape


def test_a_path_becomes_its_shape():
    assert shape("C:\\Users\\chi\\AgriS\\DonHang_HN_T10.csv") == "C:\\…\\<14 ký tự>.csv"
    assert shape("\\\\server\\share\\Ke toan\\BaoCao T10.xlsx") == "\\\\…\\<10 ký tự>.xlsx"
    assert shape("C:\\Users\\chi\\AgriS\\DonHang Q3\\") == "C:\\…\\<10 ký tự>"
    assert shape("BCTC_Q3.xlsx") == "<7 ký tự>.xlsx"


def test_the_reason_stays_and_every_name_goes():
    cases = {
        "DataSource.NotFound: File or Folder: We couldn't find the folder 'C:\\Users\\chi\\AgriS\\DonHang Q3\\'.":
            "DataSource.NotFound: File or Folder: We couldn't find the folder 'C:\\…\\<10 ký tự>'.",
        "Expression.Error: The column 'Số CT' of the table wasn't found.":
            "Expression.Error: The column 'Số CT' of the table wasn't found.",
        "Formula.Firewall: Query 'DonHang' (step 'Nguon') references other queries":
            "Formula.Firewall: Query 'DonHang' (step 'Nguon') references other queries",
        "DataFormat.Error: We couldn't convert to Number. Details: 12/10/2026":
            "DataFormat.Error: We couldn't convert to Number. Details: 12/10/2026",
        'File "C:\\Users\\chi\\Bao cao\\Q3 final.csv" is locked': 'File "C:\\…\\<8 ký tự>.csv" is locked',
        "Could not find file C:\\Data\\BCTC_Q3.xlsx while reading": "Could not find file C:\\…\\<7 ký tự>.xlsx while reading",
        "BCTC_AgriS_Q3.xlsx: bad row": "<13 ký tự>.xlsx: bad row",
    }
    for raw, want in cases.items():
        assert scrub(raw) == want


def test_a_power_query_load_error_quoting_a_company_path_never_reaches_the_log(caplog, monkeypatch):
    from office import power_query

    class Boom(Exception):
        pass

    def refresh(qt, timeout, step=None):
        raise Boom("DataSource.NotFound: File or Folder: We couldn't find the folder "
                   "'C:\\Users\\chi\\AgriS\\BCTC 2026\\DonHang\\'.")

    class App:
        DisplayAlerts = True

    class Bridge:
        def _excel(self):
            return App()

    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(power_query, "refresh_with_timeout", refresh)
    pq = power_query.PowerQuery(Bridge())
    monkeypatch.setattr(pq, "table_for", lambda name: type("LO", (), {"QueryTable": object()})())
    out = pq.load("DonHang", timeout=0.2, step=lambda *a, **k: None)
    assert "DataSource.NotFound" in out                       # the caller still gets the full reason
    assert "AgriS" not in caplog.text and "BCTC" not in caplog.text and "Users" not in caplog.text
    assert "Power Query load failed for DonHang: DataSource.NotFound" in caplog.text
    assert "C:\\…\\<7 ký tự>" in caplog.text
