"""'Hỏi: …': a BOD question answered from the saved FP&A report (and forecast pack), never computed again and never
by the AI.

Typed in the chat ("hỏi: lợi nhuận gộp lũy kế?", then "hỏi tiếp: vì sao?") it works on the newest Clicky report in
the folder of the open workbook, or the folder last dropped. Started by the robot with
`Clicky.exe --hoi-dap THU_MUC --cau-hoi FILE` it answers every question of the file into a copy of the report with the
'Hoi dap BOD' sheet, reopened and checked. Both go through engine.hoi_dap, so the chat and the file give the same
answer. A question with no figure in the report is written down for the user, not guessed. The scene log records the
question's number and status only, never its words.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional

import scene_log
from office import month_close as mc
from office.month_close import Beat

log = logging.getLogger(__name__)

TRIGGER = r"^\s*(?:bod\s*)?h[ỏo]i(?:\s*(?:ti[ếe]p|th[êe]m))?\s*[:：]\s*\S"
FOLLOW_RE = re.compile(r"^\s*(?:bod\s*)?h[ỏo]i\s*(?:ti[ếe]p|th[êe]m)\s*[:：]|^\s*(?:bod\s*)?h[ỏo]i\s*[:：]\s*\+",
                       re.IGNORECASE)
ASK_RE = re.compile(r"^\s*(?:bod\s*)?h[ỏo]i(?:\s*(?:ti[ếe]p|th[êe]m))?\s*[:：]\s*\+?\s*", re.IGNORECASE)
MARK = {"trả lời": "ok"}
VOUCHERS_RE = re.compile(r" \(chứng từ: [^)]*\)")    # on screen only; the sheet and the record keep them


class NoReport(Exception):
    pass


def find_report(folder: Path) -> tuple:
    """(report, forecast pack or None): the newest Clicky report in the folder, its parent (the folder may be the
    report's own), or the folder itself."""
    from engine import forecast, fpa_report, hoi_dap
    folder = Path(folder)
    for where in (folder, folder.parent):
        report = hoi_dap.latest(where, fpa_report.OUT_PREFIX)
        if report is not None:
            return report, hoi_dap.latest(where, forecast.OUT_PREFIX)
    raise NoReport(str(folder))


def _record_line(report: Path, rec: dict) -> None:
    from engine import hoi_dap
    with open(report.parent / hoi_dap.JSONL, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def ask(manager, transcript: str, folder: Optional[Path] = None):
    """The answer (engine.hoi_dap.Answer) and its record, the follow-up context kept on the manager."""
    from engine import hoi_dap
    from office.bod_skill import find_folder
    folder = find_folder(getattr(manager, "_excel", None), folder)
    if folder is None or not Path(folder).is_dir():
        raise NoReport("")
    report, pack = find_report(Path(folder))
    book = hoi_dap.read(report, pack)
    follow = bool(FOLLOW_RE.search(transcript))
    question = ASK_RE.sub("", transcript, count=1).strip()
    parent = getattr(manager, "_hoi_dap_parent", None) if follow else None
    a = hoi_dap.answer(book, question, parent)
    count = getattr(manager, "_hoi_dap_count", 0)
    if not follow or parent is None:
        count += 1
        manager._hoi_dap_count, manager._hoi_dap_sub = count, 0
        manager._hoi_dap_parent = a.query
        number = f"Q{count}"
    else:
        manager._hoi_dap_sub = getattr(manager, "_hoi_dap_sub", 0) + 1
        number = f"Q{count}.{manager._hoi_dap_sub}"
    rec = hoi_dap.record(number, question, a, book)
    _record_line(report, rec)
    return a, rec


async def handle(manager, transcript: str) -> str:
    try:
        a, rec = await asyncio.to_thread(ask, manager, transcript)
    except NoReport:
        scene_log.record("hoi_dap_stop", mark="warn")
        await mc.play(manager, [Beat("Em chưa thấy báo cáo FP&A nào em đã làm trong thư mục này. Chị gõ “báo cáo FP&A "
                                     "tháng” trước, rồi hỏi lại.", mark="warn")])
        return ""
    except Exception as exc:                 # noqa: BLE001 - plain words, never a traceback
        log.exception("hoi dap failed")
        scene_log.record("hoi_dap_error", error=type(exc).__name__)
        await mc.play(manager, [Beat("Em chưa trả lời được câu này. Báo cáo của chị không bị thay đổi gì.", mark="bad")])
        return ""
    scene_log.record("hoi_dap", so=rec["so"], trang_thai=rec["trang_thai"], so_lieu=len(rec["so_lieu"]))
    mark = MARK.get(a.status, "warn")
    await mc.play(manager, [Beat(VOUCHERS_RE.sub("", s), mark=mark) for s in (a.parts or [a.text])])
    return ""


def run_file(folder: Path, questions: Path) -> dict:
    """Every question of the file answered into a copy of the report. {"records", "out", "report"}."""
    from engine import hoi_dap
    report, pack = find_report(folder)
    book = hoi_dap.read(report, pack)
    records = hoi_dap.answer_file(book, hoi_dap.read_questions(questions))
    out = hoi_dap.write(book, records)
    for r in records:
        scene_log.record("hoi_dap", so=r["so"], trang_thai=r["trang_thai"], so_lieu=len(r["so_lieu"]))
    scene_log.record("hoi_dap_file", verified=out["verified"], questions=len(records),
                     answered=sum(1 for r in records if r["trang_thai"] == hoi_dap.ANSWERED))
    return {"records": records, "out": out, "report": report}


def beats_for(result: dict) -> list:
    from engine import hoi_dap
    records, out = result["records"], result["out"]
    answered = sum(1 for r in records if r["trang_thai"] == hoi_dap.ANSWERED)
    later = len(records) - answered
    ok = out["verified"]
    beats = [Beat(f"Em đã trả lời {answered}/{len(records)} câu bằng số trong báo cáo"
                  + (", mở lại kiểm tra thấy khớp." if ok else ", nhưng mở lại chưa khớp, chị đừng dùng bản này."),
                  mark="ok" if ok else "bad")]
    if later:
        beats.append(Beat(f"Còn {later} câu báo cáo chưa có số hoặc em chưa hiểu; em ghi lại để chị trả lời.",
                          mark="warn"))
    beats.append(Beat(f"Câu trả lời nằm ở sheet {hoi_dap.SHEET} trong một bản sao của báo cáo, ở thư mục "
                      f"{out['xlsx'].parent.name}. Báo cáo gốc em không sửa gì."))
    return beats


async def run(manager, folder: Path, questions: Path) -> None:
    try:
        result = await asyncio.to_thread(run_file, Path(folder), Path(questions))
    except NoReport:
        scene_log.record("hoi_dap_stop", mark="warn")
        await mc.play(manager, [Beat("Em chưa thấy báo cáo FP&A nào em đã làm trong thư mục này.", mark="warn")])
        return
    except Exception as exc:                 # noqa: BLE001
        log.exception("hoi dap file failed")
        scene_log.record("hoi_dap_error", error=type(exc).__name__)
        await mc.play(manager, [Beat("Em chưa trả lời được file câu hỏi. Báo cáo của chị không bị thay đổi gì.",
                                     mark="bad")])
        return
    await mc.play(manager, beats_for(result))


def args(argv: list) -> Optional[tuple]:
    """(folder, question file) after --hoi-dap and --cau-hoi, when both are given."""
    def after(flag):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 < len(argv):
                return argv[i + 1]
        return None
    folder, questions = after("--hoi-dap"), after("--cau-hoi")
    return (folder, questions) if folder and questions else None


SKILLS = [
    {"name": "Hỏi đáp BOD", "trigger": TRIGGER, "handler": handle,
     "description": "Trả lời câu hỏi của Ban Giám đốc bằng số trong báo cáo FP&A đã lưu, mỗi số kèm ô và nguồn. Câu "
                    "báo cáo chưa có số thì ghi lại, không đoán. Gõ “hỏi: …”, hỏi tiếp “hỏi tiếp: …”."},
]
