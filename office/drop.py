"""The one drop place for the first real month: files or a folder in, the receipt out.

The user drops the two trial balances (and the sales exports) on the drop zone, or a whole folder.
Clicky reads them where they are: nothing is copied or moved. The report workbook is the one
already open in Excel; when none is open and the folder holds exactly one report-like workbook,
Clicky opens that one. Then the month close starts at its first step, the receipt, so nothing
in the workbook changes before the user confirms what was read.

Work mode: "Làm giúp em" (Clicky does the work and stops only to ask) is the default and the
only mode in beta. "Chỉ em làm" (Clicky shows each step for the user to do) is shown as coming
next, never pretended.
"""
from __future__ import annotations

import asyncio
import re
import logging
from pathlib import Path
from typing import Optional

from office import month_close as mc
from office.month_close import Beat
from storage import set_setting, setting

log = logging.getLogger(__name__)

DO_IT, SHOW_ME = "lam_giup", "chi_em"
MODES = {
    DO_IT: ("Làm giúp em", "Em làm hết, chỉ dừng lại hỏi khi có chỗ quan trọng"),
    SHOW_ME: ("Chỉ em làm", "Có ở bản sau: em chỉ từng bước để chị tự làm và hiểu"),
}
AVAILABLE = (DO_IT,)
MODE_KEY = "work_mode"
DROP_TRIGGER = r"^\s*(thả file|tha file|chọn file|chon file|mở ô thả file|mo o tha file)\s*[.!]?\s*$"
_DATA = {".xlsx", ".xlsm", ".xls", ".csv"}


def mode() -> str:
    m = setting(MODE_KEY, DO_IT)
    return m if m in AVAILABLE else DO_IT


def set_mode(m: str) -> str:
    """Store the choice; a mode that is not ready yet stays on the default."""
    m = m if m in AVAILABLE else DO_IT
    set_setting(MODE_KEY, m)
    return m


def source_folder(paths: list) -> tuple:
    """(folder, problem): the folder the dropped things live in, or a plain sentence saying why there is none."""
    items = [Path(p) for p in paths if str(p).strip()]
    if not items:
        return None, "Em chưa nhận được file nào. Chị kéo file vào ô, hoặc bấm vào ô để chọn file nhé."
    folders = [p for p in items if p.is_dir()]
    if folders:
        if len(folders) > 1 or len(items) > 1:
            return None, "Chị thả một thư mục thôi nhé, thư mục có 2 sổ cân đối và các file bán hàng của tháng."
        return folders[0], None
    missing = [p.name for p in items if not p.exists()]
    if missing:
        return None, f"Em không mở được {missing[0]}. File có thể đã bị chuyển chỗ, chị thả lại nhé."
    data = [p for p in items if p.suffix.lower() in _DATA]
    if not data:
        return None, ("Em chỉ đọc được file Excel (.xlsx) và file CSV xuất từ phần mềm kế toán. "
                      "Chị thả 2 sổ cân đối và các file bán hàng nhé.")
    parents = {p.parent.resolve() for p in data}
    if len(parents) > 1:
        return None, ("Các file đang nằm ở nhiều thư mục khác nhau. Chị gom chúng vào một thư mục rồi thả "
                      "cả thư mục vào đây nhé. File gốc của chị không bị thay đổi.")
    return parents.pop(), None


# a monthly report's file name, and the names of the other workbooks a finance folder holds beside it
_REPORT_NAME = re.compile(r"(^| )(bao cao|bc|report|quan tri|bctc|ket qua kinh doanh|kqkd)( |$)")
_OTHER_NAME = re.compile(r"(^| )(pack|bod|trinh bay|slide|ke hoach|aop|aopr|budget|forecast|danh muc|cau hinh|"
                         r"ty gia|cat giam|tham so|nam truoc|gl|so chi tiet|mastersale|ban hang|doanh thu)( |$)")


def report_workbook(folder: Path) -> Optional[Path]:
    """The monthly report workbook in the folder: the one that is not a trial balance, a kept original or Excel's
    lock file. A folder that also holds plans, a BOD pack or catalogues: the one whose name says report
    ('BaoCao_QuanTri_T10') and nothing else. Two such names, or none: Clicky does not guess."""
    from engine.cli import read_rows
    from engine import sources
    from engine.intake import ORIGINAL_TAG
    from engine.tables import _split_camel, key
    tbs = {stem for stem, _, _ in sources.find_trial_balances(folder, read_rows)}
    found = [p for p in sorted(folder.glob("*.xls*"))
             if p.stem not in tbs and ORIGINAL_TAG not in p.stem and not p.name.startswith("~$")]
    if len(found) == 1:
        return found[0]
    named = [p for p in found if _REPORT_NAME.search(key(_split_camel(p.stem)))
             and not _OTHER_NAME.search(key(_split_camel(p.stem)))]
    return named[0] if len(named) == 1 else None


def args(argv: list) -> Optional[str]:
    """The folder after --drop, when given: the rehearsal robot drops a folder the way a person would."""
    if "--drop" in argv:
        i = argv.index("--drop")
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def _same(a: Optional[str], b: Path) -> bool:
    try:
        return bool(a) and Path(a).resolve() == b.resolve()
    except OSError:
        return False


def prepare(bridge, paths: list) -> tuple:
    """(folder, beats): beats is empty when the month close can start on that folder."""
    folder, problem = source_folder(paths)
    if problem:
        return None, [Beat(problem)]
    if _same(bridge.workbook_folder(), folder):
        return folder, []
    report = report_workbook(folder)
    if report is not None and bridge.open_workbook(str(report)):
        return folder, []
    if bridge.workbook_path():
        return folder, []               # the open, saved report reads its figures from the dropped folder
    return None, [Beat("Em đã nhận thư mục " + folder.name + ". Chị mở file báo cáo tháng (file Excel chị hay "
                       "làm) rồi thả lại, em sẽ điền vào file đó. Em chưa sửa gì cả.")]


async def handle(manager, paths: list) -> None:
    """Called when files land on the drop zone."""
    bridge = manager._excel
    folder, beats = await asyncio.to_thread(prepare, bridge, list(paths))
    if beats:
        await mc.play(manager, beats)
        return
    for _ in range(20):                 # a workbook Clicky just opened needs a moment to become the active one
        if await asyncio.to_thread(bridge.workbook_path):
            break
        await asyncio.sleep(0.5)
    await mc.run_from(manager, folder)


async def handle_open(manager, transcript: str) -> str:
    manager.sig_drop_zone.emit()
    return ""


SKILLS = [
    {"name": "Mở ô thả file", "trigger": DROP_TRIGGER, "handler": handle_open,
     "description": "Mở ô thả file để bắt đầu báo cáo tháng với file thật."},
]
