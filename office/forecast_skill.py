"""'Làm forecast gửi HO': the fiscal year's actual months and the months still to come, against the annual plan, from
a folder.

Typed in the chat it works on the folder of the open workbook, or the folder last dropped. Started by the robot with
`Clicky.exe --forecast THU_MUC`. The figures come from engine.forecast (code, Decimal, nothing leaves the PC); the
pack is a new workbook marked SƠ BỘ in a new folder, reopened and checked; the customer's files are never changed and
nothing is sent: the user sends it to head office. One idea per screen.
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

TRIGGER = (r"\b(forecast|fc|le)\b.{0,20}\b(ho|t[ổo]ng\s*c[ôo]ng\s*ty|t[ậa]p\s*[đd]o[àa]n)\b"
           r"|(l[àa]m|c[ậa]p\s*nh[ậa]t)\s*(forecast|d[ựu]\s*b[áa]o|[ưu][ớo]c\s*kqkd)"
           r"|d[ựu]\s*b[áa]o\s*(c[ảa]\s*n[ăa]m|g[ửu]i\s*ho)|[ưu][ớo]c\s*(k[ếe]t\s*qu[ảa]\s*kinh\s*doanh|kqkd)")


def prepare(folder: Path) -> tuple:
    """(Forecast, written); fpa_skill.Stop with one sentence when it must not go on."""
    from engine import forecast, fpa
    from office.fpa_skill import Stop
    try:
        f = forecast.compute(folder)
    except fpa.PickVersion as exc:
        raise Stop(Beat("Thư mục có nhiều bản kế hoạch hoặc dự báo mà không rõ bản nào đã duyệt: "
                        + ", ".join(exc.versions) + ". Chị cho em biết dùng bản nào.", mark="warn"))
    except ValueError as exc:
        raise Stop(Beat("Em chưa thấy sổ chi tiết chi phí hay sổ bán hàng nào có tháng trong thư mục này.", mark="warn")) \
            from exc
    out = forecast.write(f)
    scene_log.record("forecast", verified=out["verified"], cells=out["cells"], lines=len(f.rows), done=len(f.done),
                     rest=len(f.rest), fallback=len(f.fallback), missing=len(f.missing),
                     basis_is_plan=f.basis is f.target)
    return f, out


def beats_for(f, out: dict) -> list:
    from engine.fpa import _month_text
    from engine.monthly_report import fmt
    month = _month_text(f.period)
    ok = out["verified"]
    top = f.row("op") or f.row("gp") or f.row("opex")
    full = top.full(f.rest) if top else None
    beats = [Beat(f"Em đã làm dự báo cả năm, cập nhật đến tháng {month}"
                  + (", mở lại kiểm tra thấy khớp." if ok else ", nhưng mở lại chưa khớp, chị đừng dùng bản này."),
                  mark="ok" if ok else "bad", label=f"{top.name} cả năm ước" if top else "Dự báo",
                  value=f"{fmt(full)} đ" if top else "", note=f"Kế hoạch năm {fmt(top.plan_full)} đ" if top else "")]
    if f.rest:
        if f.basis is not None and f.basis is not f.target:
            beats.append(Beat(f"Các tháng còn lại em lấy theo {f.basis.said()}, so với kế hoạch năm."))
        else:
            beats.append(Beat("Thư mục chưa có bản dự báo, nên các tháng còn lại em lấy theo kế hoạch năm.",
                              mark="warn"))
    if f.fallback or f.missing:
        beats.append(Beat(f"Có {len(f.fallback) + len(f.missing)} tháng bản dự báo thiếu số; em ghi từng tháng trong "
                          "Phiếu nhận file.", mark="warn"))
    if f.month.tb is None:
        beats.append(Beat("Thư mục chưa có bảng cân đối số phát sinh, nên số thực hiện lấy từ các sổ chi tiết, chưa "
                          "khớp với sổ.", mark="warn"))
    beats.append(Beat(f"Bản dự báo là bản SƠ BỘ, nằm trong thư mục {out['xlsx'].parent.name}. Em không gửi đi đâu, "
                      "chị xem rồi tự gửi cho tổng công ty. File của chị em không sửa gì."))
    return beats


async def run(manager, folder: Optional[Path] = None) -> None:
    from office.bod_skill import find_folder
    from office.fpa_skill import Stop
    folder = find_folder(getattr(manager, "_excel", None), folder)
    if folder is None or not folder.is_dir():
        await mc.play(manager, [Beat("Chị mở một file trong thư mục có số tháng này, hoặc thả thư mục đó vào ô thả "
                                     "file, rồi gõ lại: làm forecast gửi HO.")])
        return
    scene_log.record("forecast_start", folder=scene_log.book_id(folder))     # an id, never the folder's name
    try:
        f, out = await asyncio.to_thread(prepare, folder)
    except Stop as stop:
        scene_log.record("forecast_stop", mark=stop.beat.mark)
        await mc.play(manager, [stop.beat])
        return
    except Exception as exc:                 # noqa: BLE001 - plain words, never a traceback
        log.exception("forecast failed")
        scene_log.record("forecast_error", error=type(exc).__name__)
        await mc.play(manager, [Beat("Em chưa làm được dự báo. File của chị chưa bị thay đổi gì. Chị bấm Báo lỗi để "
                                     "em gửi nhật ký đã che số.", mark="bad")])
        return
    await mc.play(manager, beats_for(f, out))


async def handle(manager, transcript: str) -> str:
    await run(manager, None)
    return ""


def args(argv: list) -> Optional[str]:
    """The folder after --forecast, when given."""
    if "--forecast" in argv:
        i = argv.index("--forecast")
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


SKILLS = [
    {"name": "Dự báo cả năm gửi HO", "trigger": TRIGGER, "handler": handle,
     "description": "Thực hiện các tháng đã chốt (theo sổ) cộng dự báo các tháng còn lại (bản dự báo mới nhất đã "
                    "duyệt, không có thì kế hoạch năm), so với kế hoạch năm. Bản SƠ BỘ, file mới, không gửi đi."},
]
