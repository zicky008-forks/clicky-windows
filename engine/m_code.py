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
        elif args is not None and len(args) >= 3 and _TYPED.search(args[1]):
            culture = re.fullmatch(r'\s*"([a-z]{2,3}-[A-Z]{2})"\s*', args[2])
            if culture:
                add("warning", "locale_fixed",
                    f"Đọc mọi file theo một kiểu cố định {culture.group(1)}. File nào ghi ngày hoặc số theo kiểu khác "
                    "(chi nhánh xuất từ máy cài đặt khác) sẽ bị đảo ngày tháng, báo lỗi, hoặc số bị đọc sai.")
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
        elif args is not None and len(args) >= 2:
            keys = re.findall(r'"((?:[^"]|"")*)"', args[1])
            if keys and not any(_is_doc_column(k) for k in keys):
                add("error", "distinct_keys",
                    f"Xoá trùng theo {', '.join(keys)}: hai chứng từ khác nhau có cùng các giá trị này (ví dụ cùng khách mua hai lần "
                    "trong một ngày) sẽ bị xoá mất một. Chỉ nên xoá trùng theo số chứng từ.")
        if re.search(r"\bTable\.(Skip|RemoveFirstN)\s*\([^,]+,\s*\d+\s*\)", e):
            add("warning", "fixed_skip",
                "Bỏ một số dòng đầu cố định. Nếu file xuất thêm hoặc bớt dòng tiêu đề, dữ liệu sẽ bị cắt lệch.")
        m = _PATH.search(e)
        if m:
            add("warning", "fixed_path",
                f"Đường dẫn cố định {m.group(2)}: máy khác hoặc khi đổi thư mục sẽ lỗi. Nên đưa đường dẫn vào một tham số.")
    return found


def fixed_culture(formula: str) -> Optional[str]:
    """The culture a query hardcodes for its type conversion ("en-US"), or None when it has none or reads it per file."""
    for s in steps(formula):
        args = _call_args(s.expr, "Table.TransformColumnTypes")
        if args is not None and len(args) >= 3 and _TYPED.search(args[1]):
            m = re.fullmatch(r'\s*"([a-z]{2,3}-[A-Z]{2})"\s*', args[2])
            if m:
                return m.group(1)
    return None


_DOC_WORDS = ("soct", "sochungtu", "sohd", "sohoadon", "sophieu", "invoice", "docno", "documentno", "machungtu", "id")


def _is_doc_column(name: str) -> bool:
    import unicodedata
    s = unicodedata.normalize("NFD", name.replace("đ", "d").replace("Đ", "D"))
    s = re.sub(r"[\s_\-./]", "", "".join(c for c in s if unicodedata.category(c) != "Mn")).lower()
    return any(w == s or (len(w) > 3 and w in s) for w in _DOC_WORDS)


def _m_text(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _m_sep(value: str) -> str:
    """A separator as an M text literal; a tab is written as #(tab) rather than a raw tab character."""
    return '"#(tab)"' if value == "\t" else _m_text(value)


def folder_query(folder: str, file_cultures: dict, typed: list, doc_column: Optional[str] = None,
                 folder_parameter: Optional[str] = None, delimiter=",",
                 key_columns: Optional[list] = None, period: Optional[tuple] = None) -> str:
    """M that combines CSV exports in a folder, reading each file's dates in that file's own format.

    file_cultures maps file name → culture ('vi-VN' / 'en-US'); files not listed use the
    first culture given. typed is [(column, M type)], e.g. [("Ngay", "type date")].
    delimiter is one separator for all files, or a dict file name → separator (each file's own).
    Rows are de-duplicated on the document number only, and no row is dropped for
    an error: a type error stays visible so it can be counted. key_columns replaces the
    document number as the de-duplication key: [branch, doc] when branches reuse numbers,
    or [] for whole identical rows only.
    """
    from collections import Counter
    default = Counter(file_cultures.values()).most_common(1)[0][0] if file_cultures else "vi-VN"
    source = folder_parameter or _m_text(folder)
    rules = " else ".join(f"if [Name] = {_m_text(n)} then {_m_text(c)}" for n, c in file_cultures.items())
    culture_expr = f"{rules} else {_m_text(default)}" if rules else _m_text(default)
    types = ", ".join("{" + _m_text(col) + ", " + t + "}" for col, t in typed)
    # One separator for every file, or each file's own when exports mix "," and ";".
    seps = delimiter if isinstance(delimiter, dict) else {}
    common = Counter(seps.values()).most_common(1)[0][0] if seps else (delimiter if isinstance(delimiter, str) else ",")
    if len(set(seps.values())) > 1:
        sep_rules = " else ".join(f"if [Name] = {_m_text(n)} then {_m_sep(d)}" for n, d in seps.items())
        sep_expr = f"{sep_rules} else {_m_sep(common)}"
    else:
        sep_expr = None
    lines = [
        "let",
        f"    Nguon = Folder.Files({source}),",
        '    LocCsv = Table.SelectRows(Nguon, each Text.Lower([Extension]) = ".csv"),',
        f"    DinhDang = Table.AddColumn(LocCsv, \"DinhDang\", each {culture_expr}),",
    ]
    previous = "DinhDang"
    if sep_expr:
        lines.append(f"    PhanCach = Table.AddColumn(DinhDang, \"PhanCach\", each {sep_expr}),")
        previous = "PhanCach"
    sep = "[PhanCach]" if sep_expr else _m_sep(common)
    # Folder.Files sees every CSV under the folder, a cost ledger export too. Only files that carry the
    # order columns are combined; typing another file's columns fails the whole load ("column not found").
    needed = [col for col, _ in typed] + ([doc_column] if doc_column and doc_column not in [c for c, _ in typed] else [])
    lines += [
        f"    DocFile = Table.AddColumn({previous}, \"Tho\", each "
        f"Table.PromoteHeaders(Csv.Document([Content], [Delimiter = {sep}, Encoding = 65001]), [PromoteAllScalars = true])),",
    ]
    previous = "DocFile"
    if needed:
        cols = ", ".join(_m_text(c) for c in needed)
        lines.append(f"    FileDonHang = Table.SelectRows(DocFile, each Table.HasColumns([Tho], {{{cols}}})),")
        previous = "FileDonHang"
    date_column = next((col for col, t in typed if t.strip() == "type date"), None)
    raw = "[Tho]"
    if date_column:
        # A footer ("Tổng", "Tổng cộng", a blank date) is not an order: drop it on the raw text, before the
        # date is typed, or typing "Tổng" as a date fails the whole load (robot run 07/11). Clicky's receipt
        # counts the same rows as dropped (sources.load_order_file: a date cell with no digit).
        raw = (f"Table.SelectRows([Tho], each Text.Select(Text.From(Record.FieldOrDefault(_, {_m_text(date_column)}, \"\") ?? \"\"), "
               f"{{\"0\"..\"9\"}}) <> \"\")")
    lines += [
        f"    DoiKieu = Table.AddColumn({previous}, \"Bang\", each Table.TransformColumnTypes({raw}, {{{types}}}, [DinhDang])),",
        "    GopFile = Table.Combine(DoiKieu[Bang]),",
    ]
    last = "GopFile"
    if date_column:
        # A date with digits that still fails to convert is an error, not null: it is kept and counted, and
        # comparing it must not fail the load either.
        lines.append(f"    BoDongKhongNgay = Table.SelectRows({last}, each try [{date_column}] <> null otherwise true),")
        last = "BoDongKhongNgay"
        if period:
            # Keep only the ledger's month; a date that failed to convert stays in, visible as an error.
            y, mth = period
            lines.append(f"    TrongKy = Table.SelectRows({last}, each try Date.Year([{date_column}]) = {int(y)} "
                         f"and Date.Month([{date_column}]) = {int(mth)} otherwise true),")
            last = "TrongKy"
    if key_columns is not None:
        keys = ", ".join(_m_text(k) for k in key_columns)
        lines.append(f"    XoaTrungTheoSoCT = Table.Distinct({last}" + (f", {{{keys}}})," if keys else "),"))
        last = "XoaTrungTheoSoCT"
    elif doc_column:
        lines.append(f"    XoaTrungTheoSoCT = Table.Distinct({last}, {{{_m_text(doc_column)}}}),")
        last = "XoaTrungTheoSoCT"
    lines[-1] = lines[-1].rstrip(",")
    lines += ["in", f"    {last}"]
    return "\n".join(lines)


def parameter_query(value: str) -> str:
    """M for a text parameter (Manage Parameters), so a folder path lives in one place."""
    return f'{_m_text(value)} meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]'


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
