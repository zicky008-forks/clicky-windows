"""Fetch VieNeu-TTS v3 Turbo (ONNX/CPU) into voice/vieneu/hub, so Clicky never downloads at run time.

    python tools/giong_noi/tai_vieneu.py                    # int8 + fp32 graphs (Clicky picks by CPU)
    python tools/giong_noi/tai_vieneu.py --precision fp32   # one precision only (smaller installer)

Weights: Apache-2.0 (pnnbao-ump/VieNeu-TTS-v3-Turbo, codec OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX).
The layout is a normal Hugging Face cache; Clicky points HF_HUB_CACHE at it and sets HF_HUB_OFFLINE=1.
Snapshot files are made real files (no symlinks into blobs/): onnxruntime refuses external weight data
(.data) that resolves outside the model folder, and an installer cannot keep symlinks anyway.
Needs network to huggingface.co (the build runner has it; the cloud dev box does not).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

V3_REPO = "pnnbao-ump/VieNeu-TTS-v3-Turbo"
CODEC_REPO = "OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX"
# Same lists as vieneu 3.8.3 _v3_turbo_engine/onnx_runtime_lite.py (_GRAPH_FILES, _CODEC_FILES).
GRAPH_FILES = ["vieneu_prefill.onnx", "vieneu_decode_step.onnx", "vieneu_acoustic_cached.onnx",
               "vieneu_backbone_shared.data", "vieneu_v3_heads.npz", "config.json", "tokenizer.json"]
CODEC_FILES = ["moss_audio_tokenizer_decode_full.onnx", "moss_audio_tokenizer_decode_shared.data",
               "moss_audio_tokenizer_decode_step.onnx", "codec_browser_onnx_meta.json",
               "moss_audio_tokenizer_encode.onnx", "moss_audio_tokenizer_encode.data"]
SUBFOLDER = {"int8": "onnx_int8", "fp32": "onnx_update"}
ROOT_FILES = ["config.json", "denoiser.onnx"]          # denoiser is optional (voice cloning only)


def fetch(out: Path, precisions: list) -> int:
    from huggingface_hub import hf_hub_download
    cache = out / "hub"
    total = 0

    def get(repo, name, sub=None, optional=False):
        nonlocal total
        try:
            p = Path(hf_hub_download(repo, name, subfolder=sub, cache_dir=str(cache)))
            total += p.stat().st_size
            print(f"  {repo}/{sub + '/' if sub else ''}{name}  {p.stat().st_size / 1e6:.1f} MB")
        except Exception as exc:
            if not optional:
                raise
            print(f"  (bỏ qua, không bắt buộc) {name}: {exc}")

    for name in ROOT_FILES:
        get(V3_REPO, name, optional=name != "config.json")
    for p in precisions:
        for name in GRAPH_FILES:
            get(V3_REPO, name, SUBFOLDER[p], optional=name.endswith(".json") and name != "config.json")
    for name in CODEC_FILES:
        get(CODEC_REPO, name, optional=name.endswith(".json"))
    flatten(cache)
    (out / "NGUON.txt").write_text(
        f"{V3_REPO} ({', '.join(precisions)})\n{CODEC_REPO}\nApache-2.0\n", encoding="utf-8")
    print(f"Tổng {total / 1e6:.0f} MB -> {out}")
    return total


def flatten(cache: Path) -> int:
    """Replace each snapshot symlink with its blob (moved, not copied), then drop blobs/.

    Returns files replaced. Fails loudly if blobs/ is left behind: it would double the installer size.
    """
    done = 0
    for repo in cache.glob("models--*"):
        moved = {}                                    # blob -> first real file made from it
        for link in sorted((repo / "snapshots").rglob("*")):
            if not link.is_symlink():
                continue
            target = link.resolve(strict=False)
            link.unlink()
            if target in moved:                       # two names for one blob: copy the one already moved
                shutil.copy2(moved[target], link)
            else:
                shutil.move(str(target), str(link))
                moved[target] = link
            done += 1
        blobs = repo / "blobs"
        if blobs.exists():
            shutil.rmtree(blobs, onerror=_force_remove)
        if blobs.exists():
            raise SystemExit(f"Không xoá được {blobs}: bộ cài sẽ chứa model hai lần")
    return done


def _force_remove(func, path, _exc):
    import os
    import stat
    os.chmod(path, stat.S_IWRITE)                     # Windows marks some downloaded files read-only
    func(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--precision", choices=["int8", "fp32", "both"], default="both")
    ap.add_argument("--out", type=Path, default=Path("voice") / "vieneu")
    a = ap.parse_args(argv)
    fetch(a.out, ["int8", "fp32"] if a.precision == "both" else [a.precision])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
