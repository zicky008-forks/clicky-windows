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


def review_finished(events: list) -> bool:
    """Clicky has written its result AND walked through every finding on screen.

    bod_done is logged before the walk-through starts, so it alone is not the end (Case 07, 07/11:
    the robot stopped after 2 of 12 cards). The end is story_end (builds with PR #25), or one card
    with a cell per finding that has a cell, each read out."""
    if not any(e.get("event") == "bod_done" for e in events):
        return False
    if any(e.get("event") == "story_end" for e in events):
        return True
    beats = [e for e in events if e.get("event") == "beat"]
    spoken = sum(1 for e in events if e.get("event") == "spoken")
    want = sum(1 for e in events if e.get("event") == "bod_finding" and e.get("where"))
    cards = sum(1 for e in beats if e.get("ref"))
    return bool(beats) and spoken >= len(beats) and cards >= want


def stale_cards(events: list) -> list:
    """(previous beat, beat) pairs where a beat whose cell was not found on screen left the previous
    beat's card up: no 'clear' between the two beats (Case 07 run 3: beat 8's card stayed during beat 9)."""
    idx = [i for i, e in enumerate(events) if e.get("event") == "beat"]
    out, shown = [], None
    for n, k in enumerate(idx):
        e = events[k]
        end = idx[n + 1] if n + 1 < len(idx) else len(events)
        if e.get("ref") and not e.get("box") and shown is not None:
            cleared = any(x.get("event") == "clear" for x in events[idx[n - 1] + 1:end])
            if not cleared:
                out.append((events[shown].get("i"), e.get("i")))
        if any(x.get("event") == "card" for x in events[k + 1:end]):
            shown = k
        elif any(x.get("event") == "clear" for x in events[k + 1:end]):
            shown = None
    return out


SOURCE_CLOSED = "File nguồn mở để chỉ ô đã đóng, không lưu"


def source_books(res: Result, events: list) -> None:
    """Every file other than the BOD pack that Clicky opened to point at a cell is closed again, unsaved.

    Counted per file, not per show_book: the pack itself is shown again and again and may stay open
    (grader, 07/11). Needs `book` (an id, never a name) and `pack` on show_book and close_book; a build
    that does not log them gets a CHƯA KIỂM ĐƯỢC line, never a silent pass.
    """
    shown = [e for e in events if e.get("event") == "show_book" and e.get("ok")]
    closed = [e for e in events if e.get("event") == "close_book"]
    saved = [e for e in closed if e.get("saved")]
    if not shown:
        res.add(SOURCE_CLOSED, not saved, "không mở file nguồn nào" + (", nhưng có lần lưu" if saved else ""))
        return
    if not closed:
        res.unchecked(SOURCE_CLOSED, "build chưa ghi close_book")
        return
    if saved:
        res.add(SOURCE_CLOSED, False, f"{len(saved)} lần đóng có lưu")
        return
    if any("book" not in e for e in shown + closed):
        res.unchecked(SOURCE_CLOSED, "show_book/close_book chưa ghi mã file (book), chưa tách được bộ BOD")
        return
    sources = {e["book"] for e in shown if not e.get("pack")}
    missing = sources - {e["book"] for e in closed}
    res.add(SOURCE_CLOSED, not missing,
            f"mở {len(sources)} file nguồn ngoài bộ BOD" + (f", còn {len(missing)} file chưa đóng" if missing else ", đã đóng hết"))


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
    # A group checked in part, with what is missing said out loud, is the right behaviour (grader rule);
    # only a group not checked at all fails. The part-checked ones are named in the line.
    gaps = sorted(set(done.get("not_checked") or []))
    partly = sorted(set(done.get("partly") or []) - set(gaps))
    res.add("Đủ 7 nhóm đều kiểm được", not gaps,
            "; ".join(filter(None, [f"chưa kiểm được nhóm {', '.join(str(g) for g in gaps)}" if gaps else "",
                                    f"nhóm {', '.join(str(g) for g in partly)} kiểm một phần, Clicky đã nêu phần thiếu"
                                    if partly else ""])))
    findings = [e for e in events if e.get("event") == "bod_finding"]
    refs = [e.get("ref") for e in events if e.get("event") == "beat" and e.get("ref")]
    res.add("Mỗi lỗi một thẻ chỉ vào ô", sorted(refs) == sorted(f.get("where") for f in findings),
            f"{len(findings)} lỗi, {len(refs)} thẻ có ô")
    located = [e for e in events if e.get("event") == "locate" and e.get("ref") in refs]
    missed = sorted({e.get("ref") for e in located if not e.get("box")})
    landed = not refs or (not missed and {e.get("ref") for e in located} == set(refs))
    res.add("Thẻ nằm đúng ô trên màn hình", landed, ", ".join(missed) if missed else "")
    stale = stale_cards(events)
    res.add("Không còn thẻ cũ khi sang lỗi mới", not stale,
            "; ".join(f"thẻ beat {a} còn trên màn hình khi đọc beat {b}" for a, b in stale[:4]))
    res.add("Bộ BOD không bị sửa", pack_unchanged)
    source_books(res, events)
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


def inside(path: Path, folder: Path) -> bool:
    """path is folder itself or somewhere under it."""
    p, f = path.resolve(), folder.resolve()
    return p == f or f in p.parents


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--thu-muc", help="thư mục case GIẢ LẬP có bộ BOD bản nháp")
    src.add_argument("--tu-tao", action="store_true", help="tự tạo case giả lập có 9 lỗi biết trước")
    ap.add_argument("--dap-an", default="", help="dap_an.json của case (khoá 'bod'): chấm cả lỗi cài sẵn")
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
        say(f"Robot không chạy vì Excel/PowerPoint đang mở {len(others)} file khác (robot không đọc tên). Đóng hết rồi chạy lại.")
        return 2
    out = Path(a.out) if a.out else Path.cwd() / f"dien-tap-soat-bod-{time.strftime('%Y%m%d-%H%M')}"
    if a.thu_muc and inside(out, Path(a.thu_muc)):
        # Case 07, 07/11: results inside the case folder were copied into themselves, level after level.
        say(f"Thư mục kết quả {out} nằm trong thư mục case. Chọn --out ở ngoài, ví dụ "
            f"{Path(a.thu_muc).resolve().parent / ('ket-qua-' + Path(a.thu_muc).name)}.")
        return 2
    shots = out / "anh"
    shots.mkdir(parents=True, exist_ok=True)
    case = out / "case"
    answers = None
    if a.tu_tao:
        from tests.bod_case import make_case
        answers = make_case(case, plant=True)
    else:
        shutil.copytree(a.thu_muc, case, ignore=shutil.ignore_patterns(OUT_DIR, "~$*"))
    if a.dap_an:
        key = json.loads(Path(a.dap_an).read_text(encoding="utf-8"))
        answers = key.get("bod", key) if isinstance(key, dict) else key
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
            if review_finished(events):
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
