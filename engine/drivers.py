"""Volume rates for costs that move with sales volume ('Vận chuyển = 12.000 đồng/thùng × sản lượng kế hoạch'), so a
volume-driven cost is judged against its flexed budget (rate × actual volume), not only against the static plan.

Real plans give the rate in one of three places, all read:
- a note line on a plan sheet, usually under the cost table: 'Ghi chú: <element> = <rate> đồng/<unit> × sản lượng';
- a driver column ('Định mức', 'Đơn giá', 'đ/thùng', 'đ/đơn vị') beside a row that names the element;
- the company catalogue: a rule 'Định mức <element>' or an element's 'Định mức' column.
The unit is the catalogue's standard selling unit; the volume is the sales ledger's actual volume in that unit.
Two different rates for one element are never chosen between: the line says so and is judged against the static plan.
Amounts are Decimal; nothing here is written by the LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from engine.tables import key

NOTE_RE = re.compile(r"(?P<el>[^=:;\n]{2,60}?)\s*[=:]\s*(?P<rate>\d[\d.,]*)\s*(?:đồng|đ|vnd|vnđ|dong)\s*/\s*"
                     r"(?P<unit>[^\s×x*/,;.()]+)", re.IGNORECASE)
DRIVER_HEADS = ("dinh muc", "don gia", "d/thung", "dong/thung", "d/don vi", "dong/don vi", "rate", "driver")
LEAD_WORDS = re.compile(r"^\s*(ghi chu|note|luu y|chu thich|dien giai)\s*[:\-–]?\s*", re.IGNORECASE)


@dataclass
class Rate:
    element: str                 # the element code the plan rows use
    rate: Decimal                # đồng per unit
    unit: str                    # as printed
    where: str                   # 'file · sheet' or 'Danh mục'


@dataclass
class Rates:
    by_element: dict = field(default_factory=dict)    # element code -> Rate
    clash: dict = field(default_factory=dict)         # element code -> [Rate] that disagree: asked, never chosen

    def get(self, element: str) -> Optional[Rate]:
        return self.by_element.get(element)


def _num(text) -> Optional[Decimal]:
    if text is None or isinstance(text, bool):
        return None
    if isinstance(text, (int, float, Decimal)):
        return Decimal(str(text))
    t = str(text).strip()
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", t):          # 12.000: Vietnamese thousands
        t = t.replace(".", "")
    elif re.fullmatch(r"\d{1,3}(,\d{3})+", t):         # 12,000
        t = t.replace(",", "")
    else:
        t = t.replace(",", ".")
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _element_of(text: str, names: dict) -> Optional[str]:
    """The element code a phrase names: its code or its name (folded), the longest match wins."""
    t = f" {key(LEAD_WORDS.sub('', str(text)))} "
    hits = [(len(n), code) for code, n in names.items() if n and f" {n} " in t]
    hits += [(len(key(code)), code) for code in names if f" {key(code)} " in t]
    if not hits:                                        # 'Công tác phí' for the code CTP: its initials
        words = t.split()
        for code in names:
            c = key(code).replace(" ", "")
            if len(c) >= 2 and any("".join(w[0] for w in words[i:i + len(c)]) == c
                                   for i in range(len(words) - len(c) + 1)):
                hits.append((len(c), code))
    return max(hits)[1] if len(hits) == 1 or (hits and len({h[1] for h in hits}) == 1) else None


def element_names(config, plan_elements: set) -> dict:
    """{element code: folded name} from the catalogue, plus the plan's own element codes."""
    out = {code: key(e.get("ten", "")) for code, e in (getattr(config, "elements", {}) or {}).items()}
    for code in plan_elements:
        out.setdefault(code, "")
    return out


def from_text(text: str, names: dict, where: str) -> list:
    out = []
    for m in NOTE_RE.finditer(str(text)):
        code = _element_of(m.group("el"), names)
        rate = _num(m.group("rate"))
        if code and rate:
            out.append(Rate(code, rate, m.group("unit").strip(), where))
    return out


def from_sheet(rows: list, names: dict, where: str) -> list:
    """Note lines anywhere on the sheet, and a driver column beside a row naming an element."""
    out = []
    head, col = None, None
    for i, r in enumerate(rows or []):
        cells = list(r or [])
        texts = [str(v) for v in cells if isinstance(v, str) and v.strip()]
        for t in texts:
            out += from_text(t, names, where)
        if col is None:
            for j, v in enumerate(cells):
                if isinstance(v, str) and any(h in key(v).replace(" ", "") or h in key(v) for h in DRIVER_HEADS):
                    head, col = i, j
                    break
            continue
        if col < len(cells):
            rate = _num(cells[col])
            label = " ".join(str(v) for k, v in enumerate(cells) if k != col and isinstance(v, str))
            code = _element_of(label, names) if label else None
            if code and rate:
                unit = re.search(r"/\s*(\w+)", str(rows[head][col]))
                out.append(Rate(code, rate, unit.group(1) if unit else "đơn vị", where))
    return out


def from_config(config, names: dict) -> list:
    out = []
    for k, v in (getattr(config, "rules", {}) or {}).items():
        if k.startswith("dinh muc"):
            printed = (getattr(config, "rule_text", {}) or {}).get(k, k)
            code = _element_of(re.sub(r"(?i)^định mức|^dinh muc", "", str(printed)), names)
            rate = _num(v)
            if code and rate:
                out.append(Rate(code, rate, "đơn vị", "Danh mục"))
    for code, e in (getattr(config, "elements", {}) or {}).items():
        rate = _num(e.get("dinh_muc")) if isinstance(e, dict) else None
        if rate:
            out.append(Rate(code, rate, "đơn vị", "Danh mục"))
    return out


def read(plan_files: list, config, plan_elements: set) -> Rates:
    """Every rate the plan files and the catalogue give, one per element; a disagreement is kept as a clash."""
    from engine.tables import sheets_of
    names = element_names(config, plan_elements)
    found = from_config(config, names)
    for path in plan_files:
        try:
            sheets = sheets_of(Path(path))
        except Exception:                               # noqa: BLE001 - the plan was read already; no rate here
            continue
        for sheet, rows in sheets.items():
            where = Path(path).name if Path(path).suffix.lower() == ".csv" or len(sheets) == 1 else \
                f"{Path(path).name} · sheet {sheet}"
            found += from_sheet(rows, names, where)
    out = Rates()
    for r in found:
        have = out.by_element.get(r.element)
        if have is None and r.element not in out.clash:
            out.by_element[r.element] = r
        elif have is not None and have.rate != r.rate:
            out.clash[r.element] = [have, r]
            del out.by_element[r.element]
        elif r.element in out.clash and all(x.rate != r.rate for x in out.clash[r.element]):
            out.clash[r.element].append(r)
    return out
