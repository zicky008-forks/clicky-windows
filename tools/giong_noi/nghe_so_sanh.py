"""Hear the offline Vietnamese voices on this PC side by side, reading made-up finance lines the way Clicky reads
a report (finance words, breaths, pauses), then keep the one the owner prefers.

    python tools/giong_noi/nghe_so_sanh.py --ra "So sanh giong.html"     # writes the listening page
    python tools/giong_noi/nghe_so_sanh.py --chon vieneu                  # keeps a voice (writes giong_chon.txt)

Only made-up figures are voiced, and nothing leaves the PC: both engines run offline.
The kit's launcher reads giong_chon.txt into CLICKY_GIONG, which audio.tts.vi_offline_tts.preferred_order uses.
"""
from __future__ import annotations

import argparse
import base64
import html
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# made-up lines in the style of a Vietnamese FP&A report to the BOD
LINES = [
    "Tổng chi phí nhân sự AOPR năm 2026 là 186,4 tỷ, tăng 12,6% so với AOP gốc.",
    "Khối Bán hàng tăng mạnh nhất, 14,1 tỷ, do kế hoạch tuyển thêm 48 nhân viên bán hàng kênh MT và HRC+KA.",
    "Headcount bình quân 1.248 người; chi phí nhân sự/DTT ở mức 9,8%, cao hơn FY2025 0,7pp.",
    "Đề xuất của FP&A: giữ OPEX khối hỗ trợ, rà lại KPI tuyển dụng trước khi chốt LE quý 4.",
]
NAMES = {"vieneu": "VieNeu (tự nhiên hơn, chậm hơn khi dựng)", "piper": "Piper (nhanh, giọng đều hơn)"}
CHOICE_FILE = ROOT / "giong_chon.txt"


def voices() -> dict:
    """Every offline engine that can start here: {name: synthesize}."""
    from audio.tts.vi_offline_tts import engine, piper_available, vieneu_available
    out = {}
    for name, ok in (("vieneu", vieneu_available()), ("piper", piper_available())):
        if ok:
            try:
                out[name] = engine(name).synthesize
            except Exception as exc:  # noqa: BLE001 - show which voice could not start
                print(f"Không mở được giọng {name}: {exc}")
    return out


def page(clips: dict, chosen: str = "") -> str:
    """clips: {engine: [(line, wav bytes, seconds to make)]}."""
    rows = []
    for i, line in enumerate(LINES):
        cells = "".join(
            f'<td><audio controls preload="none" src="data:audio/wav;base64,{base64.b64encode(c[i][1]).decode()}">'
            f'</audio><div class="t">dựng mất {c[i][2]:.1f} giây</div></td>' for c in clips.values())
        rows.append(f"<tr><th>{html.escape(line)}</th>{cells}</tr>")
    head = "".join(f"<td><b>{html.escape(NAMES.get(n, n))}</b></td>" for n in clips)
    now = f"<p>Đang dùng: <b>{html.escape(NAMES.get(chosen, chosen))}</b></p>" if chosen else ""
    return f"""<!doctype html><html lang="vi"><meta charset="utf-8"><title>So sánh giọng Clicky</title>
<style>body{{font:15px/1.5 "Segoe UI",sans-serif;margin:24px;color:#1b2430}}table{{border-collapse:collapse}}
th,td{{border:1px solid #d5dbe3;padding:10px;vertical-align:top;text-align:left}}th{{font-weight:400;max-width:420px}}
.t{{color:#6b7684;font-size:12px}}</style>
<h1>Nghe hai giọng đọc cùng một câu</h1>
<p>Số giả lập. Cả hai giọng chạy ngay trên máy này, không gửi gì đi. Nghe xong, quay lại cửa sổ đen và gõ số giọng anh chọn.</p>
{now}<table><tr><td></td>{head}</tr>{''.join(rows)}</table></html>"""


def build(out: Path, engines: dict = None) -> dict:
    from present.clicky_html import voice_lines
    engines = engines if engines is not None else voices()
    if not engines:
        raise SystemExit("Chưa có giọng tiếng Việt offline nào trên máy.")
    clips = {}
    for name, say in engines.items():
        clips[name] = []
        for line in LINES:
            t = time.perf_counter()
            wav, _ = voice_lines([line], say)
            clips[name].append((line, wav, time.perf_counter() - t))
        print(f"  {NAMES.get(name, name)}: xong")
    chosen = CHOICE_FILE.read_text(encoding="utf-8").strip() if CHOICE_FILE.is_file() else ""
    out.write_text(page(clips, chosen), encoding="utf-8")
    return clips


def choose(name: str) -> None:
    name = name.strip().lower()
    if name not in NAMES:
        raise SystemExit(f"Không có giọng '{name}'. Chọn: {', '.join(NAMES)}")
    CHOICE_FILE.write_text(name + "\n", encoding="utf-8")
    print(f"Đã chọn {NAMES[name]}. Các lần gắn Clicky sau sẽ dùng giọng này.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Nghe so sánh các giọng tiếng Việt offline và chọn một giọng.")
    ap.add_argument("--ra", default="So sanh giong.html")
    ap.add_argument("--chon", help="vieneu hoặc piper")
    a = ap.parse_args(argv)
    if a.chon:
        choose(a.chon)
        return 0
    clips = build(Path(a.ra))
    print(f"Đã ghi {a.ra} ({', '.join(clips)}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
