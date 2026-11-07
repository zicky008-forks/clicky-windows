"""'Soát bộ BOD': check a draft board pack before it is presented, one mistake per screen.

Typed in the chat ("soát bộ BOD", "kiểm tra bộ BOD trước khi trình") it works on the folder of the open
workbook, or on the folder last dropped. Started by the robot with `Clicky.exe --bod-review THU_MUC`.

The numbers come from engine.bod_check (code, Decimal, nothing leaves the PC). Clicky opens the pack in
Excel without saving it, lights each wrong cell with one card (the right figure big, the printed one struck
through), then says where the results were written. The pack itself is never changed.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

import scene_log
from engine.log_safe import scrub
from office import month_close as mc
from office.month_close import Beat

log = logging.getLogger(__name__)

TRIGGER = r"(so[áa]t|ki[ểe]m\s*tra|r[àa]\s*so[áa]t)\b.{0,20}(b[ộo]\s*)?(bod|pack|b[áa]o\s*c[áa]o\s*h[đd]qt|b[ộo]\s*tr[ìi]nh)"
OUT_DIR = "Clicky_soat_BOD"
PCT_CODES = {"wrong_base", "missing_pct", "missing_pp", "wrong_pp"}
SHORT = {1: "Tổng chi phí lệch sổ", 2: "Sai mẫu số %DTT", 3: "Thiếu hoặc sai điểm %", 4: "Sai phiên bản so sánh",
         5: "Tháng khoá còn số kế hoạch", 6: "Cắt giảm 3 bước", 7: "Sai loại tỷ giá"}
# A card's title names the kind of mistake: a missing %DTT is not a wrong percentage point (grading round 8).
TITLES = {"total_vs_detail": "Tổng chi phí lệch sổ", "wrong_base": "Sai mẫu số %DTT", "no_base": "Thiếu doanh thu để tính %DTT",
          "missing_pct": "Thiếu %DTT", "missing_diff": "Thiếu chênh lệch bằng tiền", "missing_pp_column": "Thiếu cột điểm %",
          "missing_pp": "Thiếu điểm %", "wrong_pp": "Sai điểm %", "wrong_version": "Sai phiên bản so sánh",
          "wrong_scenario": "Sai kịch bản so sánh", "wrong_version_value": "Số không đúng phiên bản",
          "locked_month_plan": "Tháng khoá còn số kế hoạch", "locked_month_plan_file": "File AOPR: tháng khoá còn số kế hoạch",
          "no_cut_touched": "Cắt vào khoản không được cắt", "levers_vs_gap": "Cắt giảm không khớp mức cần cắt",
          "fx_rate_type": "Sai loại tỷ giá"}


def _figure(x, code: str) -> str:
    from engine.bod_review import fmt_pct, fmt_vnd
    if x is None:
        return "trống"
    return fmt_pct(x) if code in PCT_CODES else f"{fmt_vnd(x)} đ"


def _sentence(f) -> str:
    """One short sentence for the chat and the voice: the first clause of the finding."""
    text = f.message.split(". ")[0]
    return text if len(text) <= 160 else text[:157].rsplit(" ", 1)[0] + "…"


def title_of(f) -> str:
    return TITLES.get(f.code) or SHORT.get(f.group, "Cần sửa")


def beats_for(reviews: list, out: Optional[dict]) -> list:
    beats = []
    total = sum(len(r.findings) for r in reviews)
    if not reviews:
        return [Beat("Em không thấy bộ BOD nào trong thư mục này. Tên file bộ BOD thường có chữ BOD, pack hoặc bản nháp.")]
    r0 = reviews[0]
    period = f"tháng {r0.period[5:]}/{r0.period[:4]}" if r0.period else ""
    gaps = [(g, title) for r in reviews for g, title, state, text in r.groups()
            if state in ("chua", "mot_phan") or "CHƯA KIỂM ĐƯỢC" in text]
    if not total and not gaps:
        beats.append(Beat(f"Em đã soát bộ BOD {period}: không thấy lỗi nào trong 7 nhóm kiểm.", mark="ok"))
    elif not total:
        beats.append(Beat(f"Em đã soát bộ BOD {period}: chưa thấy lỗi ở phần em kiểm được, nhưng còn phần chưa kiểm được.",
                          mark="warn"))
    else:
        beats.append(Beat(f"Em đã soát bộ BOD {period}: có {total} chỗ cần sửa trước khi trình. Em chỉ từng chỗ."))
    for r in reviews:
        for f in r.findings:
            book = getattr(f, "file", "") or r.pack
            other = book != r.pack
            beats.append(Beat(_sentence(f), ref=f.where, label=title_of(f), mark="bad",
                              value=_figure(f.expected, f.code) if f.expected is not None else "",
                              was=_figure(f.actual, f.code) if f.actual is not None and f.expected is not None else "",
                              # a cell in a source file: the card says which file, sheet and cell
                              note=f"Nhóm {f.group} · file {book}, ô {f.where}" if other else f"Nhóm {f.group}",
                              book=str(Path(r.folder) / book) if r.folder else book, book_pack=not other))
        if r.notes:
            beats.append(Beat(f"Sổ chi tiết còn {len(r.notes)} điểm cần hỏi kế toán, không phải lỗi của bộ BOD. "
                              "Em ghi trong file kết quả.", mark="warn"))
    if gaps:
        names = ", ".join(f"nhóm {g} ({SHORT.get(g, title).lower()})" for g, title in gaps)
        beats.append(Beat(f"Em chưa kiểm được {names}. Em ghi lý do và file cần thêm trong file kết quả; "
                          "chưa nên trình khi còn phần này.", mark="warn"))
    if out:
        ok = out.get("verified")
        beats.append(Beat("Em đã ghi kết quả và biên bản vào thư mục " + OUT_DIR
                          + (", mở lại kiểm tra thấy khớp." if ok else ", nhưng mở lại chưa khớp, chị đừng dùng bản này.")
                          + " Bộ BOD của chị em không sửa gì.", mark="ok" if ok else "bad"))
    return beats


def find_folder(bridge, folder: Optional[Path] = None) -> Optional[Path]:
    if folder:
        return Path(folder)
    if getattr(mc, "_SOURCE", None):
        return Path(mc._SOURCE)
    own = bridge.workbook_folder() if bridge is not None else None
    return Path(own) if own else None


def _open_pack(bridge, path: Path) -> None:
    """Show the pack in Excel so each cell can be lit. Opened as it is; Clicky never saves it."""
    try:
        if bridge.workbook_path() and Path(bridge.workbook_path()) == path:
            return
        bridge.open_workbook(str(path))
    except Exception as exc:                 # noqa: BLE001 - the results are written anyway
        log.warning("cannot open the pack in Excel: %s", scrub(exc))   # never the customer's file name


async def run(manager, folder: Optional[Path] = None) -> None:
    from engine import bod_check
    bridge = getattr(manager, "_excel", None)
    folder = find_folder(bridge, folder)
    if folder is None or not folder.is_dir():
        await mc.play(manager, [Beat("Chị mở một file trong thư mục có bộ BOD, hoặc thả thư mục đó vào ô thả file, "
                                     "rồi gõ lại: soát bộ BOD.")])
        return
    from office.excel_com import book_id
    scene_log.record("bod_start", folder=book_id(str(folder)))   # an id, never the customer's folder name
    try:
        reviews = await asyncio.to_thread(bod_check.review, folder)
        out = await asyncio.to_thread(bod_check.write_outputs, reviews, folder / OUT_DIR) if reviews else None
    except Exception as exc:                 # noqa: BLE001 - say it in plain words, never a traceback
        log.exception("BOD review failed")
        scene_log.record("bod_error", error=str(exc)[:200])
        await mc.play(manager, [Beat("Em chưa soát được bộ BOD này. File của chị chưa bị thay đổi gì. "
                                     "Chị bấm Báo lỗi để em gửi nhật ký đã che số.", mark="bad")])
        return
    beats = beats_for(reviews, out)
    pack = folder / reviews[0].pack if reviews else None
    if bridge is not None and pack is not None:
        await asyncio.to_thread(_open_pack, bridge, pack)
    try:
        await mc.play(manager, beats, keep_card=False)       # separate mistakes: no card outlives its own
    finally:
        if bridge is not None and pack is not None and hasattr(bridge, "close_opened"):
            # source files opened read-only to show a cell are closed unsaved; the pack is shown again
            from office.excel_com import book_id
            for name, closed in await asyncio.to_thread(bridge.close_opened, str(pack)) or []:
                scene_log.record("close_book", book=book_id(name), pack=book_id(name) == book_id(pack),
                                 closed=bool(closed), saved=False, read_only=True)


async def handle(manager, transcript: str) -> str:
    await run(manager, None)
    return ""


def args(argv: list) -> Optional[str]:
    """The folder after --bod-review, when given."""
    if "--bod-review" in argv:
        i = argv.index("--bod-review")
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


SKILLS = [
    {"name": "Soát bộ BOD", "trigger": TRIGGER, "handler": handle,
     "description": "Soát bộ báo cáo BOD bản nháp trước khi trình: tổng chi phí so sổ chi tiết, mẫu số %DTT theo kênh, "
                    "điểm %, phiên bản kế hoạch, AOPR tháng đã khoá, cắt giảm 3 bước, tỷ giá. Chỉ từng ô sai."},
]
