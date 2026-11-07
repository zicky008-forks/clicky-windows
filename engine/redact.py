"""Mask what leaves the machine: names become codes ("Khách #07"), identifiers become short tags ("[MST]").

Presidio has no Vietnamese, so the recogniser is built from two things:

* a catalogue read from the user's own files: the values of name columns (TenKH, KhachHang,
  NhaCungCap, NhanVien...) and identifier columns (MST, STK, CCCD, SĐT, Email). Every later
  occurrence of one of those values is replaced, wherever in the text it appears;
* shapes that can only be identifiers: emails, a tax code with its branch suffix (0101234567-001),
  phone numbers, numbers that start with 0, and numbers written after a label ("MST: ...").

Amounts are never touched. 8400000000 or 1.400.000.000 is a 10-digit amount, not an ID, so a bare
long number is kept unless its column, its label or a leading 0 says otherwise (amounts never
start with 0; tax codes, CCCD and phone numbers almost always do).

Matching folds case and Vietnamese accents: "CÔNG TY MINH ANH" and "cong ty minh anh" are one name.
"""
import re
import unicodedata
from collections import defaultdict
from functools import lru_cache
from typing import Optional

NAME_KINDS = ("khach", "ncc", "nv")
CODE_LABEL = {"khach": "Khách", "ncc": "NCC", "nv": "NV"}
TAG = {"mst": "[MST]", "stk": "[STK]", "cccd": "[CCCD]", "sdt": "[SĐT]", "email": "[email]"}
# For the one-line summary: "đã che 3 tên khách, 2 mã số thuế".
NOUN = {"khach": "tên khách", "ncc": "tên nhà cung cấp", "nv": "tên nhân viên", "mst": "mã số thuế",
        "stk": "số tài khoản", "cccd": "số CCCD", "sdt": "số điện thoại", "email": "email"}

# Header words after folding, with spaces and separators removed.
NAME_COLUMNS = {
    "khach": {"makh", "makhachhang", "tenkh", "tenkhachhang", "khachhang", "customer", "customerid",
              "customercode", "customername", "tendoituong", "doituong", "madoituong", "nguoimua",
              "tennguoimua", "donvimua", "tendonvimua"},
    "ncc": {"mancc", "ncc", "tenncc", "nhacungcap", "manhacungcap", "tennhacungcap", "supplier",
            "suppliername", "vendor", "vendorname", "nguoiban", "tennguoiban", "donviban", "tendonviban"},
    "nv": {"manv", "nhanvien", "manhanvien", "tennv", "tennhanvien", "hoten", "hovaten", "employee",
           "employeename", "nvbanhang", "nhanvienbanhang", "nhanvienkinhdoanh", "salesman", "salesperson"},
}
ID_COLUMNS = {
    "mst": ({"mst", "masothue", "msthue", "taxcode", "taxid", "vatcode"}, ("mst", "masothue")),
    "stk": ({"stk", "sotk", "sotaikhoan", "sotaikhoannganhang", "taikhoannganhang", "tknganhang",
             "bankaccount", "accountno", "accountnumber", "iban"}, ("stk", "sotaikhoannganhang")),
    "cccd": ({"cccd", "cmnd", "cmt", "cancuoc", "cancuoccongdan", "socccd", "socmnd", "cmndcccd", "cccdcmnd",
              "idcard", "nationalid"}, ("cccd", "cmnd")),
    "sdt": ({"sdt", "sodt", "dienthoai", "sodienthoai", "dtdd", "didong", "phone", "phoneno", "mobile", "tel",
             "telephone", "hotline"}, ("sdt", "dienthoai", "sodienthoai")),
    "email": ({"email", "mail", "thudientu"}, ("email",)),
}
# Fewest digits an identifier column value needs before it is hunted elsewhere in the text, so a
# short stray value (a GL account 5111 typed in a wrong column) never knocks out a real number.
MIN_ID_DIGITS = {"mst": 9, "stk": 8, "cccd": 9, "sdt": 9}
HEADER_SCAN_ROWS = 15
_WORD = re.compile(r"[^\W_]+")


@lru_cache(maxsize=4096)
def fold_char(c: str) -> str:
    base = "".join(ch for ch in unicodedata.normalize("NFD", c) if not unicodedata.combining(ch))
    return base.replace("đ", "d").replace("Đ", "D").lower()


def fold(text) -> str:
    return "".join(fold_char(c) for c in str(text or ""))


def _folded_with_index(text: str) -> tuple:
    """Folded text and, for each folded character, the index of the original character it came from."""
    out, index = [], []
    for i, c in enumerate(text):
        f = fold_char(c)
        out.append(f)
        index.extend([i] * len(f))
    return "".join(out), index


def _key(header) -> str:
    return re.sub(r"[\s_\-./()]", "", fold(header))


def column_kind(header) -> Optional[str]:
    """'khach' / 'ncc' / 'nv' for a name column, 'mst' / 'stk' / 'cccd' / 'sdt' / 'email' for an identifier."""
    k = _key(header)
    if not k:
        return None
    for kind, names in NAME_COLUMNS.items():
        if k in names:
            return kind
    for kind, (names, prefixes) in ID_COLUMNS.items():
        if k in names or k.startswith(prefixes):
            return kind
    return None


def _cell_text(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)                       # Excel hands back 101234567.0 for a tax code typed as a number
    return str(v).strip() if v is not None else ""


def _display_rank(name: str) -> tuple:
    return (not any(c.isdigit() for c in name), len(name))


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


class Catalogue:
    """Names and identifiers found in the user's files; codes are handed out on first use and kept."""

    def __init__(self):
        self._entity = {}                       # folded name → (kind, entity id)
        self._single = {}                       # one-word name, accents kept, lower case → (kind, entity id)
        self._display = {}                      # (kind, entity id) → the name as written in the file
        self._ids = {}                          # identifier value (digits, or folded text) → kind
        self._next = defaultdict(int)
        self._codes = {}                        # (kind, entity id) → number in its code
        self._by_code = {}                      # (kind, number) → (kind, entity id)
        self._index = None

    def __len__(self):
        return len(self._entity) + len(self._single) + len(self._ids)

    def add_rows(self, rows) -> int:
        """Learn from a grid whose header row names its columns; returns how many values it learnt."""
        rows = [list(r) for r in (rows or []) if r is not None]
        head = next((i for i, r in enumerate(rows[:HEADER_SCAN_ROWS])
                     if any(column_kind(_cell_text(h)) for h in r)), None)
        if head is None:
            return 0
        kinds = {i: column_kind(_cell_text(h)) for i, h in enumerate(rows[head])}
        kinds = {i: k for i, k in kinds.items() if k}
        before = len(self)
        for r in rows[head + 1:]:
            per_kind = defaultdict(list)
            for i, kind in kinds.items():
                if i < len(r):
                    text = _cell_text(r[i])
                    if text:
                        per_kind[kind].append(text)
            for kind, values in per_kind.items():
                if kind in NAME_KINDS:
                    self.add_name(kind, values)
                else:
                    for v in values:
                        self.add_id(kind, v)
        return len(self) - before

    def add_name(self, kind: str, values) -> None:
        """Values that name one party (its code and its name from one row) become one entity.

        A one-word name without digits ("Hoa", "Nam") is matched with its accents and a capital
        letter: folded, it would also hit "hóa đơn" and "năm"."""
        keys = []
        for v in values:
            v = re.sub(r"\s+", " ", v).strip()
            k = fold(v)
            # Too short to match safely, or a bare number: a code like 1001 also reads as an amount.
            if len(k) < 3 or k.replace(" ", "").isdigit():
                continue
            single = " " not in k and not any(c.isdigit() for c in k)
            keys.append((self._single if single else self._entity,
                         unicodedata.normalize("NFC", v).lower() if single else k, v))
        if not keys:
            return
        entity = next((table[k] for table, k, _ in keys if table.get(k, (None,))[0] == kind), None)
        if entity is None:
            self._next[kind] += 1
            entity = (kind, self._next[kind])
        for table, k, v in keys:
            table.setdefault(k, entity)
            shown = self._display.get(entity)
            if shown is None or _display_rank(v) > _display_rank(shown):    # the full name over the code
                self._display[entity] = v
        self._index = None

    def add_id(self, kind: str, value: str) -> None:
        if kind == "email":
            if "@" in value:
                self._ids[fold(value)] = kind
            return
        digits = _digits(value)
        if len(digits) >= MIN_ID_DIGITS.get(kind, 9) and len(digits) >= len(value.replace(" ", "")) - 4:
            self._ids[digits] = kind

    def code(self, entity: tuple) -> str:
        if entity not in self._codes:
            kind = entity[0]
            n = 1 + max((num for k, num in self._by_code if k == kind), default=0)
            self._codes[entity] = n
            self._by_code[(kind, n)] = entity
        return f"{CODE_LABEL[entity[0]]} #{self._codes[entity]:02d}"

    def name_for(self, kind: str, number: int) -> Optional[str]:
        entity = self._by_code.get((kind, number))
        return self._display.get(entity) if entity else None

    def names(self) -> list:
        """Every name as written in the files (for tests and the log check)."""
        return sorted(set(self._display.values()))

    def index(self) -> tuple:
        """(first word → name lengths in words, longest first; words → [(accent-kept key or None, entity)]).

        Matching walks the words of the text and tries each length with one dict lookup, so a
        catalogue of thousands of "Công ty ..." costs a few lookups per word, not one per name."""
        if self._index is None:
            lengths, names = defaultdict(set), defaultdict(list)
            for k, entity in self._entity.items():
                words = tuple(_WORD.findall(k))
                if words:
                    lengths[words[0]].add(len(words))
                    names[words].append((None, entity))
            for k, entity in self._single.items():
                lengths[fold(k)].add(1)
                names[(fold(k),)].append((k, entity))
            self._index = ({w: sorted(n, reverse=True) for w, n in lengths.items()}, dict(names))
        return self._index

    def ids(self) -> dict:
        return self._ids


# ── Shapes ────────────────────────────────────────────────────────────────────

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_MST_BRANCH = re.compile(r"(?<![\d.,])\d{10} ?- ?\d{3}(?!\d)")
_PHONE_84 = re.compile(r"(?<![\w+])\+84[\s.-]?\d(?:[\s.-]?\d){7,9}(?!\d)")
# 0912 345 678, 028 3823 4567, 0912.345.678: kept only when the digits add up to 9-11.
_PHONE_GROUPED = re.compile(r"(?<![\d.,])0\d{1,3}(?:[ .-]\d{2,4}){2,3}(?![\d])")
_DIGIT_RUN = re.compile(r"(?<![\d.,])(\d{8,})(?:\.0+)?(?!\d)")    # 101234567.0 is how Excel hands back a code
_ZERO_LED = re.compile(r"(?<![\d.,])0\d{8,}(?!\d)")
# Labels on folded text. "ĐT" alone is matched on the original text: folded it reads "dt", which is also doanh thu.
_LABELS = {
    "mst": r"mst|ma so thue|ms thue|tax code|tax id",
    "stk": r"stk|so tk|so tai khoan|tai khoan ngan hang|tk ngan hang|account no|acc no|bank account",
    "cccd": r"cccd|cmnd|cmt|can cuoc(?: cong dan)?",
    "sdt": r"sdt|so dt|dien thoai|so dien thoai|dtdd|di dong|tel|phone|mobile|hotline|zalo",
}
_LABEL_RE = {kind: re.compile(rf"(?<![a-z0-9])(?:{alt})(?![a-z0-9])[\s:#.=-]*(?:la\s+)?(?=[+\d])")
             for kind, alt in _LABELS.items()}
_DT_LABEL = re.compile(r"(?<!\w)[Đđ][Tt](?!\w)[\s:#.=-]*(?=[+\d])")
_LABEL_DIGITS = {"mst": (9, 14), "stk": (8, 19), "cccd": (9, 12), "sdt": (9, 12)}
_VALUE = re.compile(r"\+?\d+(?:[ .-]\d+)*")


def _zero_led_kind(digits: str) -> str:
    n = len(digits)
    if n == 10:
        return "sdt" if digits[1] in "35789" else "mst"
    return {12: "cccd", 13: "mst"}.get(n, "sdt" if n <= 11 else "stk")


def _labelled_value(text: str, start: int, kind: str) -> Optional[tuple]:
    """(start, end) of the number after a label, taking groups while the digit count fits the kind."""
    m = _VALUE.match(text, start)
    if not m:
        return None
    low, high = _LABEL_DIGITS[kind]
    end, count = start, 0
    for g in re.finditer(r"\+?\d+", m.group(0)):
        if count + len(_digits(g.group(0))) > high:
            break
        count += len(_digits(g.group(0)))
        end = start + g.end()
    return (start, end) if low <= count <= high else None


class Masker:
    """Masks the parts of one send with one catalogue, counting what it covered."""

    def __init__(self, catalogue: Optional[Catalogue] = None):
        self.catalogue = catalogue or Catalogue()
        self._seen = defaultdict(set)

    @property
    def counts(self) -> dict:
        return {k: len(v) for k, v in self._seen.items() if v}

    def mask(self, text: str) -> str:
        if not text:
            return text
        spans = []                                       # (start, end, replacement, kind, original)

        def add(s, e, kind, replacement=None):
            if e > s:
                spans.append((s, e, replacement or TAG[kind], kind, text[s:e]))

        for m in _EMAIL.finditer(text):
            add(m.start(), m.end(), "email")
        for m in _MST_BRANCH.finditer(text):
            add(m.start(), m.end(), "mst")
        for m in _PHONE_84.finditer(text):
            add(m.start(), m.end(), "sdt")
        for m in _PHONE_GROUPED.finditer(text):
            if 9 <= len(_digits(m.group(0))) <= 11:
                add(m.start(), m.end(), "sdt")
        for m in _ZERO_LED.finditer(text):
            add(m.start(), m.end(), _zero_led_kind(m.group(0)))

        folded, index = _folded_with_index(text)
        same_length = len(folded) == len(text)

        def orig(fs, fe):
            if same_length:
                return fs, fe
            s, e = index[fs], index[fe - 1] + 1
            while e < len(text) and unicodedata.combining(text[e]):
                e += 1
            return s, e

        for kind, rx in _LABEL_RE.items():
            for m in rx.finditer(folded):
                found = _labelled_value(folded, m.end(), kind)
                if found:
                    add(*orig(*found), kind)
        for m in _DT_LABEL.finditer(text):
            found = _labelled_value(text, m.end(), "sdt")
            if found:
                add(*found, "sdt")

        # Identifier column values, wherever they reappear (emails are already caught by their shape).
        ids = self.catalogue.ids()
        if ids:
            for m in _DIGIT_RUN.finditer(text):
                kind = ids.get(m.group(1))
                if kind:
                    add(m.start(), m.end(), kind)

        lengths, names = self.catalogue.index()
        if names:
            words = [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(folded)]
            i = 0
            while i < len(words):
                step = 1
                for n in lengths.get(words[i][0], ()):
                    hit = None
                    s, e = orig(words[i][1], words[min(i + n, len(words)) - 1][2])
                    for exact, entity in names.get(tuple(w for w, _, _ in words[i:i + n]), ()):
                        # A one-word name must be written with its accents and a capital: "Hoa", not "hóa".
                        if exact is None or (text[s].isupper() and unicodedata.normalize("NFC", text[s:e]).lower() == exact):
                            hit = entity
                            break
                    if hit:
                        spans.append((s, e, self.catalogue.code(hit), hit[0], hit))
                        step = n
                        break
                i += step

        # Where two overlap the one starting first wins, the longer on a tie; replaced right to left.
        spans.sort(key=lambda sp: (sp[0], -(sp[1] - sp[0])))
        chosen, last_end = [], -1
        for sp in spans:
            if sp[0] >= last_end:
                chosen.append(sp)
                last_end = sp[1]
        out = text
        for s, e, replacement, kind, original in reversed(chosen):
            out = out[:s] + replacement + out[e:]
            self._seen[kind].add(original)
        return out


def summary(lines: int, counts: dict, images: int = 0) -> str:
    """"Gửi 14 dòng · đã che 3 tên khách, 2 mã số thuế"."""
    order = list(CODE_LABEL) + list(TAG)
    covered = ", ".join(f"{counts[k]} {NOUN[k]}" for k in order if counts.get(k))
    text = f"Gửi {lines} dòng · " + (f"đã che {covered}" if covered else "không thấy tên hay mã số nào cần che")
    if images:
        text += f" · kèm {images} ảnh màn hình"
    return text


# ── Back to names, on this machine only ──────────────────────────────────────

_CODE = re.compile(r"\b(Khách|NCC|NV)\s?#\s?(\d{1,4})\b", re.IGNORECASE)
_CODE_KIND = {"khách": "khach", "ncc": "ncc", "nv": "nv"}


def unmask(text: str, catalogue: Catalogue) -> str:
    """The AI only ever saw codes; the user reads the real names again in the answer."""
    def back(m):
        name = catalogue.name_for(_CODE_KIND[m.group(1).lower()], int(m.group(2)))
        return name or m.group(0)
    return _CODE.sub(back, text)


class Unmasker:
    """unmask() for a stream: holds back a tail that could still grow into a code ("Khá", "NCC #0")."""

    _TAIL = re.compile(r"(?:\b(?:K|Kh|Khá|Khác|Khách|N|NC|NCC|NV)(?:\s?#?\s?\d{0,4})?)$", re.IGNORECASE)

    def __init__(self, catalogue: Catalogue):
        self.catalogue = catalogue
        self._buf = ""

    def feed(self, chunk: str) -> str:
        self._buf += chunk or ""
        m = self._TAIL.search(self._buf)
        cut = m.start() if m else len(self._buf)
        out, self._buf = self._buf[:cut], self._buf[cut:]
        return unmask(out, self.catalogue)

    def finish(self) -> str:
        out, self._buf = self._buf, ""
        return unmask(out, self.catalogue)
