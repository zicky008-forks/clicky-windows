"""Blind listening test: VieNeu v3 vs Piper vi_VN vs one online voice, on the test PC's own CPU.

1) Make the clips (made-up finance answers only, so the online voice never hears real numbers):
       python tools/giong_noi/nghe_mu.py tao --thu-muc D:/giong-noi/nghe-mu
   Writes clips with random names, index.html (the listening page) and dap-an.json (which clip is which
   engine, plus how long each took to make). Do not open dap-an.json before listening.
2) The listener opens index.html, rates every clip 1-5 for "nghe tự nhiên" and "nghe rõ số", then presses
   "Lưu kết quả" (downloads ket-qua-nghe-mu.json).
3) Reveal:
       python tools/giong_noi/nghe_mu.py giai --thu-muc D:/giong-noi/nghe-mu --ket-qua ket-qua-nghe-mu.json

Rule for the default voice: the offline engine with the best average wins if it is within 0.5 points of the
online voice on both questions and makes a sentence fast enough (first sentence ready in under 1.5 s).
Otherwise the result goes to Anh Tuấn to decide.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

SENTENCES = [
    "Dạ, lợi nhuận sau thuế tháng 10 là 436 triệu, thấp hơn 120 triệu so với số trên báo cáo cũ.",
    "Chênh lệch -5,42 tỷ nằm ở tài khoản 511, do hàng bán bị trả lại hạch toán nhầm.",
    "Nếu giá đường giảm 5%, lợi nhuận gộp quý 3 còn khoảng 2,9 tỷ.",
    "Biên lợi nhuận gộp đạt 12,5%, tăng 1,3 điểm phần trăm so với cùng kỳ.",
    "Chị mở slide số 12 giúp em, phần chi phí bán hàng tăng 2,5 tỷ nằm ở đó.",
    "Em đã đối chiếu xong: bảng cân đối khớp, không còn dòng nào bị bỏ qua.",
    "Doanh thu thuần năm 2026 dự kiến 1.200 tỷ, chi phí lãi vay tăng 850 triệu.",
    "Kịch bản xấu nhất, lợi nhuận là -120 triệu. Chị có muốn em xem kịch bản khác không?",
]
EDGE_VOICE = "vi-VN-HoaiMyNeural"


def make_clip(engine: str, text: str) -> tuple:
    """(audio bytes, file extension, seconds to make)."""
    t = time.perf_counter()
    if engine == "online":
        import edge_tts

        async def run():
            out = b""
            async for chunk in edge_tts.Communicate(text, EDGE_VOICE).stream():
                if chunk["type"] == "audio":
                    out += chunk["data"]
            return out
        data, ext = asyncio.run(run()), "mp3"
    else:
        from audio.tts import vi_offline_tts
        data, ext = vi_offline_tts.engine(engine).synthesize(text), "wav"
    return data, ext, round(time.perf_counter() - t, 2)


def engines() -> list:
    from audio.tts import vi_offline_tts
    out = []
    if vi_offline_tts.vieneu_available():
        out.append("vieneu")
    if vi_offline_tts.piper_available():
        out.append("piper")
    try:
        import edge_tts  # noqa: F401
        out.append("online")
    except ImportError:
        pass
    return out


def page(rows: list) -> str:
    blocks = []
    for i, row in enumerate(rows, 1):
        clips = "".join(
            f'<div class="clip"><b>{html.escape(c)}</b><audio controls preload="none" src="{html.escape(c)}"></audio>'
            f'<label>Tự nhiên <select data-clip="{html.escape(c)}" data-q="tu_nhien">{_opts()}</select></label>'
            f'<label>Rõ số <select data-clip="{html.escape(c)}" data-q="ro_so">{_opts()}</select></label></div>'
            for c in row["clips"])
        blocks.append(f'<section><h2>Câu {i}</h2><p>{html.escape(row["text"])}</p>{clips}</section>')
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nghe mù giọng đọc</title><style>
body{{font:16px/1.5 system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;background:#fff;color:#1a1a1a}}
section{{border-top:1px solid #ddd;padding:12px 0}} .clip{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:6px 0}}
audio{{height:32px}} button{{font-size:16px;padding:8px 16px}}
</style></head><body><h1>Nghe mù giọng đọc</h1>
<p>Mỗi câu có vài bản đọc, tên ngẫu nhiên. Chấm 1 (kém) đến 5 (như người thật) cho "tự nhiên" và "nghe rõ số". Chấm xong bấm Lưu.</p>
{''.join(blocks)}
<p><button id="save">Lưu kết quả</button></p>
<script>
document.getElementById('save').onclick=function(){{
  var r={{}};document.querySelectorAll('select').forEach(function(s){{if(s.value){{(r[s.dataset.clip]=r[s.dataset.clip]||{{}})[s.dataset.q]=+s.value}}}});
  var a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(r,null,1)],{{type:'application/json'}}));
  a.download='ket-qua-nghe-mu.json';a.click();}};
</script></body></html>"""


def _opts() -> str:
    return '<option value="">–</option>' + "".join(f'<option>{n}</option>' for n in range(1, 6))


def create(folder: Path, seed=None, skip=()) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    names = rng.sample(range(100, 1000), len(SENTENCES) * 4)
    key, rows = {"clip": {}, "giay": {}, "khong_chay": {}}, []
    found = []
    for engine in engines():
        if engine in skip:
            continue
        try:
            make_clip(engine, "Xin chào.")             # also loads the model, so timings below are per sentence
            found.append(engine)
        except Exception as exc:                       # say which voice is missing, never drop it silently
            key["khong_chay"][engine] = str(exc)[:300]
            print(f"Giọng {engine} không chạy: {exc}", file=sys.stderr)
    if len(found) < 2:
        raise SystemExit(f"Cần ít nhất 2 giọng để so, mới có: {found}")
    for text in SENTENCES:
        clips = []
        for engine in rng.sample(found, len(found)):
            data, ext, sec = make_clip(engine, text)
            name = f"giong-{names.pop()}.{ext}"
            (folder / name).write_bytes(data)
            key["clip"][name] = engine
            key["giay"].setdefault(engine, []).append(sec)
            clips.append(name)
        rows.append({"text": text, "clips": clips})
    try:
        from audio.tts.vi_offline_tts import cpu_has_vnni
        key["cpu_vnni"] = cpu_has_vnni()
    except Exception:
        pass
    (folder / "index.html").write_text(page(rows), encoding="utf-8")
    (folder / "dap-an.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    return key


def reveal(folder: Path, result: Path) -> dict:
    key = json.loads((folder / "dap-an.json").read_text(encoding="utf-8"))
    ratings = json.loads(result.read_text(encoding="utf-8"))
    out = {}
    for clip, scores in ratings.items():
        engine = key["clip"].get(clip)
        if not engine:
            continue
        for q, v in scores.items():
            out.setdefault(engine, {}).setdefault(q, []).append(v)
    summary = {e: {q: round(sum(v) / len(v), 2) for q, v in qs.items()} for e, qs in out.items()}
    for e, secs in key["giay"].items():
        summary.setdefault(e, {})["giay_cau_dau"] = secs[0]
        summary[e]["giay_trung_binh"] = round(sum(secs) / len(secs), 2)
    return summary


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="lenh", required=True)
    t = sub.add_parser("tao")
    t.add_argument("--thu-muc", type=Path, required=True)
    t.add_argument("--bo", action="append", default=[], help="bỏ một giọng: vieneu | piper | online")
    g = sub.add_parser("giai")
    g.add_argument("--thu-muc", type=Path, required=True)
    g.add_argument("--ket-qua", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.lenh == "tao":
        key = create(a.thu_muc, skip=set(a.bo))
        print(f"Đã tạo {len(key['clip'])} bản đọc trong {a.thu_muc}. Mở index.html để nghe.")
        print("Thời gian tạo (giây):", json.dumps(key["giay"]))
        return 0
    print(json.dumps(reveal(a.thu_muc, a.ket_qua), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
