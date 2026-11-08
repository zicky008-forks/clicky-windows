"""Download an offline Vietnamese speech model into voice/asr-vi (used by the build and by the voice gate).

    python tools/giong_noi/tai_model.py                 # the model we ship: zipformer-vi 70k h (Apache-2.0)
    python tools/giong_noi/tai_model.py --model 30m --out D:/thu/30m   # internal A/B only, never shipped

Each archive is checked against a pinned SHA-256 before anything is unpacked. bpe.vocab (needed for hotwords)
is made from bpe.model with sentencepiece, which is a build-time tool only (pip install sentencepiece).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# Windows consoles and CI runners default to cp1252, which cannot print Vietnamese.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
MODELS = {
    # Weights: Apache-2.0 (huggingface.co/zzasdf/viet_iter3_pseudo_label). About 70,000 hours. Shipped.
    "70k": ("asr-models/sherpa-onnx-zipformer-vi-int8-2025-04-20",
            "48d0fdc9b3515eb9b00c4dfec2883207ee5ebe5c95b1959e7afce87fc3391938", True),
    # Weights: CC-BY-NC-ND-4.0 (huggingface.co/hynt/Zipformer-30M-RNNT-6000h). Non-commercial, no derivatives:
    # allowed for an internal comparison only. Never bundle it in a build that is sold.
    "30m": ("asr-models/sherpa-onnx-zipformer-vi-30M-int8-2026-02-09",
            "da8b637947091829d7ee9eda23da2a4ec7caa399233a3f4e34eb719fb2ea6b9b", False),
    # Piper voice vi_VN-vais1000-medium: dataset CC BY 4.0 (attribution in NGUON.txt). Fine-tuned from the
    # English "lessac" voice, whose licence is research-only: a lawyer must confirm before selling.
    # Only the .onnx, tokens.txt and MODEL_CARD are kept; the espeak-ng-data folder (GPL) is not.
    "piper-vais1000": ("tts-models/vits-piper-vi_VN-vais1000-medium",
                       "fa1367710767d36ed5cf13b4a449e20c35ffd12791c2e47c2e64142bfa55551a", True),
}
LICENCE = {"70k": "Apache-2.0", "30m": "CC-BY-NC-ND-4.0: chỉ dùng thử nội bộ",
           "piper-vais1000": "Giọng: dữ liệu VAIS-1000 CC BY 4.0 (https://ieee-dataport.org/documents/"
                             "vais-1000-vietnamese-speech-synthesis-corpus); tinh chỉnh từ giọng lessac (cần luật sư xem)"}
KEEP = (".onnx", "tokens.txt", "bpe.model", "MODEL_CARD")
# GitHub release downloads sometimes answer 502/503/504 or drop the connection; the build should not fail on one.
WAITS = (5, 20, 60)


def download(url: str, waits=WAITS, opener=urllib.request.urlopen, sleep=time.sleep) -> bytes:
    """The bytes at url, retried after each wait on a server error (5xx), a timeout or a dropped connection.
    A client error (404, 403) is raised at once: retrying cannot fix a wrong address."""
    for attempt, wait in enumerate((*waits, None), 1):
        try:
            return opener(url, timeout=600).read()
        except urllib.error.HTTPError as e:
            if e.code < 500 or wait is None:
                raise
            why = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if wait is None:
                raise
            why = type(e).__name__
        print(f"Lần {attempt} lỗi ({why}), thử lại sau {wait} giây", file=sys.stderr)
        sleep(wait)
    raise AssertionError("unreachable")


def archive(url: str, sha: str, cache: Path = None, get=download) -> bytes:
    """The archive at url, checked against sha. With a cache folder (the build keeps one between runs), an archive
    already there with the right SHA-256 is used without the network, and a fresh download is stored there."""
    name = url.rsplit("/", 1)[-1]
    kept = cache / name if cache else None
    if kept and kept.is_file():
        data = kept.read_bytes()
        if hashlib.sha256(data).hexdigest() == sha:
            print(f"Dùng bản đã lưu {kept}")
            return data
    print(f"Tải {url}")
    data = get(url)
    got = hashlib.sha256(data).hexdigest()
    if got != sha:
        raise SystemExit(f"SHA-256 không khớp cho {name}: {got}")
    if kept:
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(data)
    return data


def fetch(name: str, out: Path, cache: Path = None) -> Path:
    path, sha, shippable = MODELS[name]
    stem = path.rsplit("/", 1)[-1]
    url = BASE + path + ".tar.bz2"
    data = archive(url, sha, cache)
    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as tar:
        for m in tar.getmembers():
            base = Path(m.name).name
            if m.isfile() and base.endswith(KEEP) and "/test_wavs/" not in m.name and "espeak-ng-data" not in m.name:
                (tmp / base).write_bytes(tar.extractfile(m).read())
    if (tmp / "bpe.model").is_file():
        make_vocab(tmp)
    (tmp / "NGUON.txt").write_text(f"{stem}\n{url}\nsha256 {sha}\n{LICENCE[name]}\n", encoding="utf-8")
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    print(f"Xong: {out}")
    return out


def make_vocab(folder: Path) -> None:
    try:
        import sentencepiece as spm
    except ImportError:
        print("Không có sentencepiece: bỏ hotwords (pip install sentencepiece để bật)", file=sys.stderr)
        return
    sp = spm.SentencePieceProcessor(model_file=str(folder / "bpe.model"))
    lines = (f"{sp.id_to_piece(i)}\t{sp.get_score(i)}" for i in range(sp.get_piece_size()))
    (folder / "bpe.vocab").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=sorted(MODELS), default="70k")
    ap.add_argument("--out", type=Path, default=Path("voice") / "asr-vi")
    ap.add_argument("--cache", type=Path, default=os.environ.get("CLICKY_MODEL_CACHE") or None,
                    help="folder that keeps downloaded archives between builds")
    a = ap.parse_args(argv)
    fetch(a.model, a.out, Path(a.cache).expanduser() if a.cache else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
