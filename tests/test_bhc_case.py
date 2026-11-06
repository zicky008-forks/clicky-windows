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
