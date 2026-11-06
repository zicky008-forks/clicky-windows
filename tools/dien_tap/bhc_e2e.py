"""Diễn tập 4 chặng (bhc_e2e): one robot plays the finance team through a whole month on a made-up FMCG case,
then grades each stage ĐẠT/KHÔNG ĐẠT. Practice (synthetic) data only, on the Windows test PC.

    python tools\\dien_tap\\bhc_e2e.py                    (writes a fresh case with tools/dien_tap/bhc_case.py)
    python tools\\dien_tap\\bhc_e2e.py --thu-muc <case>    (a case folder that has dap_an.json)
    python tools\\dien_tap\\bhc_e2e.py --chang 3,4         (only some stages; 1 and 2 always run together)

The four stages, each on the installed Clicky with the scene log on and CLICKY_LOCAL_ONLY=1:
 1. Thả thư mục: Clicky.exe --drop <case>, as if the folder were dropped on the drop zone. Clicky must
    show the receipt (files read, rows used and left out) before it changes anything, and open the right
    report workbook out of the many in the folder.
 2. Chốt số: the robot presses "Đúng rồi, làm tiếp", then "Sửa giúp tôi" if a fix is offered, then
    "Lưu và kiểm tra". Clicky must say the profit dap_an.json holds, reopen the saved file and find it
    matching, keep the original (_ban-goc), and leave every source file unchanged.
 3. Soát bộ BOD: tools/dien_tap/bod_robot.py on the same folder, graded against the planted mistakes.
 4. Thuyết trình + hỏi đáp: tools/dien_tap/present_robot.py on the case's talk (trinh_bay/).

Results: <out>/ket-qua-4-chang.md and .json, and one sub-folder per stage with its scene.jsonl, screenshots
and Clicky's own results. Exit code 0 = all stages ĐẠT, 1 = a stage KHÔNG ĐẠT, 2 = could not run.
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

from present_robot import Result, file_hash, find_clicky, other_files_open, read_events, say, screenshot  # noqa: E402

INTAKE = "Đúng rồi, làm tiếp"
FIX = "Sửa giúp tôi"
SAVE = "Lưu và kiểm tra"
AUTO_PRESS = (INTAKE, FIX, SAVE)
ORIGINAL_TAG = "_ban-goc"


# ── grading, pure (tested off Windows) ────────────────────────────────────────────────────────

def grade_close(events: list, key: dict, sources_unchanged: bool, original_kept: bool) -> Result:
    """Stages 1 and 2 from the scene log of one run: the receipt came first, the right report was used,
    the profit is the expected one, the saved file was reopened and matched."""
    from engine.monthly_report import spoken
    res = Result()
    kinds = [e.get("event") for e in events]
    intake = next((e for e in events if e.get("event") == "intake"), None)
    first_change = next((i for i, k in enumerate(kinds) if k in ("verify", "delivered")), None)
    receipt_first = intake is not None and (first_change is None or kinds.index("intake") < first_change)
    files = [str(f.get("name", "")) for f in (intake or {}).get("files", []) if isinstance(f, dict)]
    balanced = all(abs(float(f.get("total", 0)) - float(f.get("used", 0)) - float(f.get("dropped", 0))) <= 1
                   for f in (intake or {}).get("files", []) if isinstance(f, dict))
    res.add("1a. Phiếu nhận file hiện trước khi sửa gì", receipt_first,
            "" if intake else "không thấy phiếu nhận file (intake) trong nhật ký")
    res.add("1b. Mỗi file: dòng dùng + dòng bỏ = tổng", intake is not None and balanced,
            ", ".join(files) if files else "")
    report = key.get("report", "")
    verify = [e for e in events if e.get("event") == "verify"]
    path = str((verify[-1] if verify else {}).get("path") or "")
    res.add("1c. Mở đúng file báo cáo", bool(path) and Path(path).name == report,
            f"Clicky lưu {Path(path).name}" if path else "Clicky chưa lưu file báo cáo nào")
    want = spoken(Decimal(str(key["close"]["lnst"])))
    said = [str(e.get("value") or "") for e in events if e.get("event") == "beat"
            and str(e.get("title") or "").startswith("Lợi nhuận sau thuế")]
    res.add("2a. Lợi nhuận sau thuế đúng", want in said, f"cần {want}, Clicky nói {', '.join(said) or 'chưa nói'}")
    ok = bool(verify) and verify[-1].get("ok") is True and any(e.get("event") == "delivered" for e in events)
    diffs = (verify[-1].get("diffs") if verify else None) or []
    res.add("2b. Mở lại file đã lưu, đối chiếu khớp", ok,
            "" if ok else ("; ".join(str(d.get("cell")) for d in diffs) or "chưa đối chiếu lại file đã lưu"))
    res.add("2c. Giữ bản gốc (_ban-goc)", original_kept)
    res.add("2d. File nguồn không bị sửa", sources_unchanged)
    errors = [str(e.get("text", "")) for e in events if e.get("event") == "error"]
    res.add("2e. Không có lỗi hiện cho người dùng", not errors, "; ".join(errors[:3]))
    return res


def summary(stages: list) -> Result:
    """[(stage name, Result or None when it could not run, detail)] -> one line per stage."""
    res = Result()
    for name, r, detail in stages:
        if r is None:
            res.add(name, False, detail or "không chạy được")
        else:
            bad = [l.name for l in r.lines if not l.ok]
            res.add(name, r.ok, detail or ("" if r.ok else "trượt: " + "; ".join(bad)))
    return res


# ── driving Clicky on Windows ─────────────────────────────────────────────────────────────────

def stop_clicky() -> None:
    subprocess.run(["taskkill", "/IM", "Clicky.exe", "/F"], capture_output=True)
    time.sleep(2.0)


def close_workbooks(names: set) -> None:
    """Close the case's workbooks in Excel without saving: Clicky saved what it had to save itself."""
    try:
        import win32com.client
        app = win32com.client.GetActiveObject("Excel.Application")
        for i in range(app.Workbooks.Count, 0, -1):
            wb = app.Workbooks(i)
            if str(wb.Name) in names:
                wb.Close(SaveChanges=False)
    except Exception:                         # noqa: BLE001 - Excel already closed
        pass


def run_close(exe: str, case: Path, key: dict, out: Path, timeout: float) -> Result:
    import drivers
    out.mkdir(parents=True, exist_ok=True)
    shots = out / "anh"
    shots.mkdir(exist_ok=True)
    log = out / "scene.jsonl"
    report = case / key["report"]
    sources = [p for p in case.iterdir() if p.is_file() and p != report and p.name != "dap_an.json"]
    hashes = {p: file_hash(p) for p in sources}
    env = dict(os.environ, CLICKY_SCENE_LOG=str(log), CLICKY_LOCAL_ONLY="1")
    say(f"Chặng 1–2: {exe} --drop {case}")
    subprocess.Popen([exe, "--drop", str(case)], env=env, cwd=str(Path(exe).parent))
    driver = drivers.Uia("Clicky.exe")
    pressed, shot = set(), 0
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        events = read_events(log)
        for i, e in enumerate(events):
            if e.get("event") != "choices" or i in pressed:
                continue
            hit = next((lab for lab in e.get("labels") or [] if lab in AUTO_PRESS), None)
            if hit:
                pressed.add(i)
                time.sleep(1.0)
                screenshot(shots / f"{shot:02d}-truoc-khi-bam.png")
                shot += 1
                say(f"   Bấm \"{hit}\"")
                if driver.wait_for(hit, 20):
                    driver.press(hit)
        if any(e.get("event") in ("delivered", "error") for e in events) or \
                any(e.get("event") == "verify" and not e.get("ok") for e in events):
            time.sleep(4.0)
            break
        time.sleep(1.0)
    screenshot(shots / f"{shot:02d}-ket-thuc.png")
    stop_clicky()
    close_workbooks({report.name})
    unchanged = all(p.exists() and file_hash(p) == h for p, h in hashes.items())
    kept = any(ORIGINAL_TAG in p.stem for p in case.glob("*.xls*"))
    res = grade_close(read_events(log), key, unchanged, kept)
    (out / "ket-qua.md").write_text(res.markdown("Chặng 1–2: thả thư mục, chốt số"), encoding="utf-8")
    return res


def _sub(main, argv: list, out: Path):
    """Run another robot's main() and read the result it wrote (None when it could not run)."""
    code = main(argv)
    for name in ("ket-qua-robot.json", "ket-qua.json"):
        p = out / name
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            res = Result()
            for line in data.get("lines", []):
                res.add(line["name"], bool(line["ok"]), line.get("detail", ""))
            return res
    return None if code == 2 else Result()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--thu-muc", default="", help="thư mục case GIẢ LẬP có dap_an.json (mặc định: tự tạo bằng bhc_case.py)")
    ap.add_argument("--sach", action="store_true", help="tự tạo bản sạch: bộ BOD không có lỗi cài sẵn")
    ap.add_argument("--chang", default="1,2,3,4", help="các chặng chạy, ví dụ 3,4 (1 và 2 luôn chạy cùng nhau)")
    ap.add_argument("--clicky", default="", help="đường dẫn Clicky.exe (mặc định: bản cài mới nhất)")
    ap.add_argument("--out", default="", help="thư mục kết quả (mặc định: dien-tap-4-chang-<giờ> trong thư mục hiện tại)")
    ap.add_argument("--timeout", type=float, default=600, help="giây chờ tối đa cho mỗi chặng")
    a = ap.parse_args(argv)
    stages_wanted = {s.strip() for s in a.chang.split(",") if s.strip()}

    exe = a.clicky or find_clicky()
    if not exe or not Path(exe).exists():
        say("Không thấy Clicky.exe. Cài Clicky hoặc truyền --clicky.")
        return 2
    others = other_files_open()
    if others:
        say(f"Robot không chạy vì Excel/PowerPoint đang mở file khác: {', '.join(others)}. Đóng hết rồi chạy lại.")
        return 2
    out = Path(a.out) if a.out else Path.cwd() / f"dien-tap-4-chang-{time.strftime('%Y%m%d-%H%M')}"
    case = out / "case"
    out.mkdir(parents=True, exist_ok=True)
    if a.thu_muc:
        shutil.copytree(a.thu_muc, case, ignore=shutil.ignore_patterns("Clicky_*", "~$*", "dien-tap-*"))
    else:
        import bhc_case
        bhc_case.make(case, plant=not a.sach)
    key = json.loads((case / "dap_an.json").read_text(encoding="utf-8"))

    from storage import set_setting, setting
    old_recording = bool(setting("allow_screen_recording", False))
    set_setting("allow_screen_recording", True)
    stages = []
    try:
        stop_clicky()
        if stages_wanted & {"1", "2"}:
            stages.append(("Chặng 1–2: thả thư mục, chốt số", run_close(exe, case, key, out / "1-2-chot-so", a.timeout), ""))
            stop_clicky()
        if "3" in stages_wanted:
            import bod_robot
            sub = out / "3-soat-bod"
            say("Chặng 3: soát bộ BOD")
            stages.append(("Chặng 3: soát bộ BOD", _sub(bod_robot.main, ["--thu-muc", str(case), "--dap-an",
                                                                          str(case / "dap_an.json"), "--clicky", exe,
                                                                          "--out", str(sub), "--timeout",
                                                                          str(a.timeout)], sub), ""))
            stop_clicky()
        if "4" in stages_wanted:
            import present_robot
            talk = key["talk"]
            sub = out / "4-trinh-bay"
            say("Chặng 4: thuyết trình + hỏi đáp")
            stages.append(("Chặng 4: thuyết trình + hỏi đáp",
                           _sub(present_robot.main, ["--spec", str(case / talk["spec"]), "--xlsx", str(case / talk["xlsx"]),
                                                     "--deck", str(case / talk["deck"]), "--clicky", exe, "--out", str(sub)],
                                sub), ""))
            stop_clicky()
    finally:
        set_setting("allow_screen_recording", old_recording)
    res = summary(stages)
    title = "Diễn tập 4 chặng" + (" (bản sạch)" if a.sach else "")
    (out / "ket-qua-4-chang.md").write_text(res.markdown(title) + "\n" + "\n".join(
        r.markdown(name) for name, r, _ in stages if r is not None), encoding="utf-8")
    (out / "ket-qua-4-chang.json").write_text(json.dumps(
        {"ok": res.ok, "lines": [asdict(l) for l in res.lines],
         "stages": {name: [asdict(l) for l in r.lines] if r is not None else None for name, r, _ in stages}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    say(res.markdown(title))
    say(f"Kết quả: {out}")
    if any(r is None for _, r, _ in stages):
        return 2
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
