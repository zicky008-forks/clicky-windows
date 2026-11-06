"""Checks before a talk. Any failure stops the talk: nothing is presented until all pass.

The checks are data (a list of dicts, usually loaded from the client's kiem_tra_truoc.yaml) built from
a few generic primitives, so no client cell address lives in this repository:

  {loai: so_slide, bang: 20}
  {loai: co_sheet, ten: [Tong_quan, Kich_ban, ...]}
  {loai: placeholder}                                      # every token in script + bank resolves
  {loai: so_tren_slide, slide: [1, 2, 16, 19], dinh_dang: bn}
  {loai: o_bang, o: "Kich_ban!F12:F20", gia_tri: 0}
  {loai: o_bang_khoa, o: "Kich_ban!B8", khoa: ratio_aop, sai_so: 0.0001}
  {loai: tong, cac_o: ["Kich_ban!B28", "Kich_ban!B29"], bang_o: "Kich_ban!B27", sai_so: 0.05}
  {loai: an_toan}                                          # local-only, offline voice, AI off
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from present.placeholders import Resolver, tokens
from present.vn_format import fmt

_SLIDE_NUMBER = re.compile(r"(?<![\d,.])\d{1,3}(?:\.\d{3})*,\d(?![\d])")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    checks: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def lines(self) -> list:
        return [("ĐẠT  " if c.ok else "KHÔNG ĐẠT  ") + c.name + (f": {c.detail}" if c.detail else "") for c in self.checks]


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _cells(resolver: Resolver, ref: str) -> list:
    from present.whatif import expand
    return [(one, resolver.source.cell(*one.split("!", 1))) for one in expand(ref)]


def run(checks: list, resolver: Resolver, deck=None, script: dict = None, bank: dict = None,
        safety: dict = None) -> Report:
    rep = Report()
    for spec in checks:
        kind = spec.get("loai")
        try:
            rep.checks.append(_one(kind, spec, resolver, deck, script or {}, bank or {}, safety or {}))
        except Exception as exc:            # noqa: BLE001 - a broken check is a failed check
            rep.checks.append(Check(str(kind), False, f"không chạy được: {exc}"))
    return rep


def _one(kind, spec, resolver, deck, script, bank, safety) -> Check:
    if kind == "so_slide":
        n = deck.count()
        return Check("Đủ slide", n == spec["bang"], f"{n}/{spec['bang']}")
    if kind == "co_sheet":
        have = set(resolver.source.sheet_names())
        miss = [s for s in spec["ten"] if s not in have]
        return Check("Đủ sheet", not miss, "thiếu " + ", ".join(miss) if miss else f"{len(spec['ten'])}/{len(spec['ten'])}")
    if kind == "placeholder":
        texts = []
        for s in script.get("slides", []):
            texts.append(s.get("loi_noi", ""))
            texts += [c.get("shape_chua", "") for c in s.get("chi_vao", []) or []]
        texts += [q.get("tra_loi", "") for q in bank.get("cau_hoi", []) if not q.get("what_if") and not q.get("tham_so")]
        seen, bad = set(), []
        for t in texts:
            for tok in tokens(t):
                if tok in seen or tok.startswith("{slide:"):
                    continue
                seen.add(tok)
                r = resolver.fill(tok)
                bad += [f"{a} ({b})" for a, b in r.missing]
        return Check("Mọi placeholder đọc được", not bad, "; ".join(bad) if bad else f"{len(seen)}/{len(seen)}")
    if kind == "so_tren_slide":
        allowed = set()
        for raw, k in resolver.summary().values():
            try:
                allowed.add(fmt(raw, spec.get("dinh_dang", "bn")).split(" ")[0])
            except ValueError:
                pass
        allowed |= set(spec.get("bo_qua") or [])     # fixed thresholds written on the slide, e.g. "0,5"
        total, bad = 0, []
        for n in spec["slide"]:
            for shape in deck.slide_shapes(n):
                for m in _SLIDE_NUMBER.finditer(shape.text):
                    total += 1
                    if m.group(0) not in allowed:
                        bad.append(f"slide {n}: {m.group(0)}")
        return Check("Số trên slide khớp Excel", not bad, (f"khớp {total - len(bad)}/{total}") + ("; lệch " + ", ".join(bad) if bad else ""))
    if kind == "o_bang":
        bad = [ref for ref, v in _cells(resolver, spec["o"]) if _num(v) is None or abs(_num(v) - spec["gia_tri"]) > spec.get("sai_so", 1e-9)]
        return Check(f"{spec['o']} = {spec['gia_tri']}", not bad, ", ".join(bad))
    if kind == "o_bang_khoa":
        raw, _ = resolver.summary()[spec["khoa"]]
        v = resolver.source.cell(*spec["o"].split("!", 1))
        ok = _num(v) is not None and _num(raw) is not None and abs(_num(v) - _num(raw)) <= spec.get("sai_so", 0.05)
        return Check(f"{spec['o']} khớp {spec['khoa']}", ok, "" if ok else "lệch")
    if kind == "tong":
        parts = [_num(resolver.source.cell(*r.split("!", 1))) for r in spec["cac_o"]]
        target = _num(resolver.source.cell(*spec["bang_o"].split("!", 1)))
        ok = None not in parts and target is not None and abs(sum(parts) - target) <= spec.get("sai_so", 0.05)
        return Check(" + ".join(spec["cac_o"]) + " = " + spec["bang_o"], ok, "" if ok else "lệch")
    if kind == "an_toan":
        local = os.environ.get("CLICKY_LOCAL_ONLY") == "1"
        voice_ok = safety.get("voice_offline", False)
        ai_off = not safety.get("ai_on", True)
        rehearsal_fake = safety.get("synthetic", False)
        ok = rehearsal_fake or (local and voice_ok and ai_off)
        detail = f"local-only={'bật' if local else 'tắt'}, giọng={'offline' if voice_ok else 'online'}, AI={'tắt' if ai_off else 'bật'}"
        return Check("Chế độ an toàn", ok, detail + (" (số giả lập)" if rehearsal_fake else ""))
    return Check(str(kind), False, "không biết loại kiểm tra này")
