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


# ── Order files of any format: one reader per file, the engine's own rules written into the M ─────────────────────

# The names the combined table gives each role. sources.find_columns recognises every one of them, so the loaded
# table is read back the same way whatever each file called its columns.
CANON = {"doc": "Số CT", "date": "Ngày", "amount": "Thành tiền", "cost": "Giá vốn", "branch": "Chi nhánh",
         "customer": "Mã KH", "item": "Mã hàng", "status": "Trạng thái"}
SOURCE_COLUMN = "Tệp nguồn"
_ROLE_ORDER = ("doc", "date", "amount", "cost", "branch", "customer", "item", "status")
_SS_NS = "urn:schemas-microsoft-com:office:spreadsheet"


def _m_list(values) -> str:
    return "{" + ", ".join(_m_text(v) for v in values) + "}"


def _m_field(name: str) -> str:
    return '#"' + name.replace('"', '""') + '"'


def _fold_table() -> str:
    """trial_balance.fold as an M record: each accented lower-case letter to its plain one, combining marks to ""."""
    from engine.trial_balance import fold
    pairs = {}
    for cp in list(range(0xC0, 0x250)) + list(range(0x1E00, 0x1F00)):
        c = chr(cp).lower()
        if len(c) == 1:
            f = fold(c)
            if f != c and len(f) == 1:
                pairs[c] = f
    for cp in range(0x300, 0x370):                 # a decomposed accent (text from a Mac) folds away
        pairs[chr(cp)] = ""
    return "[" + ", ".join(f"{_m_field(c)} = {_m_text(f)}" for c, f in sorted(pairs.items())) + "]"


def _role_names() -> tuple:
    """(first-tier names, ranked extra names) as M records, straight from engine.sources so the two cannot drift."""
    from engine import sources
    first = {"doc": sources.DOC_COLS, "date": sources.DATE_COLS, "amount": sources.AMOUNT_COLS,
             "cost": sources.COST_COLS, "branch": sources.BRANCH_COLS, "customer": sources.CUSTOMER_COLS,
             "item": sources.ITEM_COLS}
    tier1 = "[" + ", ".join(f"{_m_field(r)} = {_m_list(sorted(first[r]))}" for r in _ROLE_ORDER if r in first) + "]"
    tier2 = "[" + ", ".join(f"{_m_field(r)} = {_m_list(sources.MORE_COLS[r])}" for r in _ROLE_ORDER
                            if r in sources.MORE_COLS) + "]"
    return tier1, tier2


_ORDER_M = r"""let
    // Clicky: đọc mọi file đơn hàng trong thư mục theo đúng nội dung file (CSV, .xls thật, HTML hay XML lưu
    // thành .xls), cùng luật với phiếu nhận file của Clicky: tìm dòng tiêu đề, bỏ dòng tổng và ký tên, để riêng đơn huỷ.
    ChuSo = {"0".."9"},
    KhoangTrang = {" ", "#(tab)", "#(cr)", "#(lf)", Character.FromNumber(160), Character.FromNumber(65279)},
    BangDau = @@FOLD@@,
    BoDau = (t as text) as text => Text.Combine(List.Transform(Text.ToList(Text.Lower(t)), each Record.FieldOrDefault(BangDau, _, _))),
    GomTrang = (t as text) as text => Text.Combine(List.Select(Text.SplitAny(t, Text.Combine(KhoangTrang)), each _ <> ""), " "),
    Khoa = (t as text) as text => Text.Remove(BoDau(t), KhoangTrang & {"_", "-", ".", "/"}),
    Chu = (v) as text => if v = null then "" else if v is text then Text.Trim(v, KhoangTrang)
        else if v is date then Date.ToText(v, "yyyy-MM-dd") else if v is datetime then DateTime.ToText(v, "yyyy-MM-dd HH:mm:ss")
        else if v is logical then (if v then "TRUE" else "FALSE") else Text.From(v, "en-US"),
    SoDau = (t as text) as text => Text.Combine(List.FirstN(Text.ToList(t), each List.Contains(ChuSo, _))),
    CoChuSo = (v) as logical => if v = null or v is logical then false else if v is text then Text.Select(v, ChuSo) <> "" else true,
    // sources.parse_date: 2026-10-13, or 13/10/2026 (day first) or 10/13/2026 (month first, thu = "en-US").
    DocNgay = (v, thu as text) => if v = null then null else if v is date then v else if v is datetime then DateTime.Date(v)
        else if v is datetimezone then DateTime.Date(DateTimeZone.RemoveZone(v)) else if not (v is text) then null else
        let
            t = Text.TrimStart(v, KhoangTrang),
            g1 = SoDau(t), r1 = Text.Range(t, Text.Length(g1)), s1 = Text.Start(r1, 1),
            r2 = Text.Range(r1, Text.Length(s1)), g2 = SoDau(r2), r3 = Text.Range(r2, Text.Length(g2)), s2 = Text.Start(r3, 1),
            g3 = SoDau(Text.Range(r3, Text.Length(s2))),
            vach = {"/", ".", "-"},
            iso = Text.Length(g1) = 4 and s1 = "-" and List.Contains({1, 2}, Text.Length(g2)) and s2 = "-" and Text.Length(g3) >= 1,
            xet = List.Contains({1, 2}, Text.Length(g1)) and List.Contains(vach, s1) and List.Contains({1, 2}, Text.Length(g2))
                and List.Contains(vach, s2) and Text.Length(g3) >= 4,
            a = if iso or xet then Number.FromText(g1, "en-US") else null,
            b = if iso or xet then Number.FromText(g2, "en-US") else null,
            c = if iso then Number.FromText(Text.Start(g3, 2), "en-US") else if xet then Number.FromText(Text.Start(g3, 4), "en-US") else null
        in
            if iso then (try #date(a, b, c) otherwise null)
            else if xet then (try (if thu = "en-US" then #date(c, a, b) else #date(c, b, a)) otherwise null)
            else null,
    LaNgay = (v) as logical => DocNgay(v, "vi-VN") <> null or DocNgay(v, "en-US") <> null,
    // trial_balance.parse_amount: 1.250.000,5 / 1,250,000.5 / (1.000) / - / blank.
    Nhom = (t as text, nghin as text, le as text) as logical =>
        let
            nguyen = if Text.Contains(t, le) then Text.BeforeDelimiter(t, le) else t,
            phan = if Text.Contains(t, le) then Text.AfterDelimiter(t, le) else null,
            khoi = Text.Split(nguyen, nghin),
            so = (x) => x <> "" and Text.Select(x, ChuSo) = x
        in
            List.Count(khoi) > 1 and List.Contains({1, 2, 3}, Text.Length(khoi{0})) and so(khoi{0})
            and List.AllTrue(List.Transform(List.Skip(khoi, 1), each Text.Length(_) = 3 and so(_)))
            and (phan = null or so(phan)),
    DocSo = (v) => if v = null then null else if v is number then v else if not (v is text) then null else
        let
            s0 = Text.Remove(v, KhoangTrang),
            am = (Text.StartsWith(s0, "(") and Text.EndsWith(s0, ")")) or Text.StartsWith(s0, "-"),
            s1 = Text.Trim(s0, {"(", ")", "-"}),
            s2 = if Nhom(s1, ".", ",") then Text.Replace(Text.Remove(s1, "."), ",", ".")
                 else if Nhom(s1, ",", ".") then Text.Remove(s1, ",") else s1,
            n = if Text.Contains(s2, ",") then null else try Number.FromText(s2, "en-US") otherwise null
        in
            if List.Contains({"", "-", "–"}, s0) then 0 else if n = null then null else if am then -n else n,
    Huy = (v) as logical => List.Contains(@@CANCELLED@@, Text.Trim(GomTrang(BoDau(Chu(v))), {" ", "."})),
    // sources.find_columns: the first-tier names, leftmost column first; the extra names only fill an empty role.
    Nho = @@REMEMBERED@@,
    Ten1 = @@TIER1@@,
    Ten2 = @@TIER2@@,
    // sources._role_key: a unit after the name ("Số tiền (VND)") is taken off before the names are looked up.
    DonVi = @@UNITS@@,
    BoDonVi = (k as text) as text =>
        let u = List.Select(DonVi, each Text.EndsWith(k, _) and Text.Length(k) > Text.Length(_))
        in if List.IsEmpty(u) then k else Text.Start(k, Text.Length(k) - Text.Length(u{0})),
    TimCot = (tieuDe as list) as record =>
        let
            k0 = List.Transform(tieuDe, each Khoa(Chu(_))),
            k = List.Transform(k0, BoDonVi),
            mot = List.Accumulate(Record.FieldNames(Ten1), [], (st, r) =>
                let i = List.PositionOfAny(k, Record.Field(Ten1, r)) in if i >= 0 then Record.AddField(st, r, i) else st),
            hai = List.Accumulate(Record.FieldNames(Ten2), mot, (st, r) =>
                if Record.HasFields(st, r) then st else
                let hit = List.Select(List.Transform(Record.Field(Ten2, r), each List.PositionOf(k, _)), each _ >= 0)
                in if List.IsEmpty(hit) then st else Record.AddField(st, r, hit{0})),
            // sources.columns_of: when no name gives a date and an amount, the columns chị named for this header.
            dau = List.Count(List.FirstN(k0, each _ = "")),
            duoi = List.Count(List.FirstN(List.Reverse(k0), each _ = "")),
            chuKy = if dau = List.Count(k0) then "" else Text.Combine(List.Range(k0, dau, List.Count(k0) - dau - duoi), "|"),
            nho = Record.FieldOrDefault(Nho, chuKy, []),
            them = List.Accumulate(Record.FieldNames(nho), hai, (st, r) =>
                let i = List.PositionOf(k0, Record.Field(nho, r)) in if i >= 0 then Record.Combine({st, Record.AddField([], r, i)}) else st)
        in if Record.HasFields(hai, {"date", "amount"}) or not Record.HasFields(them, {"date", "amount"}) then hai else them,
    O = (r as list, c as record, vai as text) => let i = Record.FieldOrDefault(c, vai, null) in
        if i = null or i >= List.Count(r) then null else r{i},
    // engine.tabular: what the bytes are, not what the name says.
    Dau = (b as binary, n as number) as binary => Binary.Range(b, 0, List.Min({n, Binary.Length(b)})),
    MaHoa = (b as binary, macDinh as number) as number =>
        let d = Binary.ToList(Dau(b, 2)) in if d = {255, 254} then 1200 else if d = {254, 255} then 1201 else macDinh,
    Loai = (b as binary, ext as text, maHoa as number) as text => if ext = ".csv" then "csv" else
        let
            d = Binary.ToList(Dau(b, 8)),
            dau = Text.Lower(Text.TrimStart(Text.FromBinary(Dau(b, 4096), MaHoa(b, maHoa)), KhoangTrang)),
            co = (x) => Text.Contains(dau, x)
        in
            if d = {208, 207, 17, 224, 161, 177, 26, 225} then "xls"
            else if List.FirstN(d, 4) = {80, 75, 3, 4} then "xlsx"
            else if Text.StartsWith(dau, "<?xml") or Text.StartsWith(dau, "<workbook") then
                (if Text.Contains(Text.FromBinary(Dau(b, 65536), MaHoa(b, maHoa)), @@SSNS@@) then "xml"
                 else if co("<html") or co("<table") then "html" else "xml")
            else if Text.StartsWith(dau, "<") and List.AnyTrue(List.Transform({"<html", "<table", "<body", "<head", "<meta", "<!doctype html"}, co)) then "html"
            else if List.Contains(Binary.ToList(Dau(b, 4096)), 0) and MaHoa(b, maHoa) = maHoa then "binary"
            else "text",
    // A .csv as sources.read_csv: the separator judged on the first non-blank line. Other text as engine.tabular:
    // the separator that the line with the most of it has most of, among the first 30 lines.
    DocChu = (b as binary, maHoa as number, csv as logical) as list =>
        let
            dong = List.Transform(Lines.FromBinary(b, QuoteStyle.None, false, MaHoa(b, maHoa)), each Text.Replace(_, Character.FromNumber(65279), "")),
            co = List.Select(dong, each Text.Trim(_, KhoangTrang) <> ""),
            dem = (line as text, d as text) as number => List.Count(Text.Split(line, d)) - 1,
            dau = if List.IsEmpty(co) then "" else co{0},
            mau = Text.Start(Text.Combine(dong, "#(lf)"), 4096),
            theoDau = List.Transform({",", ";", "#(tab)"}, each dem(dau, _)),
            theoMau = List.Transform({",", ";", "#(tab)"}, each dem(mau, _)),
            ba = List.Transform({"#(tab)", ";", ","}, (d) => List.Max(List.Transform(List.FirstN(co, 30), each dem(_, d)) & {0})),
            phanCach = if csv then (if List.Max(theoDau) > 0 then {",", ";", "#(tab)"}{List.PositionOf(theoDau, List.Max(theoDau))}
                                    else {",", ";", "#(tab)"}{List.PositionOf(theoMau, List.Max(theoMau))})
                       else (if List.Max(ba) > 0 then {"#(tab)", ";", ","}{List.PositionOf(ba, List.Max(ba))} else ","),
            tach = Splitter.SplitTextByDelimiter(phanCach, QuoteStyle.Csv)
        in {List.Transform(dong, each List.Transform(tach(_), (x) => Text.Trim(x, KhoangTrang)))},
    // An .xlsx lists sheets, tables and names with a Kind. A real .xls (Excel 97-2003) comes back with no Kind
    // column on some PCs (robot 07/11): its sheets are the names ending in "$" ("$'" when the name has a space and is
    // quoted), any other row is a defined name.
    DocExcel = (b as binary) as list =>
        let
            so = Excel.Workbook(b, null, true),
            to = if Table.HasColumns(so, "Kind") then Table.SelectRows(so, each [Kind] = "Sheet")
                 else let la = Table.SelectRows(so, each let n = Text.From([Name]) in Text.EndsWith(n, "$") or Text.EndsWith(n, "$'"))
                      in if Table.IsEmpty(la) then so else la
        in List.Transform(to[Data], Table.ToRows),
    // HTML as engine.tabular._Tables: every <table>, rows <tr>, cells <td>/<th>, colspan as blank cells after.
    Cat = (t as text, the as text) as list =>
        let
            vt = Text.PositionOf(Text.Lower(t), the, Occurrence.All),
            dau = {0} & List.Transform(vt, each _ + Text.Length(the))
        in List.Transform(List.Positions(dau), (i) => Text.Range(t, dau{i}, (if i < List.Count(vt) then vt{i} else Text.Length(t)) - dau{i})),
    TruocThe = (t as text, the as list) as text =>
        let vt = List.Select(List.Transform(the, each Text.PositionOf(Text.Lower(t), _)), each _ >= 0)
        in if List.IsEmpty(vt) then t else Text.Start(t, List.Min(vt)),
    BoThe = (t as text) as text =>
        let p = Text.Split(Text.Replace(Text.Replace(t, "<br", " <br"), "<BR", " <BR"), "<")
        in p{0} & Text.Combine(List.Transform(List.Skip(p, 1), each if Text.Contains(_, ">") then Text.AfterDelimiter(_, ">") else "<" & _)),
    KyTu = (t as text) as text =>
        let
            ten = List.Accumulate({{"&nbsp;", " "}, {"&lt;", "<"}, {"&gt;", ">"}, {"&quot;", @@QUOTE@@}, {"&#39;", "'"}, {"&apos;", "'"}}, t,
                (s, x) => Text.Replace(s, x{0}, x{1})),
            p = Text.Split(ten, "&#"),
            so = (x as text) => let hex = Text.StartsWith(x, "x") or Text.StartsWith(x, "X"), body = if hex then Text.Range(x, 1) else x,
                     d = if hex then Text.Combine(List.FirstN(Text.ToList(body), each Text.Contains("0123456789abcdefABCDEF", _))) else SoDau(body)
                 in if d <> "" and Text.StartsWith(Text.Range(body, Text.Length(d)), ";")
                    then Character.FromNumber(if hex then List.Accumulate(Text.ToList(Text.Lower(d)), 0, (n, ch) => n * 16 + Text.PositionOf("0123456789abcdef", ch)) else Number.FromText(d, "en-US")) & Text.Range(body, Text.Length(d) + 1)
                    else "&#" & x
        in Text.Replace(p{0} & Text.Combine(List.Transform(List.Skip(p, 1), so)), "&amp;", "&"),
    O_Html = (manh as text) as list =>
        let
            thuocTinh = Text.Lower(Text.BeforeDelimiter(manh, ">")),
            sau = Text.AfterDelimiter(Text.AfterDelimiter(thuocTinh, "colspan"), "="),
            soCot = SoDau(Text.TrimStart(sau, {" ", @@QUOTE@@, "'"})),
            n = if Text.Contains(thuocTinh, "colspan") and soCot <> "" and Number.FromText(soCot, "en-US") > 0 and Number.FromText(soCot, "en-US") < 200 then Number.FromText(soCot, "en-US") else 1,
            chu = Text.Trim(GomTrang(KyTu(BoThe(TruocThe(Text.AfterDelimiter(manh, ">"), {"</td", "</th", "</tr", "</table"})))), KhoangTrang)
        in {chu} & List.Repeat({""}, n - 1),
    DocHtml = (b as binary, maHoa as number) as list =>
        let
            t = Text.FromBinary(b, MaHoa(b, maHoa)),
            bang = List.Transform(List.Skip(Cat(t, "<table"), 1), each TruocThe(_, {"</table"})),
            o = (dong as text) as list => List.Select(List.Skip(List.Combine(List.Transform(Cat(dong, "<th"), each Cat(_, "<td"))), 1), each
                    List.Contains({">", " ", "#(tab)", "#(cr)", "#(lf)", "/"}, Text.Start(_, 1))),
            hang = (tb as text) as list => List.Transform(List.Skip(Cat(tb, "<tr"), 1), each List.Combine(List.Transform(o(_), O_Html)))
        in List.Transform(bang, hang),
    // SpreadsheetML 2003 as engine.tabular._xml: Worksheet / Table / Row / Cell, ss:Index and ss:MergeAcross kept.
    Con = (t as table, ten as text) as list => Table.SelectRows(t, each [Name] = ten)[Value],
    ChuXml = (v) as text => if v is text then v else if v is table then Text.Combine(List.Transform(Table.Column(v, "Value"), ChuXml)) else "",
    TT = (o, ten as text) => let a = if o is table then Table.SelectRows(o, each [Name] = ten and [Namespace] = @@SSNS@@) else null
        in if a = null or Table.IsEmpty(a) then null else a{0}[Value],
    DongXml = (row as table) as list => List.Accumulate(Table.ToRecords(Table.SelectRows(row, each [Name] = "Cell")), {}, (st, cell) =>
        let
            at = TT(cell[Attributes], "Index"),
            gop = TT(cell[Attributes], "MergeAcross"),
            truoc = if at <> null and at <> "" and Text.Select(at, ChuSo) = at then st & List.Repeat({""}, List.Max({0, Number.FromText(at, "en-US") - 1 - List.Count(st)})) else st,
            data = if cell[Value] is table then Table.SelectRows(cell[Value], each [Name] = "Data") else #table({"Value"}, {}),
            chu = if Table.IsEmpty(data) then "" else Text.Trim(GomTrang(ChuXml(data{0}[Value])), KhoangTrang),
            them = if gop <> null and gop <> "" and Text.Select(gop, ChuSo) = gop then List.Repeat({""}, List.Min({Number.FromText(gop, "en-US"), 200})) else {}
        in truoc & {chu} & them),
    DocXml = (b as binary) as list =>
        let
            goc = Xml.Document(b),
            so = List.Combine(List.Transform(goc[Value], each if _ is table then Con(_, "Worksheet") else {})),
            bang = (ws) => List.Combine(List.Transform(Con(ws, "Table"), (t) => List.Transform(Con(t, "Row"), each if _ is table then DongXml(_) else {})))
        in List.Transform(so, bang),
    Trong = (r as list) as logical => not List.AnyTrue(List.Transform(r, each Chu(_) <> "")),
    // sources.load_order_file, on one file.
    DocDon = (b as binary, ten as text, ext as text) as list =>
        let
            p = Record.FieldOrDefault(ThamSo, ten, MacDinh),
            loai = Loai(b, ext, p[ma_hoa]),
            ung = if loai = "csv" then DocChu(b, p[ma_hoa], true) else if loai = "text" then DocChu(b, p[ma_hoa], false)
                  else if loai = "xls" or loai = "xlsx" then DocExcel(b) else if loai = "html" then DocHtml(b, p[ma_hoa])
                  else if loai = "xml" then DocXml(b) else {},
            sach = List.Buffer(List.Transform(ung, each List.Select(_, (r) => not Trong(r)))),
            tieuDe = (rows as list) as number => List.First(List.Select(List.Positions(List.FirstN(rows, @@SCAN@@)), each
                         Record.HasFields(TimCot(rows{_}), {"date", "amount"}) and List.Count(rows) > _ + 1), -1),
            chon = List.First(List.Select(sach, each tieuDe(_) >= 0), null),
            h = if chon = null then -1 else tieuDe(chon),
            c = if h < 0 then [] else TimCot(chon{h}),
            than = if h < 0 then {} else List.Skip(chon, h + 1),
            coNgay = List.Select(than, each CoChuSo(O(_, c, "date"))),
            cuoi = List.Last(List.Select(List.Positions(coNgay), each LaNgay(O(coNgay{_}, c, "date"))), -1),
            inRa = if loai <> "csv" or h > 0 then List.FirstN(coNgay, cuoi + 1) else coNgay,
            conLai = if Record.HasFields(c, "status") then List.Select(inRa, each not Huy(O(_, c, "status"))) else inRa
        in List.Transform(conLai, (r) => {@@CELLS@@, ten}),
    // Every cell of a file is worked out here, so a failure names its file: a file the receipt read (in ThamSo) must
    // load, and its error says "Tệp <name>: …"; any other file in the folder (one the receipt also left out) gives
    // no rows, as before.
    DonHang = (b as binary, ten as text, ext as text) as list =>
        let kq = try List.Buffer(List.Transform(DocDon(b, ten, ext), List.Buffer))
        in if not kq[HasError] then kq[Value]
           else if Record.HasFields(ThamSo, ten) then
               error Error.Record(kq[Error][Reason], "Tệp " & ten & ": " & kq[Error][Message], kq[Error][Detail])
           else {},
    ThamSo = @@PARAMS@@,
    MacDinh = [thu = "vi-VN", ma_hoa = 65001],
    Nguon = Folder.Files(@@FOLDER@@),
    Goc = @@FOLDER@@ & (if Text.EndsWith(@@FOLDER@@, "\") then "" else "\"),
    LocFile = Table.SelectRows(Nguon, each List.Contains({".csv", ".xls"}, Text.Lower([Extension])) and not Text.StartsWith([Name], "~$")
        and List.Count(Text.Split([Folder Path], "\")) - List.Count(Text.Split(Goc, "\")) <= 1),
    KhoaTen = Table.AddColumn(LocFile, "KhoaTen", each Text.Lower([Name])),
    SapXep = Table.Sort(KhoaTen, {{"KhoaTen", Order.Ascending}}),
    DocTungFile = Table.AddColumn(SapXep, "Dong", each DonHang([Content], [Name], Text.Lower([Extension]))),
    GopFile = Table.FromRows(List.Combine(DocTungFile[Dong]), @@ROWTYPE@@)"""





def order_files_query(folder_parameter: str, file_params: dict, roles: list, dedup: Optional[list],
                      period: Optional[tuple] = None, remembered: Optional[dict] = None) -> str:
    """M that reads every order export in the folder, whatever it is, with the rules engine.sources applies.

    Each file is read by its bytes (engine.tabular): a real .xls or an .xlsx under that name through
    Excel.Workbook, HTML saved as .xls with text functions (no browser engine), SpreadsheetML through
    Xml.Document, a .csv or other text with its own separator. Then, as sources.load_order_file does: the header
    is the first of the top HEADER_SCAN non-blank rows naming a date and an amount (same names, same ranking);
    a row whose date cell has no digit is a footer; in a print layout (not a .csv, or a title above the header)
    nothing after the last real date is an order; a Status that clearly says cancelled sets the row aside.
    Dates and amounts are parsed the way sources.parse_date and trial_balance.parse_amount do.

    file_params: file name → {"order": "vi-VN"|"en-US", "encoding": code page}; a file not listed (next month's)
    reads day-first in UTF-8. roles: the roles to output. dedup: None → by document number; [] → whole identical
    orders; a list of roles (["branch", "doc"]) → those. Rows without a document number are never merged.
    remembered: header signature → {role: column key} that a person named (sources.REMEMBERED), only for the
    headers of this folder's files, so no other company's column names are written into this workbook.
    """
    from engine import sources
    tier1, tier2 = _role_names()
    params = "[" + ", ".join(
        f"{_m_field(name)} = [thu = {_m_text(p.get('order') or 'vi-VN')}, ma_hoa = {int(p.get('encoding') or 65001)}]"
        for name, p in sorted(file_params.items())) + "]"
    out_roles = [r for r in _ROLE_ORDER if r in roles]
    told = "[" + ", ".join(
        f"{_m_field(sig)} = [" + ", ".join(f"{_m_field(r)} = {_m_text(k)}" for r, k in sorted(cols.items())) + "]"
        for sig, cols in sorted((remembered or {}).items())) + "]"
    kinds = {"doc": "nullable text", "date": "nullable date", "amount": "nullable number", "cost": "nullable number"}
    row_type = "type table [" + ", ".join(f"{_m_field(CANON[r])} = {kinds.get(r, 'nullable text')}" for r in out_roles) \
               + f", {_m_field(SOURCE_COLUMN)} = text]"
    cells = ", ".join(
        "DocNgay(O(r, c, \"date\"), p[thu])" if r == "date" else
        f"DocSo(O(r, c, {_m_text(r)}))" if r in ("amount", "cost") else
        f"Chu(O(r, c, {_m_text(r)}))" for r in out_roles)
    m = _ORDER_M
    for token, value in (("@@FOLD@@", _fold_table()), ("@@CANCELLED@@", _m_list(sorted(sources.CANCELLED))),
                         ("@@TIER1@@", tier1), ("@@TIER2@@", tier2), ("@@UNITS@@", _m_list(sources.UNIT_SUFFIXES)), ("@@SCAN@@", str(sources.HEADER_SCAN)),
                         ("@@CELLS@@", cells), ("@@PARAMS@@", params), ("@@FOLDER@@", folder_parameter),
                         ("@@ROWTYPE@@", row_type), ("@@SSNS@@", _m_text(_SS_NS)), ("@@QUOTE@@", '""""'),
                         ("@@REMEMBERED@@", told)):
        m = m.replace(token, value)
    out, last = [m], "GopFile"
    f = {r: "[" + _m_field(CANON[r]) + "]" for r in out_roles}
    if period and "date" in out_roles:
        # The ledger's month only; a date that could not be read stays in, as the receipt counts it.
        y, mth = period
        out.append(f"    TrongKy = Table.SelectRows({last}, each {f['date']} = null or "
                   f"(Date.Year({f['date']}) = {int(y)} and Date.Month({f['date']}) = {int(mth)}))")
        last = "TrongKy"
    if "doc" in out_roles:
        content = [CANON[r] for r in out_roles if r != "status"]
        keys = content if dedup == [] else [CANON[r] for r in dedup] if dedup else [CANON["doc"]]
        out.append(f"    CoSoCT = Table.SelectRows({last}, each {f['doc']} <> null and {f['doc']} <> \"\")")
        out.append(f"    KhongSoCT = Table.SelectRows({last}, each {f['doc']} = null or {f['doc']} = \"\")")
        out.append(f"    XoaTrungTheoSoCT = Table.Combine({{Table.Distinct(CoSoCT, {_m_list(keys)}), KhongSoCT}})")
        last = "XoaTrungTheoSoCT"
    return ",\n".join(out) + f"\nin\n    {last}"
