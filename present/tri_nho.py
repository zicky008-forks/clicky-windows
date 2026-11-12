"""Company memory for the presenter: three plain text files the owner reads and edits in Notepad.

- CAU_TREO.md: questions readers parked ("câu treo") and the owner's promises. The owner writes "Trả lời:" under
  a question. The next build reads the answer aloud at the start of the talk, once per report, and keeps it as a
  ready answer if someone asks again.
- SEP_HAY_HOI.md: what each boss usually asks. After a build, these questions are tried on the new file so the
  owner learns which ones the report cannot answer yet. This file never goes into a report.
- BAI_HOC.md: lessons from earlier months, shown to the owner after each build.

The files live on the owner's PC (default: "Clicky tri nho" in Documents, or CLICKY_TRI_NHO). Nothing is sent.
A parked question reaches the owner by e-mail (the page's "Gửi" button). He copies that e-mail and runs
`python -m present.tri_nho ghi` (or the Ghi_cau_treo.bat launcher): the questions are added to CAU_TREO.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

TREO, SEP, BAI_HOC = "CAU_TREO.md", "SEP_HAY_HOI.md", "BAI_HOC.md"
MAIL_MARK = "Gửi từ Clicky"

TEMPLATES = {
    TREO: """# Câu treo và lời hứa

> Mỗi câu hỏi bắt đầu bằng "- Câu:". Anh điền dòng "Trả lời:" rồi kéo báo cáo vào Clicky như mọi khi.
> Câu đã có trả lời được Clicky đọc ở đầu bản trình bày kế tiếp (một lần cho mỗi báo cáo),
> và dùng để trả lời khi có người hỏi lại. Đừng xóa dòng "Đã nói trong:", Clicky dùng nó để không nhắc lại.
> Lời hứa bắt đầu bằng "- Hứa:". Ghi "Xong: có" khi đã làm. Clicky nhắc anh các lời hứa chưa xong sau mỗi lần dựng.
> Ví dụ:
>     - Câu: Vì sao khối CNTT tăng 12%?
>       Ai hỏi: anh Hùng
>       Trả lời: Do dự án ERP chuyển chi phí thuê ngoài sang lương.
>     - Hứa: Gửi chi tiết lương khối bán hàng cho CFO
>       Hạn: 20/11/2026

""",
    SEP: """# Các sếp hay hỏi gì

> Mỗi sếp một mục bắt đầu bằng "## ". Mỗi câu sếp hay hỏi là một dòng bắt đầu bằng "- ".
> Sau mỗi lần dựng, Clicky thử trả lời các câu này trên bản mới và báo anh câu nào báo cáo còn thiếu.
> File này chỉ nằm trên máy anh, không bao giờ được gắn vào báo cáo gửi đi.
> Ví dụ:
>     ## Anh Hùng (CFO)
>     - Chi phí lương khối bán hàng AOPR?
>     - Headcount khối bán hàng?

""",
    BAI_HOC: """# Bài học các kỳ trước

> Mỗi bài học là một dòng bắt đầu bằng "- ". Clicky nhắc lại cho anh sau mỗi lần dựng báo cáo.
> Ví dụ:
>     - Tháng 9 sếp hỏi lương bình quân theo khối, báo cáo chưa có cột này.

""",
}


def fold(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or "")).replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%,.\- ]+", " ", s)).strip()


def folder(path: Optional[str] = None) -> Path:
    if path:
        return Path(path)
    if os.environ.get("CLICKY_TRI_NHO"):
        return Path(os.environ["CLICKY_TRI_NHO"])
    return Path.home() / "Documents" / "Clicky tri nho"


def ensure(path: Optional[str] = None) -> Path:
    """Create the folder and any missing file from its template. Existing files are never rewritten."""
    d = folder(path)
    d.mkdir(parents=True, exist_ok=True)
    for name, text in TEMPLATES.items():
        f = d / name
        if not f.exists():
            f.write_text(text, encoding="utf-8")
    return d


# --- CAU_TREO.md --------------------------------------------------------------------------------------

KEYS = {"ai hoi": "ai_hoi", "ngay hoi": "ngay", "bao cao": "bao_cao", "tra loi": "tra_loi",
        "da noi trong": "da_noi", "han": "han", "xong": "xong"}


@dataclass
class Entry:
    kind: str                 # "cau" (a parked question) or "hua" (a promise)
    text: str
    line: int                 # index of the "- Câu:" / "- Hứa:" line
    fields: dict = field(default_factory=dict)
    lines: dict = field(default_factory=dict)  # field -> line index

    @property
    def answer(self) -> str:
        return self.fields.get("tra_loi", "").strip()

    @property
    def done(self) -> bool:
        return fold(self.fields.get("xong", "")) in ("co", "x", "xong", "roi", "da xong")

    def said_in(self) -> list:
        return [x.strip() for x in self.fields.get("da_noi", "").split(";") if x.strip()]


HEAD = re.compile(r"^- (Câu|Hứa)\s*:\s*(.*)$")
FIELD = re.compile(r"^\s+([^:]{2,20}):\s*(.*)$")


def read_treo(text: str) -> list:
    out, cur = [], None
    for i, line in enumerate(text.splitlines()):
        m = HEAD.match(line)
        if m:
            cur = Entry("cau" if m.group(1) == "Câu" else "hua", m.group(2).strip(), i)
            out.append(cur)
            continue
        f = FIELD.match(line) if cur else None
        if f and fold(f.group(1)) in KEYS:
            k = KEYS[fold(f.group(1))]
            cur.fields[k], cur.lines[k] = f.group(2).strip(), i
        elif line.strip() and not line.startswith(" "):
            cur = None
    return out


def add_questions(questions: list, report: str = "", asked_by: str = "", when: str = "",
                  path: Optional[str] = None) -> dict:
    """Append parked questions to CAU_TREO.md; a question already there (same words) is not added twice."""
    d = ensure(path)
    f = d / TREO
    text = f.read_text(encoding="utf-8")
    have = {fold(e.text) for e in read_treo(text) if e.kind == "cau"}
    when = when or dt.date.today().strftime("%d/%m/%Y")
    added, same = [], []
    block = []
    for q in questions:
        q = re.sub(r"\s+", " ", str(q)).strip()
        if not q:
            continue
        if fold(q) in have:
            same.append(q)
            continue
        have.add(fold(q))
        added.append(q)
        block += [f"- Câu: {q}", f"  Ai hỏi: {asked_by}".rstrip(), f"  Ngày hỏi: {when}",
                  f"  Báo cáo: {report}".rstrip(), "  Trả lời: ", ""]
    if block:
        f.write_text(text.rstrip("\n") + "\n\n" + "\n".join(block), encoding="utf-8")
    return {"file": str(f), "added": added, "already": same}


def mark_said(report: str, entries: list, path: Optional[str] = None) -> None:
    """Write "Đã nói trong: <report>" under each answered question the talk now reads, so the next report does
    not repeat it. Only those lines change; the owner's text stays as he wrote it."""
    if not entries:
        return
    f = folder(path) / TREO
    lines = f.read_text(encoding="utf-8").splitlines()
    fresh = {e.line: e for e in read_treo("\n".join(lines))}
    for e in sorted(entries, key=lambda x: -x.line):
        cur = fresh.get(e.line)
        if not cur or report in cur.said_in():
            continue
        value = "; ".join(cur.said_in() + [report])
        if "da_noi" in cur.lines:
            lines[cur.lines["da_noi"]] = f"  Đã nói trong: {value}"
        else:
            last = max([cur.line] + list(cur.lines.values()))
            lines.insert(last + 1, f"  Đã nói trong: {value}")
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- SEP_HAY_HOI.md and BAI_HOC.md ---------------------------------------------------------------------

def read_sep(text: str) -> dict:
    out, boss = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            boss = line[3:].strip()
            out.setdefault(boss, [])
        elif line.startswith("- ") and boss and line[2:].strip():
            out[boss].append(line[2:].strip())
    return out


def read_lessons(text: str) -> list:
    return [line[2:].strip() for line in text.splitlines() if line.startswith("- ") and line[2:].strip()]


@dataclass
class Memory:
    path: Path
    questions: list           # every "- Câu:" entry
    promises: list            # every "- Hứa:" entry
    bosses: dict              # boss -> usual questions
    lessons: list

    def for_talk(self, report: str) -> list:
        """Answered questions to read at the start of this report's talk: not yet said in another report."""
        return [e for e in self.questions if e.answer and (not e.said_in() or report in e.said_in())]

    def bank(self, limit: int = 60) -> list:
        """Answered questions, newest last, kept as ready answers on the page."""
        return [e for e in self.questions if e.answer][-limit:]

    def open_questions(self) -> list:
        return [e for e in self.questions if not e.answer]

    def open_promises(self) -> list:
        return [e for e in self.promises if not e.done]


def load(path: Optional[str] = None) -> Memory:
    d = ensure(path)
    entries = read_treo((d / TREO).read_text(encoding="utf-8"))
    return Memory(d, [e for e in entries if e.kind == "cau"], [e for e in entries if e.kind == "hua"],
                  read_sep((d / SEP).read_text(encoding="utf-8")),
                  read_lessons((d / BAI_HOC).read_text(encoding="utf-8")))


# --- the e-mail a reader sends from the page -----------------------------------------------------------

def parse_mail(text: str) -> dict:
    """Questions from a Clicky e-mail, copied as text. The page writes: report name in quotes, numbered
    questions, and a closing line with the report code."""
    if MAIL_MARK not in text:
        raise ValueError("Đoạn chữ này không phải email câu hỏi do Clicky gửi (thiếu dòng \"Gửi từ Clicky\").")
    m = re.search(r'báo cáo\s+[“"](.+?)[”"]', text)
    qs = [q.strip() for q in re.findall(r"^\s*\d{1,2}\.\s+(.+?)\s*$", text, re.M)]
    if not qs:
        raise ValueError("Không thấy câu hỏi đánh số nào trong email.")
    return {"report": m.group(1).strip() if m else "", "questions": qs}


def clipboard() -> str:
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        try:
            return root.clipboard_get()
        finally:
            root.destroy()
    except Exception:
        pass
    if sys.platform == "win32":
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"], capture_output=True,
                           text=True, encoding="utf-8", timeout=20)
        if r.returncode == 0:
            return r.stdout
    raise RuntimeError("Không đọc được bộ nhớ tạm (clipboard).")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Trí nhớ của Clicky: câu treo, lời hứa, các sếp hay hỏi gì.")
    sub = ap.add_subparsers(dest="lenh", required=True)
    g = sub.add_parser("ghi", help="ghi câu hỏi từ email Clicky (đã chép) vào CAU_TREO.md")
    g.add_argument("file", nargs="?", help="file .txt/.eml chứa email; bỏ trống = lấy từ bộ nhớ tạm")
    g.add_argument("--ai-hoi", default="")
    sub.add_parser("xem", help="tóm tắt: câu chưa trả lời, lời hứa chưa xong")
    ap.add_argument("--thu-muc", help="thư mục trí nhớ (mặc định CLICKY_TRI_NHO hoặc Documents\\Clicky tri nho)")
    a = ap.parse_args(argv)
    try:
        if a.lenh == "ghi":
            text = (Path(a.file).read_text(encoding="utf-8", errors="replace") if a.file else clipboard())
            mail = parse_mail(text)
            res = add_questions(mail["questions"], mail["report"], a.ai_hoi, path=a.thu_muc)
            print(f"Đã ghi {len(res['added'])} câu mới vào {res['file']}.")
            for q in res["added"]:
                print("  +", q)
            if res["already"]:
                print(f"{len(res['already'])} câu đã có sẵn, không ghi lại.")
            return 0
        mem = load(a.thu_muc)
        print(f"Trí nhớ: {mem.path}")
        print(f"Câu chưa trả lời: {len(mem.open_questions())}. Lời hứa chưa xong: {len(mem.open_promises())}.")
        for e in mem.open_questions():
            print("  ? ", e.text)
        for e in mem.open_promises():
            print("  * ", e.text, f"(hạn {e.fields['han']})" if e.fields.get("han") else "")
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Chưa làm được: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
