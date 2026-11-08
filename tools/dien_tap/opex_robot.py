"""Diễn tập tách chi phí cho các phòng: the robot starts the installed Clicky on a case folder with
--opex-split, waits for the run to end, and grades it. Practice (synthetic) data only.

    python tools\\dien_tap\\opex_robot.py --thu-muc <thu_muc_case>     (a synthetic case folder)

What it does:
 1. refuses to run while Excel has any other file open (real company files stay untouched);
 2. copies the case folder into the output folder (the original stays clean);
 3. starts Clicky.exe --opex-split on the copy with the scene log on, CLICKY_LOCAL_ONLY=1 and CLICKY_OUTLOOK=0.
    CLICKY_OUTLOOK=0 is not optional: the test PC's Outlook is a company mailbox, and a saved draft would sync to
    the company mail server. With it Clicky never starts Outlook and writes each draft as an .eml file;
 4. checks the case files were not changed, one workbook and one .eml per department, nothing sent, and no
    person's name or pay voucher from the case's pay lines in any department file or draft;
 5. writes ket-qua-robot.md + ket-qua-robot.json.

Exit code 0 = ĐẠT, 1 = KHÔNG ĐẠT, 2 = could not run.
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
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from present_robot import Result, file_hash, find_clicky, other_files_open, read_events, say  # noqa: E402

OUT_PREFIX = "Clicky_chi_phi_phong_ban"
# The names and pay vouchers in tao_case_chi_phi's pay lines. Pay is personal data: none may leave the FP&A team.
PERSONAL = ("Nguyễn Văn An", "Trần Thị Bình", "PL77701", "PL77702")


def robot_env(log: Path) -> dict:
    """The environment Clicky runs in for this robot: local only, never Outlook, never the PC's company
    configuration."""
    from robot_env import clean_env
    return clean_env(log, CLICKY_LOCAL_ONLY="1", CLICKY_OUTLOOK="0")


def finished(events: list) -> bool:
    if any(e.get("event") in ("opex_stop", "opex_error") for e in events):
        return True
    return any(e.get("event") == "opex_split" for e in events) and any(e.get("event") == "story_end" for e in events)


def file_texts(result_dir) -> dict:
    """Every text a department receives: each workbook's cells and properties, each draft's headers and parts."""
    import email
    import email.policy
    from openpyxl import load_workbook
    out = {}
    for p in sorted(Path(result_dir).glob("*.xlsx")) if result_dir else []:
        wb = load_workbook(p, read_only=True)
        cells = [str(v) for ws in wb.worksheets for row in ws.iter_rows(values_only=True) for v in row if v is not None]
        props = [str(getattr(wb.properties, k, "") or "") for k in ("title", "subject", "keywords", "description")]
        wb.close()
        out[p.name] = "\n".join(props + cells)
    for p in sorted(Path(result_dir).glob("*.eml")) if result_dir else []:
        m = email.message_from_bytes(p.read_bytes(), policy=email.policy.default)
        parts = [str(v) for v in m.values()]
        for part in m.walk():
            if part.get_content_maintype() == "text":
                parts.append(part.get_content())
            if part.get_filename():
                parts.append(part.get_filename())
        out[p.name] = "\n".join(parts)
    return out


def leaks(texts: dict) -> list:
    """The files that carry a pay line's name or voucher (file names only: the robot never repeats the names)."""
    return [name for name, text in texts.items() if any(x in text for x in PERSONAL)]


def grade(events: list, unchanged: bool, result_dir) -> Result:
    res = Result()
    split = [e for e in events if e.get("event") == "opex_split"]
    stop = [e for e in events if e.get("event") in ("opex_stop", "opex_error")]
    res.add("Tách xong chi phí cho các phòng", bool(split) and not stop,
            "" if split else ("Clicky dừng: " + stop[-1]["event"]) if stop else "không thấy kết quả tách")
    drafts = [e for e in events if e.get("event") == "drafts_saved"]
    d = drafts[-1] if drafts else {}
    res.add("Không mở Outlook, không gửi thư nào", bool(drafts) and d.get("outlook") == 0 and d.get("sent") == 0,
            "" if drafts else "không thấy bước lưu thư nháp")
    packs = split[-1].get("packs", 0) if split else 0
    files = sorted(Path(result_dir).glob("*.xlsx")) if result_dir else []
    emls = sorted(Path(result_dir).glob("*.eml")) if result_dir else []
    res.add("Mỗi phòng một file và một thư nháp", packs > 0 and len(files) == packs and len(emls) == packs
            and d.get("eml") == packs, f"{packs} phòng, {len(files)} file, {len(emls)} thư nháp")
    res.add("Khớp sổ cân đối trước khi tách", bool(split) and split[-1].get("matched") is not False,
            "" if not split or split[-1].get("matched") else "thư mục không có bảng cân đối")
    leaked = leaks(file_texts(result_dir)) if result_dir else []
    res.add("Không lộ tên người hay chứng từ lương", bool(files) and not leaked,
            ("có trong: " + ", ".join(leaked)) if leaked else "")
    res.add("File của case không bị sửa", unchanged)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--thu-muc", required=True, help="thư mục case GIẢ LẬP có sổ chi tiết chi phí")
    ap.add_argument("--clicky", default="", help="đường dẫn Clicky.exe (mặc định: bản cài mới nhất)")
    ap.add_argument("--out", default="", help="thư mục kết quả (mặc định: dien-tap-chi-phi-<giờ> trong thư mục hiện tại)")
    ap.add_argument("--timeout", type=float, default=300, help="giây chờ tối đa")
    a = ap.parse_args(argv)
    exe = a.clicky or find_clicky()
    if not exe or not Path(exe).exists():
        say("Không thấy Clicky.exe. Cài Clicky hoặc truyền --clicky.")
        return 2
    if other_files_open():
        say("Robot không chạy vì Excel/PowerPoint đang mở file khác (robot không đọc tên). Đóng hết rồi chạy lại.")
        return 2
    out = Path(a.out) if a.out else Path.cwd() / f"dien-tap-chi-phi-{time.strftime('%Y%m%d-%H%M')}"
    out.mkdir(parents=True, exist_ok=True)
    case = out / "case"
    shutil.copytree(a.thu_muc, case, ignore=shutil.ignore_patterns(OUT_PREFIX + "*", "~$*"))
    hashes = {p: file_hash(p) for p in case.iterdir() if p.is_file()}
    log = out / "scene.jsonl"
    say(f"1/2 Mở Clicky tách chi phí: {exe} --opex-split {case}")
    subprocess.Popen([exe, "--opex-split", str(case)], env=robot_env(log), cwd=str(Path(exe).parent))
    end = time.monotonic() + a.timeout
    while time.monotonic() < end and not finished(read_events(log)):
        time.sleep(0.5)
    say("2/2 Chấm")
    unchanged = all(p.exists() and file_hash(p) == h for p, h in hashes.items())
    result_dirs = sorted(case.glob(OUT_PREFIX + "*"))
    res = grade(read_events(log), unchanged, result_dirs[-1] if result_dirs else None)
    title = "Diễn tập tách chi phí cho các phòng"
    (out / "ket-qua-robot.md").write_text(res.markdown(title), encoding="utf-8")
    (out / "ket-qua-robot.json").write_text(json.dumps({"ok": res.ok, "lines": [asdict(l) for l in res.lines]},
                                                       ensure_ascii=False, indent=2), encoding="utf-8")
    say(res.markdown(title))
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
