"""The pictures the talk draws on the report, each from figures the code has locked (gate 1, item 18).

The talk names a beat (its "vai"): the size of the change, the contrast with revenue, the driver, the rest without
it, the cap. Each beat may carry one picture. The kind of picture is fixed per beat; every figure in it is either
printed in the report (its place is named in the source chip) or worked out by code with Decimal from printed
figures (the chip says «em tính» and shows the formula). Nothing here comes from an AI, and nothing is drawn when a
figure is missing, doubted, or does not add up.

Kinds:
- "cot": two or three columns side by side (plan and actual, actual and cap), the difference between them;
- "so_sanh": growth rates side by side (cost and revenue, the whole and the rest without the driver);
- "cau": a bridge from the plan total to the actual total, one step per row, the driver first;
- "duong": a month line, actual solid, forecast dashed and labelled «dự báo», the plan as a target.

Every column says what it is: "thuc_te" (actual), "du_bao" (a forecast or a revised plan) or "ke_hoach" (the plan).
Actual and forecast are never drawn the same way.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

from present import clicky_html as C
from present import story as S

ACTUAL = re.compile(r"thực hiện|thực tế|\bth\b|\btt\b|actual|\bact\b|lũy kế|ytd", re.I)
FORECAST = re.compile(r"dự báo|forecast|\bfc\b|ước|\ble\b|outlook", re.I)
REVISED = re.compile(r"aopr|điều chỉnh|revised|\bkhđc\b|\bdc\b|\breforecast\b", re.I)


def kind_of(name: str) -> str:
    """thuc_te, du_bao, dieu_chinh or ke_hoach, from the column's own name; a revised plan is not an actual."""
    if FORECAST.search(name or ""):
        return "du_bao"
    if REVISED.search(name or ""):
        return "dieu_chinh"
    if ACTUAL.search(name or ""):
        return "thuc_te"
    return "ke_hoach"


def _f(v: Decimal) -> float:
    return float(v)


def _bar(name: str, tok: str, v: Decimal, kind: Optional[str] = None, **extra) -> dict:
    return dict({"ten": name, "so": tok, "v": _f(v), "loai": kind or kind_of(name)}, **extra)


def _table_name(places: dict, t) -> str:
    p = places.get(t.id)
    sec = places.get(p.section) if p is not None and p.section else None
    head = (sec.anchor if sec is not None else "") or ""
    return re.sub(r"\s+", " ", head).strip().rstrip(".:")[:60]


def _src(places: dict, t, where: str) -> str:
    name = _table_name(places, t)
    return f"Bảng «{name}», {where}" if name else f"Bảng trong báo cáo, {where}"


def visuals(items: list, places: dict, en: bool = False, doubted: frozenset = frozenset()) -> list:
    """The pictures this report supports, one per beat at most, each with its source. Empty when the report has
    no plan-actual table (the talk is then the report in page order and draws nothing new)."""
    if en:
        return []
    t = S.main_table(places, en)
    if t is None:
        return []
    out: list = []
    p_tok, p_val = S._num(t.total, t.plan, en)
    a_tok, a_val = S._num(t.total, t.actual, en)
    if not p_val or p_val <= 0 or a_val <= 0:
        return []
    unit = t.unit or ""
    plan_n, act_n = _clip(t.plan_name, 18), _clip(t.actual_name, 18)   # the column's own short name, as printed
    tdelta = S.delta(t, t.total, en)
    up = a_val > p_val

    # quy_mo: the plan and the actual totals side by side, the gap between them
    if tdelta and tdelta[0] not in doubted:
        out.append({"vai": "quy_mo", "kieu": "cot", "tieu_de": f"{_cap_first(t.total.label)}: {plan_n} và {act_n}", "don_vi": unit,
                    "cot": [_bar(plan_n, p_tok, p_val, kind_of(t.plan_name)), _bar(act_n, a_tok, a_val, kind_of(t.actual_name))],
                    "chenh": {"so": tdelta[0], "dau": "+" if up else "−",
                              "cong_thuc": (f"{a_tok} − {p_tok}" if tdelta[2] else "")},
                    "nguon": _src(places, t, f"dòng {t.total.label}"), "tinh": bool(tdelta[2])})

    # mau_thuan: cost growth against revenue growth, the table's own rate when the printed one is in doubt
    con = S.contrast(items, en)
    if con is not None:
        it, cost, rev = con
        if rev.tok not in doubted:
            ctok, cv, how = cost.tok, C.value(cost.tok, en), ""
            if ctok in doubted:
                expr = f"({a_tok} - {p_tok}) / {p_tok} × 100"
                got = S.robust_token(expr, en)
                if got is None:
                    ctok = None
                else:
                    ctok, cv, how = got[0], S.evaluate(expr, en), S.pretty(expr)
            if ctok is not None and cv is not None and C.value(rev.tok, en) is not None:
                out.append({"vai": "mau_thuan", "kieu": "so_sanh", "tieu_de": "Tốc độ tăng", "don_vi": "%",
                            "cot": [_bar("Chi phí", ctok, cv, kind_of(t.actual_name),
                                         **({"cong_thuc": how} if how else {})),
                                    _bar("Doanh thu", rev.tok, C.value(rev.tok, en), "doanh_thu")],
                            "nguon": ("Em tính từ dòng Tổng; doanh thu theo câu báo cáo" if how else "Câu báo cáo «" + _clip(it.text) + "»"),
                            "tinh": bool(how)})

    # dong_luc: a bridge from the plan total to the actual total, the biggest rows first, the rest in one step
    groups = S.group_rows(t, en)
    moves = [(r, S.delta(t, r, en)) for r in t.rows if r.id not in groups]
    if tdelta and moves and all(d is not None for _r, d in moves):
        moves.sort(key=lambda x: -abs(x[1][1]))
        top = moves[:3]
        rest = moves[3:]
        steps = [_bar(S._short(r.label), d[0], d[1], "buoc", **({"cong_thuc": S.pretty(d[2])} if d[2] else {}))
                 for r, d in top]
        tinh = any(d[2] for _r, d in top)
        rv = sum((d[1] for _r, d in rest), Decimal(0))
        if rest and rv:                            # rows that cancel out are no step of their own
            places_ = max(S.decimals(d[0], en) for _r, d in moves)
            steps.append(_bar(f"{len(rest)} {t.what or 'dòng'} khác", S.say(abs(rv), places_, en), rv, "buoc",
                              cong_thuc=" ".join(("+ " if d[1] >= 0 else "− ") + d[0] for _r, d in rest).lstrip("+ ")))
            tinh = True
        total = sum((Decimal(str(s["v"])) for s in steps), Decimal(0)) + (rv if rest and not rv else 0)
        tol = Decimal(1).scaleb(-max(S.decimals(tdelta[0], en), 1)) * len(steps)
        if abs(total - tdelta[1]) <= tol:          # the steps add up to the change, or the bridge is not drawn
            for s in steps:                        # a fall is drawn down, its figure said as it is printed
                s["dau"] = "+" if s["v"] >= 0 else "−"
            share = S.say(abs(top[0][1][1]) / abs(tdelta[1]) * S.HUNDRED, 0, en) if tdelta[1] else ""
            out.append({"vai": "dong_luc", "kieu": "cau", "tieu_de": f"Từ {plan_n} đến {act_n}", "don_vi": unit,
                        "dau": _bar(plan_n, p_tok, p_val, kind_of(t.plan_name)),
                        "buoc": steps,
                        "cuoi": _bar(act_n, a_tok, a_val, kind_of(t.actual_name)),
                        "noi_bat": 0, "chi_phi": bool(C.COST_WORDS.search(" ".join((_table_name(places, t), t.total.label, t.plan_name,
                                                                                   " ".join(p.text for p in places.values() if p.kind == "title"))))
                                                      or all(C.COST_WORDS.search(r.label) for r in t.rows[:3])),
                        "ty_trong": ({"so": share, "cong_thuc": f"{top[0][1][0]} ÷ {tdelta[0]} × 100"}
                                                    if share and Decimal("0") < abs(top[0][1][1]) <= abs(tdelta[1]) else None),
                        "nguon": _src(places, t, "cột chênh lệch từng dòng") + ("; em tính phần cộng dồn" if tinh else ""),
                        "tinh": tinh})

    # phan_con_lai: the whole, the rest without the driver, and revenue, side by side
    if tdelta and moves:
        top_r, (top_tok, top_val, top_f) = moves[0]
        tp, ta = S._num(top_r, t.plan, en), S._num(top_r, t.actual, en)
        if tp and ta and p_val - tp[1] > 0:
            num = f"(({a_tok} - {p_tok}) - ({ta[0]} - {tp[0]}))"
            expr = f"{num} / ({p_tok} - {tp[0]}) × 100"
            got = S.robust_token(expr, en)
            wexpr = f"({a_tok} - {p_tok}) / {p_tok} × 100"
            tpct = S._num(t.total, t.pct, en) if t.pct is not None else None   # the printed rate first
            whole = (tpct[0], tpct[1], "") if tpct else (lambda g: (g[0], S.evaluate(wexpr, en), S.pretty(wexpr)) if g else None)(S.robust_token(wexpr, en))
            if got and whole:
                ka = kind_of(t.actual_name)
                bars = [_bar("Cả bảng", whole[0], whole[1], ka, **({"cong_thuc": whole[2]} if whole[2] else {})),
                        _bar(f"Bỏ {S._short(top_r.label)} ra", got[0], S.evaluate(expr, en), ka, cong_thuc=S.pretty(expr))]
                if con is not None and con[2].tok not in doubted:
                    bars.append(_bar("Doanh thu", con[2].tok, C.value(con[2].tok, en), "doanh_thu"))
                out.append({"vai": "phan_con_lai", "kieu": "so_sanh", "tieu_de": "Tốc độ tăng, có và không có " + S._short(top_r.label),
                            "don_vi": "%", "cot": bars, "nguon": "Em tính từ dòng Tổng và dòng " + S._short(top_r.label), "tinh": True})

    # kq_cau: planned profit to actual profit, one step per row that moved it (TB3), drawn only when they add up
    pt = S.pl_table(places, en, skip=t.id)
    steps = S.pl_bridge(pt, en) if pt is not None else None
    pp, pa = (S._num(pt.total, pt.plan, en), S._num(pt.total, pt.actual, en)) if pt is not None else (None, None)
    if steps and len(steps) >= 2 and pp and pa and not any(x[1] in doubted for x in steps):
        pplan, pact = _clip(pt.plan_name, 18), _clip(pt.actual_name, 18)
        bars = [_bar(S._short(r.label), tok, eff, "buoc", **({"cong_thuc": S.pretty(d[2])} if d[2] else {})) for r, tok, eff, d in steps]
        for b in bars:
            b["dau"] = "+" if b["v"] >= 0 else "−"
        out.append({"vai": "kq_cau", "kieu": "cau", "tieu_de": f"{_cap_first(pt.total.label)}: từ {pplan} đến {pact}", "don_vi": pt.unit or "",
                    "dau": _bar(pplan, pp[0], pp[1], kind_of(pt.plan_name)), "buoc": bars,
                    "cuoi": _bar(pact, pa[0], pa[1], kind_of(pt.actual_name)),
                    "noi_bat": max(range(len(steps)), key=lambda k: abs(steps[k][2])), "chi_phi": False, "ty_trong": None,
                    "nguon": _src(places, pt, "cột chênh lệch từng dòng; chi phí tăng là lợi nhuận giảm"),
                    "tinh": any(d[2] for _r, _t, _e, d in steps)})

    # ca_nam: where the year lands, month by month added up; actual solid, forecast dashed «dự báo», AOP the target
    y = S.year_table(places, en)
    if y is not None:
        f = S.year_figures(y, en)
        run, acc = [], Decimal(0)
        for row, cols in ((y.actual, y.a_cols), (y.forecast, y.f_cols)):
            for i in cols:
                acc += S._num(row, i, en)[1]
                run.append(acc)
        na = len(y.a_cols)
        plan_run, pacc = [], Decimal(0)
        for i in (y.plan_cols if len(y.plan_cols) == len(y.months) else []):
            pacc += S._num(y.plan, i, en)[1]
            plan_run.append(_f(pacc))
        pname = "AOP" if re.search(r"\baop\b", y.plan.label or "", re.I) else "Kế hoạch"
        out.append({"vai": "ca_nam", "cung": ["ca_nam_du_bao", "ca_nam_khoang_cach"], "kieu": "duong",
                    "tieu_de": f"{_cap_first(y.subject or 'kết quả')} cộng dồn: thực hiện và dự báo", "don_vi": y.unit or "",
                    "thang": y.months, "thuc_te": [_f(v) for v in run[:na]], "du_bao": [_f(v) for v in run[na:]],
                    "ke_hoach": plan_run, "muc_tieu": {"v": _f(f["plan"][1]), "ten": f"{pname} cả năm", "so": f["plan"][0]},
                    "cuoi": {"v": _f(f["year"][1]), "so": f["year"][0]},
                    "chenh": {"so": f["gap"][0], "dau": "−" if f["gap"][1] < 0 else "+",
                              "cong_thuc": f"cả năm ước {f['year'][0]} − {pname} cả năm {f['plan'][0]}"},
                    "nguon": _src(places, y, "dòng Thực hiện, Dự báo, " + _clip(re.sub(r'^[\W_]+', '', y.plan.label), 24)) + "; em cộng dồn từng tháng",
                    "tinh": True})

    # tran: the actual total against the proposed cap, the cut between them (both printed, never a doubted cap)
    note, cards, _pairs = S.reco(items, places)
    cut = next((c for c in cards if c.figs and any(f.sign == "-" for f in c.figs)), None)
    cap = next((c for c in cards if c is not cut and c.figs and c.figs[0].level), None)
    if cap is not None and cut is not None:
        ctok = cap.figs[0].tok
        gtok = next(f.tok for f in cut.figs if f.sign == "-")
        cv, gv = C.value(ctok, en), C.value(gtok, en)
        if ctok not in doubted and gtok not in doubted and cv and gv and abs((a_val - cv) - gv) <= Decimal("0.051"):
            out.append({"vai": "tran", "kieu": "cot", "tieu_de": f"{act_n} và trần đề xuất", "don_vi": unit,
                        "cot": [_bar(act_n, a_tok, a_val, kind_of(t.actual_name)), _bar("Trần đề xuất", ctok, cv, "tran")],
                        "chenh": {"so": gtok, "dau": "−", "cong_thuc": ""},
                        "nguon": "Thẻ «" + _clip(cap.text, 40) + "» và «" + _clip(cut.text, 40) + "»", "tinh": False})
    return out


def _cap_first(s: str) -> str:
    s = (s or "").strip()
    return s[:1].upper() + s[1:]


def _clip(s: str, n: int = 60) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"
