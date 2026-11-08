"""Who has answered about their operating costs, and what they said: a local tracker, like gop-y.jsonl.

One JSON line per event in data_dir()/phan-hoi-chi-phi.jsonl: 'sent' when a department's workbook and draft were
made, 'reply' when the workbook came back and was read. Nothing here leaves the PC.

A returned workbook is known by its keywords (engine.opex_split.tag), not its name. Its 'Phản hồi' column is read
line by line: Đúng, Sai, Cần hỏi, blank, or anything else (named, never guessed). Every amount and label is compared
with the copy Clicky wrote, so a figure changed by hand is named rather than taken in.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from engine.opex_split import DETAIL, REPLIES, SUMMARY, read_tag
from engine.tables import key

FILE_NAME = "phan-hoi-chi-phi.jsonl"
BLANK = "chưa chọn"
_FOLD = {key(r): r for r in REPLIES}


def _file() -> Path:
    from storage import data_dir
    return data_dir() / FILE_NAME


def _append(entry: dict, now: Optional[float] = None) -> dict:
    entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now if now is not None else time.time())),
             **entry}
    with open(_file(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def events() -> list:
    try:
        lines = _file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def record_sent(period: str, pack, path: Path, how: str, now: Optional[float] = None) -> dict:
    """A department's workbook was written and its draft saved (how: 'outlook', 'eml' or '')."""
    return _append({"kind": "sent", "period": period, "dept": pack.dept, "name": pack.name, "file": str(path),
                    "draft": how, "to": bool(pack.email), "lines": len(pack.details), "total": str(pack.totals[0])},
                   now)


# ── A returned workbook ─────────────────────────────────────────────────────

@dataclass
class Row:
    sheet: str
    row: int
    label: str
    amount: Optional[Decimal]
    reply: str          # one of REPLIES, BLANK, or the text as written
    note: str = ""


@dataclass
class Reply:
    period: str
    dept: str
    file: str
    rows: list = field(default_factory=list)            # [Row]
    changed: list = field(default_factory=list)         # [(sheet, row, what was sent, what came back)]

    @property
    def counts(self) -> dict:
        out = dict.fromkeys(REPLIES + (BLANK, "khác"), 0)
        for r in self.rows:
            out[r.reply if r.reply in out else "khác"] += 1
        return out

    @property
    def odd(self) -> list:
        """Rows answered with something other than the three choices."""
        return [r for r in self.rows if r.reply not in REPLIES + (BLANK,)]

    @property
    def to_follow(self) -> list:
        """Rows accounting has to look at: Sai, Cần hỏi, an odd answer, or a note left without a choice."""
        return [r for r in self.rows if r.reply not in ("Đúng", BLANK) or (r.reply == BLANK and r.note)]

    @property
    def done(self) -> bool:
        """Every row answered and nothing to follow up, no figure changed."""
        return bool(self.rows) and not self.to_follow and not self.changed and not self.counts[BLANK]


def _amount(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:                    # noqa: BLE001 - text where a figure was: compared as text
        return None


def _rows(path: Path) -> tuple:
    """(keywords, [Row]) of one workbook: summary lines above 'Tổng cộng', then every detail line."""
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    try:
        tag_text = wb.properties.keywords
        out = []
        if SUMMARY in wb.sheetnames:
            for n, r in enumerate(wb[SUMMARY].iter_rows(min_row=4, values_only=True), start=4):
                if not r or r[0] is None:
                    continue
                if str(r[0]).strip() == "Tổng cộng":
                    break
                out.append(_row(SUMMARY, n, str(r[0]), r[1], r[8] if len(r) > 8 else None, r[9] if len(r) > 9 else None))
        if DETAIL in wb.sheetnames:
            for n, r in enumerate(wb[DETAIL].iter_rows(min_row=3, values_only=True), start=3):
                if not r or all(v is None for v in r):
                    continue
                label = " · ".join(str(x) for x in (r[2], r[3]) if x not in (None, ""))
                out.append(_row(DETAIL, n, label, r[4], r[6] if len(r) > 6 else None, r[7] if len(r) > 7 else None))
    finally:
        wb.close()
    return tag_text, out


def _row(sheet, n, label, amount, reply, note) -> Row:
    text = str(reply or "").strip()
    return Row(sheet, n, label, _amount(amount), _FOLD.get(key(text), text) if text else BLANK,
               str(note or "").strip())


def read_reply(path: Path, original: Optional[Path] = None) -> Optional[Reply]:
    """The answers in a returned workbook, or None when it is not one Clicky wrote. original: the copy sent out
    (found from the tracker when not given); every amount and label is compared with it."""
    tag_text, rows = _rows(Path(path))
    found = read_tag(tag_text)
    if not found:
        return None
    period, dept = found
    rp = Reply(period, dept, str(path), rows)
    if original is None:
        sent = [e for e in events() if e.get("kind") == "sent" and e.get("period") == period and e.get("dept") == dept]
        original = Path(sent[-1]["file"]) if sent else None
    if original is not None and Path(original).exists() and Path(original).resolve() != Path(path).resolve():
        _tag, was = _rows(Path(original))
        back = {(r.sheet, r.row): r for r in rows}
        for w in was:
            b = back.get((w.sheet, w.row))
            if b is None:
                rp.changed.append((w.sheet, w.row, f"{w.label}: {w.amount}", "dòng bị xoá"))
            elif b.amount != w.amount or b.label != w.label:
                rp.changed.append((w.sheet, w.row, f"{w.label}: {w.amount}", f"{b.label}: {b.amount}"))
        sent_keys = {(w.sheet, w.row) for w in was}
        for b in rows:
            if (b.sheet, b.row) not in sent_keys:
                rp.changed.append((b.sheet, b.row, "dòng không có trong bản gửi đi", f"{b.label}: {b.amount}"))
    return rp


def record_reply(rp: Reply, now: Optional[float] = None) -> dict:
    c = rp.counts
    return _append({"kind": "reply", "period": rp.period, "dept": rp.dept, "file": rp.file,
                    "counts": c, "follow": len(rp.to_follow), "changed": len(rp.changed), "done": rp.done}, now)


# ── Where each department stands ────────────────────────────────────────────

@dataclass
class Standing:
    dept: str
    name: str
    state: str          # "chờ phản hồi", "đã xác nhận", "cần xử lý"
    follow: int = 0
    changed: int = 0
    draft: str = ""


def standing(period: str) -> list:
    """The latest state of every department sent a workbook for period, in the order they were sent."""
    out = {}
    for e in events():
        if e.get("period") != period:
            continue
        d = e.get("dept", "")
        if e.get("kind") == "sent":
            out[d] = Standing(d, e.get("name", d), "chờ phản hồi", draft=e.get("draft", ""))
        elif e.get("kind") == "reply" and d in out:
            s = out[d]
            s.state = "đã xác nhận" if e.get("done") else "cần xử lý"
            s.follow, s.changed = int(e.get("follow", 0)), int(e.get("changed", 0))
    return list(out.values())


def sentence(period: str) -> str:
    """One line: how many departments answered, and who still has something open."""
    ss = standing(period)
    if not ss:
        return "Em chưa gửi phòng nào chi phí tháng này."
    waiting = [s.name for s in ss if s.state == "chờ phản hồi"]
    open_ = [s.name for s in ss if s.state == "cần xử lý"]
    ok = len(ss) - len(waiting) - len(open_)
    parts = [f"{ok}/{len(ss)} phòng đã xác nhận"]
    if open_:
        parts.append(f"cần xử lý: {', '.join(open_)}")
    if waiting:
        parts.append(f"chưa trả lời: {', '.join(waiting)}")
    return "; ".join(parts) + "."
