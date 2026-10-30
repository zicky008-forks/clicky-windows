"""Voice skill: "lập báo cáo tháng" builds the monthly management report inside the open workbook.

It finds the two trial-balance sheets (this period and the one before it), adds
the report, questions-to-verify, check and cleaned trial-balance sheets with live formulas, and speaks
the conclusion. It only adds new sheets; the user's own sheets are never changed.
"""
import asyncio

TRIGGER = (r"(l[ậa]p|l[àa]m|t[ạa]o|d[ựu]ng|xu[ấa]t)\b.{0,20}b[áa]o\s+c[áa]o.{0,30}\bth[áa]ng\b"
           r"|\b(build|make|create)\b.{0,20}\bmonthly\s+report\b")
OUTPUT_PREFIXES = ("TB ky nay", "TB ky truoc", "BC thang", "Cau hoi xac minh", "Kiem tra")
NEED_TWO = ("Anh mở file Excel có hai sheet cân đối phát sinh, kỳ này và kỳ trước, "
            "rồi gọi lại em. Em tìm thấy {n} sheet đọc được.")


def find_trial_balances(bridge) -> list:
    """(sheet name, rows) for every sheet that reads as a trial balance, in tab order."""
    from engine.trial_balance import read_trial_balance
    found = []
    for name in bridge.sheet_names():
        if name.startswith(OUTPUT_PREFIXES):
            continue
        rows = bridge.sheet_rows(name)
        if not rows:
            continue
        try:
            if read_trial_balance(rows, name).lines:
                found.append((name, rows))
        except ValueError:
            continue
    return found


def pick_periods(found: list, active_sheet: str):
    """The active sheet is this period when it is a trial balance, else the last one; the previous is the one before it."""
    names = [n for n, _ in found]
    i = names.index(active_sheet) if active_sheet in names else len(found) - 1
    return found[i], found[i - 1] if i > 0 else found[1]


def run(bridge) -> str:
    from engine.build import SHEET_NAMES, build
    from engine.report_sheet import quote
    if not bridge.available():
        return NEED_TWO.format(n=0)
    found = find_trial_balances(bridge)
    if len(found) < 2:
        return NEED_TWO.format(n=len(found))
    ctx = bridge.context() or {}
    (cur_name, cur_rows), (prev_name, prev_rows) = pick_periods(found, ctx.get("sheet", ""))
    names = {}
    for key in ("report", "questions", "checks", "tb_cur", "tb_prev"):
        actual = bridge.add_sheet(SHEET_NAMES[key])
        if not actual:
            return "Em không thêm được sheet mới vào file này. Anh kiểm tra file có đang bị khoá hoặc chỉ đọc không."
        names[key] = actual
    report = build(cur_rows, prev_rows, cur_name, prev_name, text_prefix="'", names=names)
    for name, grid in report.sheets.items():
        if not bridge.write(f"{quote(name)}!A1", grid):
            return f"Em tính xong nhưng ghi vào sheet {name} bị lỗi. Kết quả: {report.summary}"
    last = 4 + len(report.figures)
    bridge.format_numbers(f"{quote(names['report'])}!C5:E{last}")
    bridge.format_numbers(f"{quote(names['report'])}!F5:F{last}", "0.0%")
    bridge.format_numbers(f"{quote(names['questions'])}!B2:B{len(report.sheets[names['questions']])}")
    for key in ("tb_cur", "tb_prev"):
        bridge.format_numbers(f"{quote(names[key])}!C2:H{len(report.sheets[names[key]])}")
    bridge.activate(names["report"])
    return report.summary


async def handle(manager, transcript: str) -> str:
    return await asyncio.to_thread(run, manager._excel)


SKILL = {
    "name": "Báo cáo quản trị tháng",
    "trigger": TRIGGER,
    "description": "Lập báo cáo KQKD tháng này so với tháng trước từ hai sheet cân đối phát sinh, có công thức truy nguồn và kiểm bẫy.",
    "handler": handle,
}
