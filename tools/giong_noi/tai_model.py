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
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
MODELS = {
    # Weights: Apache-2.0 (huggingface.co/zzasdf/viet_iter3_pseudo_label). About 70,000 hours. Shipped.
    "70k": ("sherpa-onnx-zipformer-vi-int8-2025-04-20",
            "48d0fdc9b3515eb9b00c4dfec2883207ee5ebe5c95b1959e7afce87fc3391938", True),
    # Weights: CC-BY-NC-ND-4.0 (huggingface.co/hynt/Zipformer-30M-RNNT-6000h). Non-commercial, no derivatives:
    # allowed for an internal comparison only. Never bundle it in a build that is sold.
    "30m": ("sherpa-onnx-zipformer-vi-30M-int8-2026-02-09",
            "da8b637947091829d7ee9eda23da2a4ec7caa399233a3f4e34eb719fb2ea6b9b", False),
}
KEEP = (".onnx", "tokens.txt", "bpe.model")


def fetch(name: str, out: Path) -> Path:
    stem, sha, shippable = MODELS[name]
    url = BASE + stem + ".tar.bz2"
    print(f"Tải {url}")
    data = urllib.request.urlopen(url, timeout=600).read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha:
        raise SystemExit(f"SHA-256 không khớp cho {stem}: {got}")
    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as tar:
        for m in tar.getmembers():
            base = Path(m.name).name
            if m.isfile() and base.endswith(KEEP) and "/test_wavs/" not in m.name:
                (tmp / base).write_bytes(tar.extractfile(m).read())
    make_vocab(tmp)
    (tmp / "NGUON.txt").write_text(
        f"{stem}\n{url}\nsha256 {sha}\n" + ("Apache-2.0\n" if shippable else "CC-BY-NC-ND-4.0: chỉ dùng thử nội bộ\n"),
        encoding="utf-8")
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
    a = ap.parse_args(argv)
    fetch(a.model, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
