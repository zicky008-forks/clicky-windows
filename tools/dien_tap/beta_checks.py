"""Robot checks for the six screen jobs approved for the 16/11 beta.

Spec: /mnt/project-files/thiet-ke/giao-dien-5-sao/6 viec truoc beta 16-11.md (approved 03/11/2026).
Each job is checked only when the case's expected file has its key, so a build without the job
is not failed for it until the case asks for it.

What Clicky must write to the scene log (scene_log.record) for these checks — the contract:

  intake   {files: [{name, kind, period, unit, rows, checks, total, used, dropped}],
            dropped: [{what, amount, reason, file}], text}         job 1 + 2: the "Em đọc được những gì" card
  verify   {ok: bool, path, diffs: [{cell, want, got}]}            job 3: re-read of the saved xlsx
  status   {label: "SƠ BỘ" | "CHÍNH THỨC"}                         job 4: report status shown/changed
  roles    {files: [{name, role}]}                                 job 5: role given to each input
  ai_call  {chars, preview_shown: bool, approved: bool, local_only: bool}   job 6: every call that leaves the PC
  write    {ref}                                                   job 1c: every cell write (none before confirm)
  delivered {path}                                                 job 3b: the file is handed to the user

Test hook (only when the variable is set): CLICKY_TEST_TAMPER="<Sheet!Cell>=<delta>" — right after saving,
Clicky itself adds <delta> to that cell in the saved file, before re-reading it (job 3b).

And on disk:
  <workbook>_ban-goc.xlsx next to the workbook                     job 4
  sheet "Bien ban Clicky" in the workbook                          job 4
  egress log (default %LOCALAPPDATA%\\Clicky\\gui-di.jsonl): one JSON line per call, the text sent   job 6
"""
from __future__ import annotations

import re
from pathlib import Path

from checks import Check, text_found

_IDS = re.compile(r"(?<!\d)(\d{13}|\d{12}|\d{10})(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _events(events, kind):
    return [e for e in events if e.get("event") == kind]


def _text_of(e) -> str:
    parts = []
    for v in e.values():
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, (list, dict)):
            parts.append(str(v))
    return " ".join(parts)


def intake_checks(events, expected, untouched_before_confirm) -> list:
    """Job 1: a receipt card before any computing; nothing changed in the workbook until confirmed."""
    spec = expected["intake"]
    out = []
    intakes = _events(events, "intake")
    first_draw = next((e["t"] for e in events if e.get("event") == "draw"
                       and (e.get("shape") or {}).get("kind") == "spotlight"), None)
    shown = bool(intakes)
    early = shown and (first_draw is None or intakes[0]["t"] <= first_draw)
    out.append(Check("Phiếu nhận file hiện trước khi tính", shown and early,
                     "không thấy phiếu nhận file" if not shown else
                     ("hiện trước cảnh đầu tiên" if early else "phiếu hiện sau khi đã bắt đầu tính")))
    if not shown:
        return out
    card = intakes[0]
    files = card.get("files") or []
    out.append(Check("Phiếu nêu đủ các file", len(files) >= spec.get("files_min", 1),
                     f"{len(files)} file (cần ít nhất {spec.get('files_min', 1)})"))
    blob = _text_of(card)
    missing = [m for m in spec.get("must_mention", []) if m.casefold() not in blob.casefold()]
    out.append(Check("Phiếu nêu những gì bị bỏ ra", not missing,
                     "thiếu: " + ", ".join(missing) if missing else "đủ các mục cần nêu"))
    label = spec.get("confirm_label", "Đúng rồi, làm tiếp")
    offered = any(label in (e.get("labels") or []) for e in _events(events, "choices"))
    out.append(Check(f"Có nút \"{label}\"", offered, "" if offered else "không thấy nút xác nhận"))
    early_writes = writes_before(events, label)
    out.append(Check("Không có lệnh ghi ô nào trước khi bấm xác nhận", early_writes == 0,
                     f"{early_writes} lệnh ghi" if early_writes else "không có"))
    if untouched_before_confirm is not None:
        out.append(Check("Chưa bấm xác nhận thì chưa ô nào bị sửa", untouched_before_confirm,
                         "file giữ nguyên tới lúc bấm" if untouched_before_confirm else "đã có ô bị sửa trước khi bấm"))
    return out


def coverage_checks(events, expected) -> list:
    """Job 2: nothing dropped silently: per file, used + dropped = total."""
    tol = expected["coverage"].get("tolerance", 1)
    intakes = _events(events, "intake")
    if not intakes:
        return [Check("Không bỏ qua im lặng", False, "không có phiếu nhận file để đối chiếu")]
    bad, n = [], 0
    for f in intakes[0].get("files") or []:
        if not all(isinstance(f.get(k), (int, float)) for k in ("total", "used", "dropped")):
            bad.append(f"{f.get('name', '?')}: thiếu tổng/dùng/bỏ")
            continue
        n += 1
        gap = f["total"] - f["used"] - f["dropped"]
        if abs(gap) > tol:
            bad.append(f"{f.get('name', '?')}: lệch {gap:,.0f}")
    dropped_sum = sum(d.get("amount") or 0 for d in intakes[0].get("dropped") or [] if isinstance(d.get("amount"), (int, float)))
    listed_sum = sum(f.get("dropped") or 0 for f in intakes[0].get("files") or [] if isinstance(f.get("dropped"), (int, float)))
    if abs(dropped_sum - listed_sum) > tol:
        bad.append(f"phần bỏ được liệt kê {dropped_sum:,.0f} khác tổng bỏ {listed_sum:,.0f}")
    return [Check("Không bỏ qua im lặng (dùng + bỏ = tổng file)", not bad,
                  "; ".join(bad[:4]) if bad else f"{n} file khớp")]


def verify_checks(events, expected) -> list:
    """Job 3: the saved file is re-read; 'khớp 0 đ' is shown only after a clean re-read."""
    spec = expected["verify"]
    v = _events(events, "verify")
    out = [Check("Mở lại file đã lưu và đối chiếu", bool(v), "" if v else "không thấy bước đối chiếu lại")]
    if v:
        want_ok = spec.get("ok", True)
        last = v[-1]
        out.append(Check("Kết quả đối chiếu lại đúng như mong đợi", bool(last.get("ok")) == want_ok,
                         "khớp" if last.get("ok") else f"lệch {len(last.get('diffs') or [])} ô"))
        claim = spec.get("ok_text", "Khớp sổ cái")
        claimed_after_fail = any(claim.casefold() in _text_of(e).casefold()
                                 for e in events if e.get("t", 0) >= last.get("t", 0)
                                 and e.get("event") in ("beat", "card", "said")) and not last.get("ok")
        out.append(Check("Không báo khớp khi file lưu bị lệch", not claimed_after_fail,
                         "báo khớp dù đối chiếu lại thấy lệch" if claimed_after_fail else ""))
    return out


def audit_checks(sheets, events, expected, workbook: Path | None) -> list:
    """Job 4: 'Bien ban Clicky' sheet with one line per change, original kept, SƠ BỘ by default."""
    spec = expected["audit"]
    name = spec.get("sheet", "Bien ban Clicky")
    rows = next((r for n, r in sheets.items() if n.casefold() == name.casefold()), None)
    out = [Check(f"Có sheet \"{name}\"", rows is not None, "" if rows is not None else "không có")]
    if rows is not None:
        lines = [" ".join(str(c) for c in (r or []) if c not in (None, "")) for r in rows]
        missing = []
        for want in spec.get("rows", []):
            parts = want if isinstance(want, list) else [want]
            if not any(all(p.casefold() in ln.casefold() for p in parts) for ln in lines):
                missing.append(" + ".join(parts))
        out.append(Check("Biên bản ghi đủ các thay đổi", not missing,
                         "thiếu dòng: " + "; ".join(missing[:4]) if missing else f"{len([l for l in lines if l])} dòng"))
    status = spec.get("status", "SƠ BỘ")
    seen = text_found(status, "any", sheets, events)
    out.append(Check(f"Nhãn \"{status}\" hiện", seen, "" if seen else "không thấy nhãn"))
    if spec.get("not_status"):
        wrong = any((e.get("label") or "") == spec["not_status"] for e in _events(events, "status"))
        out.append(Check(f"Không tự chuyển sang \"{spec['not_status']}\"", not wrong,
                         "tự chuyển khi chưa ai bấm" if wrong else ""))
    if workbook is not None and spec.get("original_copy", True):
        orig = workbook.with_name(f"{workbook.stem}_ban-goc{workbook.suffix}")
        out.append(Check("Lưu bản gốc _ban-goc.xlsx", orig.exists(), str(orig.name)))
    return out


def _file_stem(text: str) -> str:
    """File name named by a button, without "Dùng " and without the extension, for exact comparison."""
    text = re.sub(r"^\s*Dùng\s+", "", text or "", flags=re.IGNORECASE).strip()
    return re.sub(r"\.(xlsx|xlsm|xls|csv|pdf|png|jpg|jpeg|txt|docx)$", "", text, flags=re.IGNORECASE).casefold()


def roles_checks(sheets, events, expected) -> list:
    """Job 5: two inputs with the same role → one question; figures from photos never used."""
    spec = expected["roles"]
    out = []
    ask = spec.get("ask_contains")
    if ask:
        asked = any(ask.casefold() in (e.get("question") or "").casefold() for e in _events(events, "choices"))
        out.append(Check("Hỏi khi có hai nguồn cùng vai trò", asked, "" if asked else f"không hỏi về \"{ask}\""))
    for part in spec.get("choose", []):
        want = _file_stem(part)
        chosen = any(_file_stem(e.get("label") or "") == want for e in _events(events, "chosen"))
        out.append(Check(f"Robot chọn được bản \"{part}\"", chosen, "" if chosen else "Clicky không hỏi / không có lựa chọn này"))
    for value in spec.get("numbers_absent", []):
        found = any(isinstance(c, (int, float)) and not isinstance(c, bool) and abs(c - value) < 0.5
                    for rows in sheets.values() for r in rows or [] for c in r or [])
        out.append(Check("Số từ ảnh/email không vào báo cáo", not found,
                         "có một số từ nguồn không chính thức trong file" if found else ""))
    return out


_MONEY_NEAR = re.compile(r"(đ|đồng|vnd|doanh thu|giá vốn|lợi nhuận|chi phí|số tiền|tổng)", re.IGNORECASE)


def _digits(text: str) -> str:
    return re.sub(r"[\s.\-_/]", "", text)


def egress_checks(events, expected, egress_text: str | None, network: dict | None = None) -> list:
    """Job 6: what leaves the PC is masked, previewed the first time, and nothing leaves in local-only mode."""
    spec = expected["egress"]
    calls = _events(events, "ai_call")
    out = []
    if spec.get("local_only"):
        sent = [c for c in calls if c.get("approved") or not c.get("local_only")]
        out.append(Check("Chế độ chỉ chạy trên máy: Clicky không gửi gì", not sent,
                         f"{len(sent)} lần gửi đi" + (f" ({len(calls)} lần dừng tại máy)" if calls and not sent else "")))
        if egress_text is not None:
            lines = [ln for ln in egress_text.splitlines() if ln.strip()]
            out.append(Check("Chế độ chỉ chạy trên máy: nhật ký gửi đi không thêm dòng nào", not lines,
                             f"{len(lines)} dòng mới"))
        if network is not None:
            n = network.get("connections", 0)
            out.append(Check("Chế độ chỉ chạy trên máy: 0 kết nối mạng ra ngoài", n == 0,
                             f"{n} kết nối" + (f" (cổng {', '.join(map(str, network.get('ports', [])[:5]))})" if n else "")))
        return out
    if calls:
        first = calls[0]
        out.append(Check("Lần gửi đầu có xem trước và được duyệt",
                         bool(first.get("preview_shown")) and bool(first.get("approved")), ""))
    if egress_text is None:
        out.append(Check("Có nhật ký gửi đi", False, "không đọc được nhật ký gửi đi"))
        return out
    flat = _digits(egress_text)
    names = [n for n in spec.get("names", []) if n and n.casefold() in egress_text.casefold()]
    ids = [i for i in spec.get("ids", []) if i and _digits(str(i)) in flat]
    mails = _EMAIL.findall(egress_text)
    leaks = (["tên trong danh mục"] if names else []) + (["mã số đã cài trong case"] if ids else []) \
        + (["email"] if mails else [])
    # Report the kind of leak only: the leaked value itself is private.
    out.append(Check("Nhật ký gửi đi đã che tên, mã số, email", not leaks,
                     "lộ: " + ", ".join(leaks) if leaks else "không lộ gì"))
    # A 10–13 digit run not next to a money word may be a tax code or account: shown for a human to look at.
    loose = 0
    for part in re.split(r"[;,\n|{}\[\]\"]", egress_text):
        if _IDS.search(part) and not _MONEY_NEAR.search(part):
            loose += len(_IDS.findall(part))
    out.append(Check("Chuỗi 10/12/13 số không đứng cạnh chữ chỉ tiền (cần người xem)", loose == 0,
                     f"{loose} chỗ" if loose else "không có", blocking=False))
    return out


def tamper_checks(events, expected) -> list:
    """Job 3b: a total changed after saving must be caught: right cell, right amount, file not handed over."""
    spec = expected["tamper"]
    v = [e for e in _events(events, "verify")]
    if not v:
        return [Check("Phát hiện ô bị sửa sau khi lưu", False, "không có bước đối chiếu lại")]
    last = v[-1]
    cell = spec["cell"].split("!")[-1].replace("$", "").upper()
    diffs = last.get("diffs") or []
    hit = next((d for d in diffs if str(d.get("cell", "")).split("!")[-1].replace("$", "").upper() == cell), None)
    amount_ok = False
    if hit and isinstance(hit.get("want"), (int, float)) and isinstance(hit.get("got"), (int, float)):
        amount_ok = abs(abs(hit["got"] - hit["want"]) - abs(spec["delta"])) <= spec.get("tolerance", 1)
    out = [Check("Phát hiện ô bị sửa sau khi lưu", not last.get("ok") and hit is not None,
                 "đúng ô" if hit else ("báo khớp" if last.get("ok") else "báo lệch nhưng sai ô"))]
    out.append(Check("Nói đúng số tiền lệch", amount_ok, "" if amount_ok else "số lệch không đúng"))
    delivered = any(e.get("event") == "delivered" for e in events if e.get("t", 0) >= last.get("t", 0))
    claimed = any("khớp sổ cái" in _text_of(e).casefold() for e in events
                  if e.get("t", 0) >= last.get("t", 0) and e.get("event") in ("beat", "card"))
    out.append(Check("Không giao file khi lệch", not delivered and not claimed,
                     "vẫn giao file / báo khớp" if delivered or claimed else ""))
    return out


def writes_before(events, label: str) -> int:
    """Cell writes Clicky logged before the user pressed `label` (scene event 'write')."""
    pressed = next((e["t"] for e in events if e.get("event") == "chosen" and e.get("label") == label), None)
    return sum(1 for e in events if e.get("event") == "write" and (pressed is None or e.get("t", 0) < pressed))


def sample_checks(events, expected, workbook: Path | None) -> list:
    """Job 10: "xem em làm" plays the bundled sample alone; the closing card states the real time."""
    spec = expected["sample"]
    out = []
    v = _events(events, "verify")
    out.append(Check("File mẫu chạy tới đối chiếu lại và khớp", bool(v) and bool(v[-1].get("ok")),
                     "không có bước đối chiếu" if not v else ("khớp" if v[-1].get("ok") else "lệch")))
    closing = next((e for e in reversed(_events(events, "beat")) if (e.get("title") or "").startswith("Xong trong")), None)
    turns = _events(events, "turn")
    if closing is None or not turns:
        out.append(Check("Thẻ cuối nói đúng thời gian thật", False, "không thấy thẻ \"Xong trong …\""))
    else:
        m = re.match(r"Xong trong (?:(\d+) phút )?(\d+) giây", closing["title"])
        said = (int(m.group(1) or 0) * 60 + int(m.group(2))) if m else None
        real = closing["t"] - turns[0]["t"]
        ok = said is not None and abs(said - real) <= max(spec.get("time_tolerance", 5), 0.15 * real)
        out.append(Check("Thẻ cuối nói đúng thời gian thật", ok,
                         f"thẻ ghi {said} giây, robot đo {real:.0f} giây" if said is not None else closing["title"]))
    if workbook is not None:
        orig = workbook.with_name(f"{workbook.stem}_ban-goc{workbook.suffix}")
        out.append(Check("File mẫu còn bản gốc _ban-goc", orig.exists(), orig.name))
    clicks = [e for e in _events(events, "chosen")]
    out.append(Check("File mẫu chạy không cần bấm gì", not clicks or spec.get("clicks_ok", False),
                     f"{len(clicks)} lần bấm" if clicks else "không bấm lần nào", blocking=False))
    return out


def evaluate(events, sheets, expected, *, untouched_before_confirm=None, workbook=None, egress_text=None,
             network=None) -> list:
    out = []
    if "intake" in expected:
        out += intake_checks(events, expected, untouched_before_confirm)
    if "coverage" in expected:
        out += coverage_checks(events, expected)
    if "verify" in expected:
        out += verify_checks(events, expected)
    if "audit" in expected:
        out += audit_checks(sheets, events, expected, workbook)
    if "roles" in expected:
        out += roles_checks(sheets, events, expected)
    if "egress" in expected:
        out += egress_checks(events, expected, egress_text, network)
    if "tamper" in expected:
        out += tamper_checks(events, expected)
    if "sample" in expected:
        out += sample_checks(events, expected, workbook)
    return out
