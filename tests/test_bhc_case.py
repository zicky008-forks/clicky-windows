"""The four-stage BHC-style rehearsal case: every stage passes on the engine alone, draft and clean."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))


@pytest.mark.parametrize("plant", [True, False])
def test_the_four_stage_case_passes_every_stage_on_the_engine(tmp_path, plant):
    import bhc_case
    bhc_case.make(tmp_path, plant=plant)
    res = bhc_case.check(tmp_path)
    assert res.ok and len(res.lines) == 4, res.markdown("bhc")


def test_the_case_csvs_look_like_real_exports_and_only_the_order_file_is_read_as_orders(tmp_path):
    """UTF-8 with BOM, comma separated, like an ERP export; the cost ledger CSV beside it is not an order file."""
    import bhc_case
    from engine import sources
    bhc_case.make(tmp_path)
    for name in ("Mastersale_T07-T10.csv", "GL_ChiTiet_ChiPhi_T07-T10.csv"):
        raw = (tmp_path / name).read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf") and b";" not in raw.splitlines()[0], name
    assert [f.name for f in sources.find_order_files(tmp_path)] == ["Mastersale_T07-T10.csv"]
