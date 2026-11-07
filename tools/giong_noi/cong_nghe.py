"""Voice gate: does Clicky hear an accountant's Vietnamese, offline, fast enough?

Gate: at least 28 of the 30 sentences in cau_cong.json right (every 'phai_co' item present after the
words-to-digits step), and every sentence under 3 seconds from key release to text, on the test PC.

1) Record once (the person reads each sentence; Enter starts, Enter stops):
       python tools/giong_noi/cong_nghe.py ghi --thu-muc D:/giong-noi/lan-1
2) Score one or more models on the same recordings (A/B):
       python tools/giong_noi/cong_nghe.py cham --thu-muc D:/giong-noi/lan-1 --model voice/asr-vi --model D:/thu/30m
   Writes ket-qua.json and ket-qua.md next to the recordings.

The time per sentence is measured the way the app does it: trim silence + decode + words-to-digits,
after the model is loaded (loading happens once, at app start).
Recordings are the tester's own voice reading made-up sentences: no company data.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from pathlib import Path

# Windows consoles and CI runners default to cp1252, which cannot print Vietnamese.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

PASS_COUNT = 28
MAX_SECONDS = 3.0


def load_sentences() -> list:
    return json.loads((HERE / "cau_cong.json").read_text(encoding="utf-8"))["cau"]


def check(text: str, sentence: dict) -> list:
    """Items of 'phai_co' missing from the normalized text (empty list = right)."""
    low = text.lower()
    return [p for p in sentence["phai_co"] if p.lower() not in low]


def wav_name(sid: int) -> str:
    return f"cau-{sid:02d}.wav"


def record(folder: Path) -> None:
    import numpy as np
    import sounddevice as sd
    folder.mkdir(parents=True, exist_ok=True)
    for s in load_sentences():
        print(f"\nCâu {s['id']}/30: {s['doc']}")
        input("  Nhấn Enter rồi đọc, đọc xong nhấn Enter lần nữa... ")
        chunks = []
        stream = sd.InputStream(samplerate=16000, channels=1, dtype="int16",
                                callback=lambda data, *_: chunks.append(data.copy()))
        with stream:
            input("  Đang ghi. Nhấn Enter khi đọc xong.")
        pcm = np.concatenate(chunks).tobytes() if chunks else b""
        with wave.open(str(folder / wav_name(s["id"])), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm)
        print(f"  Đã lưu {wav_name(s['id'])} ({len(pcm) / 32000:.1f} giây)")


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit(f"{path.name}: cần 16 kHz, mono, 16-bit")
        return w.readframes(w.getnframes())


def score(folder: Path, models: list) -> dict:
    from audio.stt.sherpa_vi_stt import SherpaViSTT
    sentences = load_sentences()
    report = {"thu_muc": str(folder), "cong": {"dung_toi_thieu": PASS_COUNT, "giay_toi_da": MAX_SECONDS},
              "model": []}
    for m in models:
        stt = SherpaViSTT(Path(m))
        t = time.perf_counter()
        stt.warm_up()
        load = time.perf_counter() - t
        rows = []
        for s in sentences:
            wav = folder / wav_name(s["id"])
            if not wav.is_file():
                rows.append({"id": s["id"], "thieu_file": True, "dung": False, "giay": None})
                continue
            pcm = read_pcm(wav)
            t = time.perf_counter()
            text = stt.transcribe_sync(pcm)
            sec = time.perf_counter() - t
            missing = check(text, s)
            rows.append({"id": s["id"], "doc": s["doc"], "nghe_tho": stt.last_raw, "ra": text,
                         "thieu": missing, "dung": not missing, "giay": round(sec, 3),
                         "do_dai_giay": round(len(pcm) / 32000, 2)})
        right = sum(r["dung"] for r in rows)
        times = [r["giay"] for r in rows if r["giay"] is not None]
        slowest = max(times) if times else None
        report["model"].append({
            "model": str(m), "nguon": _source(Path(m)), "nap_giay": round(load, 2),
            "dung": right, "tong": len(rows), "giay_cham_nhat": slowest,
            "giay_trung_binh": round(sum(times) / len(times), 3) if times else None,
            "dat": right >= PASS_COUNT and slowest is not None and slowest < MAX_SECONDS,
            "cau": rows})
    return report


def _source(folder: Path) -> str:
    f = folder / "NGUON.txt"
    return f.read_text(encoding="utf-8").splitlines()[0] if f.is_file() else folder.name


def markdown(report: dict) -> str:
    out = ["# Kết quả cổng giọng nói", "",
           f"Cổng: ít nhất {PASS_COUNT}/30 câu đúng và câu chậm nhất dưới {MAX_SECONDS:g} giây.", ""]
    out.append("| Model | Đúng | Chậm nhất | Trung bình | Nạp | Kết luận |")
    out.append("| --- | --- | --- | --- | --- | --- |")
    for m in report["model"]:
        out.append(f"| {m['nguon']} | {m['dung']}/{m['tong']} | {m['giay_cham_nhat']} s | {m['giay_trung_binh']} s | "
                   f"{m['nap_giay']} s | {'ĐẠT' if m['dat'] else 'KHÔNG ĐẠT'} |")
    for m in report["model"]:
        wrong = [r for r in m["cau"] if not r["dung"]]
        if wrong:
            out += ["", f"## Câu sai: {m['nguon']}", ""]
            for r in wrong:
                if r.get("thieu_file"):
                    out.append(f"- Câu {r['id']}: thiếu file ghi âm")
                else:
                    out.append(f"- Câu {r['id']}: nghe ra \"{r['ra']}\"; thiếu {', '.join(r['thieu'])}")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="lenh", required=True)
    g = sub.add_parser("ghi")
    g.add_argument("--thu-muc", type=Path, required=True)
    c = sub.add_parser("cham")
    c.add_argument("--thu-muc", type=Path, required=True)
    c.add_argument("--model", action="append", required=True, help="thư mục model (lặp lại để so A/B)")
    a = ap.parse_args(argv)
    if a.lenh == "ghi":
        record(a.thu_muc)
        return 0
    report = score(a.thu_muc, a.model)
    (a.thu_muc / "ket-qua.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (a.thu_muc / "ket-qua.md").write_text(markdown(report), encoding="utf-8")
    print(markdown(report))
    return 0 if all(m["dat"] for m in report["model"][:1]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
