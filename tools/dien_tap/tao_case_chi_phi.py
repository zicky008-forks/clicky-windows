"""Makes the synthetic case folder for the opex split rehearsal (tests/fpa_samples.opex_folder): a made-up
company's cost detail for 2026, its AOP, a catalogue with department addresses and the October trial balance.
Two pay lines name a person, so the robot can check those names never reach a department's file or draft.

    python tools\\dien_tap\\tao_case_chi_phi.py --out <thu_muc_moi>
    python tools\\dien_tap\\tao_case_chi_phi.py --out <thu_muc_moi> --du-bao     (for the forecast robot)

--du-bao adds an approved forecast (LE T09, approved 20/09/2026) that lacks one department x cost element line in
November, so the forecast must take that line from the annual plan and say it.

Every figure and name is invented. The folder must not exist yet: nothing is ever overwritten.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

import fpa_samples  # noqa: E402

PERSONAL = fpa_samples.PERSONAL
FORECAST_FILE, FORECAST_VERSION, FORECAST_APPROVED = "LE_T09_2026.csv", "LE T09", "20/09/2026"
FORECAST_MISSING = (11, "MKT", "QC")              # (month, department, cost element) the forecast leaves out


def make(out: Path, forecast: bool = False) -> Path:
    if out.exists():
        raise FileExistsError(out)
    d = fpa_samples.opex_folder(Path(out), tb=True, payroll=True)
    if forecast:
        fpa_samples.write_csv(d / FORECAST_FILE, fpa_samples.plan_rows(
            FORECAST_VERSION, approved=FORECAST_APPROVED, scale=2, drop=[FORECAST_MISSING]))
    return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="thư mục mới cho case giả lập (chưa có sẵn)")
    ap.add_argument("--du-bao", action="store_true", help="thêm bản dự báo LE T09 đã duyệt, thiếu một dòng tháng 11")
    a = ap.parse_args(argv)
    try:
        d = make(Path(a.out), forecast=a.du_bao)
    except FileExistsError:
        print(f"Thư mục {a.out} đã có. Chọn một thư mục mới.")
        return 2
    print(f"Đã tạo case giả lập: {d}")
    robot = "forecast_robot.py" if a.du_bao else "opex_robot.py"
    print(f"Chạy tiếp: python tools\\dien_tap\\{robot} --thu-muc \"{d}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
