"""The sample file first: "Xem em làm" runs Case 01 (made-up company Sông Hồng) end to end by itself.

The sample is copied from the app's own files into the user's Clicky folder (the bundled copy stays
untouched), opened in Excel, and the whole month close plays without a click: receipt, fix, report,
save and re-read. The closing card states the real time it took and the figures the code found;
nothing on it is typed in. Then "Giờ thử với file của tôi" leads to the user's own files.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import time
from pathlib import Path

from office import month_close as mc
from office.month_close import Beat

log = logging.getLogger(__name__)

SAMPLE = "Mau_SongHong_T10"
OFFER_CHOICES = ("Xem em làm", "Bỏ qua")
DONE_CHOICES = ("Giờ thử với file của tôi", "Xem lại")
SAMPLE_TRIGGER = (r"^\s*(xem em làm|xem em lam|xem lại|xem lai|chạy file mẫu|chay file mau|thử file mẫu|thu file mau)"
                  r"\s*[.!]?\s*$")
OWN_TRIGGER = r"^\s*(giờ thử với file của tôi|gio thu voi file cua toi|thử file của tôi|thu file cua toi)\s*[.!]?\s*$"
OFFER_TRIGGER = r"\b(file mẫu|file mau)\b"
PAUSE_S = 2.5                 # a confirmed button stays on screen this long before it is "pressed"


def bundled() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / "mau" / SAMPLE


def prepare(target_root: Path | None = None) -> Path:
    """Copy the sample folder to a fresh place the user can write to; returns the workbook's path."""
    from storage import data_dir
    root = target_root or (data_dir() / "File mau")
    dest = root / time.strftime("%Y%m%d-%H%M%S")
    shutil.copytree(bundled(), dest)
    return dest / f"{SAMPLE}.xlsx"


def offer() -> list:
    return [Beat("File mẫu này có 1 lỗi giấu. Chị xem em tìm nhé? Chị không cần làm gì, chỉ xem.",
                 choices=OFFER_CHOICES)]


def closing(seconds: float, workbook: Path) -> Beat:
    """The last card: real time, the figures found, and that the original is still there."""
    last = mc._LAST
    original = workbook.with_name(f"{workbook.stem}_ban-goc{workbook.suffix}")
    kept = "bản gốc vẫn còn" if original.exists() else "chưa thấy bản gốc"
    took = f"{int(seconds)} giây" if seconds < 120 else f"{int(seconds // 60)} phút {int(seconds % 60)} giây"
    if last.get("overstated"):
        text = (f"Em tìm ra {mc.spoken(last['overstated'])} hàng trả lại bị ghi sai tài khoản. Lợi nhuận đúng là "
                f"{mc.spoken(last['real'])}, không phải {mc.spoken(last['pat'])}. Em đã sửa, ghi biên bản, {kept}.")
    else:
        text = f"Em đã làm xong file mẫu, ghi biên bản, {kept}."
    return Beat(text, None, f"Xong trong {took}", "ok", "", "Làm tay việc này thường mất 1 đến 2 giờ.",
                choices=DONE_CHOICES)


async def _press(manager) -> None:
    """Leave the question up long enough to read, then take it down as if the first button was pressed."""
    await asyncio.sleep(PAUSE_S)
    manager.sig_turn_started.emit("")          # hides the choice card
    manager.sig_clear_drawings.emit()


async def run(manager) -> str:
    """Play the whole sample with no clicks. Only practice data is ever touched."""
    started = time.monotonic()
    bridge, pq = manager._excel, mc._pq(manager)
    try:
        workbook = await asyncio.to_thread(prepare)
    except OSError as exc:
        log.warning("copying the sample failed: %s", exc)
        await mc.play(manager, [Beat("Em chưa chép được file mẫu vào máy. Chị thử lại sau ít phút nhé.")])
        return ""
    if not await asyncio.to_thread(bridge.open_workbook, str(workbook)):
        await mc.play(manager, [Beat("Em chưa mở được Excel. Chị mở Excel lên rồi bấm Xem em làm lần nữa nhé.")])
        return ""
    for _ in range(20):                         # Excel needs a moment before the new workbook is the active one
        if await asyncio.to_thread(bridge.workbook_path) == str(workbook):
            break
        await asyncio.sleep(0.5)
    mc.use_folder(None)                         # the sample reads its own folder, never a dropped one
    mc._CHOSEN.clear()
    mc._PICKS.clear()
    order = mc.machine_date_order()
    steps = (lambda: mc.start(bridge, pq, order), lambda: mc.confirm(bridge, pq),
             lambda: mc.approve(bridge, pq), lambda: mc.save_and_verify(bridge))
    for step in steps:
        beats = await asyncio.to_thread(step)
        if not beats:
            break
        await mc.play(manager, beats)
        if not (beats[-1].choices and mc.stage()):
            break                               # the run stopped on its own (an error, or nothing left to ask)
        await _press(manager)
    await mc.play(manager, [closing(time.monotonic() - started, workbook)])
    return ""


async def handle_offer(manager, transcript: str) -> str:
    await mc.play(manager, offer())
    return ""


async def handle_run(manager, transcript: str) -> str:
    return await run(manager)


async def handle_own(manager, transcript: str) -> str:
    """After the sample: the one drop place for the user's own files."""
    manager.sig_drop_zone.emit()
    return ""


SKILLS = [
    {"name": "Chạy file mẫu", "trigger": SAMPLE_TRIGGER, "handler": handle_run,
     "description": "Mở file mẫu Sông Hồng (số liệu giả lập) và chạy trọn báo cáo tháng để người dùng xem."},
    {"name": "Thử với file của mình", "trigger": OWN_TRIGGER, "handler": handle_own,
     "description": "Sau file mẫu: hướng dẫn chạy với file thật của người dùng."},
    {"name": "Mời xem file mẫu", "trigger": OFFER_TRIGGER, "handler": handle_offer,
     "description": "Hỏi người dùng có muốn xem Clicky làm file mẫu không."},
]
