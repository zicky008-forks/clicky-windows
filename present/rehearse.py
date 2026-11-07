"""Rehearse a talk without Excel or PowerPoint: python-pptx reads the deck, openpyxl reads the workbook,
LibreOffice recalculates what-ifs on a temporary copy. For practice data on any PC (and CI); the real
talk on the client's PC uses PowerPointBridge, ComSource and WhatIf over COM.

    python -m present.rehearse --spec DIR --xlsx so_lieu.xlsx --deck deck.pptx [--ask "câu hỏi" ...]

DIR holds kich_ban.yaml, hoi_dap.yaml and kiem_tra_truoc.yaml (the client's files, kept outside this repo).
Output: the spoken lines, the answers and bien_ban_thuyet_trinh.md next to the workbook.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from present import deck as deck_mod
from present import preflight
from present.placeholders import OpenpyxlSource, Resolver
from present.talk import Minutes, Stop, Talk
from present.whatif import WhatIfRun, expand

EMU_PER_PT = 12700


class PptxDeck:
    """Read-only stand-in for PowerPointBridge: shapes in points, a 1280x720-pixel 'screen'."""

    def __init__(self, path: str):
        from pptx import Presentation
        self.prs = Presentation(path)
        self.w = self.prs.slide_width / EMU_PER_PT
        self.h = self.prs.slide_height / EMU_PER_PT

    def count(self) -> int:
        return len(self.prs.slides)

    def goto(self, n: int) -> int:
        if not 1 <= n <= self.count():
            raise IndexError(f"không có slide {n}")
        return n

    def slide_shapes(self, n: int) -> list:
        out = []
        for i, sh in enumerate(self.prs.slides[n - 1].shapes, 1):
            text = sh.text_frame.text if sh.has_text_frame else ""
            if getattr(sh, "has_table", False) and sh.has_table:
                text = " | ".join(c.text for r in sh.table.rows for c in r.cells)
            is_title = bool(sh.is_placeholder and sh.placeholder_format.type in (1, 3))
            chart_type = None
            if getattr(sh, "has_chart", False) and sh.has_chart:
                chart_type = -1
            out.append(deck_mod.ShapeInfo(i, sh.name, sh.left / EMU_PER_PT, sh.top / EMU_PER_PT, sh.width / EMU_PER_PT,
                                          sh.height / EMU_PER_PT, text, is_title, chart_type is not None,
                                          bool(getattr(sh, "has_table", False) and sh.has_table), chart_type))
        return out

    def title(self, n: int) -> str:
        t = deck_mod.find(self.slide_shapes(n), kind="tiêu đề")
        return t.text.strip() if t else ""

    def screen_rect(self, shape) -> tuple:
        return deck_mod.to_screen(shape, self.w, self.h, 0, 0, 960, 540)     # 1280x720 px at 96 dpi


class LibreOfficeWhatIf:
    """Write inputs into a temporary copy, recalculate with LibreOffice, read results. The source file is never touched."""

    def __init__(self, xlsx: str):
        self.xlsx = Path(xlsx)

    def run(self, inputs: dict, outputs: list) -> WhatIfRun:
        import openpyxl
        cells = {one: v for ref, v in inputs.items() for one in expand(ref)}
        run = WhatIfRun(inputs=cells)
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "kb.xlsx"
            shutil.copy(self.xlsx, copy)
            wb = openpyxl.load_workbook(copy)
            for ref, v in cells.items():
                sheet, addr = ref.split("!", 1)
                wb[sheet][addr].value = v
            wb.save(copy)
            out = Path(tmp) / "out"
            subprocess.run(["soffice", "--headless", "--calc", "--convert-to", "xlsx", "--outdir", str(out), str(copy)],
                           check=True, capture_output=True, timeout=120)
            calc = openpyxl.load_workbook(out / "kb.xlsx", data_only=True)
            for ref in outputs:
                sheet, addr = ref.split("!", 1)
                run.results[ref] = calc[sheet][addr].value
        run.restored = True            # the source file was never written
        return run


def recalc(xlsx: str) -> str:
    """openpyxl-written formulas carry no cached values; let LibreOffice compute them once."""
    out = Path(tempfile.mkdtemp())
    subprocess.run(["soffice", "--headless", "--calc", "--convert-to", "xlsx", "--outdir", str(out), xlsx],
                   check=True, capture_output=True, timeout=120)
    return str(out / Path(xlsx).name)


def main(argv=None) -> int:
    import yaml
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", required=True)
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--deck", required=True)
    ap.add_argument("--ask", action="append", default=[])
    ap.add_argument("--slides", default="")
    ap.add_argument("--minutes", default="")
    a = ap.parse_args(argv)
    spec = Path(a.spec)
    load = lambda n: yaml.safe_load((spec / n).read_text(encoding="utf-8")) if (spec / n).exists() else {}
    script, bank, checks = load("kich_ban.yaml"), load("hoi_dap.yaml"), load("kiem_tra_truoc.yaml")
    values = recalc(a.xlsx)
    resolver = Resolver(OpenpyxlSource(values))
    deck = PptxDeck(a.deck)
    minutes = Minutes()
    rep = preflight.run((checks or {}).get("kiem_tra", []), resolver, deck, script, bank, {"synthetic": True})
    minutes.preflight = rep.lines()
    print("\n".join(rep.lines()))
    talk = Talk(script, bank, resolver, deck, say=lambda shown, spoken: print("  NÓI:", shown),
                point=lambda rect, label: print("  CHỈ:", label, rect), wait=lambda s: None,
                whatif=LibreOfficeWhatIf(a.xlsx), minutes=minutes)
    from present.talk import estimate_seconds
    print(f"Ước tính thời lượng: {estimate_seconds(script) / 60:.1f} phút (mục tiêu {(script.get('meta') or {}).get('thoi_luong_muc_tieu_phut', '?')} phút)")
    code = 0 if rep.ok else 1
    if rep.ok:
        try:
            only = [int(x) for x in a.slides.split(",") if x.strip()] or None
            talk.present(only)
        except Stop as exc:
            print("DỪNG:", exc)
            code = 2
    for q in a.ask:
        print("HỎI:", q)
        try:
            r = talk.answer(q)
            if r.get("choices") or r.get("ask"):
                print("  HỎI LẠI:", r.get("ask", "Anh chị hỏi ý nào ạ?"), r.get("choices"))
        except Stop as exc:
            print("  DỪNG:", exc)
            code = 2
    target = Path(a.minutes) if a.minutes else Path(a.xlsx).with_name("bien_ban_thuyet_trinh.md")
    target.write_text(minutes.markdown(), encoding="utf-8")
    print("Biên bản:", target)
    return code


if __name__ == "__main__":
    sys.exit(main())
