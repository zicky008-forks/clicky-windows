"""'Tách chi phí cho các phòng': the month's operating costs, one workbook per department, and an Outlook draft
for each. Then 'xem phản hồi chi phí': read the workbooks the departments sent back.

Typed in the chat it works on the folder of the open workbook, or the folder last dropped. Started by the robot with
`Clicky.exe --opex-split THU_MUC` (and `--opex-replies THU_MUC`).

The figures come from engine.fpa and engine.opex_split (code, Decimal). When the folder has a trial balance the
costs are matched to it first, and nothing is split while they do not match: a department should never be asked to
check a figure that is about to change. Drafts are saved, never sent (office.outlook_drafts); an Outlook draft
syncs to the company mail server like any email the user writes, and CLICKY_OUTLOOK=0 keeps them as .eml files. The tracker of who
answered is a local file (engine.opex_feedback). The customer's files are never changed: everything is written to a
new folder.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

import scene_log
from office import month_close as mc
from office.month_close import Beat

log = logging.getLogger(__name__)

SPLIT_TRIGGER = (r"(t[áa]ch|chia|g[ửu]i|ph[âa]n\s*b[ổo]|breakdown)\b.{0,30}chi\s*ph[íi].{0,40}"
                 r"(ph[òo]ng\s*ban|c[áa]c\s*ph[òo]ng|t[ừu]ng\s*ph[òo]ng|b[ộo]\s*ph[ậa]n)")
REPLY_TRIGGER = (r"(ph[ảa]n\s*h[ồo]i|tr[ảa]\s*l[ờơo]i).{0,30}chi\s*ph[íi]"
                 r"|chi\s*ph[íi].{0,40}(ph[ảa]n\s*h[ồo]i|tr[ảa]\s*l[ờơo]i)")
OUT_PREFIX = "Clicky_chi_phi_phong_ban"


def _fy_start(config, default: int = 7) -> int:
    from engine.bod_inputs import num
    fy = num(config.rule("thang bat dau")) or num(config.rule("nien do"))
    return int(fy) if fy is not None and 1 <= int(fy) <= 12 else default


def _trial_balance(folder: Path, unused: list):
    """The trial balance in the folder, if one is there (a file the cost reader left for the month close)."""
    from engine.bod_inputs import hint_of
    from engine.tables import sheets_of
    from engine.trial_balance import read_trial_balance
    for name, _why in unused:
        if hint_of(Path(name).stem) != "tb":
            continue
        try:
            sheets = sheets_of(folder / name)
            sheet, rows = max(sheets.items(), key=lambda kv: len(kv[1]))
            tb = read_trial_balance(rows, sheet)
            if tb.lines:
                return tb
        except Exception:                    # noqa: BLE001 - not a trial balance after all
            continue
    return None


def out_folder(folder: Path, period: str) -> Path:
    """A new folder for this run; an earlier run's folder is never written into."""
    base = folder / f"{OUT_PREFIX}_T{period[5:7]}-{period[:4]}"
    path, n = base, 1
    while path.exists():
        n += 1
        path = base.with_name(f"{base.name} ({n})")
    return path


class Stop(Exception):
    """A reason not to split, said to the user as one beat."""

    def __init__(self, beat: Beat):
        self.beat = beat
        super().__init__(beat.text)


def prepare(folder: Path, outlook=None) -> dict:
    """Everything the split does, without the screen: read, match, split, write, reopen, save drafts, track.
    Returns what the beats need. Raises Stop with the one sentence when it must not go on."""
    from engine import bod_inputs, fpa, opex_feedback, opex_split
    from office import outlook_drafts
    b = bod_inputs.load(folder)
    fy = _fy_start(b.config)
    if fy != 7:
        b = bod_inputs.load(folder, fy_start=fy)
    if not b.gl:
        raise Stop(Beat("Em không thấy sổ chi tiết chi phí nào trong thư mục này, nên chưa tách được cho các phòng.",
                        mark="warn"))
    try:
        v = fpa.compute(b.gl, b.plans, fy_start=fy, config=b.config)
    except fpa.PickVersion as exc:
        raise Stop(Beat("Thư mục có nhiều bản kế hoạch mà không bản nào ghi đã duyệt: "
                        + ", ".join(exc.versions) + ". Chị cho em biết so với bản nào.", mark="warn"))
    tb = _trial_balance(Path(folder), b.unused)
    matched = None
    if tb is not None:
        matched = fpa.match_books(b.gl, tb, v.period)
        if not matched.ok:
            raise Stop(Beat(matched.sentence() + " Em chưa tách cho các phòng, để các phòng không phải kiểm số "
                            "sắp đổi.", mark="bad"))
    packs = opex_split.split(v, b.gl, b.config)
    out = out_folder(Path(folder), v.period)
    paths = [opex_split.write_pack(p, v, out) for p in packs]     # each reopened and checked before it counts
    sender = str(b.config.rule("nguoi gui", default="") or "")
    reply_by = str(b.config.rule("han phan hoi", default="") or "")
    drafts = [opex_split.draft(p, v, path, reply_by=reply_by, sender=sender) for p, path in zip(packs, paths)]
    saved = outlook_drafts.save_all(drafts, out, outlook=outlook)
    for p, path, s in zip(packs, paths, saved):
        opex_feedback.record_sent(v.period, p, path, s.how)
    scene_log.record("opex_split", packs=len(packs), unassigned=sum(1 for p in packs if not p.dept),
                     no_address=len(opex_split.no_address(packs)), matched=None if matched is None else matched.ok,
                     outlook=sum(s.how == "outlook" for s in saved), eml=sum(s.how == "eml" for s in saved))
    return {"v": v, "packs": packs, "paths": paths, "saved": saved, "out": out, "matched": matched,
            "left_out": v.left_out()}


def beats_for(r: dict) -> list:
    from engine.fpa import _month_text
    from engine.opex_split import no_address
    v, packs, saved, matched = r["v"], r["packs"], r["saved"], r["matched"]
    month = _month_text(v.period)
    depts = [p for p in packs if p.dept]
    beats = [Beat(f"Em đã tách chi phí tháng {month} cho {len(depts)} phòng, mỗi phòng một file, mở lại kiểm tra "
                  "thấy khớp.", mark="ok", label="Chi phí theo phòng", value=f"{len(depts)} phòng",
                  note="Bản SƠ BỘ")]
    if matched is not None:
        beats.append(Beat(matched.sentence(), mark="ok"))
    else:
        beats.append(Beat("Thư mục chưa có bảng cân đối tài khoản nên em chưa khớp được với sổ. Các file vẫn là bản "
                          "SƠ BỘ.", mark="warn"))
    unassigned = next((p for p in packs if not p.dept), None)
    if unassigned is not None:
        beats.append(Beat(f"Có {len(unassigned.details)} dòng chưa gán bộ phận, em để riêng một file gửi kế toán gán "
                          "giúp.", mark="warn"))
    outlook = sum(s.how == "outlook" for s in saved)
    eml = sum(s.how == "eml" for s in saved)
    failed = sum(not s.how for s in saved)
    if outlook:
        beats.append(Beat(f"Em đã lưu {outlook} thư nháp trong Outlook, mục Thư nháp. Em không gửi thư nào: chị mở "
                          "từng thư, xem lại rồi bấm Gửi.", mark="ok"))
    if eml:
        beats.append(Beat(f"Em lưu {eml} thư nháp thành file trong thư mục kết quả. Chị mở từng file, xem lại rồi bấm "
                          "Gửi. Em không gửi thư nào.", mark="ok"))
    if failed:
        beats.append(Beat(f"Còn {failed} thư em chưa lưu được nháp; file chi phí của các phòng đó vẫn có trong thư mục "
                          "kết quả.", mark="warn"))
    missing = no_address(packs)
    if missing:
        beats.append(Beat("Danh mục chưa có email của " + ", ".join(missing) + ", nên thư nháp chưa có người nhận. "
                          "Chị điền người nhận trước khi gửi.", mark="warn"))
    for said in r["left_out"]:
        beats.append(Beat(said + ".", mark="warn"))
    beats.append(Beat(f"Khi các phòng trả lời kèm file, chị lưu file vào thư mục này rồi gõ: xem phản hồi chi phí. "
                      f"File của chị em không sửa gì; kết quả nằm trong thư mục {r['out'].name}."))
    return beats


def find_folder(bridge, folder: Optional[Path] = None) -> Optional[Path]:
    from office.bod_skill import find_folder as find
    return find(bridge, folder)


async def run(manager, folder: Optional[Path] = None) -> None:
    bridge = getattr(manager, "_excel", None)
    folder = find_folder(bridge, folder)
    if folder is None or not folder.is_dir():
        await mc.play(manager, [Beat("Chị mở một file trong thư mục có sổ chi tiết chi phí, hoặc thả thư mục đó vào ô "
                                     "thả file, rồi gõ lại: tách chi phí cho các phòng.")])
        return
    scene_log.record("opex_start", folder=scene_log.book_id(folder))   # an id, never the folder's name
    try:
        r = await asyncio.to_thread(prepare, folder)
    except Stop as stop:
        scene_log.record("opex_stop", mark=stop.beat.mark)
        await mc.play(manager, [stop.beat])
        return
    except Exception as exc:                 # noqa: BLE001 - plain words, never a traceback
        log.exception("opex split failed")
        scene_log.record("opex_error", error=type(exc).__name__)
        await mc.play(manager, [Beat("Em chưa tách được chi phí cho các phòng. File của chị chưa bị thay đổi gì. "
                                     "Chị bấm Báo lỗi để em gửi nhật ký đã che số.", mark="bad")])
        return
    await mc.play(manager, beats_for(r))


# ── The answers ─────────────────────────────────────────────────────────────

def replies(folder: Path) -> list:
    """Every returned workbook in folder (not in Clicky's own result folders), read and recorded."""
    from engine import opex_feedback
    out = []
    for p in sorted(Path(folder).iterdir()):
        if not p.is_file() or p.suffix.lower() not in (".xlsx", ".xlsm") or p.name.startswith("~$"):
            continue
        try:
            rp = opex_feedback.read_reply(p)
        except Exception:                    # noqa: BLE001 - not one of ours, or unreadable: not an answer
            continue
        if rp is not None:
            opex_feedback.record_reply(rp)
            out.append(rp)
    return out


def reply_beats(found: list, names: dict) -> list:
    from engine import opex_feedback
    if not found:
        return [Beat("Em chưa thấy file phản hồi nào trong thư mục này. Chị lưu file các phòng gửi lại vào đây rồi gõ "
                     "lại: xem phản hồi chi phí.", mark="warn")]
    period = found[0].period
    beats = [Beat(f"Em đã đọc {len(found)} file phản hồi: " + opex_feedback.sentence(period),
                  mark="ok" if all(r.done for r in found) else "warn")]
    for r in found:
        name = names.get(r.dept) or r.dept or "kế toán"
        if r.changed:
            beats.append(Beat(f"{name} đã sửa {len(r.changed)} số trong file. Em không lấy số sửa tay; chị hỏi lại "
                              "phòng này.", mark="bad"))
        c = r.counts
        asks = [f"{c[k]} dòng {k}" for k in ("Sai", "Cần hỏi", "khác") if c[k]]
        if asks:
            beats.append(Beat(f"{name}: " + ", ".join(asks) + ". Em ghi lại để chị chuyển kế toán.", mark="warn"))
        elif c[opex_feedback.BLANK] and not r.changed:
            beats.append(Beat(f"{name} còn {c[opex_feedback.BLANK]} dòng chưa chọn phản hồi.", mark="warn"))
    return beats


async def run_replies(manager, folder: Optional[Path] = None) -> None:
    from engine import opex_feedback
    bridge = getattr(manager, "_excel", None)
    folder = find_folder(bridge, folder)
    if folder is None or not folder.is_dir():
        await mc.play(manager, [Beat("Chị mở một file trong thư mục có các file phản hồi, rồi gõ lại: xem phản hồi "
                                     "chi phí.")])
        return
    try:
        found = await asyncio.to_thread(replies, folder)
    except Exception as exc:                 # noqa: BLE001
        log.exception("reading opex replies failed")
        scene_log.record("opex_replies_error", error=type(exc).__name__)
        await mc.play(manager, [Beat("Em chưa đọc được các file phản hồi. File chưa bị thay đổi gì.", mark="bad")])
        return
    names = {e.get("dept", ""): e.get("name", "") for e in opex_feedback.events() if e.get("kind") == "sent"}
    scene_log.record("opex_replies", files=len(found), done=sum(r.done for r in found),
                     changed=sum(len(r.changed) for r in found))
    await mc.play(manager, reply_beats(found, names))


async def handle(manager, transcript: str) -> str:
    await run(manager, None)
    return ""


async def handle_replies(manager, transcript: str) -> str:
    await run_replies(manager, None)
    return ""


def _flag(argv: list, flag: str) -> Optional[str]:
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def args(argv: list) -> Optional[str]:
    """The folder after --opex-split, when given."""
    return _flag(argv, "--opex-split")


def reply_args(argv: list) -> Optional[str]:
    """The folder after --opex-replies, when given."""
    return _flag(argv, "--opex-replies")


SKILLS = [
    {"name": "Xem phản hồi chi phí", "trigger": REPLY_TRIGGER, "handler": handle_replies,
     "description": "Đọc các file chi phí các phòng gửi lại: dòng Đúng, Sai, Cần hỏi, số bị sửa tay, phòng chưa trả lời."},
    {"name": "Tách chi phí cho các phòng", "trigger": SPLIT_TRIGGER, "handler": handle,
     "description": "Tách chi phí hoạt động tháng theo phòng ban, mỗi phòng một file để kiểm tra, và lưu thư nháp "
                    "Outlook cho từng phòng (không tự gửi)."},
]
