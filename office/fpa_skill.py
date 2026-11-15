"""'Làm báo cáo FP&A tháng': the month's P&L and operating costs against the approved plan, from a folder.

Typed in the chat it works on the folder of the open workbook, or the folder last dropped. Started by the robot with
`Clicky.exe --fpa-report THU_MUC`. The figures come from engine.fpa_report (code, Decimal, nothing leaves the PC);
the report is a new workbook marked SƠ BỘ in a new folder, reopened and checked; the customer's files are never
changed. One idea per screen: the result, the books, what is worth a question, what was left out.
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

TRIGGER = (r"b[áa]o\s*c[áa]o\s*(fp\s*&?\s*a|th[ựu]c\s*hi[ệe]n\s*so\s*(v[ớo]i\s*)?k[ếe]\s*ho[ạa]ch)"
           r"|\bfp\s*&?\s*a\b.{0,20}th[áa]ng|so\s*v[ớo]i\s*(aop|k[ếe]\s*ho[ạa]ch\s*n[ăa]m)")
ALARMS_SHOWN = 3
CONFIRM_TITLE = "Em hiểu thế này, đúng chưa?"
CONFIRM_YES = "Đúng rồi, làm báo cáo FP&A tháng"
CONFIRM_FIX = "Sửa trước khi làm báo cáo FP&A"
FIX_HINT = ("Chị gõ điều cần sửa kèm câu lệnh, ví dụ: làm báo cáo FP&A tháng so với AOPR. Em đọc lại thư mục và "
            "hỏi chị lần nữa trước khi làm.")
_ASKED: dict = {}                      # folder -> (files' signature, the user's words when asked)


class Stop(Exception):
    def __init__(self, beat: Beat):
        self.beat = beat
        super().__init__(beat.text)


def picked(said: str, choices: list) -> tuple:
    """(version, file) the user named in the chat among the folder's plans ('so với AOP v4', or the file's name);
    (None, None) when the words name none or more than one. The longest version named wins ('AOP v4' over 'AOP')."""
    from engine.tables import key
    words = f" {key(said)} "
    hits = [(v, f) for v, f in choices if f" {key(v)} " in words]
    if hits:
        best = max(len(key(v)) for v, _f in hits)
        hits = [(v, f) for v, f in hits if len(key(v)) == best]
    if len(hits) > 1:                                     # one name in two files: the file's name tells them apart
        by_file = [(v, f) for v, f in hits if key(Path(f).stem) in key(said)]
        hits = by_file if len(by_file) == 1 else hits
    return hits[0] if len(hits) == 1 else (None, None)


def prepare(folder: Path, said: str = "") -> tuple:
    """(Month, written) for the folder; Stop with one sentence when it must not go on. said is the user's words:
    after Clicky asked which plan, the answer names it and the report goes on with that plan."""
    from engine import fpa, fpa_report
    try:
        m = fpa_report.compute(folder)
    except fpa.PickVersion as exc:
        version, file = picked(said, exc.choices)
        if version is None:
            raise Stop(Beat("Thư mục có nhiều bản kế hoạch mà không bản nào ghi đã duyệt: " + ", ".join(exc.versions)
                            + f". Chị gõ lại kèm tên bản, ví dụ: làm báo cáo FP&A tháng so với {exc.versions[-1]}.",
                            mark="warn"))
        m = fpa_report.compute(folder, version=version, plan_file=file)
    except ValueError as exc:
        raise Stop(Beat("Em chưa thấy sổ chi tiết chi phí hay sổ bán hàng nào có tháng trong thư mục này.", mark="warn")) \
            from exc
    out = fpa_report.write(m)
    from engine import variance_class
    _r, _u, alarms = variance_class.summary(m.classified)
    scene_log.record("fpa_report", verified=out["verified"], cells=out["cells"], lines=len(m.pnl),
                     books=m.tb is not None, alarms=len(alarms))
    return m, out


def signature(folder: Path) -> tuple:
    """The folder's files as they stand (name, size, time), so a confirmation never covers files changed since."""
    out = []
    for p in sorted(Path(folder).iterdir()):
        if p.is_file():
            st = p.stat()
            out.append((p.name, st.st_size, int(st.st_mtime)))
    return tuple(out)


def confirm_beat(m) -> Beat:
    """One screen, read in under a minute: what Clicky understood, and two buttons."""
    from engine import fpa_report
    lines = fpa_report.understood(m)
    return Beat(CONFIRM_TITLE + " " + " ".join(lines), label=CONFIRM_TITLE, choices=(CONFIRM_YES, CONFIRM_FIX))


def _books(m) -> Beat:
    if m.tb is None:
        return Beat("Thư mục chưa có bảng cân đối số phát sinh, nên số trên báo cáo lấy từ các sổ chi tiết, chưa khớp "
                    "với sổ.", mark="warn")
    bad = []
    if m.sales_books is not None and not m.sales_books.ok:
        bad.append("doanh thu hoặc giá vốn")
    if m.cost_books is not None and not m.cost_books.ok:
        bad.append("chi phí")
    if bad:
        return Beat(" và ".join(bad).capitalize() + " tháng này chưa khớp bảng cân đối. Em ghi chỗ lệch trong Phiếu "
                    "nhận file; chị đừng trình bản này khi chưa rõ.", mark="bad")
    if m.sales is None:
        return Beat("Số tháng trên báo cáo lấy từ bảng cân đối số phát sinh. Thư mục chưa có sổ bán hàng, nên doanh "
                    "thu và giá vốn chưa chia theo mã hàng.", mark="warn")
    return Beat("Số tháng trên báo cáo lấy từ bảng cân đối số phát sinh; doanh thu, giá vốn và chi phí đều khớp sổ chi "
                "tiết.", mark="ok")


def beats_for(m, out: dict) -> list:
    from engine.fpa import _month_text
    from engine.monthly_report import fmt
    from engine import fpa_report, variance_class
    month = _month_text(m.period)
    op = m.row("op") or m.row("gp") or m.row("opex")
    ok = out["verified"]
    beats = [Beat(f"Em đã làm báo cáo FP&A tháng {month}"
                  + (", mở lại kiểm tra thấy khớp." if ok else ", nhưng mở lại chưa khớp, chị đừng dùng bản này."),
                  mark="ok" if ok else "bad", label=op.name if op else "Báo cáo FP&A",
                  value=f"{fmt(op.actual)} đ" if op else "",
                  note=(f"Kế hoạch {fmt(op.plan)} đ" if op.has_plan else "Chưa có kế hoạch") if op else "")]
    beats.append(_books(m))
    stopped = [c[0] for c in m.checks if c[2] == fpa_report.FAIL and c[4]]
    if stopped:
        beats.append(Beat(f"Kiểm {', '.join(stopped)} không đạt. Theo cấu hình của chị, em dừng ở đây, chưa làm bước "
                          "sau.", mark="bad"))
    if m.classified:
        material = [c for c in m.classified if c.material]
        _r, _u, alarms = variance_class.summary(m.classified)
        beats.append(Beat(f"Chi phí có {len(material)} dòng chênh trên ngưỡng. Sau khi tách lệch thời điểm, khoản một "
                          f"lần và biến động theo doanh thu, còn {len(alarms)} dòng cần hỏi.",
                          mark="warn" if alarms else "ok"))
        for c in sorted(alarms, key=lambda c: -abs(c.underlying))[:ALARMS_SHOWN]:
            beats.append(Beat(f"{c.line.label}: {c.verdict} {fmt(abs(c.underlying))} đ.", mark="warn",
                              label=c.line.label, value=f"{fmt(c.underlying)} đ", note=c.verdict))
    left = (m.costs.left_out() if m.costs is not None else []) + (m.sales.left_out() if m.sales is not None else [])
    if left:
        beats.append(Beat(f"Có {len(left)} điều em chưa tính vào số hoặc cần chị cho biết; em ghi từng điều trong "
                          "Phiếu nhận file.", mark="warn"))
    beats.append(Beat(f"Báo cáo là bản SƠ BỘ, nằm trong thư mục {out['xlsx'].parent.name}. File của chị em không sửa "
                      "gì."))
    return beats


def compute_only(folder: Path, said: str = ""):
    """The month as prepare() would build it, without writing anything."""
    from engine import fpa, fpa_report
    try:
        return fpa_report.compute(folder)
    except fpa.PickVersion as exc:
        version, file = picked(said, exc.choices)
        if version is None:
            raise
        return fpa_report.compute(folder, version=version, plan_file=file)


async def run(manager, folder: Optional[Path] = None, said: str = "", confirm: bool = False) -> None:
    from office.bod_skill import find_folder
    folder = find_folder(getattr(manager, "_excel", None), folder)
    if folder is None or not folder.is_dir():
        await mc.play(manager, [Beat("Chị mở một file trong thư mục có số tháng này, hoặc thả thư mục đó vào ô thả "
                                     "file, rồi gõ lại: làm báo cáo FP&A tháng.")])
        return
    scene_log.record("fpa_start", folder=scene_log.book_id(folder))     # an id, never the folder's name
    if confirm:
        from engine.tables import key
        words = key(said)
        if key(CONFIRM_FIX) in words:
            _ASKED.pop(str(folder), None)
            await mc.play(manager, [Beat(FIX_HINT)])
            return
        asked = _ASKED.get(str(folder))
        sig = await asyncio.to_thread(signature, folder)
        if key(CONFIRM_YES) in words and asked is not None and asked[0] == sig:
            said = asked[1]                              # the plan the user named when asked still holds
            _ASKED.pop(str(folder), None)
            scene_log.record("fpa_confirmed")
        else:
            try:
                m = await asyncio.to_thread(compute_only, folder, said)
            except Exception:                            # noqa: BLE001 - prepare() below says it in plain words
                m = None
            if m is not None:
                _ASKED[str(folder)] = (sig, said)
                beat = confirm_beat(m)
                scene_log.record("fpa_confirm_asked", choices=list(beat.choices))
                await mc.play(manager, [beat])
                return
    try:
        m, out = await asyncio.to_thread(prepare, folder, said)
    except Stop as stop:
        scene_log.record("fpa_stop", mark=stop.beat.mark)
        await mc.play(manager, [stop.beat])
        return
    except Exception as exc:                 # noqa: BLE001 - plain words, never a traceback
        log.exception("FP&A report failed")
        scene_log.record("fpa_error", error=type(exc).__name__)
        await mc.play(manager, [Beat("Em chưa làm được báo cáo FP&A. File của chị chưa bị thay đổi gì. Chị bấm Báo "
                                     "lỗi để em gửi nhật ký đã che số.", mark="bad")])
        return
    beats = beats_for(m, out)
    try:
        extra = await asyncio.to_thread(learn, m, out)
    except Exception:                                    # noqa: BLE001 - the report stands; memory is a bonus
        log.exception("company memory step failed")
        extra = None
    if extra is not None:
        beats.insert(len(beats) - 1, extra)
    await mc.play(manager, beats)


def learn(m, out: dict, path: Optional[str] = None) -> Optional[Beat]:
    """Company memory after the report: the bosses' usual questions (SEP_HAY_HOI.md) tried on the saved report,
    the month's progress written to TIEN_BO.csv, and lessons proposed unticked in BAI_HOC.md for the owner to approve.
    Only when the memory folder exists already (the owner set it up); nothing is sent anywhere."""
    from present import tri_nho
    from engine import hoi_dap
    if not tri_nho.folder(path).exists():
        return None
    mem = tri_nho.load(path)
    period = f"{m.period[5:7]}/{m.period[:4]}"
    usual = [(boss, q) for boss, qs in mem.bosses.items() for q in qs]
    unanswered = []
    if usual:
        book = hoi_dap.read(out["xlsx"])
        for boss, q in usual:
            if hoi_dap.answer(book, q).status != hoi_dap.ANSWERED:
                unanswered.append((boss, q))
    tri_nho.propose_lessons(period, unanswered, path)
    said = tri_nho.record_progress(period, len(usual), len(usual) - len(unanswered), path)["said"]
    scene_log.record("tri_nho_progress", asked=len(usual), answered=len(usual) - len(unanswered))
    return Beat(said, mark="ok" if not unanswered else "warn")


async def handle(manager, transcript: str) -> str:
    await run(manager, None, transcript or "", confirm=True)
    return ""


def args(argv: list) -> Optional[str]:
    """The folder after --fpa-report, when given."""
    if "--fpa-report" in argv:
        i = argv.index("--fpa-report")
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


SKILLS = [
    {"name": "Báo cáo FP&A tháng", "trigger": TRIGGER, "handler": handle,
     "description": "P&L tháng và lũy kế so với kế hoạch đã duyệt (số tháng từ bảng cân đối), chi phí theo bộ phận "
                    "có phân loại chênh lệch, cầu nối lợi nhuận gộp, phiếu nhận file. Bản SƠ BỘ, file mới."},
]
