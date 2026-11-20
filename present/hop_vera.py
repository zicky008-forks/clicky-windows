"""Hộp Vera: the seven everyday pieces Vera needs before she presents, and the check that they fit together.

Each piece is one small file the owner can read in Notepad. They live in one folder beside the report (or in the
memory folder, see present/tri_nho.py); the company's box stays on the company PC and never enters this repository.

  1 Từ điển số        tu_dien_so.yaml                 where each figure lives (sheet, key, unit), no numbers
  2 Bối cảnh công ty  boi_canh.yaml, TU_DOC.txt,      the company's words, abbreviations and how to say them,
                      CHU_BAO_CAO.txt                 who owns the report
  3 Sổ quy tắc        quy_tac.yaml, kiem_tra_truoc.yaml, BAI_HOC.md   rules every answer obeys
  4 Kịch bản kể       kich_ban.yaml (or <report>_kich_ban_Clicky.yaml) the talk
  5 Câu hỏi BOD đã duyệt  hoi_dap.yaml, SEP_HAY_HOI.md the questions the owner approved
  6 Nguồn bằng chứng  drill / what_if in hoi_dap.yaml, nguon.yaml   where each answer can be opened
  7 Sổ hẹn trả lời    CAU_TREO.md                     questions parked for the owner, and his promises

`open_box(folder)` says which pieces are there, part there or missing. `check(box, ...)` runs the six cross-piece
checks of the design (https://claude.ai/artifact/RGXCHqwz8RmkjdGFvHBbe6) and gives each a light: «xanh» (fits) or
«vàng» (the owner looks before the talk). A check that needs Excel or the deck and has neither is «vàng», never
«xanh»: nothing is passed without being looked at.

Rule 1 of the rule book (`khong_phan_xet`) is used by the presenter page too (present/clicky_html.py puts
`rules(box)` into the page): a judgement question («có đáng lo không») never gets «Có.» / «Không.»; the figure and
its direction are said, the judgement is the owner's.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from present.placeholders import tokens


def fold(s) -> str:
    s = unicodedata.normalize("NFD", str(s or "")).replace("đ", "d").replace("Đ", "D")
    return re.sub(r"\s+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn").lower()).strip()


@dataclass(frozen=True)
class Piece:
    so: int
    key: str
    ten: str
    viec: str                      # what it does, in everyday words
    files: tuple                   # any of these makes the piece present; the first is the one Vera drafts
    can_du: int = 1                # how many of the files make it whole


PIECES = (
    Piece(1, "tu_dien_so", "Từ điển số", "Mỗi con số nằm ở sheet nào, ô nào, đơn vị gì. Không chứa số.",
          ("tu_dien_so.yaml",)),
    Piece(2, "boi_canh", "Bối cảnh công ty", "Chữ viết tắt, tên kênh, tên phòng và cách đọc; ai là chủ báo cáo.",
          ("boi_canh.yaml", "TU_DOC.txt", "CHU_BAO_CAO.txt"), 2),
    Piece(3, "quy_tac", "Sổ quy tắc", "Những điều Vera luôn giữ khi trả lời, và các kiểm tra trước giờ trình.",
          ("quy_tac.yaml", "kiem_tra_truoc.yaml", "BAI_HOC.md"), 2),
    Piece(4, "kich_ban", "Kịch bản kể", "Lời kể từng cảnh, chỉ vào đâu, dừng ở đâu.",
          ("kich_ban.yaml", "*_kich_ban_Clicky.yaml")),
    Piece(5, "hoi_dap", "Câu hỏi BOD đã duyệt", "Câu sếp hay hỏi và câu trả lời anh đã duyệt.",
          ("hoi_dap.yaml", "SEP_HAY_HOI.md")),
    Piece(6, "nguon", "Nguồn bằng chứng", "Mỗi câu trả lời mở được ở đâu: sheet, ô, dòng sổ cái, file giải trình.",
          ("nguon.yaml", "hoi_dap.yaml")),
    Piece(7, "cau_treo", "Sổ hẹn trả lời", "Câu chưa trả lời được trong buổi họp, ghi lại để anh trả lời sau.",
          ("CAU_TREO.md",)),
)

# --- Sổ quy tắc --------------------------------------------------------------------------------------------
# Folded phrases that make a question a judgement. The owner adds his own in quy_tac.yaml (khong_phan_xet.tu).
JUDGE_WORDS = ("dang lo", "dang ngai", "dang quan ngai", "co sao khong", "co van de khong", "nghiem trong",
               "co on khong", "on khong", "co tot khong", "co xau khong", "nguy hiem", "dang bao dong",
               "co kha quan khong", "co chap nhan duoc khong", "chap nhan duoc khong", "co hop ly khong",
               "hop ly khong", "co nen lo khong")
JUDGE_NOTE = "Báo cáo chỉ ghi số, không đánh giá có đáng lo hay không."
VERDICT_LEAD = re.compile(r"^\s*(Có|Không|Chưa)(?: so với [^;.]+)?(?:; (?:có|không|chưa) so với [^;.]+)*\s*[,.]\s*"
                          r"(?:theo số trên bảng\.?\s*)?")
VERDICT_SAID = re.compile(r"\b(?:(?:rất|khá|hơi|không|chưa|cũng)\s+)?(?:đáng lo(?: ngại)?|nghiêm trọng|đáng báo động|"
                          r"ổn|chấp nhận được|hợp lý|khả quan|không sao)\s*[.,;!]", re.I)

DEFAULT_RULES = {
    "khong_phan_xet": {
        "bat": True,
        "viec": "Câu hỏi «có đáng lo / ổn / nghiêm trọng không» là việc của chủ báo cáo. Vera nói số và chiều "
                "lệch, không mở đầu bằng «Có.» hay «Không.», và nói rõ báo cáo không đánh giá.",
        "tu": list(JUDGE_WORDS),
        "cau": JUDGE_NOTE,
    },
    "luon_noi_ky_don_vi": {
        "bat": True,
        "viec": "Mỗi con số nói kèm kỳ (tháng, lũy kế, cả năm), phiên bản (AOP, AOPR, dự báo) và đơn vị.",
    },
    "muc_tieu_la_aop": {
        "bat": True,
        "viec": "Kế hoạch so sánh là AOP. Dự báo không bao giờ là mục tiêu, trừ khi anh ghi rõ. Có cả AOP và "
                "AOPR thì Vera hỏi anh dùng bản nào.",
    },
}

QUY_TAC_TEMPLATE = """# Sổ quy tắc của Vera (miếng ghép số 3 trong Hộp Vera)
# Mỗi quy tắc có «bat: có» hoặc «bat: không». Anh chỉ cần sửa chữ, không cần biết lập trình.
# File này nằm trên máy anh, không gửi đi đâu.

khong_phan_xet:
  bat: có
  viec: "Câu hỏi «có đáng lo / ổn / nghiêm trọng không» là việc của anh. Vera nói số và chiều lệch, không phán Có/Không."
  # thêm cụm từ của công ty anh (viết không dấu cũng được), mỗi cụm một dòng:
  them_tu: []
  cau: "Báo cáo chỉ ghi số, không đánh giá có đáng lo hay không."

luon_noi_ky_don_vi:
  bat: có

muc_tieu_la_aop:
  bat: có
"""


def _yes(v) -> bool:
    return v is True or fold(v) in ("co", "yes", "true", "1", "bat")


def rules(box: Optional["Box"] = None) -> dict:
    """The rule book: defaults, then the owner's quy_tac.yaml on top. Always every default rule, so a short file
    never switches a rule off by leaving it out."""
    out = {k: dict(v) for k, v in DEFAULT_RULES.items()}
    own = (box.data.get("quy_tac.yaml") if box else None) or {}
    for k, v in own.items() if isinstance(own, dict) else ():
        if k not in out or not isinstance(v, dict):
            continue
        if "bat" in v:
            out[k]["bat"] = _yes(v["bat"])
        if k == "khong_phan_xet":
            extra = [fold(x) for x in (v.get("them_tu") or []) if fold(x)]
            out[k]["tu"] = list(dict.fromkeys(list(JUDGE_WORDS) + extra))
            if str(v.get("cau") or "").strip():
                out[k]["cau"] = str(v["cau"]).strip()
    return out


def judgement(question: str, book: Optional[dict] = None) -> str:
    """The judgement phrase in a question («dang lo»), or "" when the question asks only for facts."""
    rule = (book or DEFAULT_RULES)["khong_phan_xet"]
    if not rule.get("bat", True):
        return ""
    x = " " + re.sub(r"[?.!,;:]+", " ", fold(question)) + " "
    x = re.sub(r"\s+", " ", x)
    return next((w for w in rule.get("tu") or JUDGE_WORDS if " " + w + " " in x), "")


def guard(question: str, answer: str, book: Optional[dict] = None) -> tuple:
    """Rule 1 applied to one answer: (answer as it may be said, True when the rule changed it)."""
    if not judgement(question, book):
        return answer, False
    note = (book or DEFAULT_RULES)["khong_phan_xet"].get("cau") or JUDGE_NOTE
    out = VERDICT_LEAD.sub("", str(answer), count=1)
    out = out[:1].upper() + out[1:]
    if "không đánh giá" not in out:
        out = out.rstrip() + " " + note
    return out, out != answer


# --- the box -----------------------------------------------------------------------------------------------
@dataclass
class Box:
    folders: list
    found: dict = field(default_factory=dict)       # piece key -> [Path]
    data: dict = field(default_factory=dict)        # file name -> parsed yaml or text
    broken: dict = field(default_factory=dict)      # file name -> why it could not be read

    def status(self, p: Piece) -> str:
        n = len(self.found.get(p.key) or [])
        return "đủ" if n >= p.can_du else "một phần" if n else "thiếu"


def _read(path: Path):
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1258", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    if path.suffix.lower() in (".yaml", ".yml"):
        return yaml.safe_load(text) or {}
    return text


def open_box(*folders) -> Box:
    """Read the pieces in the given folders (the report's folder, the box, the memory folder), first one wins."""
    dirs = [Path(f) for f in folders if f and Path(f).is_dir()]
    box = Box(folders=dirs)
    for p in PIECES:
        hits = []
        for name in p.files:
            for d in dirs:
                got = sorted(d.glob(name)) if "*" in name else ([d / name] if (d / name).is_file() else [])
                if got:
                    hits.append(got[0])
                    break
        if p.key == "nguon":                         # hoi_dap.yaml counts as evidence only when it holds drills
            hits = [h for h in hits if h.name != "hoi_dap.yaml" or _has_drill(h)]
        box.found[p.key] = hits
        for h in hits:
            key = "kich_ban.yaml" if h.name.endswith("_kich_ban_Clicky.yaml") else h.name
            if key in box.data or key in box.broken:
                continue
            try:
                box.data[key] = _read(h)
            except (OSError, yaml.YAMLError) as exc:
                box.broken[key] = str(exc).splitlines()[0][:160]
    return box


def _has_drill(path: Path) -> bool:
    try:
        bank = _read(path) or {}
    except (OSError, yaml.YAMLError):
        return False
    return any(isinstance(q, dict) and (q.get("drill") or q.get("what_if")) for q in bank.get("cau_hoi") or [])


# --- the six checks ----------------------------------------------------------------------------------------
@dataclass
class Light:
    ten: str
    den: str                       # "xanh" or "vàng"
    chi_tiet: str = ""

    @property
    def ok(self) -> bool:
        return self.den == "xanh"


def _script_texts(script: dict) -> list:
    out = []
    meta = script.get("meta") or {}
    out += [str(meta.get("mo_dau") or ""), str(meta.get("ket_thuc") or "")]
    for s in script.get("slides") or []:
        out.append(str(s.get("loi_noi") or ""))
        out += [str(c.get("shape_chua") or "") for c in s.get("chi_vao") or [] if isinstance(c, dict)]
    for s in script.get("canh") or []:                  # the HTML-report talk
        out += [str(c.get("noi") or "") for c in s.get("cau") or [] if isinstance(c, dict)]
    return out


def _bank(box: Box) -> list:
    return [q for q in (box.data.get("hoi_dap.yaml") or {}).get("cau_hoi") or [] if isinstance(q, dict)]


def _context_words(box: Box) -> set:
    words = set()
    for line in str(box.data.get("TU_DOC.txt") or "").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            words.add(line.split("=", 1)[0].strip().upper())
    ctx = box.data.get("boi_canh.yaml") or {}
    for k in ("viet_tat", "tu_rieng", "kenh", "phong"):
        v = ctx.get(k) if isinstance(ctx, dict) else None
        words |= {str(x).strip().upper() for x in (v.keys() if isinstance(v, dict) else v or [])}
    return words


ABBR = re.compile(r"(?<![\w{:])([A-Z][A-Z&]{1,5})(?![\w}])")
COMMON_ABBR = {"AOP", "AOPR", "KH", "TH", "DTT", "LE", "FC", "BOD", "CFO", "CEO", "FY", "YTD", "MTD", "VND", "VAT",
               "HR", "IT", "KPI", "P&L", "GL", "SG&A", "S&M", "EBIT", "EBITDA", "COGS", "PVM", "OK", "PPT", "AI", "FP&A",
               "T", "Q"}


def check(box: Box, resolver=None, deck=None) -> list:
    """The six checks between pieces. resolver/deck are the live Excel and PowerPoint (present/placeholders.py,
    office COM); without them the checks that need them are yellow and say why."""
    script = box.data.get("kich_ban.yaml") or {}
    dic = (box.data.get("tu_dien_so.yaml") or {}).get("khoa_chinh") or {}
    bank = _bank(box)
    book = rules(box)
    lights = []

    # 1 Kịch bản ↔ Từ điển số: every {tq:key} said or answered is in the dictionary
    toks = {m for t in _script_texts(script) + [str(q.get("tra_loi") or "") for q in bank] for m in tokens(t)}
    keys = {re.match(r"\{tq:([^}|]+)", t).group(1) for t in toks if t.startswith("{tq:")}
    if not script and not bank:
        lights.append(Light("Kịch bản ↔ Từ điển số", "vàng", "chưa có kịch bản hay câu hỏi BOD để soát"))
    elif not dic and keys:
        lights.append(Light("Kịch bản ↔ Từ điển số", "vàng", f"chưa có Từ điển số cho {len(keys)} chỗ số"))
    else:
        miss = sorted(keys - set(dic))
        lights.append(Light("Kịch bản ↔ Từ điển số", "vàng" if miss else "xanh",
                            ("chưa có trong Từ điển: " + ", ".join(miss[:12])) if miss else f"{len(keys)}/{len(keys)} chỗ số có trong Từ điển"))

    # 2 Từ điển số ↔ Excel: every key still reads a figure
    if resolver is None:
        lights.append(Light("Từ điển số ↔ Excel", "vàng", "chưa mở Excel số liệu để đọc thử"))
    else:
        bad = []
        for k in sorted(dic):
            r = resolver.fill("{tq:" + k + "}")
            bad += [f"{k} ({why})" for _, why in r.missing]
        lights.append(Light("Từ điển số ↔ Excel", "vàng" if bad else "xanh",
                            "; ".join(bad[:8]) if bad else f"{len(dic)}/{len(dic)} ô đọc được"))

    # 3 Slide ↔ Excel: the existing pre-talk check of kiem_tra_truoc.yaml
    specs = [c for c in (box.data.get("kiem_tra_truoc.yaml") or {}).get("kiem_tra") or [] if isinstance(c, dict)
             and c.get("loai") == "so_tren_slide"]
    if not specs:
        lights.append(Light("Slide ↔ Excel", "vàng", "Sổ quy tắc chưa có kiểm tra số trên slide"))
    elif resolver is None or deck is None:
        lights.append(Light("Slide ↔ Excel", "vàng", "chưa mở cả Excel và PowerPoint để so"))
    else:
        from present import preflight
        rep = preflight.run(specs, resolver, deck=deck)
        bad = [c for c in rep.checks if not c.ok]
        lights.append(Light("Slide ↔ Excel", "vàng" if bad else "xanh", "; ".join(c.detail for c in (bad or rep.checks))))

    # 4 Câu hỏi BOD ↔ Nguồn bằng chứng: each approved answer opens somewhere
    if not bank:
        lights.append(Light("Câu hỏi BOD ↔ Bằng chứng", "vàng", "chưa có câu hỏi BOD đã duyệt"))
    else:
        src = (box.data.get("nguon.yaml") or {}) if isinstance(box.data.get("nguon.yaml"), dict) else {}
        miss = [str(q.get("id") or q.get("hoi_mau") or "?") for q in bank
                if not (q.get("drill") or q.get("what_if") or src.get(str(q.get("id"))))]
        lights.append(Light("Câu hỏi BOD ↔ Bằng chứng", "vàng" if miss else "xanh",
                            ("chưa có chỗ mở: " + ", ".join(miss[:12])) if miss else f"{len(bank)}/{len(bank)} câu mở được nguồn"))

    # 5 Bối cảnh ↔ Slide: every abbreviation said or shown has a meaning and a reading
    shown = " ".join(_script_texts(script) + [str(q.get("tra_loi") or "") + " " + str(q.get("hoi_mau") or "") for q in bank])
    if deck is not None:
        try:
            shown += " " + " ".join(sh.text for n in range(1, deck.count() + 1) for sh in deck.slide_shapes(n))
        except Exception:  # noqa: BLE001 - a deck that cannot be read is said in check 3
            pass
    abbr = {m.group(1) for m in ABBR.finditer(re.sub(r"\{[^{}]*\}", " ", shown))} - COMMON_ABBR
    known = _context_words(box)
    miss = sorted(a for a in abbr if a not in known)
    lights.append(Light("Bối cảnh ↔ Slide", "vàng" if miss else "xanh",
                        ("chưa có nghĩa và cách đọc: " + ", ".join(miss[:12])) if miss else
                        (f"{len(abbr)} chữ viết tắt đều có trong Bối cảnh" if abbr else "không có chữ viết tắt lạ")))

    # 6 Sổ quy tắc ↔ Câu trả lời: no approved answer judges, and the rule is on
    bad = []
    if not book["khong_phan_xet"].get("bat", True):
        bad.append("quy tắc «không phán xét» đang tắt")
    for q in bank:
        a = str(q.get("tra_loi") or "")
        if (judgement(str(q.get("hoi_mau") or ""), book) and VERDICT_LEAD.match(a)) or VERDICT_SAID.search(a):
            bad.append(str(q.get("id") or q.get("hoi_mau")))
    lights.append(Light("Sổ quy tắc ↔ Câu trả lời", "vàng" if bad else "xanh",
                        ("câu đang phán xét: " + ", ".join(bad[:12])) if bad else "không câu nào phán «có/không đáng lo»"))
    return lights


def ensure_rules(folder) -> Path:
    """Write quy_tac.yaml with the default rules when the box has none. Never overwrites the owner's file."""
    p = Path(folder) / "quy_tac.yaml"
    if not p.exists():
        p.write_text(QUY_TAC_TEMPLATE, encoding="utf-8")
    return p


def summary(box: Box, lights: list) -> str:
    lines = ["Hộp Vera"]
    for p in PIECES:
        st = box.status(p)
        mark = {"đủ": "✓", "một phần": "◐", "thiếu": "○"}[st]
        lines.append(f"  {mark} {p.so}. {p.ten}: {st}" + (f" ({', '.join(h.name for h in box.found[p.key])})" if box.found[p.key] else ""))
    for name, why in box.broken.items():
        lines.append(f"  ! {name} không đọc được: {why}")
    green = sum(1 for x in lights if x.ok)
    lines.append(f"Soát trước giờ trình: {green}/{len(lights)} xanh")
    for x in lights:
        lines.append(f"  {'🟢' if x.ok else '🟡'} {x.ten}: {x.chi_tiet}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m present.hop_vera", description="Mở Hộp Vera và soát 6 điểm khớp.")
    ap.add_argument("thu_muc", nargs="+", help="thư mục hộp (cạnh báo cáo), có thể thêm thư mục trí nhớ")
    ap.add_argument("--tao-quy-tac", action="store_true", help="tạo quy_tac.yaml mẫu nếu chưa có")
    ap.add_argument("--xlsx", help="file Excel số liệu (bản tập, đọc bằng openpyxl)")
    ap.add_argument("--deck", help="file PowerPoint (bản tập, đọc bằng python-pptx)")
    a = ap.parse_args(argv)
    if a.tao_quy_tac:
        ensure_rules(a.thu_muc[0])
    box = open_box(*a.thu_muc)
    resolver = deck = None
    if a.xlsx:
        from present.placeholders import OpenpyxlSource, Resolver
        from present.rehearse import recalc
        resolver = Resolver(OpenpyxlSource(recalc(a.xlsx)))
    if a.deck:
        from present.rehearse import PptxDeck
        deck = PptxDeck(a.deck)
    lights = check(box, resolver, deck)
    sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
    print(summary(box, lights))
    return 0 if all(x.ok for x in lights) else 1


if __name__ == "__main__":
    raise SystemExit(main())
