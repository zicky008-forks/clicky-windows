"""The role of each input: only "Số chính thức" may put a figure in the report.

    Số chính thức          the trial balances: every number in the report comes from here
    Chi tiết để giải thích  sales exports: explain and cross-check the figures, never replace them
    Kế hoạch               budget / plan files
    Lý do                  emails, letters, documents: the why behind a figure
    Ghi chú                photos, pasted text, notes, queries to check: read to understand, no figure taken

Photos, emails and pasted text are never a source of numbers. When two inputs claim the same role for the
same thing (two trial balances of one month, two plans) Clicky asks which one to use; it never picks.
"""
from __future__ import annotations

import time
from pathlib import Path

from engine.trial_balance import fold

OFFICIAL, DETAIL, PLAN, REASON, NOTE = "Số chính thức", "Chi tiết để giải thích", "Kế hoạch", "Lý do", "Ghi chú"
_PLAN_WORDS = ("ke hoach", "kehoach", "plan", "budget", "ngan sach", "ngansach", "du toan", "dutoan", "target", "chi tieu")
_REASON_SUFFIXES = {".eml", ".msg", ".docx", ".doc", ".pdf", ".rtf"}
_NOTE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic", ".txt", ".m", ".pq", ".md"}


def is_plan(name: str) -> bool:
    t = fold(Path(name).stem).replace("_", " ").replace("-", " ")
    return any(w in t or w.replace(" ", "") in t.replace(" ", "") for w in _PLAN_WORDS)


def of_file(name: str, *, trial_balance: bool = False, orders: bool = False) -> str:
    """Role from what the file was read as, else from its kind; anything unknown is only a note."""
    if trial_balance:
        return OFFICIAL
    if orders:
        return DETAIL
    suffix = Path(name).suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xls", ".csv") and is_plan(name):
        return PLAN
    if suffix in _REASON_SUFFIXES:
        return REASON
    return NOTE


def modified(path: Path) -> str:
    """'03/11 08:40': when the file was last changed, the one thing that tells two copies apart."""
    try:
        return time.strftime("%d/%m %H:%M", time.localtime(path.stat().st_mtime))
    except OSError:
        return ""
