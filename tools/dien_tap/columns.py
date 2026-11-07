"""Answering Clicky's column question from the answer key, and keeping its remembered answers out of the run.

Clicky asks which column is the sale date and which is the amount when an order file's column names are
unknown (close_step "column_question", then "column_answer"). The robot answers only from the case's answer
key, `"columns": [{"file": "Shopee_T10.csv", "date": "Order date", "amount": "Total"}]` or
`{"file": ..., "not_orders": true}`. With no entry for the file it presses nothing and the run fails with
that reason: a guessed answer would grade the robot, not Clicky.

Clicky keeps the answers in <data_dir>/cot_da_nho.json, so a second lap would not be asked again. Each run
starts without that file and puts back afterwards whatever was there before (the tester's own answers).
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

COLUMN_YES, COLUMN_NO = "Đúng, dùng file này", "Không phải đơn hàng"     # office/month_close.py
MEMORY_FILE = "cot_da_nho.json"
BACKUP_SUFFIX = ".truoc-dien-tap"

_GUESS = re.compile(r"cột\s+\"([^\"]+)\"\s+là\s+ngày\s+bán,\s*cột\s+\"([^\"]+)\"\s+là\s+số\s+tiền", re.IGNORECASE)
_EXT = re.compile(r"\.(xlsx|xlsm|xlsb|xls|csv|txt)$", re.IGNORECASE)


def _fold(text) -> str:
    text = unicodedata.normalize("NFC", str(text or "")).strip().casefold()
    return re.sub(r"\s+", " ", text)


def _same_file(a, b) -> bool:
    return _fold(a) == _fold(b) or _fold(_EXT.sub("", str(a or ""))) == _fold(_EXT.sub("", str(b or "")))


def entry_for(key: list, file: str):
    return next((c for c in key or [] if isinstance(c, dict) and _same_file(c.get("file"), file)), None)


def answer(step: dict, text: str, key: list):
    """(button to press, problem). Exactly one of the two is set.

    step: the close_step "column_question" event (file, need, options); text: what Clicky said with it."""
    file, need, options = step.get("file") or "?", step.get("need"), list(step.get("options") or [])
    want = entry_for(key, file)
    if want is None:
        return None, (f"Clicky hỏi cột của file {file} nhưng đáp án không có mục \"columns\" cho file này; "
                      "robot không đoán")
    if want.get("not_orders"):
        hit = next((o for o in options if _fold(o) == _fold(COLUMN_NO)), None)
        return (hit, None) if hit else (None, f"file {file}: không có nút \"{COLUMN_NO}\"")
    if need == "confirm":
        m = _GUESS.search(text or "")
        if not m:
            return None, f"file {file}: không đọc được Clicky đoán cột nào"
        date, amount = m.group(1), m.group(2)
        if _fold(date) == _fold(want.get("date")) and _fold(amount) == _fold(want.get("amount")):
            hit = next((o for o in options if _fold(o) == _fold(COLUMN_YES)), None)
            return (hit, None) if hit else (None, f"file {file}: không có nút \"{COLUMN_YES}\"")
        return None, (f"file {file}: Clicky đoán ngày bán = \"{date}\", số tiền = \"{amount}\"; "
                      f"đáp án là \"{want.get('date')}\" và \"{want.get('amount')}\"")
    if need in ("date", "amount"):
        col = want.get(need)
        hit = next((o for o in options if _fold(o) == _fold(f"Cột {col}")), None)
        if hit:
            return hit, None
        what = "ngày bán" if need == "date" else "số tiền"
        shown = ", ".join(o for o in options if _fold(o) != _fold(COLUMN_NO)) or "không có cột nào"
        return None, f"file {file}: cột {what} theo đáp án là \"{col}\" nhưng Clicky chỉ đưa {shown}"
    return None, f"file {file}: câu hỏi cột không rõ loại ({need})"


class Answerer:
    """An AUTO_PRESS entry: answers each column question once, from the answer key; records what it could not."""
    needs_context = True

    def __init__(self, key: list):
        self.key = key or []
        self.problems: list = []
        self.answered: list = []
        self._seen: set = set()

    def __call__(self, e: dict, before: list):
        q = next((i for i in range(len(before) - 1, -1, -1)
                  if before[i].get("event") == "close_step" and before[i].get("step") == "column_question"), None)
        if q is None or q in self._seen:
            return None
        labels = list(e.get("labels") or [])
        if not set(before[q].get("options") or []) & set(labels):
            return None                                    # a later card, not this question's buttons
        self._seen.add(q)
        text = next((b.get("text") or "" for b in reversed(before[q:]) if b.get("event") == "beat"), "")
        hit, problem = answer(before[q], text, self.key)
        if problem:
            self.problems.append(problem)
            return None
        self.answered.append(f"{before[q].get('file')}: {hit}")
        return hit


def check_lines(events: list, key: list, answerer: Answerer | None):
    """(name, ok, detail, blocking) rows for the report."""
    asked = [e for e in events if e.get("event") == "close_step" and e.get("step") == "column_question"]
    rows = []
    problems = answerer.problems if answerer else []
    if asked or problems:
        rows.append(("Câu hỏi tên cột lạ được trả lời theo đáp án", not problems,
                     "; ".join(problems[:3]) if problems else
                     f"{len(asked)} câu hỏi, đã trả lời: " + "; ".join(answerer.answered if answerer else []), True))
    files = {e.get("file") for e in asked}
    not_asked = [c.get("file") for c in key or [] if isinstance(c, dict)
                 and not any(_same_file(c.get("file"), f) for f in files)]
    if not_asked:
        rows.append(("Clicky hỏi tên cột của file có cột lạ", False,
                     "không hỏi: " + ", ".join(map(str, not_asked)), False))
    return rows


def isolate_memory(data_dir: Path):
    """Start the run with no remembered columns; return the function that puts the tester's file back."""
    path = data_dir / MEMORY_FILE
    backup = data_dir / (MEMORY_FILE + BACKUP_SUFFIX)
    if path.exists():
        if backup.exists():            # an earlier run crashed before restoring: its backup is the tester's file
            path.unlink()
        else:
            path.replace(backup)

    def restore() -> None:
        try:
            if path.exists():
                path.unlink()
            if backup.exists():
                backup.replace(path)
        except OSError:
            pass
    return restore
