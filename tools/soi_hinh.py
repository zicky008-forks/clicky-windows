"""Screen check of a real report, printed as geometry only: for each place where Clicky's screen check fails,
where the place, the answer bubble and its leader line sit, in pixels and element tags. No text, no figures.

    python tools/soi_hinh.py "<report>.html"

The report (and its _kich_ban_Clicky.yaml, if any) is copied into a temporary folder, Clicky is built there with a
silent voice, the screen check runs, and the folder is deleted. Nothing is written next to the report, nothing is
sent anywhere. Used to fix a failed screen check on a report whose figures must stay on this PC.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or not Path(argv[0]).is_file():
        print("Cách dùng: python tools/soi_hinh.py <báo cáo>.html")
        return 2
    os.environ["CLICKY_LOCAL_ONLY"] = "1"
    from present import clicky_html, kiem_hinh
    src = Path(argv[0])
    tmp = Path(tempfile.mkdtemp(prefix="clicky_soi_"))
    try:
        copy = tmp / src.name
        shutil.copyfile(src, copy)
        script = src.with_name(src.stem + "_kich_ban_Clicky.yaml")
        if script.is_file():
            shutil.copyfile(script, tmp / script.name)
        clicky_html.wav_to_mp3 = lambda wav: (wav, 3.0)
        clicky_html.progress = lambda *_: None
        out = clicky_html.build(str(copy), owner="anh", contact="a@b.vn", voice=lambda t: b"")["out"]
        soi: list = []
        res = kiem_hinh.check(out, soi=soi)
        print(f"Kiểm hình: {len(res['problems'])} chỗ chưa ổn" + (": " + kiem_hinh.kinds(res["problems"], top=6) if res["problems"] else "."))
        for item in soi:
            print("SOI " + json.dumps(item, ensure_ascii=False))
        return 0 if res.get("ok") else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
