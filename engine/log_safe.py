"""Error text made safe for clicky.log: every path and file name replaced by its shape.

clicky.log stays on a PC that other people may look at, so it never holds a customer's file or folder
name. Excel and Power Query errors quote them ("Could not find file 'C:\\Users\\…\\BCTC_Q3.xlsx'"). The
kind of error and its reason stay readable (DataFormat.Error, Expression.Error, a missing column or a
step name), so a broken query can still be diagnosed; only names on disk become their shape:
``C:\\…\\<12 ký tự>.csv``.
"""
from __future__ import annotations

import re

_EXT = r"(?:xlsx|xlsm|xlsb|xls|csv|txt|tsv|json|xml|pbix|pbip|pptx|docx|pdf|zip|eml|png|jpe?g)"
# An opening quote never follows a letter ("couldn't" is not a quote); the closing one never precedes one.
_QUOTED = re.compile(r"(?<![^\W\d_])(['\"“‘])([^'\"”’\r\n]*?)(['\"”’])(?![^\W\d_])")
# An unquoted path starts at a drive, a UNC share or a URL. Everything up to the last \ or / on the line is
# folder (folder names may have spaces); the last piece runs to a known extension, else to the first space.
_START = r"(?:https?://|[A-Za-z]:[\\/]|\\\\)"
_PATH = re.compile(rf"{_START}(?:[^\r\n'\"]*[\\/])?(?:[^\\/\r\n'\"]*?\.{_EXT}\b|[^\s\\/'\"]*)", re.IGNORECASE)
_FILE = re.compile(rf"(?<![\w<])[\w\-.]+\.{_EXT}\b", re.IGNORECASE)


def shape(name: str) -> str:
    """C:\\…\\<12 ký tự>.csv for a path, <12 ký tự>.csv for a bare file name: length and kind, never the name."""
    text = name.strip()
    root = ""
    m = re.match(r"^([A-Za-z]:)[\\/]", text)
    url = re.match(r"^(https?)://", text, re.IGNORECASE)
    if url:
        root = url.group(1).lower() + "://<…>/"         # the tenant and the user name never stay
        text = text[url.end():]
        if "/" not in text.rstrip("/"):
            return root.rstrip("/")
    elif m:
        root = m.group(1) + "\\…\\"
    elif text.startswith("\\\\"):
        root = "\\\\…\\"
    last = re.split(r"[\\/]", text.rstrip("\\/"))[-1] if text.rstrip("\\/") else ""
    stem, dot, ext = last.rpartition(".")
    if dot and re.fullmatch(_EXT, ext, re.IGNORECASE):
        return f"{root}<{len(stem)} ký tự>.{ext.lower()}"
    return f"{root}<{len(last)} ký tự>" if last else (root or "<trống>")


def _looks_like_name(inner: str) -> bool:
    return bool(_PATH.match(inner) or "\\" in inner or "/" in inner or _FILE.fullmatch(inner.strip()))


def scrub(text) -> str:
    """The text with every path and file name replaced by shape(); everything else kept."""
    out = str(text or "")
    out = _QUOTED.sub(lambda m: m.group(1) + shape(m.group(2)) + m.group(3)
                      if _looks_like_name(m.group(2)) else m.group(0), out)
    out = _PATH.sub(lambda m: m.group(0) if "…" in m.group(0) else shape(m.group(0)), out)
    out = _FILE.sub(lambda m: shape(m.group(0)), out)
    return out
