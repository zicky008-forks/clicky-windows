"""Voice skills for Power Query.

"kiểm tra power query" checks every query in the open workbook and writes a findings sheet.
"sửa query theo định dạng en-US" adds that source format to type conversions that lack one,
after saving the old M. Only the user's own command triggers a change.
"""
import asyncio
import re

CHECK_TRIGGER = (r"(ki[ểe]m\s*tra|r[àa]\s*so[áa]t|soi|check|review)\b.{0,25}"
                 r"(power\s*query|query|truy\s*v[ấa]n)")
FIX_TRIGGER = r"(s[ửu]a|fix)\b.{0,40}(power\s*query|query|truy\s*v[ấa]n)"
# Spoken names for source formats; STT rarely produces "en-US" literally.
CULTURE_WORDS = [(r"\b(m[ỹy]|us|en-us|american)\b", "en-US"), (r"\b(vi[ệe]t(\s*nam)?|vi-vn|vn)\b", "vi-VN"),
                 (r"\b(anh\s+qu[ốo]c|uk|en-gb|british)\b", "en-GB")]
FINDINGS_SHEET = "Kiem tra PQ"


def findings_grid(findings: list) -> list:
    rows = [["Query", "Bước (Applied Steps)", "Mức độ", "Mã", "Nội dung", "Sửa tự động"]]
    for f in findings:
        rows.append([f.query, f.step, "Lỗi" if f.severity == "error" else "Lưu ý", f.code, f.message,
                     "Nói: sửa query theo kiểu Mỹ (hoặc kiểu Việt Nam)" if f.fix == "culture" else ""])
    if len(rows) == 1:
        rows.append(["", "", "OK", "", "Không thấy bẫy nào trong các query", ""])
    return rows


def summary(queries: list, findings: list) -> str:
    if not queries:
        return "File đang mở không có Power Query nào."
    errors = [f for f in findings if f.severity == "error"]
    if not findings:
        return f"Em kiểm tra {len(queries)} query, không thấy bẫy nào."
    worst = (errors or findings)[0]
    text = (f"Em kiểm tra {len(queries)} query, thấy {len(findings)} điểm cần xem, trong đó {len(errors)} lỗi. "
            f"Đáng lo nhất là query {worst.query}, bước {worst.step}: {worst.message}")
    if any(f.fix == "culture" for f in findings):
        text += " Nếu file nguồn dùng ngày kiểu Mỹ, anh nói: sửa query theo kiểu Mỹ."
    return text + f" Chi tiết ở sheet {FINDINGS_SHEET}."


def run_check(bridge) -> str:
    from engine import m_code
    from engine.report_sheet import quote
    from office.power_query import PowerQuery
    if not bridge.available():
        return "Anh mở file Excel có Power Query rồi gọi lại em."
    pq = PowerQuery(bridge)
    queries = pq.list()
    findings = [f for name, formula in queries for f in m_code.lint(name, formula)]
    if queries:
        sheet = bridge.add_sheet(FINDINGS_SHEET)
        if sheet:
            bridge.write(f"{quote(sheet)}!A1", findings_grid(findings))
    return summary(queries, findings)


def culture_from(transcript: str):
    m = re.search(r"\b([a-z]{2,3})-([a-z]{2})\b", transcript, re.IGNORECASE)
    if m:
        return f"{m.group(1).lower()}-{m.group(2).upper()}"
    for pattern, culture in CULTURE_WORDS:
        if re.search(pattern, transcript, re.IGNORECASE):
            return culture
    return None


def run_fix(bridge, transcript: str) -> str:
    from engine import m_code
    from office.power_query import PowerQuery
    culture = culture_from(transcript)
    if not culture:
        return "Anh nói rõ file nguồn dùng định dạng nào, ví dụ: sửa query theo kiểu Mỹ, hoặc theo kiểu Việt Nam."
    if not bridge.available():
        return "Anh mở file Excel có Power Query rồi gọi lại em."
    pq = PowerQuery(bridge)
    fixed = []
    for name, formula in pq.list():
        new, steps = m_code.add_culture(formula, culture)
        if steps and pq.replace(name, new):
            fixed.append(f"{name} (bước {', '.join(steps)})")
    if not fixed:
        return "Không có bước đổi kiểu nào thiếu định dạng nguồn, em không sửa gì."
    return (f"Em đã thêm định dạng {culture} vào {len(fixed)} query: {'; '.join(fixed)}. "
            "Bản M cũ được lưu trong power_query_backups.json. Anh bấm Refresh All để tải lại và kiểm tra số dòng.")


async def handle_check(manager, transcript: str) -> str:
    return await asyncio.to_thread(run_check, manager._excel)


async def handle_fix(manager, transcript: str) -> str:
    return await asyncio.to_thread(run_fix, manager._excel, transcript)


SKILLS = [
    {"name": "Sửa định dạng Power Query", "trigger": FIX_TRIGGER, "handler": handle_fix,
     "description": "Thêm định dạng nguồn (en-US, vi-VN...) cho các bước đổi kiểu ngày/số, lưu bản M cũ trước khi sửa."},
    {"name": "Kiểm tra Power Query", "trigger": CHECK_TRIGGER, "handler": handle_check,
     "description": "Soi mọi query trong file: ngày tháng đọc sai định dạng, xoá dòng lỗi âm thầm, gộp Inner, xoá trùng toàn cột, đường dẫn cố định."},
]
