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


class Stop(Exception):
    def __init__(self, beat: Beat):
        self.beat = beat
        super().__init__(beat.text)


def prepare(folder: Path) -> tuple:
    """(Month, written) for the folder; Stop with one sentence when it must not go on."""
    from engine import fpa, fpa_report
    try:
        m = fpa_report.compute(folder)
    except fpa.PickVersion as exc:
        raise Stop(Beat("Thư mục có nhiều bản kế hoạch mà không bản nào ghi đã duyệt: " + ", ".join(exc.versions)
                        + ". Chị cho em biết so với bản nào.", mark="warn"))
    except ValueError as exc:
        raise Stop(Beat("Em chưa thấy sổ chi tiết chi phí hay sổ bán hàng nào có tháng trong thư mục này.", mark="warn")) \
            from exc
    out = fpa_report.write(m)
    from engine import variance_class
    _r, _u, alarms = variance_class.summary(m.classified)
    scene_log.record("fpa_report", verified=out["verified"], cells=out["cells"], lines=len(m.pnl),
                     books=m.tb is not None, alarms=len(alarms))
    return m, out


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
                  value=f"{fmt(op.actual)} đ" if op else "", note=f"Kế hoạch {fmt(op.plan)} đ" if op else "")]
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


async def run(manager, folder: Optional[Path] = None) -> None:
    from office.bod_skill import find_folder
    folder = find_folder(getattr(manager, "_excel", None), folder)
    if folder is None or not folder.is_dir():
        await mc.play(manager, [Beat("Chị mở một file trong thư mục có số tháng này, hoặc thả thư mục đó vào ô thả "
                                     "file, rồi gõ lại: làm báo cáo FP&A tháng.")])
        return
    scene_log.record("fpa_start", folder=scene_log.book_id(folder))     # an id, never the folder's name
    try:
        m, out = await asyncio.to_thread(prepare, folder)
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
    await mc.play(manager, beats_for(m, out))


async def handle(manager, transcript: str) -> str:
    await run(manager, None)
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
