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


def words(text) -> str:
    """Folded text with punctuation as spaces, for whole-word matching: 'DTT site S1, mọi khối' -> 'dtt site s1 moi khoi'."""
    return re.sub(r"[^0-9a-z%]+", " ", key(text)).strip()


class Facts:
    """The folder as records the pack's labels can be matched to."""

    def __init__(self, b: Bundle, division: str, fy_start: int):
        self.b, self.cfg, self.division, self.fy_start = b, b.config, division, fy_start
        prefixes = sorted({e["tk"] for e in self.cfg.elements.values() if e.get("tk")}, key=len, reverse=True)
        self.cost_prefixes = tuple(p for p in prefixes if p) or ("6",)
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
        for r in self.gl + self.plans:
            for dim in ("dong", "dept", "element", "channel", "nhom"):
                if r.get(dim):
                    names[dim].setdefault(key(r[dim]), r[dim])
        for k, (name, _) in self.cfg.channels.items():
            names["channel"].setdefault(k, name)
        self.names = names
        self.sites = sorted({s.site for s in b.sales if s.site} | {r["site"] for r in self.gl if r["site"]})
        self.divisions = sorted({s.division for s in b.sales if s.division} | {r["division"] for r in self.gl if r["division"]})

    def is_cost(self, account: str) -> bool:
        return any(str(account).startswith(p) for p in self.cost_prefixes) if account else True

    def _line_of(self, element: str, account: str) -> tuple:
        e = self.cfg.elements.get(element) or {}
        dong, nhom = e.get("dong", ""), e.get("nhom", "")
        if not dong:
            for m in self.cfg.bod:
                if m.get("ce") and m["ce"] == element or m.get("tk") and str(account).startswith(m["tk"]):
                    dong, nhom = m["dong"], nhom or m.get("nhom", "")
                    break
        return dong, nhom

    def _rec(self, e) -> dict:
        dong, nhom = self._line_of(e.element, e.account)
        return {"period": e.period, "amount": e.amount, "division": e.division, "site": e.site, "channel": e.channel,
                "dept": e.dept, "element": e.element, "account": e.account, "dong": dong, "nhom": nhom, "usd": e.usd,
                "text": e.text, "ref": e.ref, "where": e.where, "cost": self.is_cost(e.account)}

    def _plan(self, p) -> dict:
        dong, nhom = self._line_of(p.element, "")
        v = version_of(key(p.version)) or key(p.version)
        return {"version": v, "period": p.month, "fy": fiscal_year(p.month, self.fy_start) if p.month else None,
                "amount": p.amount, "division": p.division, "dept": p.dept, "element": p.element,
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

    def dim_of(self, label: str, section: str = "") -> str:
        for text in (label, f"{section} {label}"):
            k = key(text)
            for dim in ("dong", "dept", "element", "channel", "nhom"):
                if k in self.names[dim]:
                    return dim
        return ""

    def in_division(self, r: dict) -> bool:
        return not self.division or not r.get("division") or key(r["division"]) == key(self.division)

    def actual(self, match: Callable, periods) -> Decimal:
        periods = set(periods)
        return sum((r["amount"] for r in self.gl if r["cost"] and r["period"] in periods and self.in_division(r)
                    and match(r)), ZERO)

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
                and (division is None or key(s.division) == key(division))
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
    rule = b.config.rule("khoi")
    if isinstance(rule, str) and rule.strip():
        return rule.strip(), "danh mục cấu hình"
    if len(names) == 1:
        return names[0], "sổ chi tiết chỉ có một khối"
    return "", "không xác định"


def locked_months(cfg: Config, fy: int, fy_start: int, period: str) -> list:
    months = fy_months(fy, fy_start)
    for words in (("khoa", "den"), ("khoa so",), ("lock",), ("da khoa",)):
        v = cfg.rule(*words)
        if v is None:
            continue
        from engine.bod_inputs import month_key
        m = month_key(v, None, f"{fy}/{(fy + 1) % 100:02d}", fy_start)
        if m and m in months:
            return months[:months.index(m) + 1]
    return months[:months.index(period) + 1] if period in months else []


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


def _months_for(col: Col, period: str, fy: int, fy_start: int) -> list:
    if col.ytd:
        months = fy_months(fy, fy_start)
        return months[:months.index(period) + 1] if period in months else [period]
    return [period]


def _cell(sheet: Sheet, row: Row, col: Optional[Col]) -> str:
    return sheet.addr(row.n, col.index) if col is not None else sheet.addr(row.n, 0)


def check_totals(f: Facts, pack: Pack, period: str, fy: int) -> list:
    out = []
    for s in pack.sheets:
        if s.kind not in ("lines", "other"):
            continue
        cands = []
        if s.kind == "lines":
            dims = Counter(f.dim_of(r.label, r.section) for r in s.rows if not r.total)
            rows_are = dims.most_common(1)[0][0] if dims else ""
            for r in s.rows:
                label = key(r.label)
                if not r.total or rows_are not in ("dept", "element", "dong", "nhom", ""):
                    continue
                if "doanh thu" in label or "dtt" in label:
                    continue
                for i, groups in enumerate(s.sections()):
                    g = groups[0]
                    if g.value.version not in ("", "thuc te") or g.value.previous:
                        continue
                    v = s.num(r, g.value)
                    if v is not None:
                        cands.append((_cell(s, r, g.value), v, _months_for(g.value, period, fy, f.fy_start), i))
            best = {}
            for c in cands:                                  # subtotals ('Cộng chi phí bán hàng') are not the total
                if c[3] not in best or abs(c[1]) > abs(best[c[3]][1]):
                    best[c[3]] = c
            cands = [c[:3] for c in best.values()]
        for where, label, v in (s.loose if s.kind == "other" else []):
            if re.search(r"tong chi phi", key(label)) and not any(c[0] == where for c in cands):
                cands.append((where, v, [period] if not re.search(r"luy ke|ytd", key(label)) else
                              _months_for(Col(0, "", "", "value", ytd=True), period, fy, f.fy_start)))
        for where, value, months in cands:
            out += _total_finding(f, where, value, months)
    return out


def _total_finding(f: Facts, where: str, value: Decimal, months: list) -> list:
    months = set(months)
    first, last = min(months), max(months)
    mine = [r for r in f.gl if f.in_division(r) and r["cost"]]
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
         sum((r["amount"] for r in f.gl if r["cost"] and r["period"] in months and not f.in_division(r)), ZERO)),
        ("Chi phí chưa gán phòng ban (bộ BOD bỏ ra)", -sum((r["amount"] for r in inside if not r["dept"]), ZERO)),
        ("Chiết khấu cho nhà phân phối ghi vào chi phí (bộ BOD bỏ ra)",
         -sum((r["amount"] for r in inside if distributor(r)), ZERO)),
        ("Dòng không thuộc tài khoản chi phí (bộ BOD cộng vào)",
         sum((r["amount"] for r in f.gl if not r["cost"] and r["period"] in months and f.in_division(r)), ZERO)),
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


def check_lines(f: Facts, pack: Pack, period: str, fy: int) -> list:
    out = []
    for s in pack.of("lines"):
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
            for r in s.rows:
                value = s.num(r, cur.value)
                if value is None:
                    continue
                pct = s.num(r, cur.pct) / pct_scale if cur.pct and s.num(r, cur.pct) is not None else None
                want, base_label = _want_pct(f, r, value, months, default, override)
                if cur.pct is not None and pct is not None and want is not None and base_label[1] == "rule" \
                        and abs(want - pct) > PCT_TOLERANCE:
                    used = next((n for n, b in f.candidate_bases(months, f.channel(r.label, r.section))
                                 if abs(value / b - pct) <= PCT_TOLERANCE), "")
                    if not used:
                        continue        # the printed % fits no known revenue: not sure enough to call it wrong
                    out.append(Finding(2, "wrong_base", _cell(s, r, cur.pct),
                                       f"{r.label}: %DTT phải là {fmt_pct(want)} (chia cho {base_label[0]} theo quy tắc kênh), "
                                       f"bộ BOD ghi {fmt_pct(pct)}, đang chia cho {used}", want, pct))
                if cur.pct is not None and pct is None and value and not _is_revenue(r.label):
                    out.append(Finding(3, "missing_pct", _cell(s, r, cur.pct),
                                       f"{r.label}: có giá trị {fmt_vnd(value)} đ nhưng thiếu %DTT"
                                       + (f", phải là {fmt_pct(want)}" if want is not None else ""), want, None))
            # 3. comparisons: gap in money and in points
            for g in groups[1:]:
                out += _check_compare(s, cur, g, pct_scale)
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


def _check_compare(s: Sheet, cur: Group, g: Group, pct_scale: Decimal) -> list:
    out = []
    rows = [r for r in s.rows if s.num(r, cur.value) is not None and s.num(r, g.value) is not None]
    if g.diff is not None:
        pairs = [(s.num(r, cur.value) - s.num(r, g.value), s.num(r, g.diff)) for r in rows if s.num(r, g.diff) is not None]
        sign = _sign(pairs)
        for r in rows:
            want = sign * (s.num(r, cur.value) - s.num(r, g.value))
            got = s.num(r, g.diff)
            if got is None:
                out.append(Finding(3, "missing_diff", _cell(s, r, g.diff),
                                   f"{r.label}: thiếu chênh lệch bằng tiền so với {g.value.text}, phải là {fmt_vnd(want)} đ",
                                   want, None))
    if cur.pct is None or g.pct is None:
        return out
    pcts = [(r, s.num(r, cur.pct), s.num(r, g.pct)) for r in rows]
    if g.pp is None:
        if any(a is not None and b is not None for _, a, b in pcts):
            head = g.diff or g.pct
            out.append(Finding(3, "missing_pp_column", s.addr(s.header_row, head.index),
                               f"So với {g.value.text}: có %DTT hai bên nhưng không có cột chênh lệch điểm %"))
        return out
    pairs = [((a - b) / pct_scale, s.num(r, g.pp)) for r, a, b in pcts
             if a is not None and b is not None and s.num(r, g.pp) is not None]
    scale = _pp_scale(pairs)
    sign = _sign([(w * scale, got) for w, got in pairs]) if pairs else 1
    for r, a, b in pcts:
        if a is None or b is None or _is_revenue(r.label):
            continue
        want = sign * (a - b) / pct_scale
        got = s.num(r, g.pp)
        if got is None:
            out.append(Finding(3, "missing_pp", _cell(s, r, g.pp),
                               f"{r.label}: thiếu chênh lệch điểm %, phải là {fmt_pct(want)} điểm", want, None))
        elif abs(want - got / scale) > PCT_TOLERANCE:
            out.append(Finding(3, "wrong_pp", _cell(s, r, g.pp),
                               f"{r.label}: chênh lệch phải là {fmt_pct(want)} điểm (%DTT {cur.value.text} − %DTT {g.value.text}), "
                               f"bộ BOD ghi {fmt_pct(got / scale)}", want, got / scale))
    return out


def check_versions(f: Facts, pack: Pack, period: str, fy: int) -> list:
    out = []
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
                    continue
                allowed = convention_previous(f.cfg, available) if col_fy < fy else []
                matched, cells = Counter(), []
                for r in s.rows:
                    match = f.matcher(r.label, r.section)
                    v = s.num(r, col)
                    if match is None or v is None:
                        continue
                    values = {ver: f.plan(ver, match, months) for ver in available}
                    hit = [ver for ver, x in values.items() if x is not None and abs(x - v) <= VND_TOLERANCE]
                    for h in hit:
                        matched[h] += 1
                    cells.append((r, v, values))
                if not matched:
                    continue
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


def check_months(f: Facts, pack: Pack, period: str, fy: int) -> list:
    out = []
    locked = locked_months(f.cfg, fy, f.fy_start, period)
    months = fy_months(fy, f.fy_start)
    for s in pack.of("months"):
        text = key(" ".join([s.name, s.title] + [c.text for c in s.cols]))
        if version_of(text) not in ("aopr",) and not re.search(r"aopr ?\d", text):
            continue
        for r in s.rows:
            match = f.matcher(r.label, r.section)
            if match is None:
                continue
            for c in s.col("month"):
                m = next((x for x in months if int(x[5:]) == c.month), None)
                if m not in locked:
                    continue
                v = s.num(r, c)
                if v is None:
                    continue
                act = f.actual(match, [m])
                if abs(v - act) <= VND_TOLERANCE:
                    continue
                plan = f.plan("aop", match, [m])
                if plan is not None and abs(plan - v) <= VND_TOLERANCE:
                    out.append(Finding(5, "locked_month_plan", _cell(s, r, c),
                                       f"{r.label}, tháng {m[5:]}/{m[:4]} đã khoá sổ: AOPR phải là số thực tế {fmt_vnd(act)} đ, "
                                       f"đang là số kế hoạch AOP {fmt_vnd(v)} đ", act, v))
    # the revised plan file itself
    revised = [p for p in f.plans if p["version"].startswith("aopr") and p["period"] in locked]
    for p in revised:
        match = lambda r, p=p: r.get("dept") == p["dept"] and r.get("element") == p["element"] and (
            not p["channel"] or r.get("channel") == p["channel"])
        if not p["dept"] and not p["element"]:
            continue
        aop = [q for q in f.plans if q["version"] == "aop" and q["period"] == p["period"] and match(q)]
        act = f.actual(match, [p["period"]])
        if aop and abs(sum(q["amount"] for q in aop) - p["amount"]) <= VND_TOLERANCE and abs(act - p["amount"]) > VND_TOLERANCE:
            out.append(Finding(5, "locked_month_plan_file", p["where"],
                               f"File AOPR, {p['dept']} {p['element']} tháng {p['period'][5:]}/{p['period'][:4]} đã khoá: "
                               f"phải là thực tế {fmt_vnd(act)} đ, đang là kế hoạch {fmt_vnd(p['amount'])} đ", act, p["amount"]))
    return out


CUT_ROLES = (("cut2", r"buoc ?2|cat phan (xin )?tang|phan xin tang|cat tang"), ("cut3", r"buoc ?3|cat deu|cat dong deu"),
             ("after", r"sau cat|con lai|duoc duyet|ngan sach duyet|final"),
             ("request", r"de xuat|xin|yeu cau|request|de nghi"), ("base", r"co so|nam truoc|tham chieu|base|goc"))


def cut_params(b: Bundle, pack: Pack) -> tuple:
    """(ceiling, where it was read, extra no-cut names)."""
    from engine.bod_pack import read_sheet
    ceiling, where, names = None, "", set()
    sheets = [read_sheet(n, rows) for n, rows in b.cut] + pack.of("cut")
    for s in sheets:
        for addr, label, v in s.loose:
            if CEILING_RE.search(key(label)) and ceiling is None:
                ceiling, where = v, addr
        for x in s.grid:
            cells = [str(v) for v in (x or []) if isinstance(v, str)]
            for i, t in enumerate(cells):
                if re.search(r"khong cat|mien cat", key(t)) and i + 1 < len(cells):
                    names |= {p.strip() for p in re.split(r"[;,\n]", cells[i + 1]) if p.strip()}
        if s.kind != "cut":
            from engine.bod_inputs import CONFIG_SPECS, _truthy
            from engine.tables import read_table
            t = read_table(s.grid, s.name, CONFIG_SPECS["depts"], 2)
            if t is not None and "khong_cat" in t.columns:
                for _, v in t.rows:
                    if _truthy(t.get(v, "khong_cat", "")):
                        names.add(str(t.get(v, "ma", "") or t.get(v, "ten", "")).strip())
    return ceiling, where, {n for n in names if n}


def check_cuts(f: Facts, b: Bundle, pack: Pack) -> list:
    out = []
    ceiling, _, extra = cut_params(b, pack)
    no_cut = f.cfg.no_cut() | extra
    for s in pack.of("cut"):
        roles = {}
        for c in s.cols:
            if c.role in ("label", "text"):
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
            c2, c3 = s.num(r, roles.get("cut2")) or ZERO, s.num(r, roles.get("cut3")) or ZERO
            cut_col = roles.get("cut2") if c2 else roles.get("cut3") or roles.get("cut2")
            lines.append(rv.CutLine(r.label, _cell(s, r, cut_col), s.num(r, roles.get("request")) or ZERO,
                                    s.num(r, roles.get("base")) or ZERO, abs(c2), abs(c3), s.num(r, roles.get("after"))))
        protected = set()
        for l in lines:
            k = key(l.dept)
            tokens = {key(t) for t in re.split(r"[\s\-–:|/]+", l.dept)}
            if any(key(n) == k or key(n) in tokens or (len(key(n)) > 3 and key(n) in k) for n in no_cut):
                protected.add(l.dept)
        where_total = ""
        for addr, label, _ in s.loose:
            if LEVERS_RE.search(key(label)):
                where_total = addr
        if not where_total and total_row is not None:
            where_total = _cell(s, total_row, roles.get("cut3") or roles.get("cut2"))
        if ceiling is None:
            for addr, label, v in s.loose:
                if CEILING_RE.search(key(label)):
                    ceiling = v
        found = rv.check_cut(lines, ceiling if ceiling is not None else ZERO, protected, where_total or s.addr(s.header_row, 0))
        if ceiling is None or "request" not in roles:
            found = [x for x in found if x.code != "levers_vs_gap"]
        out += found
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


def check_fx_sheets(f: Facts, b: Bundle, pack: Pack, period: str, fy: int) -> tuple:
    from engine.bod_inputs import month_key
    out, notes = [], []
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
            continue
        rows = []
        for r in s.rows:
            m = None
            if month_col is not None and month_col.index < len(r.values):
                m = month_key(r.values[month_col.index], None, f"{fy}/{(fy + 1) % 100:02d}", f.fy_start)
            m = m or month_key(r.label, None, f"{fy}/{(fy + 1) % 100:02d}", f.fy_start) or period
            rows.append((_cell(s, r, impact), r.label, m, s.num(r, usd), s.num(r, impact)))
        rows = [x for x in rows if x[3] is not None]
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
    return out, notes


def data_notes(f: Facts, period: str, fy: int) -> list:
    """Traps in the detail itself. Said apart, never as pack errors."""
    notes = []
    months = set(fy_months(fy, f.fy_start))
    mine = [r for r in f.gl if f.in_division(r) and r["cost"]]
    no_dept = [r for r in mine if not r["dept"] and r["period"] in months]
    if no_dept:
        notes.append(("Chi phí chưa gán phòng ban",
                      f"{len(no_dept)} dòng, {fmt_vnd(sum((r['amount'] for r in no_dept), ZERO))} đ: cần hỏi kế toán gán phòng.",
                      ", ".join(r["where"] for r in no_dept[:5])))
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
        rv_ = Review(folder, path.name, receipt=b.receipt, unused=b.unused)
        period = pack_period(pack, fy_start)
        if not period:
            counts = Counter(e.period for e in b.gl if e.period)
            month = pack_month(pack)
            pool = [p for p in counts if month is None or int(p[5:]) == month]
            # the latest month with real volume: a few next-period reversals in the export do not make a month
            busy = [p for p in pool if counts[p] >= max(counts.values()) / 4] or pool
            period = max(busy) if busy else ""
            rv_.assumptions.append(f"Bộ BOD không ghi năm của kỳ; em lấy theo sổ chi tiết: {period[5:]}/{period[:4]}")
        division, how = find_division(pack, b)
        rv_.period, rv_.division = period, division
        rv_.assumptions.append(f"Khối: {division or 'tất cả'} (theo {how})")
        if not period:
            rv_.notes.append(("Không xác định được kỳ", "Không soát được bộ BOD khi không biết kỳ.", path.name))
            out.append(rv_)
            continue
        fy = fiscal_year(period, fy_start)
        f = Facts(b, division, fy_start)
        found = check_totals(f, pack, period, fy) + check_lines(f, pack, period, fy) + check_versions(f, pack, period, fy) \
            + check_months(f, pack, period, fy) + check_cuts(f, b, pack)
        fx, fx_notes = check_fx_sheets(f, b, pack, period, fy)
        rv_.findings = _merge(found + fx)
        rv_.notes = data_notes(f, period, fy) + fx_notes
        for x in rv_.findings:
            scene_log.record("bod_finding", pack=path.name, group=x.group, code=x.code, where=x.where,
                             expected=None if x.expected is None else str(x.expected),
                             actual=None if x.actual is None else str(x.actual), message=x.message)
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
        for a in r.assumptions:
            lines.append(f"- Giả định: {a}")
        lines.append("")
        for g in sorted(GROUP_TITLES):
            xs = [x for x in r.findings if x.group == g]
            lines.append(f"### Nhóm {g}. {GROUP_TITLES[g]}: " + ("không thấy lỗi" if not xs else f"{len(xs)} lỗi"))
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
                     notes=sum(len(r.notes) for r in reviews))
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
    print(f"Đã soát {len(reviews)} bộ BOD: {res['findings']} lỗi. Kết quả: {res['md']} và {res['xlsx']}"
          + ("" if res["verified"] else " (CHƯA khớp khi mở lại file)"))
    return 0 if res["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
