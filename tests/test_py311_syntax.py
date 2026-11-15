"""CI runs Python 3.11: f-strings must not use 3.12-only syntax (PEP 701).

A backslash or the f-string's own quote inside a replacement field compiles on
3.12+ but is a SyntaxError on 3.11, which broke CI collection once (story.py).
"""
import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIRS = ("present", "engine", "office", "tools", "ui", "ai", "audio", "screen")


def _files():
    for d in DIRS:
        yield from sorted((ROOT / d).rglob("*.py"))
    yield from sorted(ROOT.glob("*.py"))


def _offenders(path):
    src = path.read_text(encoding="utf-8")
    bad = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.JoinedStr):
            continue
        whole = ast.get_source_segment(src, node) or ""
        m = re.match(r"[A-Za-z]*('''|\"\"\"|'|\")", whole)
        quote = m.group(1) if m else None
        for part in node.values:
            if not isinstance(part, ast.FormattedValue):
                continue
            seg = ast.get_source_segment(src, part.value) or ""
            if "\\" in seg or (quote and quote in seg):
                bad.append(f"{path.relative_to(ROOT)}:{part.value.lineno}: {seg[:60]}")
    return bad


@pytest.mark.skipif(sys.version_info < (3, 12), reason="3.11 itself refuses such files at import")
def test_no_pep701_only_fstrings():
    bad = [b for f in _files() for b in _offenders(f)]
    assert not bad, "f-string syntax that Python 3.11 rejects:\n" + "\n".join(bad)
