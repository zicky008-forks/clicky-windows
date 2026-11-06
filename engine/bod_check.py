"""Review a BOD draft pack against the folder it was made from, before it goes to the board.

    python -m engine.bod_check THU_MUC [-o THU_MUC_KET_QUA]

Reads the folder (engine.bod_inputs), every pack in it (engine.bod_pack), runs the seven groups of checks
(engine.bod_review) and writes three things next to nothing of the client's: ket-qua.md, a workbook
'Clicky_soat_BOD_<ky>.xlsx' with the findings and the 'Bien ban Clicky' sheet, and scene log events. The
pack itself is never changed. The workbook is re-opened after saving and compared with what was computed.

Findings are pack errors only. What the detail itself gets wrong (cost without a department, a next-period
reversal in the export, a distributor discount posted as cost) is listed apart as notes, and used to
explain a gap, so a trap in the data is never reported as an error of the pack.
"""
from __future__ import annotations

import argparse
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

import scene_log
from engine import bod_review as rv
from engine.bod_inputs import Bundle, Config, load, num
from engine.bod_inputs import unassigned as unassigned_label
from engine.bod_pack import Col, Group, Pack, Row, Sheet, fy_of, pack_month, pack_period, read_pack, to_num, version_of
from engine.bod_review import D, Finding, PCT_TOLERANCE, VND_TOLERANCE, ZERO, fmt_pct, fmt_vnd
from engine.tables import is_total, key

GROUP_TITLES = {
    1: "Tổng chi phí so với sổ chi tiết",
    2: "Mẫu số %DTT theo kênh",
    3: "Giá trị đi cùng %DTT và chênh lệch điểm %",
    4: "Phiên bản kế hoạch dùng để so sánh",
    5: "AOPR: tháng đã khoá phải là số thực tế",
    6: "Cắt giảm 3 bước",
    7: "Ảnh hưởng tỷ giá",
}
DISCOUNT_RE = re.compile(r"chiet khau|\bck\b|khuyen mai|ho tro ban hang|thuong doanh so|rebate|discount")
DISTRIBUTOR_RE = re.compile(r"nha phan phoi|\bnpp\b|dai ly|distributor")
REVERSAL_RE = re.compile(r"\bdao\b|hoan nhap|reversal|reverse|dao but toan")
LEVERS_RE = re.compile(r"tong don bay|tong cat|tong so cat|tong muc cat")
CEILING_RE = re.compile(r"\btran\b|ceiling|muc tran")


# ── what the folder says ────────────────────────────────────────────────────────

def fiscal_year(period: str, fy_start: int) -> int:
    y, m = int(period[:4]), int(period[5:7])
    return y if m >= fy_start else y - 1


def fy_months(fy: int, fy_start: int) -> list:
    out = []
    for i in range(12):
        m = (fy_start - 1 + i) % 12 + 1
        out.append(f"{fy if m >= fy_start else fy + 1:04d}-{m:02d}")
    return out


def shift_year(period: str, years: int) -> str:
    return f"{int(period[:4]) + years:04d}{period[4:]}"


def accounts(text) -> list:
    """'641*, 6421; 6428x' -> ['641', '6421', '6428']: account prefixes as a mapping writes them. A range
    '6421-6428' gives each account in it, a number read as 6421.0 gives '6421', and 'trừ 6425' is left out."""
    text = re.split(r"\b(?:tru|ngoai tru|except|excl)\b|trừ", str(text or ""), maxsplit=1)[0]
    out = []
    for t in re.split(r"[\s,;/|+]+", text):
        t = re.sub(r"\.0+$", "", t.strip("()")).rstrip("*xX.")
        rng = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", t)
        if rng and len(rng.group(1)) == len(rng.group(2)) and 0 <= int(rng.group(2)) - int(rng.group(1)) <= 200:
            out += [str(n) for n in range(int(rng.group(1)), int(rng.group(2)) + 1)]
        elif re.fullmatch(r"\d+", t):
            out.append(t)
    return out


def words(text) -> str:
    """Folded text with punctuation as spaces, for whole-word matching: 'DTT site S1, mọi khối' -> 'dtt site s1 moi khoi'."""
    return re.sub(r"[^0-9a-z%]+", " ", key(text)).strip()


def is_distributor_discount(r: dict) -> bool:
    return bool(DISCOUNT_RE.search(key(r.get("text", ""))) and DISTRIBUTOR_RE.search(key(r.get("text", ""))))


class Facts:
    """The folder as records the pack's labels can be matched to."""

    def __init__(self, b: Bundle, division: str, fy_start: int):
        self.b, self.cfg, self.division, self.fy_start = b, b.config, division, fy_start
        prefixes = sorted({t for e in self.cfg.elements.values() for t in accounts(e.get("tk"))}, key=len, reverse=True)
        # selling (641) and administration (642) are operating costs on every Vietnamese chart, whatever the
        # catalogue lists; the catalogue adds its own accounts
        self.cost_prefixes = tuple(sorted({p for p in prefixes if p} | {"641", "642"}, key=len, reverse=True)) \
            if prefixes else ("6",)
        has641 = any(str(e.account).startswith("641") for e in b.gl)
        self.sell_admin = (("641",), ("642",)) if has641 or not any(str(e.account).startswith("642") for e in b.gl) \
            else (("6421",), ("6422",))     # TT133 books selling under 6421 and administration under 6422
        self.canon = {}                      # every division: 'tm' and 'thuong mai' -> one name
        for e in b.gl:
            d = self.cfg.depts.get(e.dept) or {}
            if e.division and d.get("khoi"):
                a, c = key(e.division), key(d["khoi"])
                root = self.canon.get(a) or self.canon.get(c) or c
                self.canon[a] = self.canon[c] = root
        self.aliases = {key(division)} if division else set()
        for _ in range(2):                   # 'TM' in the detail, 'Thương mại' in sales and titles: one division
            for e in b.gl:
                d = self.cfg.depts.get(e.dept) or {}
                if e.division and d.get("khoi") and ({key(e.division), key(d["khoi"])} & self.aliases):
                    self.aliases |= {key(e.division), key(d["khoi"])}
        known = {key(c) for c in self.cfg.depts} | {key(d.get("ten", "")) for d in self.cfg.depts.values()}
        codes = [e.dept for e in b.gl if e.dept]
        # a code the department list does not know is unassigned, unless the detail uses another coding altogether
        self.strict_depts = bool(known) and bool(codes) and sum(key(c) in known for c in codes) >= 0.8 * len(codes)
        self.known_depts = known
        self.gl = [self._rec(e) for e in b.gl]
        self.plans = [self._plan(p) for p in b.plans]
        names = defaultdict(dict)
        for code, d in self.cfg.depts.items():
            names["dept"][key(code)] = code
            if d.get("ten"):
                names["dept"][key(d["ten"])] = code
                names["dept"][key(f"{code} {d['ten']}")] = code
        for code, e in self.cfg.elements.items():
            names["element"][key(code)] = code
            if e.get("ten"):
                names["element"][key(e["ten"])] = code
                names["element"][key(f"{code} {e['ten']}")] = code
        # an indicator table names a department or a cost element in its label column: read it as that, so a
        # department label never becomes a BOD line of its own
        for p in self.plans:
            if p["dong"] and not p["dept"] and not p["element"]:
                hit = _label_dim(names, p["dong"], ("dept", "element"))
                if hit:
                    p[hit[0]], p["dong"] = hit[1], (self._line_of(hit[1], "")[0] if hit[0] == "element" else "")
        for r in self.gl + self.plans:
            for dim in ("dong", "dept", "element", "channel", "nhom"):
                if r.get(dim):
                    names[dim].setdefault(key(r[dim]), r[dim])
        for k, (name, _) in self.cfg.channels.items():
            names["channel"].setdefault(k, name)
        self.names = names
        self.sites = sorted({s.site for s in b.sales if s.site} | {r["site"] for r in self.gl if r["site"]}
                            | {r["site"] for r in self.plans if r["site"]})
        self.divisions = sorted({s.division for s in b.sales if s.division} | {r["division"] for r in self.gl if r["division"]})

    def is_cost(self, account: str) -> bool:
        return any(str(account).startswith(p) for p in self.cost_prefixes) if account else True

    def _line_of(self, element: str, account: str) -> tuple:
        e = self.cfg.elements.get(element) or {}
        dong, nhom = e.get("dong", ""), e.get("nhom", "")
        if not dong:
            for m in self.cfg.bod:
                if m.get("ce") and m["ce"] == element or any(str(account).startswith(t) for t in accounts(m.get("tk"))):
                    dong, nhom = m["dong"], nhom or m.get("nhom", "")
                    break
        return dong, nhom

    def _rec(self, e) -> dict:
        dong, nhom = self._line_of(e.element, e.account)
        dept = e.dept if not self.strict_depts or key(e.dept) in self.known_depts else ""
        if dept and unassigned_label((self.cfg.depts.get(dept) or {}).get("ten", "x")):
            dept = ""                        # the catalogue's own 'P99 - Chưa phân bổ' is no department
        return {"period": e.period, "amount": e.amount, "division": e.division, "site": e.site, "channel": e.channel,
                "dept": dept, "dept_raw": e.dept, "element": e.element, "account": e.account, "dong": dong, "nhom": nhom,
                "usd": e.usd, "text": e.text, "ref": e.ref, "where": e.where, "cost": self.is_cost(e.account)}

    def _plan(self, p) -> dict:
        dong, nhom = self._line_of(p.element, "")
        v = version_of(key(p.version)) or key(p.version)
        return {"version": v, "period": p.month, "fy": fiscal_year(p.month, self.fy_start) if p.month else None,
                "amount": p.amount, "division": p.division, "dept": p.dept, "element": p.element, "site": p.site,
                "approved": p.approved,
                "dong": p.line or dong, "nhom": nhom, "channel": p.channel, "rate": p.rate, "where": p.where}

    # matching a printed label to records
    def matcher(self, label: str, section: str = "") -> Optional[Callable]:
        if not label:
            return None
        if is_total([label]):
            return lambda r: True
        for text in (label, f"{section} {label}" if section else None):
            if not text:
                continue
            k = key(text)
            for dim in ("dong", "dept", "element", "channel", "nhom"):
                if k in self.names[dim]:
                    v = self.names[dim][k]
                    return lambda r, d=dim, v=v: r.get(d) == v
            for token in re.split(r"[\s\-–:|/]+", text.strip()):
                t = key(token)
                for dim in ("dept", "element"):
                    if t and t in self.names[dim] and t == key(self.names[dim][t]):
                        v = self.names[dim][t]
                        return lambda r, d=dim, v=v: r.get(d) == v
        return None

    def row_filter(self, label: str, section: str = "") -> Optional[Callable]:
        """The matcher, narrowed to the site and the division a row names in its text columns ('S2', 'Thương mại')."""
        match = self.matcher(label, section)
        if match is None:
            return None
        site = self.site_in(label)
        division = next((d for d in sorted(self.divisions, key=len, reverse=True)
                         if re.search(rf"(^| ){re.escape(words(d))}( |$)", words(label))), "")
        if not site and not division:
            return match
        return lambda r: match(r) and (not site or key(r.get("site", "")) == key(site)) and \
            (not division or not r.get("division") or self.same_division(r["division"], division))

    def dim_of(self, label: str, section: str = "") -> str:
        for text in (label, f"{section} {label}"):
            k = key(text)
            for dim in ("dong", "dept", "element", "channel", "nhom"):
                if k in self.names[dim]:
                    return dim
        return ""

    def same_division(self, a: str, b: str) -> bool:
        a, b = key(a), key(b)
        return a == b or self.canon.get(a, a) == self.canon.get(b, b) or (a in self.aliases and b in self.aliases)

    def in_division(self, r: dict) -> bool:
        return not self.division or not r.get("division") or self.same_division(r["division"], self.division)

    def actual(self, match: Callable, periods, clean: bool = False) -> Decimal:
        """Cost in the detail. clean=True leaves out what the detail itself gets wrong (a distributor discount
        posted as cost), so a plan that already corrected it is not called wrong."""
        periods = set(periods)
        return sum((r["amount"] for r in self.gl if r["cost"] and r["period"] in periods and self.in_division(r)
                    and match(r) and not (clean and is_distributor_discount(r))), ZERO)

    def plan(self, version: str, match: Callable, periods) -> Optional[Decimal]:
        periods = set(periods)
        rows = [r for r in self.plans if _same_version(r["version"], version) and r["period"] in periods
                and self.in_division(r) and match(r)]
        return sum((r["amount"] for r in rows), ZERO) if rows else None

    def versions(self, fy: int) -> list:
        return sorted({r["version"] for r in self.plans if r["fy"] == fy})

    def channel(self, label: str, section: str = "") -> str:
        for text in (label, f"{section} {label}"):
            k = key(text)
            if k in self.cfg.channels:
                return self.cfg.channels[k][0]
        for k, (name, _) in sorted(self.cfg.channels.items(), key=lambda x: -len(x[0])):
            if re.search(rf"(^| ){re.escape(words(k))}$", words(label)):
                return name
        return ""

    def site_in(self, label: str) -> str:
        k = words(label)
        for s in sorted(self.sites, key=len, reverse=True):
            if re.search(rf"(^| ){re.escape(words(s))}( |$)", k):
                return s
        return ""

    # net revenue bases
    def revenue(self, periods, site: Optional[str] = None, division: Optional[str] = None,
                channel: Optional[str] = None) -> Optional[Decimal]:
        periods = set(periods)
        rows = [s for s in self.b.sales if s.month in periods
                and (site is None or key(s.site) == key(site))
                and (division is None or key(s.division) == key(division)
                     or key(division) in self.aliases and key(s.division) in self.aliases)
                and (channel is None or key(s.channel) == key(channel))]
        return sum((s.net for s in rows), ZERO) if rows else None

    def base_of(self, text: str, line_site: str = "", line_channel: str = "") -> tuple:
        """(site, division, channel) a denominator rule names. None = all."""
        k = words(text)
        site = None
        if not re.search(r"moi site|tat ca (cac )?site|toan bo (cac )?site|all sites|cac site|toan (khoi|cong ty)", k):
            hit = next((s for s in sorted(self.sites, key=len, reverse=True)
                        if re.search(rf"(^| ){re.escape(words(s))}( |$)", k)), None)
            site = hit or (line_site or None if re.search(r"\bsite\b", k) else None)
        division = next((d for d in sorted(self.divisions, key=len, reverse=True)
                         if re.search(rf"(^| ){re.escape(words(d))}( |$)", k)), None)
        if division is None and not re.search(r"toan cong ty|ca cong ty|phap nhan|tat ca (cac )?khoi|moi khoi", k):
            division = self.division or None
        channel = None
        if re.search(r"cua kenh|chinh kenh|rieng kenh|theo kenh|kenh do|ban than kenh|tung kenh", k):
            channel = line_channel or None
        return site, division, channel

    def base_name(self, site, division, channel) -> str:
        parts = ["DTT"]
        parts.append(f"kênh {channel}" if channel else "")
        parts.append(f"khối {division}" if division else "toàn công ty")
        parts.append(f"site {site}" if site else "mọi site")
        return " ".join(p for p in parts if p)

    def candidate_bases(self, periods, line_channel: str = "") -> list:
        out = []
        for division in [self.division or None, None]:
            for site in [None] + self.sites:
                for channel in [None, line_channel or None] if line_channel else [None]:
                    v = self.revenue(periods, site, division, channel)
                    if v:
                        out.append((self.base_name(site, division, channel), v))
        seen, uniq = set(), []
        for n, v in out:
            if n not in seen:
                seen.add(n)
                uniq.append((n, v))
        return uniq


def _label_dim(names: dict, text: str, dims: tuple) -> Optional[tuple]:
    """(dimension, value) a label names: the whole label, else one token that is a code ('P01 - Kinh doanh')."""
    k = key(text)
    for dim in dims:
        if k in names[dim]:
            return dim, names[dim][k]
    for token in re.split(r"[\s\-–:|/]+", str(text).strip()):
        t = key(token)
        for dim in dims:
            if t and t in names[dim] and t == key(names[dim][t]):
                return dim, names[dim][t]
    return None


def _same_version(a: str, b: str) -> bool:
    a, b = key(a).replace(" ", ""), key(b).replace(" ", "")
    if a == b:
        return True
    return (a == "aopr" and b.startswith("aopr")) or (b == "aopr" and a.startswith("aopr"))


# ── pack-level helpers ──────────────────────────────────────────────────────────

def find_division(pack: Pack, b: Bundle) -> tuple:
    """(division, how it was found)."""
    names = sorted({e.division for e in b.gl if e.division} | {s.division for s in b.sales if s.division}, key=len,
                   reverse=True)
    k = words(pack.title)
    for d in names:
        if re.search(rf"(^| )(khoi )?{re.escape(words(d))}( |$)", k):
            return d, "tiêu đề bộ BOD"
    # the detail may hold division codes ('TM') while the title says the name: go through the departments
    by_code = defaultdict(set)
    for e in b.gl:
        d = b.config.depts.get(e.dept)
        if e.division and d and d.get("khoi"):
            by_code[e.division].add(d["khoi"])
    for code, names_ in by_code.items():
        if any(re.search(rf"(^| )(khoi )?{re.escape(words(n))}( |$)", k) for n in names_):
            return code, "tiêu đề bộ BOD và danh mục phòng ban"
    rule = b.config.rule("khoi")
    if isinstance(rule, str) and rule.strip():
        return rule.strip(), "danh mục cấu hình"
    if len(names) == 1:
        return names[0], "sổ chi tiết chỉ có một khối"
    return "", "không xác định"


def locked_months(cfg: Config, fy: int, fy_start: int, period: str, approved: str = "") -> tuple:
    """(months closed when the revised plan was made, how it is known). A month is locked only when a file says
    so: its books were closed before the version's approval date, or the configuration names the last closed
    month (both given: both must hold). The month under review is never locked. ([], "") when nothing says."""
    import calendar
    from engine.bod_inputs import month_key
    months = [m for m in fy_months(fy, fy_start) if m < period]
    upto, how = None, []
    for words in (("khoa", "den"), ("khoa so",), ("lock",), ("da khoa",)):
        v = cfg.rule(*words)
        m = month_key(v, None, f"{fy}/{(fy + 1) % 100:02d}", fy_start) if v is not None else None
        if m:
            upto = m
            break
    if approved:
        def closed(m):
            y, mm = int(m[:4]), int(m[5:])
            return f"{m}-{calendar.monthrange(y, mm)[1]:02d}" < approved
        months = [m for m in months if closed(m)]
        how.append(f"tháng chốt trước ngày duyệt {approved[8:]}/{approved[5:7]}/{approved[:4]}")
    if upto:
        months = [m for m in months if m <= upto]
        how.append(f"danh mục: khoá sổ đến {upto[5:]}/{upto[:4]}")
    if not how:
        return [], ""
    return months, "; ".join(how)


def convention_previous(cfg: Config, available: list) -> list:
    """Plan versions last year is compared with, from the client's rules; else that year's last revision."""
    for words in (("nam truoc",), ("ly",), ("cung ky",)):
        v = cfg.rule(*words)
        if isinstance(v, str) and version_of(key(v)):
            return [version_of(key(v))]
    revised = sorted(v for v in available if v.startswith("aopr"))
    return [revised[-1]] if revised else []


def fx_convention(cfg: Config) -> Optional[str]:
    from engine.bod_inputs import rate_type
    for k, v in cfg.rules.items():
        if "ty gia" in k and isinstance(v, str) and rate_type(v) in ("binh quan", "cuoi ky", "dau ky"):
            if not re.search(r"ke hoach|aop|danh gia lai", k):
                return rate_type(v)
    return None


# ── the review ──────────────────────────────────────────────────────────────────

NOT_RUN = "CHƯA KIỂM ĐƯỢC"
DEFAULT_WHY = {
    1: ("không thấy dòng tổng chi phí nào trên bộ BOD", "dòng tổng chi phí của khối trong kỳ (dòng 'Tổng' hoặc dòng cha có các dòng con)"),
    2: ("không thấy dòng nào trên bộ BOD có cả chi phí, %DTT và quy tắc mẫu số", "cột %DTT trên bộ BOD và quy tắc mẫu số theo kênh trong danh mục"),
    3: ("không thấy bảng nào trên bộ BOD có cột %DTT hoặc cột chênh lệch", "bảng có giá trị, %DTT và chênh lệch"),
    4: ("không thấy cột so sánh với kế hoạch nào khớp được với file kế hoạch", "cột so sánh trên bộ BOD và file kế hoạch của năm đó"),
    5: ("không thấy bảng AOPR theo tháng trên bộ BOD, cũng không có file AOPR có tháng đã khoá", "bảng AOPR theo tháng hoặc file AOPR"),
    6: ("không thấy bảng cắt giảm có cột bước 2 hoặc bước 3", "bảng cắt giảm 3 bước"),
    7: ("không thấy bảng ảnh hưởng tỷ giá có cột USD và cột ảnh hưởng", "bảng ảnh hưởng tỷ giá trên bộ BOD"),
}


@dataclass
class Coverage:
    """What each group really looked at. A group that looked at nothing says why, never 'không thấy lỗi'."""
    checked: dict = field(default_factory=lambda: defaultdict(int))
    why: dict = field(default_factory=dict)            # group -> [(reason, what is needed)]
    part: dict = field(default_factory=dict)           # group -> [part of the group that could not be checked]

    def partly(self, group: int, what: str) -> None:
        if what not in self.part.setdefault(group, []):
            self.part[group].append(what)

    def ran(self, group: int, n: int = 1) -> None:
        self.checked[group] += n

    def cannot(self, group: int, why: str, need: str = "") -> None:
        if (why, need) not in self.why.setdefault(group, []):
            self.why[group].append((why, need))

    def status(self, group: int, findings: list) -> tuple:
        """(state, sentence): 'loi' errors found, 'ok' all checked and clean, 'mot_phan' clean where it could look
        but part of the group could not be checked, 'chua' nothing could be checked."""
        n = sum(1 for x in findings if x.group == group)
        reasons = self.why.get(group) or []
        why = "; ".join(w for w, _ in reasons)
        need = "; ".join(sorted({n_ for _, n_ in reasons if n_}))
        gaps = [f"phần {w}" for w in self.part.get(group, [])] + ([why + (f". Cần: {need}" if need else "")] if why else [])
        rest = "".join(f"; {NOT_RUN.lower()} {g}" for g in gaps)
        if n:
            return "loi", f"{n} lỗi" + rest
        if self.checked.get(group):
            if gaps:
                return "mot_phan", f"không thấy lỗi ở {self.checked[group]} ô/dòng đã soát; {NOT_RUN} phần còn lại: " + "; ".join(gaps)
            return "ok", f"không thấy lỗi (đã soát {self.checked[group]} ô/dòng)"
        reasons = reasons or [DEFAULT_WHY[group]]
        why = "; ".join(w for w, _ in reasons)
        need = "; ".join(sorted({n_ for _, n_ in reasons if n_}))
        return "chua", f"{NOT_RUN}: {why}" + (f". Cần: {need}" if need else "")


@dataclass
class Review:
    folder: Path
    pack: str = ""
    period: str = ""
    division: str = ""
    findings: list = field(default_factory=list)
    notes: list = field(default_factory=list)          # [(title, detail, where)] data traps, not pack errors
    assumptions: list = field(default_factory=list)    # what Clicky had to decide, said out loud
    receipt: list = field(default_factory=list)
    unused: list = field(default_factory=list)
    coverage: Coverage = field(default_factory=Coverage)
    read: str = ""                                      # how much of the pack was read

    def groups(self) -> list:
        """[(group, title, state, sentence)] for all seven groups."""
        return [(g, GROUP_TITLES[g], *self.coverage.status(g, self.findings)) for g in sorted(GROUP_TITLES)]


def _months_for(col: Col, period: str, fy: int, fy_start: int) -> list:
    if col.ytd:
        months = fy_months(fy, fy_start)
        return months[:months.index(period) + 1] if period in months else [period]
    return [period]


def _cell(sheet: Sheet, row: Row, col: Optional[Col]) -> str:
    return sheet.addr(row.n, col.index) if col is not None else sheet.addr(row.n, 0)


PROFIT_RE = re.compile(r"loi nhuan|\blai\b|\blo\b|ebit|ebitda|gross profit|bien loi|\blnst\b|\blntt\b")


def _spans(values: list) -> dict:
    """{parent row: (first child, last child)} for rows whose value is the sum of the rows right below them, or
    right above them, at least two. Found bottom-up, so a parent of parents adds its children's totals only."""
    spans: dict = {}
    n = len(values)
    changed = True
    while changed:
        changed = False
        for i in range(n):
            v = values[i]
            if i in spans or v is None or not v:
                continue
            for step in (1, -1):
                acc, count, j = ZERO, 0, i + step
                while 0 <= j < n and values[j] is not None and not (step == -1 and any(a <= i <= b for a, b in spans.values())):
                    acc += values[j]
                    count += 1
                    lo, hi = spans.get(j, (j, j))
                    edge = hi if step == 1 else lo
                    if count >= 2 and abs(acc - v) <= VND_TOLERANCE:
                        spans[i] = (i + 1, edge) if step == 1 else (edge, i - 1)
                        changed = True
                        break
                    if abs(acc) > abs(v) * 3:
                        break
                    j = (hi + 1) if step == 1 else (lo - 1)
                if i in spans:
                    break
    return spans


def _parent_total(f: Facts, s: Sheet, col: Col) -> Optional[Row]:
    """The cost total of a sheet that prints no 'Tổng' row: the top parent row ('Chi phí hoạt động') with its
    children right below (or right above) it, found by the sum of the children or, when the parent itself is
    wrong, by a cost label that names no department or element next to rows that do."""
    rows = [r for r in s.rows if not _is_revenue(r.label) and not PROFIT_RE.search(key(r.label))]
    values = [s.num(r, col) for r in rows]
    spans = _spans(values)
    top = [i for i in spans if not any(a <= i <= b for j, (a, b) in spans.items() if j != i)]
    if not top:
        for i, r in enumerate(rows):
            k = key(r.label)
            if not re.search(r"chi phi|\bcp\b|opex|expense", k) or values[i] is None:
                continue
            if f.dim_of(r.label, r.section) in ("dept", "element", "channel"):
                continue
            below = [f.dim_of(x.label, x.section) for x in rows[i + 1:i + 6]]
            above = [f.dim_of(x.label, x.section) for x in rows[max(0, i - 5):i]]
            if sum(1 for d in below if d in ("dept", "element")) >= 2:
                spans[i] = (i + 1, len(rows) - 1)
                top.append(i)
            elif i == len(rows) - 1 and sum(1 for d in above if d in ("dept", "element")) >= 2:
                spans[i] = (0, i - 1)
                top.append(i)
    if not top:
        return None
    best = max(top, key=lambda i: abs(values[i] or ZERO))
    a, b = spans[best]
    if len(top) > 1 and b - a + 1 < (len(rows) - 1) / 2:
        return None                         # several groups side by side and no grand total: nothing to compare
    return rows[best]


def check_totals(f: Facts, pack: Pack, period: str, fy: int, cov: Optional[Coverage] = None) -> list:
    cov = cov or Coverage()
    out, all_cands = [], []
    for s in pack.sheets:
        if s.kind not in ("lines", "other"):
            continue
        cands = []
        if s.kind == "lines":
            dims = Counter(f.dim_of(r.label, r.section) for r in s.rows if not r.total)
            rows_are = dims.most_common(1)[0][0] if dims else ""
            if rows_are not in ("dept", "element", "dong", "nhom", ""):
                continue
            for i, groups in enumerate(s.sections()):
                g = groups[0]
                if g.value.version not in ("", "thuc te") or g.value.previous:
                    continue
                totals = [r for r in s.rows if r.total and not _is_revenue(r.label)]
                if not totals:
                    parent = _parent_total(f, s, g.value)
                    totals = [parent] if parent is not None else []
                for r in totals:
                    v = s.num(r, g.value)
                    if v is not None:
                        cands.append((_cell(s, r, g.value), v, _months_for(g.value, period, fy, f.fy_start), i, r.label))
            best = {}
            for c in cands:                                  # subtotals ('Cộng chi phí bán hàng') are not the total
                if c[3] not in best or abs(c[1]) > abs(best[c[3]][1]):
                    best[c[3]] = c
            cands = [c[:3] + (c[4],) for c in best.values()]
        for where, label, v in (s.loose if s.kind == "other" else []):
            if re.search(r"tong chi phi", key(label)) and not any(c[0] == where for c in cands):
                cands.append((where, v, [period] if not re.search(r"luy ke|ytd", key(label)) else
                              _months_for(Col(0, "", "", "value", ytd=True), period, fy, f.fy_start), label))
        all_cands += cands
    if not f.gl:
        cov.cannot(1, "chưa đọc được sổ chi tiết chi phí",
                   "file sổ chi tiết có số tiền, tài khoản và phòng ban hoặc cost element")
        return out
    if not [r for r in f.gl if f.in_division(r) and r["cost"]]:
        cov.cannot(1, f"sổ chi tiết không có dòng chi phí nào của khối {f.division or '(chưa rõ)'}",
                   "sổ chi tiết có cột khối và tài khoản chi phí khớp danh mục")
        return out
    for where, value, months, label in all_cands:
        cov.ran(1)
        out += _total_finding(f, where, value, months, label)
    return out


SELL_RE = re.compile(r"ban hang|\bbh\b|cpbh|selling|\bsales exp")
ADMIN_RE = re.compile(r"quan ly|\bql(dn)?\b|cpql|\bg a\b|admin")


def cost_scope(f: Facts, label: str) -> Optional[tuple]:
    """Account prefixes a total row covers, from its words: 'bán hàng' 641, 'quản lý' 642, both (BH&QL) both;
    None for every operating cost ('Tổng chi phí', 'Chi phí hoạt động')."""
    k = key(label).replace("&", " ")
    sell, admin = bool(SELL_RE.search(k)), bool(ADMIN_RE.search(k))
    if not (sell or admin):
        return None
    return (f.sell_admin[0] if sell else ()) + (f.sell_admin[1] if admin else ())


def _total_finding(f: Facts, where: str, value: Decimal, months: list, label: str = "") -> list:
    months = set(months)
    first, last = min(months), max(months)
    scope = cost_scope(f, label)
    in_scope = (lambda r: r["cost"]) if scope is None else (lambda r: str(r["account"]).startswith(scope))
    mine = [r for r in f.gl if f.in_division(r) and in_scope(r)]
    truth = sum((r["amount"] for r in mine if r["period"] in months), ZERO)
    gap = D(value) - truth
    if abs(gap) <= VND_TOLERANCE:
        return []
    inside = [r for r in mine if r["period"] in months]
    distributor = lambda r: DISCOUNT_RE.search(key(r["text"])) and DISTRIBUTOR_RE.search(key(r["text"]))
    groups = [
        ("Bút toán của kỳ sau lẫn trong file xuất (bộ BOD cộng vào)",
         sum((r["amount"] for r in mine if r["period"] > last), ZERO)),
        ("Bút toán của kỳ trước (bộ BOD cộng vào)", sum((r["amount"] for r in mine if r["period"] < first), ZERO)),
        ("Chi phí của khối khác cùng kỳ (bộ BOD cộng vào)",
         sum((r["amount"] for r in f.gl if in_scope(r) and r["period"] in months and not f.in_division(r)), ZERO)),
        ("Chi phí chưa gán phòng ban (bộ BOD bỏ ra)", -sum((r["amount"] for r in inside if not r["dept"]), ZERO)),
        ("Chiết khấu cho nhà phân phối ghi vào chi phí (bộ BOD bỏ ra)",
         -sum((r["amount"] for r in inside if distributor(r)), ZERO)),
        ("Dòng không thuộc tài khoản chi phí (bộ BOD cộng vào)",
         sum((r["amount"] for r in f.gl if not in_scope(r) and r["period"] in months and f.in_division(r)), ZERO)),
    ]
    later = sorted({r["period"] for r in mine if r["period"] > last})
    for p in later:
        groups.append((f"Bút toán tháng {p[5:]}/{p[:4]} trong file xuất (bộ BOD cộng vào)",
                       sum((r["amount"] for r in mine if r["period"] == p), ZERO)))
    for dept in sorted({r["dept"] for r in inside if r["dept"]}):
        groups.append((f"Phòng {dept} (bộ BOD bỏ ra)", -sum((r["amount"] for r in inside if r["dept"] == dept), ZERO)))
    chosen, rest = rv.explain_gap(gap, groups)
    causes = chosen + ([("chưa giải thích được", rest)] if rest else [])
    head = ", ".join(f"{l.split(' (')[0].lower()} {fmt_vnd(a)} đ" for l, a in chosen) if chosen else ""
    return [Finding(1, "total_vs_detail", where,
                    f"Tổng chi phí trên bộ BOD là {fmt_vnd(D(value))} đ, sổ chi tiết lọc theo khối và kỳ là {fmt_vnd(truth)} đ, "
                    f"lệch {fmt_vnd(gap)} đ" + (f": {head}" if head else ""), truth, D(value), causes)]


def _sign(pairs: list) -> int:
    """+1 when gaps are printed as now − compared, −1 for compared − now (the column's own habit)."""
    plus = sum(1 for want, got in pairs if abs(want - got) <= VND_TOLERANCE)
    minus = sum(1 for want, got in pairs if abs(-want - got) <= VND_TOLERANCE)
    return -1 if minus > plus else 1


def _pp_scale(pairs: list) -> Decimal:
    """Points printed as fractions (0.0123) or as numbers (1.23): whichever matches more rows."""
    one = sum(1 for want, got in pairs if abs(want - got) <= PCT_TOLERANCE)
    hundred = sum(1 for want, got in pairs if abs(want * 100 - got) <= PCT_TOLERANCE * 100)
    return Decimal(100) if hundred > one else Decimal(1)


def _pct_scale(values: list) -> Decimal:
    """% of revenue printed as fractions (0.0142) or as numbers (1.42). A fraction column never goes past 150 %."""
    values = [abs(v) for v in values if v is not None]
    return Decimal(100) if values and max(values) > Decimal("1.5") else Decimal(1)


def check_lines(f: Facts, pack: Pack, period: str, fy: int, cov: Optional[Coverage] = None) -> list:
    cov = cov or Coverage()
    out = []
    lines = pack.of("lines")
    if not f.b.sales:
        cov.cannot(2, "chưa đọc được file doanh thu chi tiết, nên chưa tính được mẫu số %DTT",
                   "file doanh thu (kiểu Mastersale) có site, khối, kênh và doanh thu thuần hoặc thành tiền")
    if not f.cfg.channels:
        cov.cannot(2, "danh mục cấu hình chưa có quy tắc mẫu số %DTT theo kênh",
                   "sheet quy tắc mẫu số %DTT theo kênh trong danh mục cấu hình")
    if not any(g[0].pct for s in lines for g in s.sections()):
        cov.cannot(2, "bộ BOD không có cột %DTT", "cột %DTT trên bộ BOD")
    for s in lines:
        for groups in s.sections():
            cur = groups[0]
            months = _months_for(cur.value, period, fy, f.fy_start)
            pct_scale = _pct_scale([s.num(r, cur.pct) for r in s.rows]) if cur.pct else Decimal(1)
            # 2. denominators of the current scenario
            default = None
            if cur.pct is not None and f.b.sales:
                votes = Counter()
                for r in s.rows:
                    v, p = s.num(r, cur.value), s.num(r, cur.pct)
                    if v is None or p is None or f.channel(r.label, r.section):
                        continue
                    for name, base in f.candidate_bases(months):
                        if abs(v / base - p / pct_scale) <= PCT_TOLERANCE:
                            votes[name] += 1
                default = votes.most_common(1)[0][0] if votes else None
            override = _rule_votes(f, s, cur, months, pct_scale)
            rev_here = next((s.num(r, cur.value) for r in s.rows if _is_revenue(r.label) and s.num(r, cur.value)), None)
            fill = {}                        # the %DTT a row should carry, for a row that prints none
            for r in s.rows:
                value = s.num(r, cur.value)
                if value is None:
                    continue
                pct = s.num(r, cur.pct) / pct_scale if cur.pct and s.num(r, cur.pct) is not None else None
                want, base_label = _want_pct(f, r, value, months, default, override)
                if pct is None:
                    w = want if want is not None else (value / rev_here if rev_here else None)
                    if w is not None:
                        fill[r.n] = w
                if cur.pct is not None and pct is not None and want is not None and base_label[1] == "rule":
                    cov.ran(2)
                    if abs(want - pct) > PCT_TOLERANCE:
                        used = next((n for n, b in f.candidate_bases(months, f.channel(r.label, r.section))
                                     if abs(value / b - pct) <= PCT_TOLERANCE), "")
                        if used:            # a printed % that fits no known revenue is not called wrong
                            out.append(Finding(2, "wrong_base", _cell(s, r, cur.pct),
                                               f"{r.label}: %DTT phải là {fmt_pct(want)} (chia cho {base_label[0]} theo quy tắc "
                                               f"kênh), bộ BOD ghi {fmt_pct(pct)}, đang chia cho {used}", want, pct))
                if cur.pct is not None and not _is_revenue(r.label):
                    cov.ran(3)
                    if pct is None and value:
                        out.append(Finding(3, "missing_pct", _cell(s, r, cur.pct),
                                           f"{r.label}: có giá trị {fmt_vnd(value)} đ nhưng thiếu %DTT"
                                           + (f", phải là {fmt_pct(want)}" if want is not None else ""), want, None))
            # 3. comparisons: gap in money and in points
            for g in groups[1:]:
                out += _check_compare(s, cur, g, pct_scale, cov, fill)
    return out


def _is_revenue(label: str) -> bool:
    return bool(re.search(r"doanh thu|\bdtt\b|net revenue|net sales", key(label)))


def _rule_votes(f: Facts, s: Sheet, cur: Group, months: list, pct_scale: Decimal) -> dict:
    """{rule text: base name} when most channels under one rule use the same base and Clicky's reading of the
    rule's words says otherwise: the client's words win over Clicky's parse, and one odd channel stays an error."""
    if cur.pct is None:
        return {}
    by_rule = defaultdict(list)
    for r in s.rows:
        channel = f.channel(r.label, r.section)
        v, p = s.num(r, cur.value), s.num(r, cur.pct)
        rule = f.cfg.channels.get(key(channel), ("", ""))[1] if channel else ""
        if not rule or not v or p is None:
            continue
        by_rule[rule].append({n for n, b in f.candidate_bases(months) if abs(v / b - p / pct_scale) <= PCT_TOLERANCE})
    out = {}
    for rule, sets in by_rule.items():
        votes = Counter(n for names in sets for n in names)
        if not votes:
            continue
        name, n = votes.most_common(1)[0]
        parsed = f.base_name(*f.base_of(rule))
        if name != parsed and n >= 2 and n > len(sets) / 2 and votes.get(parsed, 0) < n:
            out[rule] = name
    return out


def _want_pct(f: Facts, r: Row, value: Decimal, months: list, default: Optional[str], override: dict = None) -> tuple:
    """(pct it should be, (base name, 'rule' | 'default'))."""
    channel = f.channel(r.label, r.section)
    if channel:
        rule = f.cfg.channels.get(key(channel), ("", ""))[1]
        if rule and override and rule in override:
            base = dict(f.candidate_bases(months)).get(override[rule])
            if base:
                return value / base, (override[rule], "rule")
        if rule:
            site, division, ch = f.base_of(rule, f.site_in(r.label), channel)
            base = f.revenue(months, site, division, ch)
            if base:
                return value / base, (f.base_name(site, division, ch), "rule")
    site = f.site_in(r.label)
    if site:
        rule = f.cfg.rule("mau so", "site")
        if isinstance(rule, str):
            s2, division, ch = f.base_of(rule, site)
            base = f.revenue(months, s2 or site, division, ch)
            if base:
                return value / base, (f.base_name(s2 or site, division, ch), "rule")
    if default:
        base = dict(f.candidate_bases(months)).get(default)
        if base:
            return value / base, (default, "default")
    return None, ("", "")


def _compare_base(s: Sheet, cur: Group, g: Group, pct_scale: Decimal, cur_pct: dict) -> Optional[Decimal]:
    """Revenue of the compared scenario when its %DTT is not printed: the sheet's revenue row in that column,
    else the one revenue every printed point gap agrees on."""
    rev = next((s.num(r, g.value) for r in s.rows if _is_revenue(r.label) and s.num(r, g.value)), None)
    if rev:
        return rev
    if g.pp is None:
        return None
    best = None
    for scale in (Decimal(1), Decimal(100)):
        for sign in (1, -1):
            bases = []
            for r in s.rows:
                a, pp, v = cur_pct.get(r.n), s.num(r, g.pp), s.num(r, g.value)
                if a is None or pp is None or not v or _is_revenue(r.label):
                    continue
                b = a - sign * pp / scale
                if b > 0:
                    bases.append(v / b)
            if len(bases) < 2:
                continue
            bases.sort()
            mid = bases[len(bases) // 2]
            spread = (bases[-1] - bases[0]) / mid if mid else Decimal(1)
            if spread < Decimal("0.002") and (best is None or spread < best[0]):
                best = (spread, mid)
    return best[1] if best else None


def _check_compare(s: Sheet, cur: Group, g: Group, pct_scale: Decimal, cov: Optional[Coverage] = None,
                   fill: Optional[dict] = None) -> list:
    """Gaps against one compared scenario. fill: {row: the %DTT the row should carry} for rows that print no
    %DTT, so a row missing both its %DTT and its point gap is reported for both."""
    cov = cov or Coverage()
    out = []
    rows = [r for r in s.rows if s.num(r, cur.value) is not None and s.num(r, g.value) is not None]
    if g.diff is not None:
        pairs = [(s.num(r, cur.value) - s.num(r, g.value), s.num(r, g.diff)) for r in rows if s.num(r, g.diff) is not None]
        sign = _sign(pairs)
        for r in rows:
            cov.ran(3)
            want = sign * (s.num(r, cur.value) - s.num(r, g.value))
            got = s.num(r, g.diff)
            if got is None:
                out.append(Finding(3, "missing_diff", _cell(s, r, g.diff),
                                   f"{r.label}: thiếu chênh lệch bằng tiền so với {g.value.text}, phải là {fmt_vnd(want)} đ",
                                   want, None))
    if cur.pct is None:
        return out
    cur_pct = {r.n: s.num(r, cur.pct) / pct_scale for r in rows if s.num(r, cur.pct) is not None}
    if g.pp is None:
        if g.pct is not None and any(r.n in cur_pct and s.num(r, g.pct) is not None for r in rows):
            head = g.diff or g.pct
            out.append(Finding(3, "missing_pp_column", s.addr(s.header_row, head.index),
                               f"So với {g.value.text}: có %DTT hai bên nhưng không có cột chênh lệch điểm %"))
        return out
    if g.pct is not None:
        cmp_pct = {r.n: s.num(r, g.pct) / pct_scale for r in rows if s.num(r, g.pct) is not None}
    else:
        base = _compare_base(s, cur, g, pct_scale, cur_pct)
        cmp_pct = {r.n: s.num(r, g.value) / base for r in rows} if base else {}
    pairs = [(cur_pct[r.n] - cmp_pct[r.n], s.num(r, g.pp)) for r in rows
             if r.n in cur_pct and r.n in cmp_pct and s.num(r, g.pp) is not None and not _is_revenue(r.label)]
    scale = _pp_scale(pairs)
    sign = _sign([(w * scale, got) for w, got in pairs]) if pairs else 1
    cur_pct = {**{n: v for n, v in (fill or {}).items()}, **cur_pct}
    for r in rows:
        if r.n not in cur_pct or r.n not in cmp_pct or _is_revenue(r.label):
            continue
        cov.ran(3)
        want = sign * (cur_pct[r.n] - cmp_pct[r.n])
        got = s.num(r, g.pp)
        if got is None:
            out.append(Finding(3, "missing_pp", _cell(s, r, g.pp),
                               f"{r.label}: có chênh lệch bằng tiền nhưng thiếu chênh lệch điểm %, phải là {fmt_pct(want)} điểm",
                               want, None))
        elif abs(want - got / scale) > PCT_TOLERANCE:
            out.append(Finding(3, "wrong_pp", _cell(s, r, g.pp),
                               f"{r.label}: chênh lệch phải là {fmt_pct(want)} điểm (%DTT {cur.value.text} − %DTT {g.value.text}), "
                               f"bộ BOD ghi {fmt_pct(got / scale)}", want, got / scale))
    return out


def check_versions(f: Facts, pack: Pack, period: str, fy: int, cov: Optional[Coverage] = None) -> list:
    cov = cov or Coverage()
    out = []
    if not f.plans:
        cov.cannot(4, "chưa đọc được file kế hoạch hay file năm trước nào",
                   "file kế hoạch (AOP, AOPR) và file năm trước có phiên bản, tháng, phòng ban hoặc cost element")
    for s in pack.of("lines") + pack.of("other"):
        for groups in s.sections():
            cur = groups[0]
            for g in groups[1:]:
                col = g.value
                if col.version == "thuc te":
                    continue
                col_fy = col.fy if col.fy else (fy - 1 if col.previous else fy)
                if cur.value.fy and not col.fy and col.previous:
                    col_fy = cur.value.fy - (0 if cur.value.fy < fy else 1)
                months = [shift_year(m, col_fy - fy) for m in _months_for(col, period, fy, f.fy_start)]
                available = f.versions(col_fy)
                if not available:
                    if f.plans:
                        cov.cannot(4, f"cột '{col.text}' so với FY{col_fy % 100:02d}/{(col_fy + 1) % 100:02d} nhưng em không có "
                                      "số kế hoạch hay thực tế của năm đó",
                                   f"file năm trước (FY{col_fy % 100:02d}/{(col_fy + 1) % 100:02d}) có phiên bản, tháng, "
                                   "phòng ban hoặc cost element")
                    continue
                allowed = convention_previous(f.cfg, available) if col_fy < fy else []
                matched, cells, printed = Counter(), [], 0
                for r in s.rows:
                    match = f.matcher(r.label, r.section)
                    v = s.num(r, col)
                    if match is None or v is None:
                        continue
                    if not r.total and not _is_revenue(r.label) and not unassigned_label(r.label):
                        printed += 1
                    values = {ver: f.plan(ver, match, months) for ver in available}
                    if all(x is None for x in values.values()):
                        continue
                    hit = [ver for ver, x in values.items() if x is not None and abs(x - v) <= VND_TOLERANCE]
                    for h in hit:
                        matched[h] += 1
                    cells.append((r, v, values))
                if not matched:
                    if cells:
                        cov.cannot(4, f"cột '{col.text}' không khớp phiên bản nào của FY{col_fy % 100:02d}/{(col_fy + 1) % 100:02d} "
                                      f"({', '.join(v.upper() for v in available)})", "")
                    continue
                cov.ran(4, len(cells))
                found = sum(1 for r, _, _ in cells if not r.total)
                if found < printed and col_fy < fy:
                    cov.partly(4, f"cột '{col.text}': {printed - found}/{printed} dòng không tìm thấy số "
                                  f"FY{col_fy % 100:02d}/{(col_fy + 1) % 100:02d} để so (cần file năm trước có các dòng này)")
                used = matched.most_common(1)[0][0]
                if used == "thuc te":
                    continue            # last year's actual: a legitimate comparison, not a plan version
                right = None
                if col.version and col.version != "thuc te" and not any(_same_version(col.version, a) for a in [used]):
                    right = next((a for a in available if _same_version(a, col.version)), None)
                    why = f"cột ghi {col.version.upper()} nhưng số lấy từ {used.upper()}"
                if right is None and allowed and not any(_same_version(used, a) for a in allowed):
                    right = allowed[0]
                    why = (f"năm trước phải so với {right.upper()} của năm đó, bộ BOD dùng {used.upper()}"
                           if col_fy < fy else f"phải so với {right.upper()}, bộ BOD dùng {used.upper()}")
                if right is None:
                    continue
                wrong = [(r, v, vals.get(right)) for r, v, vals in cells
                         if vals.get(right) is not None and abs(vals[right] - v) > VND_TOLERANCE]
                if not wrong:
                    continue
                main = next((w for w in wrong if w[0].total), wrong[0])
                fy_text = f"FY{col_fy % 100:02d}/{(col_fy + 1) % 100:02d}"
                out.append(Finding(4, "wrong_version", s.addr(s.header_row, col.index),
                                   f"Cột '{col.text}' ({fy_text}): {why}. {main[0].label} phải là {fmt_vnd(main[2])} đ, "
                                   f"bộ BOD ghi {fmt_vnd(main[1])} đ", main[2], main[1],
                                   [(_cell(s, r, col), want - v) for r, v, want in wrong]))
    return out


def check_months(f: Facts, pack: Pack, period: str, fy: int, cov: Optional[Coverage] = None) -> list:
    """A month already closed when the revised plan was approved must hold the actual. Only a cell that still
    holds the plan (AOP) is an error. A line the detail has no actual for (revenue, another division, a line
    the cost detail never carries) is not compared: it is said as not checked, never taken as 0."""
    cov = cov or Coverage()
    out = []
    months = fy_months(fy, f.fy_start)
    approvals = {}
    for p in f.plans:
        if p["version"].startswith("aopr") and p.get("approved"):
            approvals[p["version"]] = max(approvals.get(p["version"], ""), p["approved"])
    no_source = set()

    def has_actual(match) -> bool:
        return any(r["cost"] and f.in_division(r) and match(r) for r in f.gl)

    for s in pack.of("months"):
        text = key(" ".join([s.name, s.title] + [c.text for c in s.cols]))
        version = version_of(text)
        if version != "aopr" and not re.search(r"aopr ?\d", text):
            continue
        m_ = re.search(r"aopr ?(\d+)", text)
        version = f"aopr{m_.group(1)}" if m_ else "aopr"
        approved = approvals.get(version) or (max(approvals.values()) if approvals and version == "aopr" else "")
        locked, how = locked_months(f.cfg, fy, f.fy_start, period, approved)
        if not locked:
            cov.cannot(5, f"không biết tháng nào đã khoá khi lập {version.upper()}" if not how else
                       f"không có tháng nào khoá trước kỳ đang soát ({how})",
                       "ngày duyệt của bản AOPR (cột Ngày duyệt) hoặc dòng 'khoá sổ đến tháng' trong danh mục")
            continue
        cells = []
        for r in s.rows:
            if r.total or _is_revenue(r.label):
                if not r.total:
                    no_source.add(r.label)
                continue
            match = f.row_filter(r.label, r.section)
            if match is None:
                continue
            if not has_actual(match):
                no_source.add(r.label)
                continue
            for c in s.col("month"):
                m = next((x for x in months if int(x[5:]) == c.month), None)
                if m not in locked:
                    continue
                v = s.num(r, c)
                if v is not None:
                    cells.append((r, c, m, v, (f.actual(match, [m]), f.actual(match, [m], clean=True)), f.plan("aop", match, [m])))
        cov.ran(5, len(cells))
        out += _locked_findings(cells, lambda r, c, s=s: _cell(s, r, c), lambda r: r.label)
    # the revised plan file itself, row by row, in the division under review
    rows, other_division, unknown = [], 0, set()
    for p in f.plans:
        if not p["version"].startswith("aopr") or not (p["dept"] or p["element"] or p["dong"]):
            continue
        locked, how = locked_months(f.cfg, fy, f.fy_start, period, approvals.get(p["version"], ""))
        if not how:
            unknown.add(p["version"].upper())
            continue
        if p["period"] not in locked:
            continue
        if not f.in_division(p):
            other_division += 1
            continue
        if _is_revenue(p["dong"]):
            no_source.add(p["dong"])
            continue
        match = (lambda r, p=p: (not p["dept"] or r.get("dept") == p["dept"]) and (not p["element"] or r.get("element") == p["element"])
                 and (p["element"] or not p["dong"] or key(r.get("dong", "")) == key(p["dong"]))
                 and (not p["channel"] or r.get("channel") == p["channel"]) and (not p["site"] or key(r.get("site", "")) == key(p["site"]))
                 and (not p["division"] or not r.get("division") or f.same_division(r["division"], p["division"])))
        if not has_actual(match):
            no_source.add(" ".join(x for x in (p["dept"], p["element"], p["dong"]) if x))
            continue
        aop = [q for q in f.plans if q["version"] == "aop" and q["period"] == p["period"] and match(q)]
        mine = [r for r in f.gl if r["cost"] and r["period"] == p["period"] and f.in_division(r) and match(r)]
        act = (sum((r["amount"] for r in mine), ZERO), sum((r["amount"] for r in mine if not is_distributor_discount(r)), ZERO))
        rows.append((p, None, p["period"], p["amount"], act, sum((q["amount"] for q in aop), ZERO) if aop else None))
    cov.ran(5, len(rows))
    out += _locked_findings(rows, lambda p, c: p["where"],
                            lambda p: f"File AOPR, {' '.join(x for x in (p['site'], p['dept'], p['element'], p['dong']) if x)}",
                            code="locked_month_plan_file")
    if no_source:
        revenue = sorted(x for x in no_source if _is_revenue(x))
        need = ("file doanh thu theo tháng (bán hàng hoặc sổ cái 511) cho các dòng doanh thu" if revenue else
                "sổ chi tiết có các dòng này")
        cov.partly(5, f"{len(no_source)} dòng kế hoạch không có số thực tế cùng loại trong sổ chi tiết "
                      f"(vd. {(revenue or sorted(no_source))[0]}), nên chưa so. Cần: {need}")
    if unknown:
        cov.cannot(5, f"file {', '.join(sorted(unknown))} không cho biết tháng nào đã khoá khi lập",
                   "ngày duyệt của bản AOPR (cột Ngày duyệt) hoặc dòng 'khoá sổ đến tháng' trong danh mục")
    if not pack.of("months") and not rows and not unknown:
        cov.cannot(5, "không thấy bảng AOPR theo tháng trên bộ BOD, cũng không có dòng AOPR nào của tháng đã khoá",
                   "bảng AOPR theo tháng trên bộ BOD hoặc file AOPR có tháng, phòng ban/cost element, ngày duyệt")
    return out


def _locked_findings(cells: list, where, label, code: str = "locked_month_plan") -> list:
    """cells: [(row, col, month, printed, (actual, actual without the detail's own traps), AOP or None)]."""
    out = []
    fits = lambda v, acts: any(abs(v - a) <= VND_TOLERANCE for a in acts)
    for r, c, m, v, acts, plan in cells:
        act = acts[0]
        if fits(v, acts):
            continue
        if plan is None or abs(plan - v) > VND_TOLERANCE:
            continue                       # not the plan: a different actual (late entries) is not called wrong here
        what = f"đang là số kế hoạch AOP {fmt_vnd(v)} đ"
        out.append(Finding(5, code, where(r, c), f"{label(r)}, tháng {m[5:]}/{m[:4]} đã khoá sổ: AOPR phải là số thực tế "
                           f"{fmt_vnd(act)} đ, {what}", act, v))
    return out


CUT_ROLES = (("cut2", r"buoc ?2|cat phan (xin )?tang|phan xin tang|cat tang"), ("cut3", r"buoc ?3|cat deu|cat dong deu"),
             ("after", r"sau cat|con lai|duoc duyet|ngan sach duyet|final"),
             ("request", r"de xuat|xin|yeu cau|request|de nghi|du kien|nhu cau|dang ky|proposed|ke hoach phong"),
             ("base", r"co so|nam truoc|tham chieu|base|goc"))
TARGET_RE = re.compile(r"\bmuc tieu\b|can cat|so cat can|tong cat giam can|target|chenh (lech )?can cat|phai cat|"
                       r"khoang cach (den|toi) tran|vuot tran|tiet kiem|savings")


def _rule_texts(b: Bundle, pack: Pack) -> list:
    """Every sentence of the cut parameters and the configuration rules, folded."""
    from engine.bod_pack import read_sheet
    out = [f"{k} {key(v)}" for k, v in b.config.rules.items() if isinstance(v, str)]
    for s in [read_sheet(n, rows) for n, rows in b.cut] + pack.of("cut"):
        for x in s.grid:
            line = " ".join(key(v) for v in (x or []) if isinstance(v, str) and v.strip())
            if line:
                out.append(line)
    return out


def no_cut_steps(texts: list) -> set:
    """Which steps the no-cut list is spared from. The list is about the flat cut (step 3); the cut of the
    requested increase above the ceiling (step 2) applies to every department unless a rule sentence ties the
    list to step 2 or to every step."""
    steps = {"cut3"}
    step2 = r"(buoc ?2|phan xin tang|cat tang)"
    for t in texts:
        if not re.search(r"khong cat|mien cat|bao ve", t):
            continue
        if re.search(rf"{step2}.{{0,60}}(moi phong|tat ca|toan bo|ca phong|ap dung cho moi)", t):
            continue
        if re.search(r"ca (2|hai) buoc|moi buoc|tat ca cac buoc|buoc 2 va (buoc )?3|ca buoc 2", t) or \
                re.search(rf"(khong cat|mien cat|mien|khong (ap dung|tham gia|bi cat|bi|chiu)).{{0,25}}{step2}", t):
            steps.add("cut2")
    return steps


def cut_params(b: Bundle, pack: Pack) -> tuple:
    """(ceiling, where it was read, extra no-cut names, target to cut or None)."""
    from engine.bod_pack import read_sheet
    ceiling, where, names, target, pack_target = None, "", set(), None, None
    sheets = [read_sheet(n, rows) for n, rows in b.cut] + pack.of("cut")
    for s in sheets:
        for addr, label, v in s.loose:
            k = key(label)
            if CEILING_RE.search(k) and ceiling is None and not TARGET_RE.search(k):
                ceiling, where = v, addr
            elif TARGET_RE.search(k) and not LEVERS_RE.search(k):
                if s.kind != "cut" and target is None:
                    target = v
                elif s.kind == "cut" and pack_target is None:
                    pack_target = v
        if s.kind == "cut":
            for r in s.rows:
                k = key(r.label)
                if TARGET_RE.search(k) and not LEVERS_RE.search(k) and pack_target is None:
                    pack_target = next((s.num(r, c) for c in s.cols if s.num(r, c)), None)
        for x in s.grid:
            cells = [str(v) for v in (x or []) if isinstance(v, str)]
            for i, t in enumerate(cells):
                if re.search(r"khong cat|mien cat", key(t)) and i + 1 < len(cells):
                    listed = re.sub(r"\(.*?\)", " ", cells[i + 1])
                    names |= {p.strip() for p in re.split(r"[;,\n]", listed) if p.strip()}
        if s.kind != "cut":
            from engine.bod_inputs import CONFIG_SPECS, _truthy
            from engine.tables import read_table
            t = read_table(s.grid, s.name, CONFIG_SPECS["depts"], 2)
            if t is not None and "khong_cat" in t.columns:
                for _, v in t.rows:
                    if _truthy(t.get(v, "khong_cat", "")):
                        names.add(str(t.get(v, "ma", "") or t.get(v, "ten", "")).strip())
    return ceiling, where, {n for n in names if n}, target if target is not None else pack_target


def check_cuts(f: Facts, b: Bundle, pack: Pack, cov: Optional[Coverage] = None) -> list:
    cov = cov or Coverage()
    out = []
    ceiling, _, extra, target = cut_params(b, pack)
    no_cut = f.cfg.no_cut() | extra
    spared = no_cut_steps(_rule_texts(b, pack))
    for s in pack.of("cut"):
        roles = {}
        for c in s.cols:
            if c.role in ("label",):
                continue
            for name, rx in CUT_ROLES:
                if name not in roles and re.search(rx, c.k):
                    roles[name] = c
                    break
        if "cut2" not in roles and "cut3" not in roles:
            continue
        lines, total_row = [], None
        for r in s.rows:
            if r.total:
                total_row = r
                continue
            k = key(r.label)
            if (TARGET_RE.search(k) or CEILING_RE.search(k) or LEVERS_RE.search(k)) and not f.matcher(r.label, r.section):
                continue                     # 'Mục tiêu', 'Trần', 'Tổng đòn bẩy': not a department
            c2, c3 = s.num(r, roles.get("cut2")) or ZERO, s.num(r, roles.get("cut3")) or ZERO
            lines.append((r, s.num(r, roles.get("request")), abs(c2), abs(c3)))
        cov.ran(6, len(lines))
        for r, _, c2, c3 in lines:
            k = key(r.label)
            tokens = {key(t) for t in re.split(r"[\s\-–:|/]+", r.label)}
            if not any(key(n) == k or key(n) in tokens or (len(key(n)) > 3 and key(n) in k) for n in no_cut):
                continue
            for step, amount in (("cut2", c2), ("cut3", c3)):
                if step in spared and amount:
                    name = "bước 3 (cắt đều)" if step == "cut3" else "bước 2 (cắt phần xin tăng)"
                    out.append(Finding(6, "no_cut_touched", _cell(s, r, roles[step]),
                                       f"{r.label} thuộc danh sách không cắt nên không bị cắt ở {name}, "
                                       f"nhưng bộ BOD cắt {fmt_vnd(amount)}", ZERO, amount))
        levers = sum((c2 + c3 for _, _, c2, c3 in lines), ZERO)
        requests = [q for _, q, _, _ in lines if q is not None]
        asked = sum(requests, ZERO)
        if target is not None and requests and abs(target) > asked / 2:
            # "Mục tiêu chi phí" is a spending target, i.e. a ceiling, not the amount to cut
            ceiling, target = abs(target), None
        gap = target if target is not None else (asked - ceiling if ceiling is not None and requests else None)
        where_total = next((addr for addr, label, _ in s.loose if LEVERS_RE.search(key(label))), "")
        if not where_total and total_row is not None:
            where_total = _cell(s, total_row, roles.get("cut3") or roles.get("cut2"))
        if gap is None:
            cov.partly(6, "tổng đòn bẩy (không thấy mục tiêu cắt, hoặc trần và cột đề xuất)")
        elif abs(levers - abs(gap)) > VND_TOLERANCE:
            out.append(Finding(6, "levers_vs_gap", where_total or s.addr(s.header_row, 0),
                               f"Tổng đòn bẩy cắt {fmt_vnd(levers)} không bằng tổng chênh cần cắt {fmt_vnd(abs(gap))}"
                               + (" (mục tiêu trong file tham số)" if target is not None else " (đề xuất trừ trần)"),
                               abs(gap), levers))
    if not pack.of("cut"):
        cov.cannot(6, "không thấy bảng cắt giảm có cột bước 2 hoặc bước 3 trên bộ BOD", "bảng cắt giảm 3 bước trên bộ BOD")
    if not any(x.code == "levers_vs_gap" for x in out):
        out += _loose_levers(pack)
    return out


def _loose_levers(pack: Pack) -> list:
    """A pack that prints 'Tổng đòn bẩy' and 'Tổng chênh cần cắt' as two figures: they must be equal."""
    levers = gap = None
    for s in pack.sheets:
        for addr, label, v in s.loose:
            k = key(label)
            if LEVERS_RE.search(k) and levers is None:
                levers = (addr, v)
            elif re.search(r"chenh (lech )?can cat|can cat giam|khoang cach (den|toi) tran|vuot tran", k) and gap is None:
                gap = (addr, v)
    if levers and gap and abs(abs(levers[1]) - abs(gap[1])) > VND_TOLERANCE:
        return [Finding(6, "levers_vs_gap", levers[0], f"Tổng đòn bẩy cắt {fmt_vnd(abs(levers[1]))} không bằng "
                        f"tổng chênh cần cắt {fmt_vnd(abs(gap[1]))}", abs(gap[1]), abs(levers[1]))]
    return []


def check_fx_sheets(f: Facts, b: Bundle, pack: Pack, period: str, fy: int, cov: Optional[Coverage] = None) -> tuple:
    from engine.bod_inputs import month_key
    cov = cov or Coverage()
    out, notes = [], []
    if not b.fx:
        cov.cannot(7, "chưa đọc được bảng tỷ giá", "file tỷ giá có tháng, loại tỷ giá (bình quân, cuối kỳ, AOP) và tỷ giá")
    convention = fx_convention(f.cfg)
    rates = dict(b.fx)
    aop_rates = [p["rate"] for p in f.plans if p["rate"] is not None and p["version"] == "aop"]
    if aop_rates:
        common = Counter(aop_rates).most_common(1)[0][0]
        for m in fy_months(fy, f.fy_start):
            rates.setdefault((m, "aop"), common)
    for s in pack.of("fx"):
        cols = {c.k: c for c in s.cols}
        usd = next((c for c in s.cols if re.search(r"\busd\b|nguyen te|ngoai te", c.k) and not re.search(r"ty gia|anh huong", c.k)), None)
        impact = next((c for c in s.cols if re.search(r"anh huong|chenh lech ty gia|fx impact|tac dong", c.k)), None)
        month_col = next((c for c in s.cols if re.fullmatch(r"(thang|ky|month|period)( .*)?", c.k)), None)
        if usd is None or impact is None:
            continue
        if convention is None:
            notes.append(("Chưa có quy ước loại tỷ giá", "Danh mục cấu hình chưa ghi ảnh hưởng tỷ giá dùng tỷ giá bình quân "
                          "hay cuối kỳ, nên em chưa soát bảng tỷ giá.", s.addr(s.header_row, impact.index)))
            cov.cannot(7, "danh mục cấu hình chưa ghi quy ước loại tỷ giá (bình quân hay cuối kỳ), em không đoán",
                       "dòng quy ước tỷ giá trong danh mục cấu hình")
            continue
        rows = []
        for r in s.rows:
            m = None
            if month_col is not None and month_col.index < len(r.values):
                m = month_key(r.values[month_col.index], None, f"{fy}/{(fy + 1) % 100:02d}", f.fy_start)
            m = m or month_key(r.label, None, f"{fy}/{(fy + 1) % 100:02d}", f.fy_start) or period
            rows.append((_cell(s, r, impact), r.label, m, s.num(r, usd), s.num(r, impact)))
        rows = [x for x in rows if x[3] is not None]
        checkable = [x for x in rows if (x[2], convention) in rates and (x[2], "aop") in rates and x[4] is not None]
        cov.ran(7, len(checkable))
        if rows and not checkable:
            cov.cannot(7, f"bảng tỷ giá không có tỷ giá {convention} và AOP của các tháng trên bảng ảnh hưởng tỷ giá",
                       "tỷ giá theo tháng và loại trong file tỷ giá")
        plus = minus = 0
        for _, _, m, u, imp in rows:
            ra, rp = rates.get((m, convention)), rates.get((m, "aop"))
            if ra is None or rp is None or imp is None:
                continue
            want = u * (ra - rp)
            plus += abs(want - imp) <= VND_TOLERANCE
            minus += abs(want + imp) <= VND_TOLERANCE
        if minus > plus:                    # printed as plan − actual: the column's own habit, not an error
            found = rv.check_fx([(w, l, m, u, -imp if imp is not None else None) for w, l, m, u, imp in rows], rates,
                                (convention, "aop"))
            for x in found:
                x.expected, x.actual = -x.expected, -x.actual
            out += found
        else:
            out += rv.check_fx(rows, rates, (convention, "aop"))
    if not pack.of("fx"):
        cov.cannot(7, "không thấy bảng ảnh hưởng tỷ giá trên bộ BOD", "bảng có cột USD (nguyên tệ) và cột ảnh hưởng tỷ giá")
    return out, notes


def data_notes(f: Facts, period: str, fy: int) -> list:
    """Traps in the detail itself. Said apart, never as pack errors."""
    notes = []
    months = set(fy_months(fy, f.fy_start))
    mine = [r for r in f.gl if f.in_division(r) and r["cost"]]
    no_dept = [r for r in mine if not r["dept"] and r["period"] in months]
    if no_dept:
        codes = sorted({r["dept_raw"] for r in no_dept if r.get("dept_raw")})
        unknown = [c for c in codes if key(c) not in f.known_depts]
        notes.append(("Chi phí chưa gán phòng ban",
                      f"{len(no_dept)} dòng, {fmt_vnd(sum((r['amount'] for r in no_dept), ZERO))} đ"
                      + (f" (mã phòng ghi '{', '.join(codes[:4])}'"
                         + (", không có trong danh mục phòng ban)" if unknown else ", danh mục ghi là chưa phân bổ)")
                         if codes else "")
                      + ": cần hỏi kế toán gán phòng.", ", ".join(r["where"] for r in no_dept[:5])))
    later = [r for r in mine if r["period"] > period]
    if later:
        notes.append(("Bút toán của kỳ sau trong file xuất",
                      f"{len(later)} dòng, {fmt_vnd(sum((r['amount'] for r in later), ZERO))} đ thuộc kỳ sau "
                      + ("(bút toán đảo) " if any(REVERSAL_RE.search(key(r['text'])) for r in later) else "")
                      + "nên không tính vào kỳ này.", ", ".join(r["where"] for r in later[:5])))
    disc = [r for r in mine if DISCOUNT_RE.search(key(r["text"])) and DISTRIBUTOR_RE.search(key(r["text"]))
            and r["period"] in months]
    if disc:
        notes.append(("Chiết khấu cho nhà phân phối đang ghi vào chi phí",
                      f"{len(disc)} dòng, {fmt_vnd(sum((r['amount'] for r in disc), ZERO))} đ ghi vào tài khoản chi phí "
                      f"({', '.join(sorted({r['account'] for r in disc}))}); thường phải là giảm trừ doanh thu (521). "
                      "Cần hỏi kế toán trước khi trình.", ", ".join(r["where"] for r in disc[:5])))
    return notes


MANY = 10


def _too_many(findings: list, cov: Coverage) -> tuple:
    """A group that finds more than MANY errors has most likely matched the wrong lines, not met a pack that is
    wrong everywhere. Those are not reported as errors: the group says it could not check, and every place is
    listed in the notes so nothing is dropped in silence."""
    counts = Counter(x.group for x in findings)
    keep, notes = [], []
    for g, n in counts.items():
        if n <= MANY:
            continue
        mine = [x for x in findings if x.group == g]
        cov.cannot(g, f"{n} chỗ lệch cùng lúc: nhiều khả năng em khớp sai dòng hoặc cột, không phải bộ BOD sai hàng loạt, "
                      "nên em chưa báo là lỗi", "chị xác nhận cách đọc bảng (dòng, cột, phiên bản) trong ghi chú")
        notes.append((f"Nhóm {g}: {n} chỗ lệch em chưa dám báo lỗi",
                      "; ".join(x.message for x in mine[:3]) + (" …" if n > 3 else ""),
                      ", ".join(x.where for x in mine[:8])))
    keep = [x for x in findings if counts[x.group] <= MANY]
    return keep, notes


def _merge(findings: list) -> list:
    """One planted mistake often touches a run of cells (a column, a row). Same check, same column: one finding."""
    from engine.saved_check import split
    out, by = [], defaultdict(list)
    for x in findings:
        if x.code in ("wrong_base", "missing_pct", "missing_pp", "wrong_pp", "missing_diff", "locked_month_plan",
                      "locked_month_plan_file") and "!" in x.where:
            try:
                sheet, cell = split(x.where)
            except ValueError:
                out.append(x)
                continue
            col = re.match(r"[A-Z]+", cell).group(0) if re.match(r"[A-Z]+", cell) else cell
            by[(x.code, sheet, col)].append(x)
        else:
            out.append(x)
    for (code, sheet, col), xs in by.items():
        if len(xs) == 1 or code not in ("locked_month_plan", "locked_month_plan_file"):
            out += xs
            continue
        head = xs[0]
        head.message += f" (và {len(xs) - 1} ô khác cùng cột)"
        head.causes = [(x.where, x.diff) for x in xs]
        out.append(head)
    return sorted(out, key=lambda x: (x.group, x.where))


def review(folder: Path, fy_start: int = 7) -> list:
    folder = Path(folder)
    b = load(folder, fy_start)
    fy_rule = num(b.config.rule("thang bat dau")) or num(b.config.rule("nien do"))
    fy_start = int(fy_rule) if fy_rule is not None and 1 <= int(fy_rule) <= 12 else fy_start
    scene_log.record("bod_receipt", files=[{"name": n, "role": role, "rows": used, "skipped": len(sk)}
                                           for n, role, used, sk in b.receipt],
                     unused=[{"name": n, "why": w} for n, w in b.unused])
    out = []
    for path in b.packs:
        pack = read_pack(path)
        rows = sum(len(s.rows) for s in pack.sheets)
        cells = sum(1 for s in pack.sheets for r in s.rows for c in s.cols
                    if c.role in ("value", "pct", "diff", "pp", "month") and s.num(r, c) is not None)
        receipt = [(n, role, rows if n == path.name else used, sk) for n, role, used, sk in b.receipt]
        rv_ = Review(folder, path.name, receipt=receipt, unused=b.unused)
        rv_.read = f"{len(pack.sheets)} sheet, {rows} dòng số liệu, {cells} ô có số"
        period = pack_period(pack, fy_start)
        if not period:
            counts = Counter(e.period for e in b.gl if e.period)
            month = pack_month(pack)
            pool = [p for p in counts if month is None or int(p[5:]) == month]
            # the latest month with real volume: a few next-period reversals in the export do not make a month
            busy = [p for p in pool if counts[p] >= max(counts.values()) / 4] or pool
            period = max(busy) if busy else ""
            if period:
                rv_.assumptions.append(f"Bộ BOD không ghi năm của kỳ; em lấy theo sổ chi tiết: {period[5:]}/{period[:4]}")
        division, how = find_division(pack, b)
        rv_.period, rv_.division = period, division
        rv_.assumptions.append(f"Khối: {division or 'tất cả'} (theo {how})")
        cov = rv_.coverage
        if not period:
            rv_.notes.append(("Không xác định được kỳ", "Không soát được bộ BOD khi không biết kỳ.", path.name))
            for g in GROUP_TITLES:
                cov.cannot(g, "không xác định được kỳ của bộ BOD", "tiêu đề có tháng và năm, hoặc sổ chi tiết có kỳ")
        else:
            fy = fiscal_year(period, fy_start)
            f = Facts(b, division, fy_start)
            found = check_totals(f, pack, period, fy, cov) + check_lines(f, pack, period, fy, cov) \
                + check_versions(f, pack, period, fy, cov) + check_months(f, pack, period, fy, cov) + check_cuts(f, b, pack, cov)
            from engine.bod_inputs import hint_of
            for name, why in b.unused:
                if hint_of(Path(name).stem) == "prior":
                    cov.cannot(4, f"phần so năm trước: chưa đọc được file {name} ({why})",
                               "file năm trước có phiên bản (hoặc một cột mỗi phiên bản), tháng hoặc kỳ, và dòng chỉ tiêu")
            fx, fx_notes = check_fx_sheets(f, b, pack, period, fy, cov)
            rv_.findings, doubtful = _too_many(_merge(found + fx), cov)
            rv_.notes = data_notes(f, period, fy) + fx_notes + doubtful
        for x in rv_.findings:
            scene_log.record("bod_finding", pack=path.name, group=x.group, code=x.code, where=x.where,
                             expected=None if x.expected is None else str(x.expected),
                             actual=None if x.actual is None else str(x.actual), message=x.message)
        for g, title, state, text in rv_.groups():
            scene_log.record("bod_group", pack=path.name, group=g, title=title, state=state, text=text)
        out.append(rv_)
    return out


# ── output ──────────────────────────────────────────────────────────────────────

def _num_text(x: Optional[Decimal], code: str) -> str:
    if x is None:
        return "—"
    return fmt_pct(x) if code in ("wrong_base", "missing_pct", "missing_pp", "wrong_pp") else fmt_vnd(x) + " đ"


def markdown(reviews: list) -> str:
    lines = [f"# Kết quả soát bộ BOD ({time.strftime('%d/%m/%Y %H:%M')})", "",
             "Trạng thái: SƠ BỘ. Em soát bằng code, số tính từ sổ chi tiết, kế hoạch và danh mục cấu hình trong thư mục. "
             "Không có gì rời khỏi máy.", ""]
    for r in reviews:
        lines += [f"## {r.pack}", "", f"Kỳ: {r.period[5:]}/{r.period[:4]}" if r.period else "Kỳ: chưa rõ",
                  f"Số lỗi tìm thấy: {len(r.findings)}", ""]
        if r.read:
            lines.append(f"- Đã đọc bộ BOD: {r.read}")
        for a in r.assumptions:
            lines.append(f"- Giả định: {a}")
        lines.append("")
        not_run = [g for g, _, state, _ in r.groups() if state == "chua"]
        partly = [g for g, _, state, text in r.groups() if state == "mot_phan" or (state == "loi" and NOT_RUN.lower() in text)]
        if not_run or partly:
            lines += [f"**{NOT_RUN}" + (f" {len(not_run)} nhóm ({', '.join(str(g) for g in not_run)})" if not_run else "")
                      + (f"{', ' if not_run else ' '}một phần của nhóm {', '.join(str(g) for g in partly)}" if partly else "")
                      + "**: kết quả dưới đây chưa đủ để trình. Lý do và file cần thêm ghi ở từng nhóm.", ""]
        lines += ["| Nhóm | Kết quả |", "| --- | --- |"]
        lines += [f"| {g}. {title} | {text} |" for g, title, _, text in r.groups()]
        lines.append("")
        for g, title, state, text in r.groups():
            xs = [x for x in r.findings if x.group == g]
            lines.append(f"### Nhóm {g}. {title}: {text}")
            for i, x in enumerate(xs, 1):
                lines.append(f"{i}. Ô `{x.where}`: {x.message}.")
                lines.append(f"   - Số đúng: {_num_text(x.expected, x.code)}; bộ BOD ghi: {_num_text(x.actual, x.code)}")
                for label, amount in x.causes:
                    lines.append(f"   - {label}: {fmt_vnd(D(amount))} đ" if amount is not None else f"   - {label}")
            lines.append("")
        lines += ["## Ghi chú về dữ liệu (không phải lỗi của bộ BOD)", ""]
        lines += [f"- {t}: {d} ({w})" for t, d, w in r.notes] or ["- Không có."]
        lines += ["", "## Phiếu nhận file", "", "| File | Vai trò | Dòng dùng | Dòng bỏ | Lý do bỏ |", "| --- | --- | --- | --- | --- |"]
        for name, role, used, skipped in r.receipt:
            reasons = "; ".join(sorted({why for _, why in skipped})) or "—"
            lines.append(f"| {name} | {role} | {used} | {len(skipped)} | {reasons} |")
        for name, why in r.unused:
            lines.append(f"| {name} | Chưa dùng | 0 | — | {why} |")
        lines.append("")
    return "\n".join(lines)


FINDING_HEADER = ["#", "Nhóm", "Ô trên bộ BOD", "Lỗi", "Số đúng", "Bộ BOD ghi", "Chênh", "Mã kiểm"]


def _cellval(x: Optional[Decimal]):
    return None if x is None else float(x)


def write_outputs(reviews: list, out_dir: Path) -> dict:
    """ket-qua.md and the findings workbook. Returns {"md", "xlsx", "verified"}."""
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font
    from engine import audit
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / "ket-qua.md"
    md.write_text(markdown(reviews), encoding="utf-8")
    period = next((r.period for r in reviews if r.period), "")
    xlsx = out_dir / f"Clicky_soat_BOD_{period.replace('-', '_') or 'ky'}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Ket qua soat"
    ws.append([f"Kết quả soát bộ BOD, kỳ {period[5:]}/{period[:4]}" if period else "Kết quả soát bộ BOD"])
    ws.append([f"Trạng thái: {audit.DRAFT}. Số do code tính, không do AI."])
    ws.append([])
    ws.append(FINDING_HEADER)
    expect = {}
    n = 0
    for r in reviews:
        for x in r.findings:
            n += 1
            ws.append([n, x.group, x.where, x.message, _cellval(x.expected), _cellval(x.actual), _cellval(x.diff), x.code])
            row = ws.max_row
            if x.expected is not None:
                expect[f"'Ket qua soat'!E{row}"] = x.expected
            pct = x.code in ("wrong_base", "missing_pct", "missing_pp", "wrong_pp")
            for c in ("E", "F", "G"):
                ws[f"{c}{row}"].number_format = "0.00%" if pct else '#,##0;(#,##0);"-"'
    ws["A1"].font = Font(bold=True, size=14)
    for cell in ws[4]:
        cell.font = Font(bold=True)
    for col, width in zip("ABCDEFGH", (5, 7, 22, 90, 18, 18, 16, 20)):
        ws.column_dimensions[col].width = width
    log = audit.AuditLog(period=f"{period[5:]}/{period[:4]}" if period else "")
    for r in reviews:
        log.coverage += [[name, used + len(sk), used, len(sk), "khớp"] for name, _, used, sk in r.receipt]
        for name, _, _, sk in r.receipt:
            for why, count in Counter(w for _, w in sk).items():
                log.dropped.append([f"{count} dòng", name, None, why])
        log.dropped += [[t, w, None, d] for t, d, w in r.notes]
        log.add("Ket qua soat!A5", f"Soát {r.pack}", "", f"{len(r.findings)} lỗi", "Soát bộ BOD trước khi trình",
                source=r.pack)
    gs = wb.create_sheet("Cac nhom kiem")
    gs.append(["Kết quả từng nhóm kiểm"])
    gs.append([])
    gs.append(["Bộ BOD", "Nhóm", "Nội dung", "Kết quả"])
    for r in reviews:
        for g, title, state, text in r.groups():
            gs.append([r.pack, g, title, text])
            if state in ("chua", "mot_phan") or NOT_RUN in text:
                log.dropped.append([f"Nhóm {g}: {title}", r.pack, None, text])
    gs["A1"].font = Font(bold=True, size=14)
    for col, width in zip("ABCD", (28, 7, 44, 120)):
        gs.column_dimensions[col].width = width
    bb = wb.create_sheet(audit.SHEET)
    rows_, _ = audit.grid(log)
    for line in rows_:
        bb.append([_cellval(v) if isinstance(v, Decimal) else v for v in line])
    wb.save(xlsx)
    # "Xong" means the saved file was opened again and holds what was computed.
    wb2 = load_workbook(xlsx, data_only=True)
    got = wb2["Ket qua soat"]
    verified = all(got[ref.split("!")[1]].value is not None and
                   abs(Decimal(str(got[ref.split("!")[1]].value)) - want) <= (PCT_TOLERANCE if abs(want) < 10 else VND_TOLERANCE)
                   for ref, want in expect.items())
    rows = sum(1 for row in got.iter_rows(min_row=5, values_only=True) if row[0] is not None)
    verified = verified and rows == n
    wb2.close()
    scene_log.record("bod_done", findings=n, md=str(md), xlsx=str(xlsx), verified=verified,
                     notes=sum(len(r.notes) for r in reviews),
                     not_checked=[g for r in reviews for g, _, state, _ in r.groups() if state == "chua"],
                     partly=[g for r in reviews for g, _, state, text in r.groups()
                             if state == "mot_phan" or (state == "loi" and NOT_RUN in text)])
    return {"md": md, "xlsx": xlsx, "verified": verified, "findings": n}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder")
    ap.add_argument("-o", "--out", help="thư mục ghi kết quả (mặc định: <thư mục>/Clicky_soat_BOD)")
    args = ap.parse_args(argv)
    folder = Path(args.folder)
    reviews = review(folder)
    if not reviews:
        print("Không thấy bộ BOD nào trong thư mục (tên file có chữ BOD, pack hoặc bản nháp).")
        return 2
    res = write_outputs(reviews, Path(args.out) if args.out else folder / "Clicky_soat_BOD")
    gaps = [f"nhóm {g}" for r in reviews for g, _, state, text in r.groups() if state in ("chua", "mot_phan") or NOT_RUN in text]
    print(f"Đã soát {len(reviews)} bộ BOD: {res['findings']} lỗi. Kết quả: {res['md']} và {res['xlsx']}"
          + ("" if res["verified"] else " (CHƯA khớp khi mở lại file)")
          + (f". {NOT_RUN}: {', '.join(gaps)} (xem lý do trong ket-qua.md)" if gaps else ""))
    return 0 if res["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
