"""Pass/fail rules for one rehearsal, from Clicky's scene log and the numbers left in the workbook.

Pure functions only, so they run in the test suite on any machine.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    blocking: bool = True        # a non-blocking check is reported but does not fail the run


@dataclass
class Result:
    checks: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks if c.blocking)


def read_log(path: Path) -> list:
    events = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except ValueError:
                continue        # a half-written last line while Clicky is still talking
    return events


def find_value(rows: list, label: str):
    """First number to the right of the first cell whose text contains `label` (case-insensitive)."""
    want = label.casefold()
    for row in rows or []:
        cells = list(row or [])
        for i, cell in enumerate(cells):
            if isinstance(cell, str) and want in cell.casefold():
                for right in cells[i + 1:]:
                    if isinstance(right, bool):
                        continue
                    if isinstance(right, (int, float)):
                        return right
                # A title or note that merely mentions the label: keep looking for the real line.
    return None


def text_found(text: str, where: str, sheets: dict, events: list, sheet_hint: str = "") -> bool:
    """Is `text` (case-insensitive) in the sheets, in what Clicky showed or said, or either."""
    want = text.casefold()
    if where in ("sheets", "any"):
        for name, rows in sheets.items():
            if sheet_hint and sheet_hint.casefold() not in name.casefold():
                continue
            for row in rows or []:
                if any(isinstance(c, str) and want in c.casefold() for c in row or []):
                    return True
    if where in ("said", "any"):
        for e in events:
            for key in ("text", "title", "value", "note", "question"):
                v = e.get(key)
                if isinstance(v, str) and want in v.casefold():
                    return True
    return False


def find_in_sheets(sheets: dict, label: str, sheet_hint: str = ""):
    """Search the hinted sheet first, then every sheet."""
    order = [n for n in sheets if sheet_hint and sheet_hint.casefold() in n.casefold()]
    order += [n for n in sheets if n not in order]
    for name in order:
        value = find_value(sheets[name], label)
        if value is not None:
            return name, value
    return None, None


def _same_box(shape: dict, box: list, tol: float = 2.5) -> bool:
    try:
        got = [shape["x1"], shape["y1"], shape["x2"], shape["y2"]]
    except KeyError:
        return False
    return all(abs(a - b) <= tol for a, b in zip(got, box))


_TECH_ERROR = re.compile(
    r"Exception occurred|\bException\b|Traceback|\[\w+\.Error\]|\bError:|com_error|pywintypes|-21474\d{5}"
    r"|0x800[0-9A-Fa-f]{5}", re.IGNORECASE)
_SHOWN_KEYS = ("text", "title", "value", "note", "question", "was")


def leaked_errors(events: list) -> list:
    """Technical error text shown or said to the user (COM, Power Query, Python), from any visible event."""
    found = []
    for e in events:
        if e.get("event") not in ("beat", "said", "notice", "choices", "card"):
            continue
        for key in _SHOWN_KEYS:
            v = e.get(key)
            if isinstance(v, str) and _TECH_ERROR.search(v) and v not in found:
                found.append(v)
    return found


def stage_check(stalled: list) -> Check:
    """A stage that ran out of time is a failure on its own, not something to infer from wrong numbers."""
    return Check("Không chặng nào bị đứng quá giờ", not stalled,
                 "; ".join(f"{name}: hết {secs:.0f} giây mà chưa xong" for name, secs in stalled) if stalled
                 else "mọi chặng xong trong giờ")


def evaluate(events: list, sheets: dict, expected: dict) -> Result:
    res = Result()
    add = res.checks.append

    turns = [e for e in events if e.get("event") == "turn"]
    beats = [e for e in events if e.get("event") == "beat"]
    add(Check("Clicky nhận lệnh và bắt đầu dẫn", bool(turns) and bool(beats),
              f"{len(turns)} lệnh, {len(beats)} cảnh" if turns else "Không thấy lệnh nào tới Clicky"))

    # Every scene that names a cell must show it: a missing box is the run-2 bug (drawings silently dropped).
    missing = [b.get("ref") for b in beats if b.get("ref") and not b.get("box")]
    add(Check("Cảnh nào có ô thì ô đó hiện trên màn hình", not missing,
              "Không tìm được vị trí: " + ", ".join(map(str, missing[:5])) if missing else
              f"{sum(1 for b in beats if b.get('box'))} cảnh có khung"))

    # The lit range must be exactly where Excel says the cell is.
    off = []
    for idx, ev in enumerate(events):
        if ev.get("event") != "beat" or not ev.get("box"):
            continue
        box = ev["box"]
        spot = None
        for later in events[idx + 1:]:
            if later.get("event") == "beat":
                break
            shape = later.get("shape") or {}
            if later.get("event") == "draw" and shape.get("kind") == "spotlight":
                spot = shape
                break
        if spot is None or not _same_box(spot, box):
            off.append(ev.get("ref"))
    add(Check("Khung sáng nằm đúng ô Excel", not off,
              "Lệch hoặc không vẽ: " + ", ".join(map(str, off[:5])) if off else "Mọi khung khớp vị trí Excel trả về"))

    spoken = [e for e in events if e.get("event") == "spoken"]
    silent = [e for e in spoken if e.get("spoke") is False]
    notices = [e for e in events if e.get("event") == "notice"]
    if expected.get("voice", "text_ok") == "required":
        add(Check("Có giọng đọc", bool(spoken) and not silent,
                  f"{len(silent)}/{len(spoken)} câu không đọc được" if silent else f"{len(spoken)} câu đều đọc"))
    else:
        ok = not silent or len(notices) >= 1
        add(Check("Giọng đọc, hoặc chỉ chữ kèm đúng một lời báo", ok and len(notices) <= 3,
                  f"{len(spoken) - len(silent)}/{len(spoken)} câu đọc thành tiếng, {len(notices)} lời báo"))

    errors = [e.get("text", "") for e in events if e.get("event") == "error"]
    # A raw technical error can also slip out inside a card, a spoken line or a notice, not only as an error event.
    errors += leaked_errors(events)
    add(Check("Không có lỗi hiện ra cho người dùng", not errors, "; ".join(tidy(x)[:120] for x in errors[:3])))

    fix = expected.get("fix_label")
    if fix:
        offered = any(fix in (e.get("labels") or []) for e in events if e.get("event") == "choices")
        add(Check(f"Có nút \"{fix}\"", offered, "" if offered else "Không thấy nút sửa"))
        chosen = any(e.get("label") == fix for e in events if e.get("event") == "chosen")
        add(Check(f"Đã bấm \"{fix}\"", chosen, "" if chosen else "Robot không bấm được nút"))

    for item in expected.get("numbers", []):
        sheet, value = find_in_sheets(sheets, item["label"], item.get("sheet", ""))
        want, tol = item["value"], item.get("tolerance", 0)
        got = abs(value) if value is not None and item.get("abs") else value      # "Trừ hàng trả lại" may be negative
        ok = got is not None and abs(got - want) <= tol
        shown = "không tìm thấy" if value is None else f"{value:,.0f} (sheet {sheet})"
        add(Check(item["name"], ok, f"Cần {want:,.0f}, thấy {shown}"))

    # Warnings that must appear, and false alarms that must not, checked without revealing any number.
    for item in expected.get("texts", []):
        must = item.get("must", True)
        seen = text_found(item["text"], item.get("where", "any"), sheets, events, item.get("sheet", ""))
        add(Check(item["name"], seen == must,
                  ("Có" if seen else "Không có") + f" \"{item['text']}\"" + ("" if seen == must else
                  (" (cần có)" if must else " (không được có)"))))

    limit = expected.get("max_seconds")
    if limit and turns:
        end = max((e["t"] for e in events if e.get("event") in ("beat", "spoken")), default=turns[0]["t"])
        took = end - turns[0]["t"]
        add(Check(f"Xong trong {limit} giây", took <= limit, f"{took:.0f} giây", blocking=False))
    return res


def report_md(result: Result, meta: dict) -> str:
    verdict = "ĐẠT" if result.passed else "KHÔNG ĐẠT"
    lines = [f"# Diễn tập {meta.get('case', '')}: {verdict}", "",
             f"Bản Clicky: {meta.get('clicky', '?')} · Lúc: {meta.get('when', '?')} · Robot: {meta.get('driver', '?')}", "",
             "| Kiểm tra | Kết quả | Chi tiết |", "| --- | --- | --- |"]
    for c in result.checks:
        mark = "Đạt" if c.ok else ("Chưa đạt" if c.blocking else "Lưu ý")
        lines.append(f"| {c.name} | {mark} | {c.detail.replace('|', '/')} |")
    steps = meta.get("steps") or []
    if steps:
        lines += ["", "| Bước | Giây |", "| --- | --- |"] + [f"| {s} | {t:.1f} |" for s, t in steps]
    lines += ["", "Số liệu giả lập. Nhật ký cảnh và file kết quả chỉ nằm trên máy này."]
    return "\n".join(lines) + "\n"


_WS = re.compile(r"\s+")


def tidy(text: str) -> str:
    return _WS.sub(" ", text or "").strip()
