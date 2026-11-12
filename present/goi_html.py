"""Pack a talk into ONE self-contained HTML file that a board member opens from a SharePoint/OneDrive link.

Why one file: SharePoint often downloads an .html instead of showing it, and many mail gateways strip
anything that loads from outside. So the slides (images), Clicky's voice (MP3), the pointing script and the
approved question bank all live inside the file. It runs offline, on a laptop or a phone, and sends nothing
anywhere. The only thing that leaves is what the reader chooses to e-mail back: the questions Clicky could
not answer ("câu treo").

Same rules as the live talk (present/talk.py):
- Every figure comes from the locked workbook through present.placeholders. A line with an unresolved
  placeholder stops the build; it is never spoken or shown.
- Answers come only from the approved bank (present/qa.py rules, ported to the page). No AI runs in the page.
  A what-if needs the live workbook, so in the pack it becomes a parked question for the owner.
- The voice is the offline Vietnamese voice (audio/tts/vi_offline_tts.py). No online voice is used, so the
  pack can be built from real figures on a company PC.
- The page says plainly that the presenter is an AI assistant speaking for the owner (Luật AI 134/2025).

Optionally the same scenes are rendered to an MP4 (slides + voice + frames) for mailboxes that block links.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

from present.deck import ShapeInfo, find
from present.placeholders import OpenpyxlSource, Resolver
from present.qa import fold

EMU_PER_POINT = 12700
WATERFALL_TAGS = ("waterfall", "cx:chart")


class PackError(RuntimeError):
    """The pack cannot be built without speaking something unchecked. The message is Vietnamese."""


# --- slides -----------------------------------------------------------------------------------------

@dataclass
class SlideInfo:
    number: int
    title: str
    width: float                 # points
    height: float
    shapes: list = field(default_factory=list)


def read_deck(path: str) -> list:
    """Shapes of every slide, in points, with the same descriptors the COM reader produces."""
    from pptx import Presentation
    from pptx.enum.shapes import PP_PLACEHOLDER

    prs = Presentation(path)
    w, h = prs.slide_width / EMU_PER_POINT, prs.slide_height / EMU_PER_POINT
    out = []
    for n, slide in enumerate(prs.slides, 1):
        shapes, title = [], ""
        for i, sh in enumerate(slide.shapes, 1):
            text = sh.text_frame.text if sh.has_text_frame else ""
            is_title = bool(sh.is_placeholder and sh.placeholder_format.type in
                            (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE))
            if is_title and not title:
                title = text.strip()
            is_chart = bool(getattr(sh, "has_chart", False))
            chart_type = None
            if not is_chart and sh.shape_type is not None and int(sh.shape_type) == 3:   # chartex (waterfall)
                xml = sh._element.xml
                if any(t in xml for t in WATERFALL_TAGS):
                    is_chart, chart_type = True, 119
            is_table = bool(getattr(sh, "has_table", False))
            if is_table and not text:
                text = " | ".join(c.text for r in sh.table.rows for c in r.cells)
            if sh.left is None or sh.width is None:
                continue
            shapes.append(ShapeInfo(i, sh.name, sh.left / EMU_PER_POINT, sh.top / EMU_PER_POINT,
                                    sh.width / EMU_PER_POINT, sh.height / EMU_PER_POINT, text,
                                    is_title, is_chart, is_table, chart_type))
        out.append(SlideInfo(n, title, w, h, shapes))
    return out


def render_slides(pptx: str, workdir: Path, width_px: int = 1600) -> list:
    """Slide images (JPEG bytes) through LibreOffice -> PDF -> pdftoppm. Local tools only."""
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if not office or not shutil.which("pdftoppm"):
        raise PackError("Máy chưa có LibreOffice hoặc pdftoppm để chụp slide.")
    src = workdir / "deck.pptx"
    shutil.copyfile(pptx, src)
    subprocess.run([office, "--headless", "--convert-to", "pdf", "--outdir", str(workdir), str(src)],
                   check=True, capture_output=True, timeout=300)
    subprocess.run(["pdftoppm", "-jpeg", "-jpegopt", "quality=82", "-scale-to-x", str(width_px),
                    "-scale-to-y", "-1", str(workdir / "deck.pdf"), str(workdir / "slide")],
                   check=True, capture_output=True, timeout=300)
    files = sorted(workdir.glob("slide-*.jpg"), key=lambda p: int(re.findall(r"\d+", p.stem)[-1]))
    return [p.read_bytes() for p in files]


# --- voice ------------------------------------------------------------------------------------------

def offline_voice() -> Callable[[str], bytes]:
    """WAV bytes from the offline Vietnamese voice. Never an online voice: real figures may be in the text."""
    from audio.tts.vi_offline_tts import engine, preferred_order
    last = None
    for name in preferred_order():
        try:
            tts = engine(name)
            return tts.synthesize
        except Exception as exc:            # noqa: BLE001 - try the next offline engine
            last = exc
    raise PackError(f"Chưa có giọng tiếng Việt offline trên máy ({last}).")


def wav_to_mp3(wav: bytes) -> tuple:
    """(mp3 bytes, seconds). Mono 48 kbps keeps a 7-minute talk around 2.5 MB."""
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d) / "a.wav", Path(d) / "a.mp3"
        src.write_bytes(wav)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-ac", "1", "-b:a", "48k", str(dst)],
                       check=True, timeout=120)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                "default=nw=1:nk=1", str(dst)], capture_output=True, text=True, check=True)
        return dst.read_bytes(), round(float(probe.stdout.strip() or 0), 2)


# --- building the scenes ----------------------------------------------------------------------------

def _box(shape: ShapeInfo, slide: SlideInfo) -> list:
    """Shape rectangle as fractions of the slide, so the page scales it to any screen."""
    f = shape.framed
    return [round(f.left / slide.width, 4), round(f.top / slide.height, 4),
            round(f.width / slide.width, 4), round(f.height / slide.height, 4)]


def _when(spoken: str, needle: str, i: int, n: int) -> float:
    """Where in the line (0..1) to point: where the shape's words are said, else spread evenly."""
    k = fold(needle)
    pos = fold(spoken).find(k) if k and len(k) > 2 else -1
    if pos >= 0:
        return round(max(0.0, pos / max(1, len(fold(spoken))) - 0.03), 3)
    return round(i / max(1, n), 3)


def _fill(resolver: Resolver, text: str, where: str, extra: Optional[dict] = None) -> str:
    res = resolver.fill(text, extra)
    if res.missing:
        raise PackError(f"{where}: chưa đọc được " + "; ".join(f"{t} ({why})" for t, why in res.missing))
    return res.text


def build_scenes(slides: list, script: dict, resolver: Resolver) -> list:
    """One scene per scripted slide: the spoken line and the shapes to frame, with timing fractions."""
    by_no = {s.number: s for s in slides}
    meta = script.get("meta") or {}
    items = script.get("slides") or []
    scenes = []
    for idx, item in enumerate(items):
        no = int(item["so"])
        slide = by_no.get(no)
        if slide is None:
            raise PackError(f"Kịch bản có slide {no} nhưng deck không có.")
        resolver.slide_title = lambda s=slide: s.title
        line = _fill(resolver, item.get("loi_noi") or "", f"Slide {no}")
        if idx == 0 and meta.get("mo_dau"):
            line = _fill(resolver, meta["mo_dau"], "Lời mở đầu") + " " + line
        if idx == len(items) - 1 and meta.get("ket_thuc"):
            line = line + " " + _fill(resolver, meta["ket_thuc"], "Lời kết")
        targets = [t for t in (item.get("chi_vao") or []) if "shape" in t or "shape_chua" in t]
        points, missing = [], []
        for i, t in enumerate(targets):
            if "shape_chua" in t:
                needle = _fill(resolver, str(t["shape_chua"]), f"Slide {no}")
                shape = find(slide.shapes, contains=needle)
                label = needle
            else:
                shape = find(slide.shapes, kind=t["shape"])
                label = t["shape"]
            if shape is None:
                missing.append(label)
                continue
            said = shape.text if "shape_chua" in t else ""
            points.append({"box": _box(shape, slide), "at": _when(line, said or label, i, len(targets)),
                           "label": label})
        scenes.append({"slide": no, "title": slide.title, "point": item.get("y_chinh", ""), "line": line,
                       "points": points, "not_found": missing, "pause": float(item.get("dung_giay") or 1.5)})
    return scenes


DEPT_SHEET_KEYS = ("sheet", "cot", "tu_dong")


def _dept_rows(source, spec: dict) -> list:
    """(row number, department name) from the sheet the bank names, e.g. De_xuat column D from row 4."""
    sheet, col, first = spec["sheet"], int(spec["cot"]), int(spec["tu_dong"])
    out = []
    for offset, row in enumerate(source.rows(sheet, first, col)):
        name = str(row[col - 1] or "").strip()
        if name and not fold(name).startswith(("tong", "total", "cong")):
            out.append((first + offset, name))
    return out


def build_answers(bank: dict, resolver: Resolver, source, owner: str) -> dict:
    """Every bank entry filled now from the locked workbook. What-ifs become parked questions."""
    entries = []
    for e in bank.get("cau_hoi") or []:
        groups = (e.get("tu_khoa") or {}).get("va") or []
        drill = e.get("drill") or {}
        item = {"id": e["id"], "ask": e.get("hoi_mau", ""), "groups": groups,
                "source": f"sheet {drill['sheet']}" if drill.get("sheet") else "",
                "slide": e.get("nhay_slide")}
        if e.get("what_if"):
            item["kind"] = "park"
            item["text"] = (f"Câu kịch bản này cần chạy trên file số liệu sống. Em ghi lại để {owner} "
                            "chạy và gửi anh chị kết quả.")
        elif "ten_phong" in (e.get("tham_so") or {}):
            spec = e["tham_so"]["ten_phong"]
            item["kind"] = "dept"
            item["depts"] = []
            for row, name in _dept_rows(source, spec):
                tmpl = (e.get("tra_loi") or "").replace("<dong>", str(row))
                text = _fill(resolver, tmpl, f"Câu {e['id']} ({name})", {"ten_phong": name})
                item["depts"].append({"name": name, "key": fold(re.sub(r"^(p\.|phòng|ban|khối)\s*", "",
                                                                         name, flags=re.I)),
                                      "text": text, "source": f"sheet {spec['sheet']}, dòng {row}"})
        else:
            item["kind"] = "answer"
            item["text"] = _fill(resolver, e.get("tra_loi") or "", f"Câu {e['id']}")
        entries.append(item)
    fallback = (bank.get("khong_khop") or {}).get("tra_loi") or \
        f"Câu này em ghi lại, {owner} sẽ trả lời anh chị sau buổi họp."
    return {"entries": entries, "fallback": fallback}


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def voice_all(scenes: list, answers: dict, voice: Callable[[str], bytes],
              encode: Optional[Callable[[bytes], tuple]] = None) -> None:
    """Attach base64 audio + duration to every scene and answer. Same text, same clip (cached)."""
    cache: dict = {}
    encode = encode or wav_to_mp3

    def clip(text: str) -> dict:
        if text not in cache:
            mp3, secs = encode(voice(text))
            cache[text] = {"audio": base64.b64encode(mp3).decode("ascii"), "secs": secs}
        return cache[text]

    for s in scenes:
        s.update(clip(s["line"]))
    for e in answers["entries"]:
        if e["kind"] == "dept":
            for d in e["depts"]:
                d.update(clip(d["text"]))
        else:
            e.update(clip(e["text"]))
    answers["fallback_clip"] = clip(answers["fallback"])
    answers["which_dept"] = "Anh chị muốn hỏi phòng nào ạ?"
    answers["which_dept_clip"] = clip(answers["which_dept"])


# --- output -----------------------------------------------------------------------------------------

def write_html(out: str, data: dict) -> int:
    """The single file. Data goes in as JSON in a script tag; '</' is escaped so it cannot close the tag."""
    template = (Path(__file__).with_name("goi_html_template.html")).read_text(encoding="utf-8")
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = template.replace("__TITLE__", html.escape(data["meta"]["title"])).replace("__DATA__", blob)
    Path(out).write_text(page, encoding="utf-8")
    return len(page.encode("utf-8"))


def write_mp4(out: str, data: dict, workdir: Path) -> None:
    """Fallback video: each slide with its voice; frames appear when the line reaches them."""
    parts = []
    for k, s in enumerate(data["scenes"]):
        img = workdir / f"v{k}.jpg"
        aud = workdir / f"v{k}.mp3"
        img.write_bytes(base64.b64decode(data["slides"][s["slide"] - 1]))
        aud.write_bytes(base64.b64decode(s["audio"]))
        dur = s["secs"] + 0.8
        boxes = []
        for p in s["points"]:
            x, y, w, h = p["box"]
            t0 = round(p["at"] * s["secs"], 2)
            boxes.append(f"drawbox=x=iw*{max(0, x - 0.006):.4f}:y=ih*{max(0, y - 0.01):.4f}:"
                         f"w=iw*{w + 0.012:.4f}:h=ih*{h + 0.02:.4f}:color=0xE8A33D@0.95:t=6:"
                         f"enable='gte(t,{t0})'")
        vf = ",".join(["scale=1600:-2"] + boxes)
        part = workdir / f"p{k}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", f"{dur:.2f}", "-i", str(img),
                        "-i", str(aud), "-vf", vf, "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt",
                        "yuv420p", "-r", "10", "-c:a", "aac", "-b:a", "64k", "-af", "apad", "-t", f"{dur:.2f}",
                        str(part)], check=True, timeout=600)
        parts.append(part)
    listing = workdir / "list.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c", "copy", "-movflags", "+faststart", out], check=True, timeout=600)


def build(deck: str, script: str, bank: str, workbook: str, out_html: str, owner: str = "anh Tuấn",
          contact: str = "", label: str = "", out_mp4: Optional[str] = None, title: str = "",
          voice: Optional[Callable[[str], bytes]] = None, renderer=render_slides) -> dict:
    """Build the pack. Returns a short report (sizes, slides, answers, shapes not found)."""
    source = OpenpyxlSource(workbook)
    resolver = Resolver(source)
    slides = read_deck(deck)
    scenes = build_scenes(slides, yaml.safe_load(Path(script).read_text(encoding="utf-8")), resolver)
    answers = build_answers(yaml.safe_load(Path(bank).read_text(encoding="utf-8")), resolver, source, owner)
    voice_all(scenes, answers, voice or offline_voice())
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        images = renderer(deck, work)
        if len(images) < len(slides):
            raise PackError(f"Chỉ chụp được {len(images)}/{len(slides)} slide.")
        meta = {
            "title": title or Path(deck).stem.replace("_", " "),
            "owner": owner, "contact": contact, "label": label,
            "built": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
            "workbook": Path(workbook).name, "workbook_sha256": sha256(workbook),
            "deck": Path(deck).name, "deck_sha256": sha256(deck),
            "minutes": round(sum(s["secs"] + 0.6 for s in scenes) / 60, 1),
            "pack_id": sha256(workbook)[:12] + sha256(deck)[:12],
        }
        data = {"meta": meta, "slides": [base64.b64encode(b).decode("ascii") for b in images],
                "scenes": scenes, "qa": answers}
        size = write_html(out_html, data)
        if out_mp4:
            write_mp4(out_mp4, data, work)
    return {"html_bytes": size, "slides": len(scenes), "minutes": meta["minutes"],
            "answers": len(answers["entries"]),
            "not_found": {s["slide"]: s["not_found"] for s in scenes if s["not_found"]}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Đóng gói bài thuyết trình có Clicky thành một file HTML.")
    ap.add_argument("--deck", required=True)
    ap.add_argument("--kich-ban", required=True)
    ap.add_argument("--hoi-dap", required=True)
    ap.add_argument("--so-lieu", required=True)
    ap.add_argument("--ra", required=True, help="file .html sẽ tạo")
    ap.add_argument("--mp4", help="thêm bản video dự phòng")
    ap.add_argument("--chu", default="anh Tuấn", help="người chịu trách nhiệm, Clicky xưng là trợ lý của người này")
    ap.add_argument("--email", default="", help="địa chỉ nhận câu hỏi treo")
    ap.add_argument("--nhan", default="", help="nhãn hiện trên trang, ví dụ 'SỐ GIẢ LẬP'")
    ap.add_argument("--tieu-de", default="", help="tên bài trình bày hiện trên trang")
    a = ap.parse_args(argv)
    try:
        report = build(a.deck, a.kich_ban, a.hoi_dap, a.so_lieu, a.ra, a.chu, a.email, a.nhan, a.mp4, a.tieu_de)
    except PackError as exc:
        print(f"Chưa đóng gói được: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
