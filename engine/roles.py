"""The role of each input: only "Số chính thức" may put a figure in the report.

    Số chính thức          the trial balances: every number in the report comes from here
    Chi tiết để giải thích  sales exports: explain and cross-check the figures, never replace them
    Kế hoạch               budget / plan files (the annual plan)
    Dự báo                 a forecast or revised estimate (LE, FC, ước, dự báo): the months still to come, never the plan
    Lý do                  emails, letters, documents: the why behind a figure
    Ghi chú                photos, pasted text, notes, queries to check: read to understand, no figure taken

Photos, emails and pasted text are never a source of numbers. When two inputs claim the same role for the
same thing (two trial balances of one month, two plans) Clicky asks which one to use; it never picks.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from engine.trial_balance import fold

OFFICIAL, DETAIL, PLAN, REASON, NOTE = "Số chính thức", "Chi tiết để giải thích", "Kế hoạch", "Lý do", "Ghi chú"
FORECAST = "Dự báo"
_PLAN_WORDS = ("ke hoach", "kehoach", "plan", "budget", "ngan sach", "ngansach", "du toan", "dutoan", "target", "chi tieu",
               "forecast", "du bao", "dubao")
# A version or file named as a forecast or a revised plan, on folded text ('LE T09', 'Uoc TH 2026', 'FC2'). Shared by
# the intake (the file's role) and engine.forecast (which version gives the months to come).
FORECAST_WORDS = re.compile(r"\b(le|fc|rf|forecast|du bao|dubao|uoc|uoc tinh|uoc th|aopr|revised|reforecast|"
                            r"dieu chinh|cap nhat|muf|rolling)\d*\b")


def is_forecast(text: str) -> bool:
    """A version name or file name that says forecast or revised estimate (folded or not)."""
    t = fold(str(text)).replace("_", " ").replace("-", " ")
    t = re.sub(r"\b(ban|gia|hang|khach|kenh|cua hang|le phi) le\b", " ", t)     # bán lẻ, giá lẻ: retail, not LE
    return bool(FORECAST_WORDS.search(t))
_REASON_SUFFIXES = {".eml", ".msg", ".docx", ".doc", ".pdf", ".rtf"}
_NOTE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic", ".txt", ".m", ".pq", ".md"}


def _tokens(name: str) -> list:
    return re.findall(r"[a-z]+\d*", fold(Path(name).stem))


def plan_scenario(name: str) -> str:
    """'AOPR1' from 'AOPR1_FY2627.xlsx', 'AOP' from 'AOP FY26-27 v6.xlsx'; '' when no scenario token.

    Two plan files are only a duplicate when they carry the same scenario for the same period;
    AOP, AOPR1 and MUF side by side are versions the report compares, so Clicky never asks to pick one.
    """
    for tok in _tokens(name):
        m = re.fullmatch(r"(aopr|aop|muf|rf|le|fc|forecast|budget|plan)(\d*)", tok)
        if m:
            return (m.group(1) + m.group(2)).upper()
    return ""


def is_plan(name: str) -> bool:
    t = fold(Path(name).stem).replace("_", " ").replace("-", " ")
    if any(w in t or w.replace(" ", "") in t.replace(" ", "") for w in _PLAN_WORDS):
        return True
    return any(re.fullmatch(r"(aopr|aop|muf)\d*", tok) for tok in _tokens(name))


def of_file(name: str, *, trial_balance: bool = False, orders: bool = False) -> str:
    """Role from what the file was read as, else from its kind; anything unknown is only a note."""
    if trial_balance:
        return OFFICIAL
    if orders:
        return DETAIL
    suffix = Path(name).suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xls", ".csv") and is_forecast(Path(name).stem):
        return FORECAST
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
