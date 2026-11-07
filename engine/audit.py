"""The "Bien ban Clicky" sheet: every change Clicky made to the workbook, what it left out, and the report's status.

One line per change: #, time, cell, item, before, after, reason, who agreed ("Tự động" for a formula
Clicky added on its own), source. Under it the status (SƠ BỘ until the user marks it CHÍNH THỨC),
everything left out of the computation with its amount and reason, and per file the proof that
used + left out = what the file holds. Pure data; office.month_close writes it.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional
from engine.address import you  # the one word for the user (CLAUDE.md rule 8)

SHEET = "Bien ban Clicky"
DRAFT, FINAL = "SƠ BỘ", "CHÍNH THỨC"
HEADER = ["#", "Lúc", "Ô", "Chỉ tiêu", "Trước", "Sau", "Lý do", "Ai đồng ý", "Nguồn"]
AUTO = "Tự động"
FIRST_CHANGE_ROW = 5          # row 4 is the header


def now() -> str:
    return time.strftime("%H:%M")


def who(at: Optional[str] = None) -> str:
    """The Windows user who pressed the button, with the time. Stays on this PC like the workbook itself."""
    name = (os.environ.get("USERNAME") or os.environ.get("USER") or "").strip() or "Người dùng"
    return f"{name}, {at or now()}"


@dataclass
class Change:
    cell: str
    item: str
    before: object
    after: object
    reason: str
    agreed: str = AUTO
    source: str = ""
    at: str = field(default_factory=now)


@dataclass
class AuditLog:
    period: str = ""                       # "10/2026"
    original: str = ""                     # file name of the copy kept before the first change
    changes: list = field(default_factory=list)
    status: str = DRAFT
    dropped: list = field(default_factory=list)     # [what, file, amount, reason]
    coverage: list = field(default_factory=list)    # [file, holds, used, left out, check]

    def add(self, *args, **kwargs) -> Change:
        c = Change(*args, **kwargs)
        self.changes.append(c)
        return c


def status_note(status: str, period: str) -> str:
    if status == FINAL:
        return "Đã chuyển sang Chính thức."
    month = f" tháng {period}" if period else ""
    return (f"Chưa khóa sổ{month}. Sau khi kế toán trưởng duyệt, {you()} gõ \"chốt chính thức\" cho Clicky "
            "để đổi sang Chính thức.")


def grid(log: AuditLog) -> tuple:
    """(rows, places): places names the rows to style — status, the two lower tables' headers."""
    title = "BIÊN BẢN CLICKY · BÁO CÁO THÁNG" + (f" {log.period}" if log.period else "")
    kept = f" Bản gốc lưu ở {log.original}." if log.original else ""
    rows = [[title], ["Mọi thay đổi Clicky đã làm trên file này." + kept], [], list(HEADER)]
    for i, c in enumerate(log.changes, 1):
        rows.append([i, c.at, c.cell, c.item, c.before, c.after, c.reason, c.agreed, c.source])
    if not log.changes:
        rows.append(["", "", "", "Chưa có thay đổi nào", "", "", "", "", ""])
    rows.append([])
    places = {"status": len(rows) + 1}
    rows.append(["Trạng thái", log.status, "", status_note(log.status, log.period)])
    rows.append([])
    places["dropped"] = len(rows) + 1
    rows.append(["Những gì em bỏ ra ngoài", "File", "Số tiền", "Lý do"])
    rows += [list(r) for r in log.dropped] or [["Không bỏ dòng nào", "", "", ""]]
    rows.append([])
    places["coverage"] = len(rows) + 1
    rows.append(["Đối chiếu từng file", "File có", "Đã dùng", "Đã bỏ", "Kiểm: dùng + bỏ = file có"])
    rows += [list(r) for r in log.coverage]
    places["last"] = len(rows)
    places["changes"] = (FIRST_CHANGE_ROW, FIRST_CHANGE_ROW + max(len(log.changes), 1) - 1)
    return rows, places
