"""Read Power Query M: split a query into its applied steps and flag the traps that quietly change results.

Pure text analysis, no Excel needed. Each finding names the step it is in so the
app can point at it, and the fixes are mechanical rewrites the user approves.
"""
import re
from dataclasses import dataclass
from typing import Optional

_OPEN, _CLOSE = "([{", ")]}"


@dataclass
class Step:
    name: str          # as shown in Applied Steps, e.g. Changed Type
    expr: str
    index: int         # 1-based position in Applied Steps


@dataclass
class Finding:
    query: str
    step: str
    severity: str      # "error" | "warning"
    code: str
    message: str
    fix: Optional[str] = None     # name of a mechanical fix, if one exists


def _top_level_split(text: str, sep: str = ","):
    """Split on sep outside strings, comments and brackets."""
    parts, depth, start, i, n = [], 0, 0, 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and not (text[i] == '"' and (i + 1 >= n or text[i + 1] != '"')):
                i += 2 if text[i] == '"' else 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        elif c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return parts


def _find_keyword(text: str, word: str, last: bool = False) -> int:
    """Position of a keyword outside strings/brackets, or -1."""
    depth, i, n, hit = 0, 0, len(text), -1
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and not (text[i] == '"' and (i + 1 >= n or text[i + 1] != '"')):
                i += 2 if text[i] == '"' else 1
        elif c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
        elif depth == 0 and re.match(rf"\b{word}\b", text[i:i + len(word) + 1]) and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in '_#."')):
            hit = i
            if not last:
                return hit
        i += 1
    return hit


def steps(formula: str) -> list:
    """Applied steps of a let … in query; a query without let is one step named Source."""
    body = formula.strip()
    let = _find_keyword(body, "let")
    if let != 0:
        return [Step("Source", body, 1)]
    end = _find_keyword(body, "in", last=True)
    inner = body[3:end if end > 0 else len(body)]
    out = []
    for part in _top_level_split(inner):
        if "=" not in part:
            continue
        name, expr = part.split("=", 1)
        name = re.sub(r"//[^\n]*|/\*.*?\*/", "", name, flags=re.S).strip()
        m = re.fullmatch(r'#"((?:[^"]|"")*)"', name)
        out.append(Step(m.group(1).replace('""', '"') if m else name, expr.strip(), len(out) + 1))
    return out


_TYPED = re.compile(r"\btype\s+(date|datetime|datetimezone|number|currency|Int64\.Type|Currency\.Type|Percentage\.Type)\b")
_PATH = re.compile(r'(File\.Contents|Folder\.Files|Folder\.Contents|Excel\.Workbook\(File\.Contents)\s*\(\s*"([A-Za-z]:\\[^"]*|\\\\[^"]*)"')


def _call_args(expr: str, func: str) -> Optional[list]:
    """Top-level arguments of the first call to func in expr."""
    m = re.search(rf"\b{re.escape(func)}\s*\(", expr)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(expr) and depth:
        c = expr[i]
        if c == '"':
            i += 1
            while i < len(expr) and expr[i] != '"':
                i += 1
        elif c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
        i += 1
    return [a.strip() for a in _top_level_split(expr[m.end():i - 1])]


def lint(query: str, formula: str) -> list:
    found = []
    for s in steps(formula):
        e = s.expr

        def add(severity, code, message, fix=None):
            found.append(Finding(query, s.name, severity, code, message, fix))

        args = _call_args(e, "Table.TransformColumnTypes")
        if args is not None and len(args) == 2 and _TYPED.search(args[1]):
            add("warning", "locale_types",
                "Đổi kiểu ngày/số mà không ghi định dạng nguồn, nên Power Query đọc theo cài đặt của máy đang mở file. "
                "File chi nhánh dùng ngày kiểu Mỹ (tháng/ngày) sẽ bị đảo ngày tháng hoặc báo lỗi, số có dấu phẩy thập phân có thể bị nhân lên.",
                fix="culture")
        args = _call_args(e, "Number.FromText")
        if args is not None and len(args) == 1:
            add("warning", "locale_number", "Number.FromText không ghi định dạng nguồn; '1.234' có thể thành 1,234 hoặc 1234 tuỳ máy.")
        if re.search(r"\bTable\.RemoveRowsWithErrors\b", e):
            add("error", "remove_errors",
                "Bước này xoá âm thầm mọi dòng bị lỗi. Doanh số của các dòng đó biến mất khỏi báo cáo mà không ai biết bao nhiêu dòng.")
        if re.search(r"\bTable\.ReplaceErrorValues\b", e):
            add("warning", "replace_errors", "Lỗi bị thay bằng giá trị khác; cần biết vì sao dòng đó lỗi trước khi thay.")
        if re.search(r"JoinKind\.Inner\b", e):
            add("warning", "inner_join",
                "Gộp bảng kiểu Inner: dòng không tìm thấy mã tương ứng bị bỏ đi. Nên dùng LeftOuter rồi lọc riêng các dòng không khớp để kiểm tra.")
        args = _call_args(e, "Table.Distinct")
        if args is not None and len(args) == 1:
            add("warning", "distinct_all",
                "Xoá trùng trên toàn bộ cột: hai đơn khác nhau nhưng giống hệt số liệu sẽ bị gộp làm một. Nên ghi rõ cột khoá như số chứng từ.")
        if re.search(r"\bTable\.(Skip|RemoveFirstN)\s*\([^,]+,\s*\d+\s*\)", e):
            add("warning", "fixed_skip",
                "Bỏ một số dòng đầu cố định. Nếu file xuất thêm hoặc bớt dòng tiêu đề, dữ liệu sẽ bị cắt lệch.")
        m = _PATH.search(e)
        if m:
            add("warning", "fixed_path",
                f"Đường dẫn cố định {m.group(2)}: máy khác hoặc khi đổi thư mục sẽ lỗi. Nên đưa đường dẫn vào một tham số.")
    return found


def add_culture(formula: str, culture: str) -> tuple:
    """Add a culture argument to every two-argument Table.TransformColumnTypes. Returns (new formula, steps changed)."""
    if not re.fullmatch(r"[a-z]{2,3}-[A-Z]{2}", culture):
        raise ValueError("culture must look like en-US or vi-VN")
    changed, out, i = [], [], 0
    pattern = re.compile(r"\bTable\.TransformColumnTypes\s*\(")
    step_names = {s.expr: s.name for s in steps(formula)}
    while True:
        m = pattern.search(formula, i)
        if not m:
            out.append(formula[i:])
            break
        args = _call_args(formula[m.start():], "Table.TransformColumnTypes")
        depth, j = 1, m.end()
        while j < len(formula) and depth:
            c = formula[j]
            if c == '"':
                j += 1
                while j < len(formula) and formula[j] != '"':
                    j += 1
            elif c in _OPEN:
                depth += 1
            elif c in _CLOSE:
                depth -= 1
            j += 1
        close = j - 1
        out.append(formula[i:close])
        if len(args) == 2 and _TYPED.search(args[1]):
            out.append(f', "{culture}"')
            changed.extend(n for e, n in step_names.items() if formula[m.start():close] in e)
        out.append(")")
        i = close + 1
    return "".join(out), changed
