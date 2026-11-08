"""Makes the synthetic case folder for the opex split rehearsal (tests/fpa_samples.opex_folder): a made-up
company's cost detail for 2026, its AOP, a catalogue with department addresses and the October trial balance.
Two pay lines name a person, so the robot can check those names never reach a department's file or draft.

    python tools\\dien_tap\\tao_case_chi_phi.py --out <thu_muc_moi>

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


def make(out: Path) -> Path:
    if out.exists():
        raise FileExistsError(out)
    return fpa_samples.opex_folder(Path(out), tb=True, payroll=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="thư mục mới cho case giả lập (chưa có sẵn)")
    a = ap.parse_args(argv)
    try:
        d = make(Path(a.out))
    except FileExistsError:
        print(f"Thư mục {a.out} đã có. Chọn một thư mục mới.")
        return 2
    print(f"Đã tạo case giả lập: {d}")
    print(f"Chạy tiếp: python tools\\dien_tap\\opex_robot.py --thu-muc \"{d}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
