"""Diễn tập soát bộ BOD: the robot starts the installed Clicky on a case folder, watches it point at every
wrong cell of the draft pack, and grades the run. Practice (synthetic) data only.

    python tools\\dien_tap\\bod_robot.py --thu-muc <thu_muc_case>      (a case folder, e.g. the grader's Case 07)
    python tools\\dien_tap\\bod_robot.py --tu-tao                      (a made-up case with 9 known mistakes)

What it does:
 1. refuses to run while Excel has any other file open (real company files stay untouched);
 2. copies the case folder into the output folder (the original stays clean);
 3. turns on "Cho phép quay màn hình Clicky" for the run and puts the old setting back at the end;
 4. starts Clicky.exe --bod-review on the copy, scene log on, CLICKY_LOCAL_ONLY=1;
 5. screenshots every screen while Clicky lights each wrong cell (anh/loi-XX.png);
 6. closes the pack in Excel without saving, checks the pack copy was not changed;
 7. writes ket-qua-robot.md + ket-qua-robot.json; Clicky's own ket-qua.md, results workbook (with the
    Bien ban Clicky sheet) and scene.jsonl are in the same folder for the grader.

Pass gate (ĐẠT): the review finished, the results file was reopened and matched, all 7 groups could be checked
(none "CHƯA KIỂM ĐƯỢC"), one card per finding and
every card landed on its cell, the pack unchanged. With --tu-tao also: all 9 planted mistakes found at their
cell with the right number, at most 1 extra. Exit code 0 = ĐẠT, 1 = KHÔNG ĐẠT, 2 = could not run.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from present_robot import (Result, file_hash, find_clicky, other_files_open, read_events, say,  # noqa: E402
                           screenshot, wait_event)

PCT_CODES = {"wrong_base", "missing_pct", "missing_pp", "wrong_pp"}
OUT_DIR = "Clicky_soat_BOD"


def grade(events: list, pack_unchanged: bool, answers: list | None = None) -> Result:
    """Pure: the scene log of one run (and, for a made-up case, its planted mistakes) -> ĐẠT/KHÔNG ĐẠT lines."""
    res = Result()
    done = next((e for e in events if e.get("event") == "bod_done"), None)
    res.add("Clicky soát xong và ghi kết quả", done is not None,
            "" if done else "không thấy bod_done trong nhật ký")
    if done is None:
        res.add("Bộ BOD không bị sửa", pack_unchanged)
        return res
    res.add("Mở lại file kết quả thấy khớp", bool(done.get("verified")))
    gaps = sorted(set(done.get("not_checked") or []) | set(done.get("partly") or []))
    res.add("Đủ 7 nhóm đều kiểm được", not gaps, f"chưa kiểm được nhóm {', '.join(str(g) for g in gaps)}" if gaps else "")
    findings = [e for e in events if e.get("event") == "bod_finding"]
    refs = [e.get("ref") for e in events if e.get("event") == "beat" and e.get("ref")]
    res.add("Mỗi lỗi một thẻ chỉ vào ô", sorted(refs) == sorted(f.get("where") for f in findings),
            f"{len(findings)} lỗi, {len(refs)} thẻ có ô")
    located = [e for e in events if e.get("event") == "locate" and e.get("ref") in refs]
    missed = sorted({e.get("ref") for e in located if not e.get("box")})
    landed = not refs or (not missed and {e.get("ref") for e in located} == set(refs))
    res.add("Thẻ nằm đúng ô trên màn hình", landed, ", ".join(missed) if missed else "")
    res.add("Bộ BOD không bị sửa", pack_unchanged)
    if answers is not None:
        hit, wrong_number = 0, []
        for a in answers:
            m = [f for f in findings if f.get("where") == a["where"] and int(f.get("group", 0)) == a["group"]]
            if not m:
                continue
            tol = Decimal("0.0001") if a["code"] in PCT_CODES else Decimal(1)
            got = m[0].get("expected")
            if got is not None and abs(Decimal(str(got)) - Decimal(str(a["expected"]))) <= tol:
                hit += 1
            else:
                wrong_number.append(a["where"])
        extra = len(findings) - sum(1 for f in findings if any(f.get("where") == a["where"] for a in answers))
        res.add(f"Bắt đủ {len(answers)} lỗi cài sẵn, đúng ô đúng số", hit == len(answers),
                f"{hit}/{len(answers)}" + (f", sai số ở {', '.join(wrong_number)}" if wrong_number else ""))
        res.add("Báo động sai không quá 1", extra <= 1, f"{extra} báo động sai")
    return res


def close_pack(name: str) -> None:
    """Close the pack in Excel without saving: Clicky opened it only to point at cells."""
    try:
        import win32com.client
        app = win32com.client.GetActiveObject("Excel.Application")
        for i in range(app.Workbooks.Count, 0, -1):
            wb = app.Workbooks(i)
            if str(wb.Name) == name:
                wb.Close(SaveChanges=False)
    except Exception:                         # noqa: BLE001 - Excel already closed
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--thu-muc", help="thư mục case GIẢ LẬP có bộ BOD bản nháp")
    src.add_argument("--tu-tao", action="store_true", help="tự tạo case giả lập có 9 lỗi biết trước")
    ap.add_argument("--clicky", default="", help="đường dẫn Clicky.exe (mặc định: bản cài mới nhất)")
    ap.add_argument("--out", default="", help="thư mục kết quả (mặc định: dien-tap-soat-bod-<giờ> trong thư mục hiện tại)")
    ap.add_argument("--timeout", type=float, default=600, help="giây chờ tối đa cho cả lượt soát")
    a = ap.parse_args(argv)

    exe = a.clicky or find_clicky()
    if not exe or not Path(exe).exists():
        say("Không thấy Clicky.exe. Cài Clicky hoặc truyền --clicky.")
        return 2
    others = other_files_open()
    if others:
        say(f"Robot không chạy vì Excel/PowerPoint đang mở file khác: {', '.join(others)}. Đóng hết rồi chạy lại.")
        return 2
    out = Path(a.out) if a.out else Path.cwd() / f"dien-tap-soat-bod-{time.strftime('%Y%m%d-%H%M')}"
    shots = out / "anh"
    shots.mkdir(parents=True, exist_ok=True)
    case = out / "case"
    answers = None
    if a.tu_tao:
        from tests.bod_case import make_case
        answers = make_case(case, plant=True)
    else:
        shutil.copytree(a.thu_muc, case, ignore=shutil.ignore_patterns(OUT_DIR, "~$*"))
    packs = [p for p in case.iterdir() if p.suffix.lower() in (".xlsx", ".xlsm") and
             any(w in p.stem.lower() for w in ("bod", "pack", "nhap", "nháp"))]
    hashes = {p: file_hash(p) for p in packs}
    log = out / "scene.jsonl"
    from storage import set_setting, setting
    old_recording = bool(setting("allow_screen_recording", False))
    set_setting("allow_screen_recording", True)
    env = dict(os.environ, CLICKY_SCENE_LOG=str(log), CLICKY_LOCAL_ONLY="1")
    try:
        say(f"1/3 Mở Clicky soát bộ BOD: {exe} --bod-review {case}")
        subprocess.Popen([exe, "--bod-review", str(case)], env=env, cwd=str(Path(exe).parent))
        end = time.monotonic() + a.timeout
        say("2/3 Chụp màn hình mỗi lần Clicky chỉ vào một ô")
        seen = 0
        while time.monotonic() < end:
            events = read_events(log)
            beats = [e for e in events if e.get("event") == "beat"]
            for e in beats[seen:]:
                if e.get("ref"):
                    time.sleep(0.8)
                    screenshot(shots / f"loi-{int(e.get('i', 0)):02d}.png")
            seen = len(beats)
            done = any(e.get("event") == "bod_done" for e in events)
            spoken = sum(1 for e in events if e.get("event") == "spoken")
            if done and beats and spoken >= len(beats):
                break
            if any(e.get("event") == "bod_error" for e in events):
                break
            time.sleep(0.3)
        time.sleep(1.0)
        say("3/3 Đóng bộ BOD không lưu, chấm")
        for p in packs:
            close_pack(p.name)
    finally:
        set_setting("allow_screen_recording", old_recording)
    unchanged = all(file_hash(p) == h for p, h in hashes.items())
    res = grade(read_events(log), unchanged, answers)
    for name in ("ket-qua.md",):
        if (case / OUT_DIR / name).exists():
            shutil.copy(case / OUT_DIR / name, out / "ket-qua-clicky.md")
    title = "Diễn tập soát bộ BOD" + (" (case tự tạo)" if answers else "")
    (out / "ket-qua-robot.md").write_text(res.markdown(title), encoding="utf-8")
    (out / "ket-qua-robot.json").write_text(json.dumps({"ok": res.ok, "lines": [asdict(l) for l in res.lines]},
                                                       ensure_ascii=False, indent=2), encoding="utf-8")
    say(res.markdown(title))
    say(f"Kết quả: {out}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
