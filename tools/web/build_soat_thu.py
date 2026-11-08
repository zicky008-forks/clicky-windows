"""Build the "Clicky soát thử" web page into one folder that any static host can serve.

    python tools/web/build_soat_thu.py                       # → dist/soat-thu/
    python tools/web/build_soat_thu.py --pyodide-dir PATH    # use Pyodide files already on disk
    python tools/web/build_soat_thu.py --no-pyodide          # page + app.zip only (tests)

Everything the page needs is copied in, Pyodide included, so the page never loads code from a CDN
and its Content-Security-Policy can allow only its own origin (connect-src 'self'). app.zip holds
the engine, the page's Python glue and openpyxl (pure Python, copied from this machine's install).
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import shutil
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "web" / "soat-thu"
SAMPLE = ROOT / "assets" / "mau" / "Mau_SongHong_T10"
PYODIDE_VERSION = "0.27.7"
PYODIDE_FILES = ("pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json")
PURE_PACKAGES = ("openpyxl", "et_xmlfile")
HEADERS = """/*
  Content-Security-Policy: default-src 'self'; script-src 'self' 'wasm-unsafe-eval' 'unsafe-eval'; worker-src 'self' blob:; connect-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; form-action 'none'; base-uri 'none'
  Referrer-Policy: no-referrer
  X-Content-Type-Options: nosniff
/pyodide/*
  Cache-Control: public, max-age=31536000, immutable
"""


def package_dir(name: str) -> Path:
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit(f"{name} is not installed here: pip install {name}")
    return Path(list(spec.submodule_search_locations)[0])


def make_app_zip(out: Path) -> list:
    """engine/*.py + soat_thu.py + openpyxl + et_xmlfile, at the zip root. Returns the names written."""
    names = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        def add(path: Path, arc: str):
            z.write(path, arc)
            names.append(arc)

        for py in sorted((ROOT / "engine").glob("*.py")):
            add(py, f"engine/{py.name}")
        add(PAGE / "soat_thu.py", "soat_thu.py")
        for pkg in PURE_PACKAGES:
            base = package_dir(pkg)
            for f in sorted(base.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                    add(f, f"{pkg}/{f.relative_to(base).as_posix()}")
    return names


def fetch_pyodide(dest: Path) -> None:
    """Pyodide's core files from the npm package tarball (no Node needed)."""
    url = f"https://registry.npmjs.org/pyodide/-/pyodide-{PYODIDE_VERSION}.tgz"
    print(f"downloading {url}")
    data = urllib.request.urlopen(url, timeout=120).read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for name in PYODIDE_FILES:
            member = tar.getmember(f"package/{name}")
            (dest / name).write_bytes(tar.extractfile(member).read())


def build(out: Path, pyodide_dir: Path | None = None, with_pyodide: bool = True) -> Path:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in ("index.html", "app.js", "worker.js"):
        shutil.copy2(PAGE / name, out / name)
    (out / "_headers").write_text(HEADERS, encoding="utf-8")
    (out / "mau").mkdir()
    for name in ("TB_T10.xlsx", "TB_T09.xlsx"):
        shutil.copy2(SAMPLE / name, out / "mau" / name)
    make_app_zip(out / "app.zip")
    if with_pyodide:
        (out / "pyodide").mkdir()
        if pyodide_dir:
            for name in PYODIDE_FILES:
                shutil.copy2(Path(pyodide_dir) / name, out / "pyodide" / name)
        else:
            fetch_pyodide(out / "pyodide")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default=str(ROOT / "dist" / "soat-thu"))
    ap.add_argument("--pyodide-dir", help="folder holding Pyodide's core files (e.g. node_modules/pyodide)")
    ap.add_argument("--no-pyodide", action="store_true", help="skip Pyodide (the page will not run)")
    args = ap.parse_args(argv)
    out = build(Path(args.out), args.pyodide_dir, not args.no_pyodide)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"built {out} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
