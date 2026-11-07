"""Diễn tập: a robot plays the accountant through one whole month-close case, then grades the run.

Run on the Windows test PC, with practice data only:

    python tools\\dien_tap\\rehearse.py --workbook "C:\\...\\Case01\\BaoCao_T10.xlsx"

What it does, like a person would:
 1. copies the blank workbook next to itself (the original stays clean) and opens it in Excel;
 2. starts Clicky with the scene log on;
 3. types "làm báo cáo tháng từ thư mục này" into Clicky's chat box and presses Gửi;
 4. waits for the fix question and presses "Sửa giúp tôi";
 5. waits for the report, reads the numbers left in the workbook through Excel itself;
 6. grades the run (tools/dien_tap/checks.py) and writes ket-qua.md + ket-qua.json + scene.jsonl;
 7. at every scene it also screenshots and asks Excel itself where the cell is (visual.py), then
    checks the frame, the card and the pointer on the picture; failures are saved as marked PNGs.

Privacy: it refuses to run while another workbook is open in Excel, and nothing it writes
leaves this PC. Exit code 0 = ĐẠT, 1 = KHÔNG ĐẠT, 2 = could not run.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checks          # noqa: E402
import drivers         # noqa: E402
import visual          # noqa: E402
import beta_checks     # noqa: E402
import layout_checks   # noqa: E402
import columns         # noqa: E402
import power           # noqa: E402

HERE = Path(__file__).resolve().parent
COMPOSER = "Tin nhắn cho Clicky"
SEND = "Gửi"


def say(text: str) -> None:
    print(text, flush=True)


def find_clicky() -> str | None:
    base = os.environ.get("LOCALAPPDATA", "")
    # Velopack builds live in <app>\current\, the Setup-Clicky-bi installer in Programs\Clicky\: newest wins.
    hits = glob.glob(os.path.join(base, "*", "current", "Clicky.exe")) + \
        glob.glob(os.path.join(base, "Programs", "Clicky*", "Clicky.exe"))
    hits = sorted(hits, key=os.path.getmtime, reverse=True)
    return hits[0] if hits else None


class Clock:
    def __init__(self):
        self.t0 = self.last = time.monotonic()
        self.steps = []

    def lap(self, name: str) -> None:
        now = time.monotonic()
        self.steps.append((name, now - self.last))
        self.last = now


AUTO_PRESS = []        # (driver, labels): buttons pressed whenever they come up, e.g. the intake receipt's confirm


def _file_stem(text: str) -> str:
    import re
    text = re.sub(r"^\s*Dùng\s+", "", text or "", flags=re.IGNORECASE).strip()
    return re.sub(r"\.(xlsx|xlsm|xls|csv|pdf|png|jpg|jpeg|txt|docx)$", "", text, flags=re.IGNORECASE).casefold()


def match_label(part: str, labels) -> str | None:
    """The button that names exactly `part`: the whole file name after "Dùng ", never a substring.
    "TB_T10.xlsx" matches "Dùng TB_T10" but not "Dùng TB_T10_cu" nor "Dùng TB_T10 (1)"."""
    want = _file_stem(part)
    return next((lab for lab in labels or [] if _file_stem(lab) == want), None)


def _auto_press(events) -> None:
    for driver, labels, done in AUTO_PRESS:
        for i, e in enumerate(events):
            if e.get("event") != "choices" or i in done:
                continue
            if getattr(labels, "needs_context", False):
                hit = labels(e, before=events[:i])
            elif callable(labels):
                hit = labels(e)
            else:
                hit = next((lab for lab in e.get("labels") or [] if lab in labels), None)
            if hit:
                done.add(i)
                say(f"   Bấm \"{hit}\"")
                if driver.wait_for(hit, 20):
                    driver.press(hit)


TURN_USAGE = []         # Excel's memory and handles when each turn starts (process counters only, no COM call)


def _sample_turns(events) -> None:
    turns = [e for e in events if e.get("event") == "turn"]
    for e in turns[len(TURN_USAGE):]:
        ws = handles = None
        for pid in sorted(drivers.pids_of("EXCEL.EXE")):
            try:
                w, h = _process_usage(pid)
            except Exception:                          # noqa: BLE001 - not on Windows
                w, h = None, None
            ws = (ws or 0) + w if w is not None else ws
            handles = (handles or 0) + h if h is not None else handles
        TURN_USAGE.append({"turn": len(TURN_USAGE) + 1, "text": (e.get("text") or "")[:40], "ws_mb": ws,
                           "handles": handles})


def wait_log(log: Path, done, timeout: float, quiet: float = 0.0) -> list:
    """Poll the scene log until done(events) holds (then `quiet` seconds with no new event), or time runs out.
    Buttons registered in AUTO_PRESS are pressed as they appear."""
    end = time.monotonic() + timeout
    events = []
    while time.monotonic() < end:
        events = checks.read_log(log)
        _auto_press(events)
        _sample_turns(events)
        if done(events):
            if not quiet:
                return events
            n = len(events)
            time.sleep(quiet)
            events = checks.read_log(log)
            if len(events) == n:
                return events
            continue
        time.sleep(1.0)
    return events


def pressable_labels() -> set:
    """Every fixed label the robot presses by itself (AUTO_PRESS sets; the column and source choosers are
    answered before the receipt, so they do not matter here)."""
    out = set()
    for _, labels, _ in AUTO_PRESS:
        if isinstance(labels, (set, frozenset, list, tuple)):
            out |= set(labels)
    return out


def settled(events, pressable) -> bool:
    """Clicky has finished the work the last click started: a story_end after the last click or turn, whose
    last card asks nothing the robot is about to press. Case 05 on ebffea4: writing the sheets took ~50 s with
    no event at all, so a quiet spell is no sign of the end."""
    last = max((i for i, e in enumerate(events) if e.get("event") in ("chosen", "turn")), default=-1)
    ends = [e for e in events[last + 1:] if e.get("event") == "story_end"]
    if not ends:
        return False
    return not set(ends[-1].get("choices") or []) & set(pressable)


def story_ended(events, target: str) -> bool:
    """After the beat titled `target`, Clicky has asked its closing question or put the story away."""
    at = next((i for i, e in enumerate(events)
               if e.get("event") == "beat" and target in (e.get("title") or "")), None)
    if at is None:
        return False
    return any((e.get("event") == "beat" and e.get("choices")) or (e.get("event") == "guided" and not e.get("active"))
               or e.get("event") in ("turn", "story_end") for e in events[at + 1:])


def file_hash(path: Path) -> str:
    import hashlib
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


class NetWatch:
    """Counts Clicky.exe's outside connections (not loopback), sampled every second."""

    def __init__(self, image: str = "Clicky.exe"):
        import threading
        self.image, self.seen, self._stop = image, set(), threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self) -> dict:
        self._stop.set()
        self._thread.join(timeout=5)
        return {"connections": len(self.seen), "ports": sorted({p for _, p in self.seen})}

    def _sample(self, pids):
        try:
            import psutil
            for pid in pids:
                for c in psutil.Process(pid).net_connections(kind="inet"):
                    if c.raddr and not c.raddr.ip.startswith(("127.", "::1")):
                        self.seen.add((c.raddr.ip, c.raddr.port))
            return
        except ImportError:
            pass
        except Exception:
            return
        ids = ",".join(map(str, pids))
        cmd = (f"Get-NetTCPConnection -OwningProcess {ids} -ErrorAction SilentlyContinue | "
               "Where-Object {$_.RemoteAddress -notmatch '^(127\\.|::1|0\\.0\\.0\\.0|::)$'} | "
               "ForEach-Object { $_.RemoteAddress + ' ' + $_.RemotePort }")
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True,
                                 timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            return
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                self.seen.add((parts[0], int(parts[1])))

    def _run(self):
        while not self._stop.is_set():
            pids = drivers.pids_of(self.image)
            if pids:
                self._sample(pids)
            self._stop.wait(1.0)


def no_workbook(allow_others: bool):
    import win32com.client
    excel = win32com.client.Dispatch("Excel.Application")
    try:
        others = com_retry(lambda: excel.Workbooks.Count, tries=10)   # a count only: never the names
    except Exception as exc:
        raise SystemExit(EXCEL_STUCK + f" ({exc})")
    if others and not allow_others:
        raise SystemExit(f"Excel đang mở {others} file khác. Đóng hết rồi chạy lại.")
    return excel, None, None


COPY_TAG = "_dientap_"                # every file the robot (or Clicky, from the robot's copy) leaves in the case folder


def park_old_copies(source: Path, dest: Path) -> list:
    """Move the robot's own leftovers (earlier laps or crashed runs) out of the case folder.

    Clicky's intake lists every workbook next to the one it works on, so a copy left from an
    earlier lap shows up on the next lap's receipt as a skipped file. Only names carrying the
    robot's tag are touched: the customer's or the case's own files are never moved."""
    moved = []
    for f in sorted(source.parent.iterdir()):
        if not f.is_file() or f == source or COPY_TAG not in f.name or f.name.startswith("~$"):
            continue
        dest.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(f), str(dest / f.name))
            moved.append(f.name)
        except OSError as exc:         # still open in Excel: leave it, say so
            say(f"   Không dời được bản sao cũ {f.name}: {exc}")
    if moved:
        say(f"   Dời {len(moved)} bản sao cũ của robot ra {dest}")
    return moved


def open_workbook(source: Path, allow_others: bool, zoom: int | None = None):
    import win32com.client
    excel = win32com.client.Dispatch("Excel.Application")
    try:
        others = com_retry(lambda: excel.Workbooks.Count, tries=10)   # a count only: never the names
    except Exception as exc:
        raise SystemExit(EXCEL_STUCK + f" ({exc})")
    if others and not allow_others:
        raise SystemExit(f"Excel đang mở {others} file khác. Đóng hết rồi chạy lại "
                         "(diễn tập chỉ dùng số giả lập).")
    stamp = time.strftime("%m%d_%H%M%S")
    copy = source.with_name(f"{source.stem}{COPY_TAG}{stamp}{source.suffix}")
    shutil.copy2(source, copy)
    excel.Visible = True
    wb = excel.Workbooks.Open(str(copy))
    excel.WindowState = -4137          # xlMaximized
    if zoom:
        for ws in wb.Worksheets:       # every sheet: Clicky moves between them during the case
            ws.Activate()
            excel.ActiveWindow.Zoom = zoom
        wb.Worksheets(1).Activate()
    return excel, wb, copy


def open_chat(shortcut: str) -> None:
    """Press Clicky's text shortcut (default Ctrl+Alt+T), the way a person opens the chat box."""
    keys = {"ctrl": "{Ctrl}", "alt": "{Alt}", "shift": "{Shift}", "win": "{Win}"}
    parts = [p.strip().lower() for p in shortcut.split("+") if p.strip()]
    combo = "".join(keys.get(p, p) for p in parts)
    try:
        import uiautomation as auto
        auto.SendKeys(combo, waitTime=0.3)
        say(f"   Khung chat chưa mở, bấm {shortcut}")
    except Exception as exc:
        say(f"   Không bấm được phím tắt mở chat: {exc}")


EXCEL_STUCK = ("Excel không trả lời robot: có thể đang mở một hộp thoại (Lưu thành…) hoặc bị treo từ lượt trước. "
               "Xem màn hình, đóng hộp thoại hoặc tắt Excel (khi không còn file nào của chị) rồi chạy lại.")
BUSY = (-2147418111, -2147417846)      # RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER: Excel is busy


def com_retry(fn, tries: int = 30, wait: float = 1.0):
    """Call fn(); while Excel rejects the call because it is busy (saving, re-opening), wait and try again."""
    for n in range(tries):
        try:
            return fn()
        except Exception as exc:
            code = getattr(exc, "hresult", None) or (exc.args[0] if exc.args else None)
            if code not in BUSY or n == tries - 1:
                raise
            time.sleep(wait)


def sheet_values(wb, max_rows: int = 200, max_cols: int = 12) -> dict:
    """Every sheet's values; retried as a whole while Excel is busy (run c: Clicky had just saved)."""
    return com_retry(lambda: _sheet_values(wb, max_rows, max_cols))


def _sheet_values(wb, max_rows: int, max_cols: int) -> dict:
    out = {}
    for ws in wb.Worksheets:
        rng = ws.Range(ws.Cells(1, 1), ws.Cells(max_rows, max_cols))
        values = rng.Value2 or ()
        out[ws.Name] = [list(r) for r in values]
    return out


def main(argv=None) -> int:
    """One run. Clicky's remembered column answers are set aside for the run and put back after it."""
    data_dir = Path(os.environ.get("CLICKY_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA", "")) / "Clicky")
    restore = columns.isolate_memory(data_dir) if data_dir.is_dir() else (lambda: None)
    try:
        return _main(argv)
    finally:
        restore()


def _main(argv=None) -> int:
    AUTO_PRESS.clear()                 # --lan N runs main N times in one process: no lap answers for the next
    TURN_USAGE.clear()
    ap = argparse.ArgumentParser(description="Diễn tập trọn hành trình báo cáo tháng bằng robot.")
    ap.add_argument("--workbook", default=None, help="File Excel trống của case, nằm trong thư mục dữ liệu tháng")
    ap.add_argument("--sample", action="store_true",
                    help="Việc 10: gõ \"xem em làm\", Clicky tự chạy file mẫu có sẵn; robot đọc workbook Clicky mở")
    ap.add_argument("--expected", default=str(HERE / "case01_expected.json"), help="Đáp án cần đạt (JSON)")
    ap.add_argument("--also", action="append", default=[], metavar="JSON",
                    help="Thêm phần chấm từ file đáp án khác (ví dụ case01_layout_expected.json)")
    ap.add_argument("--clicky", default=None, help="Đường dẫn Clicky.exe (mặc định: bản cài trong LOCALAPPDATA)")
    ap.add_argument("--driver", choices=("auto", "agent-desktop", "uia"), default="auto")
    ap.add_argument("--agent-desktop", default="agent-desktop", help="Đường dẫn agent-desktop.exe")
    ap.add_argument("--command", default="làm báo cáo tháng từ thư mục này")
    ap.add_argument("--out", default=None, help="Thư mục kết quả (mặc định: dien-tap\\<giờ> cạnh file Excel)")
    ap.add_argument("--allow-other-workbooks", action="store_true")
    ap.add_argument("--chat-shortcut", default="ctrl+alt+t", help="Phím tắt mở khung chat Clicky")
    ap.add_argument("--zoom", type=int, default=None, help="Độ phóng Excel cho lần chạy (ví dụ 85, 100, 130)")
    ap.add_argument("--no-visual", action="store_true", help="Bỏ phần chụp màn hình và chấm bằng ảnh")
    ap.add_argument("--local-only", action="store_true",
                    help="Bật chế độ 'Chỉ chạy trên máy, không gửi gì' (CLICKY_LOCAL_ONLY=1) và chấm không có lần gửi nào")
    ap.add_argument("--auto-press", action="append", default=None,
                    help="Nút robot bấm bất cứ khi nào hiện ra (mặc định: \"Đúng rồi, làm tiếp\" khi đáp án không chấm phiếu nhận)")
    ap.add_argument("--tamper", default=None, metavar="SHEET!CELL=DELTA",
                    help="Việc 3b: nhờ Clicky (móc thử CLICKY_TEST_TAMPER) cộng DELTA vào ô đó ngay sau khi lưu; "
                         "chấm Clicky có phát hiện đúng ô và đúng số lệch không")
    ap.add_argument("--lan", type=int, default=1,
                    help="Chạy cùng case N lượt liền; chỉ ĐẠT khi mọi lượt đều đạt (xử lý ở run_laps)")
    ap.add_argument("--egress-log", default=None,
                    help="Nhật ký gửi đi của Clicky (mặc định %%LOCALAPPDATA%%\\Clicky\\gui-di.jsonl)")
    args = ap.parse_args(argv)

    if args.sample:
        if args.expected == ap.get_default("expected"):
            args.expected = str(HERE / "mau_expected.json")
        if args.command == ap.get_default("command"):
            args.command = "xem em làm"
    elif not args.workbook:
        ap.error("cần --workbook (hoặc --sample để chạy file mẫu)")
    expected = json.loads(Path(args.expected).read_text(encoding="utf-8"))
    for extra in args.also:
        expected = {**expected, **json.loads(Path(extra).read_text(encoding="utf-8"))}
    workbook = Path(args.workbook).resolve() if args.workbook else None
    base = workbook.parent if workbook else Path(args.expected).resolve().parent
    out = Path(args.out) if args.out else base / "dien-tap" / time.strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    log = out / "scene.jsonl"
    clock = Clock()
    stalled = []

    power_before = power.snapshot()
    say(f"   Nguồn điện: {power.describe(power_before)}")
    if drivers.pids_of("Clicky.exe"):
        say("Clicky đang chạy. Thoát Clicky (khay hệ thống → Thoát) rồi chạy lại, để robot mở bản có nhật ký cảnh.")
        return 2
    exe = args.clicky or find_clicky()
    if not exe or not Path(exe).exists():
        say("Không tìm thấy Clicky.exe. Truyền --clicky \"<đường dẫn>\".")
        return 2

    if workbook:
        say(f"1/6 Mở bản sao {workbook.name} trong Excel")
        park_old_copies(workbook, out / "ban-sao-cu")
        excel, wb, copy = open_workbook(workbook, args.allow_other_workbooks, args.zoom)
    else:
        say("1/6 File mẫu: Clicky tự mở, robot chỉ kiểm Excel đang trống")
        excel, wb, copy = no_workbook(args.allow_other_workbooks)
    baseline = sheet_values(wb) if "intake" in expected and wb is not None else None
    data_dir = Path(os.environ.get("CLICKY_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA", "")) / "Clicky")
    egress = Path(args.egress_log) if args.egress_log else data_dir / "gui-di.jsonl"
    egress_start = egress.stat().st_size if egress.exists() else 0
    if args.local_only:
        expected = dict(expected, egress=dict(expected.get("egress") or {}, local_only=True))
    if args.tamper:
        ref, _, delta = args.tamper.partition("=")
        expected = dict(expected, tamper={"cell": ref, "delta": float(delta)})
        expected.pop("verify", None)            # a clean re-read is not expected on this run
    hash_before = file_hash(copy) if copy else ""
    # Counted before Clicky starts, so the files it writes do not count; names only, never opened.
    data_files = checks.data_file_count(workbook.parent, workbook, COPY_TAG) if workbook else None
    clock.lap("Mở Excel")

    say(f"2/6 Mở Clicky: {exe}")
    env = dict(os.environ, CLICKY_SCENE_LOG=str(log))
    if args.local_only:
        env["CLICKY_LOCAL_ONLY"] = "1"
    if args.tamper:
        env["CLICKY_TEST_TAMPER"] = args.tamper
    net = NetWatch()
    net.start()
    subprocess.Popen([exe], env=env, cwd=str(Path(exe).parent))
    watcher = None
    if not args.no_visual:
        shots = out / "anh"
        shots.mkdir(exist_ok=True)
        watcher = visual.Watcher(log, shots)
        watcher.start()
    driver = None
    if args.driver in ("auto", "agent-desktop"):
        try:
            driver = drivers.AgentDesktop(args.agent_desktop)
        except drivers.DriverError as exc:
            if args.driver == "agent-desktop":
                say(f"agent-desktop không dùng được: {exc}")
                return 2
            say(f"   agent-desktop không dùng được ({exc}); dùng uiautomation.")
    if driver is None:
        driver = drivers.Uia("Clicky.exe")
    # Clicky starts with the chat hidden; a person opens it with the text shortcut, so the robot does too.
    if not driver.wait_for(COMPOSER, 20):
        open_chat(args.chat_shortcut)
    if not driver.wait_for(COMPOSER, 70):
        say("Không thấy khung chat Clicky sau 90 giây, kể cả sau khi bấm phím tắt mở chat. Máy có đang khóa màn hình không?")
        return 2
    clock.lap("Clicky sẵn sàng")

    say(f"3/6 Gõ lệnh: {args.command}")
    driver.set_text(COMPOSER, args.command)
    driver.press(SEND)
    untouched = None
    # The sample plays with no clicks at all: the robot keeps its hands off unless told otherwise.
    auto = args.auto_press if args.auto_press is not None else (
        [] if "intake" in expected or args.sample else ["Đúng rồi, làm tiếp"])
    auto = list(auto) + list(expected.get("auto_press", []))
    target = expected.get("final_title", "")
    # "Xong" means saved, reopened and reconciled: a case graded without a final title (the hidden cases) is
    # run to the end, saved and reopened. Our own Case 01 specs opt in with "verify"; "save": false opts out.
    do_save = "verify" in expected or "tamper" in expected or bool(
        expected.get("save", not args.sample and not target))
    save_label = expected.get("save_label", "Lưu và kiểm tra")
    if do_save:
        auto.append(save_label)
    if "egress" in expected and not args.local_only:
        auto.append(expected["egress"].get("approve_label", "Gửi đi"))     # the first-send preview card
    if auto:
        AUTO_PRESS.append((driver, set(auto), set()))
    roles = expected.get("roles") or {}
    if roles.get("choose"):
        # Which source to use: asked before the receipt, so it is answered whenever it comes up.
        def chooser(e, parts=tuple(roles["choose"])):
            return next(filter(None, (match_label(p, e.get("labels")) for p in parts)), None)
        AUTO_PRESS.append((driver, chooser, set()))
    answerer = columns.Answerer(expected.get("columns"))
    AUTO_PRESS.append((driver, answerer, set()))     # unknown column names: answered from the key, never guessed
    if "intake" in expected:
        confirm = expected["intake"].get("confirm_label", "Đúng rồi, làm tiếp")
        asked = lambda ev: any(confirm in (e.get("labels") or []) for e in ev if e.get("event") == "choices")  # noqa: E731
        if asked(wait_log(log, asked, expected.get("first_stage_seconds", 240))):
            untouched = wb is not None and sheet_values(wb) == baseline and file_hash(copy) == hash_before
            say(f"   Phiếu nhận file: bấm \"{confirm}\"")
            if driver.wait_for(confirm, 20):
                driver.press(confirm)
        else:
            say("   Không thấy phiếu nhận file.")
    fix = expected.get("fix_label")
    if fix:
        limit = expected.get("first_stage_seconds", 240)
        events = wait_log(log, lambda ev: any(fix in (e.get("labels") or []) for e in ev if e.get("event") == "choices"),
                          limit)
        clock.lap("Chặng 1: đối chiếu và chỉ chỗ lệch")
        if clock.steps[-1][1] >= limit:
            stalled.append(("Chặng 1", clock.steps[-1][1]))
        if any(fix in (e.get("labels") or []) for e in events if e.get("event") == "choices"):
            say(f"4/6 Bấm \"{fix}\"")
            if driver.wait_for(fix, 20):
                driver.press(fix)
            else:
                say(f"   Không thấy nút \"{fix}\" trên màn hình.")
        else:
            say("4/6 Clicky không hỏi sửa. Ghi nhận và chấm tiếp.")

    say("5/6 Chờ Clicky làm xong báo cáo")
    pressable = pressable_labels() - {save_label}
    if target:
        finished = lambda ev: any(target in (e.get("title") or "") for e in ev if e.get("event") == "beat")  # noqa: E731
    else:
        # No title to wait for: wait for the story that ends the work, then a few seconds with no new scene.
        finished = lambda ev: settled(ev, pressable)  # noqa: E731
    limit = expected.get("second_stage_seconds", 300)
    done_events = wait_log(log, finished, limit, quiet=6.0)
    if target and finished(done_events) and not story_ended(done_events, target):
        # Without a voice each scene stays up for its reading time, often longer than the quiet window:
        # the scenes after the final card (bridge, sticky notes, save question) are still to come.
        say("   Chờ Clicky kể hết các cảnh sau thẻ cuối")
        wait_log(log, lambda ev: story_ended(ev, target), expected.get("story_end_seconds", 90), quiet=2.0)
    clock.lap("Chặng 2–3: sửa và lập báo cáo")
    if not finished(done_events):
        stalled.append(("Chặng 2–3", clock.steps[-1][1]))
    offered = lambda ev: any(save_label in (e.get("labels") or []) for e in ev if e.get("event") == "choices")  # noqa: E731
    if "verify" in expected or "tamper" in expected or (do_save and offered(checks.read_log(log))):
        say("   Chờ Clicky lưu, mở lại file và đối chiếu")
        verified = lambda ev: any(e.get("event") == "verify" for e in ev)  # noqa: E731
        if not verified(wait_log(log, verified, expected.get("verify_seconds", 120), quiet=4.0)):
            stalled.append(("Lưu và đối chiếu lại", expected.get("verify_seconds", 120)))
        clock.lap("Lưu và đối chiếu lại")

    say("6/6 Đọc số trong file và chấm")
    if wb is None:                     # the sample: read the workbook Clicky opened itself
        try:
            wb = com_retry(lambda: excel.ActiveWorkbook)
            copy = Path(com_retry(lambda: wb.FullName)) if wb is not None else None
        except Exception as exc:
            raise SystemExit(EXCEL_STUCK + f" ({exc})")
    try:
        sheets = sheet_values(wb) if wb is not None else {}
    except Exception as exc:
        raise SystemExit(EXCEL_STUCK + f" ({exc})")
    if "latency" in (expected.get("layout") or {}):
        # The last turn's latency line is written when that turn ends, often after its final scene.
        wait_log(log, layout_checks.turns_measured, expected.get("latency_wait_seconds", 15))
    events = checks.read_log(log)
    result = checks.evaluate(events, sheets, expected)
    result.checks.append(checks.stage_check(stalled))
    result.checks += [checks.Check(n, ok, d, blocking=b)
                      for n, ok, d, b in columns.check_lines(events, expected.get("columns"), answerer)]
    if do_save and "verify" not in expected and "tamper" not in expected:
        result.checks.append(checks.saved_check(events, save_label))
    result.checks.append(checks.receipt_reached_check(events, data_files, expected.get("stop_before_receipt_ok", False)))
    network = net.stop()
    egress_text = None
    if "egress" in expected and egress.exists():
        with egress.open("rb") as fh:
            fh.seek(egress_start)
            egress_text = fh.read().decode("utf-8", "replace")
    elif "egress" in expected and expected["egress"].get("local_only"):
        egress_text = ""
    result.checks += beta_checks.evaluate(events, sheets, expected, untouched_before_confirm=untouched,
                                          workbook=copy, egress_text=egress_text, network=network)
    power_state = power.worst(power_before, power.snapshot())
    void = power.void_reason(power_state)
    if void and "latency" in (expected.get("layout") or {}):
        say(f"   Thời gian đo được không tính: {void}")
    result.checks += layout_checks.evaluate(events, expected, void=void)
    if watcher is not None:
        watcher.stop()
        watcher.join(timeout=10)
        add_visual(result, events, watcher.captures, out)
    meta = {"case": expected.get("case", ""), "clicky": exe, "when": time.strftime("%d/%m/%Y %H:%M"),
            "driver": f"{driver.label} {driver.version}", "steps": clock.steps, "workbook": str(copy),
            "zoom": args.zoom or "mặc định", "network": network, "power": power_state,
            "power_text": power.describe(power_state), "timing_void": void,
            "mode": "local-only" if args.local_only else ("tamper " + args.tamper if args.tamper else "thường")}
    (out / "ket-qua.md").write_text(checks.report_md(result, meta), encoding="utf-8")
    if TURN_USAGE:
        (out / "excel-moi-luot-noi.json").write_text(json.dumps(TURN_USAGE, ensure_ascii=False, indent=1),
                                                     encoding="utf-8")
        for u in TURN_USAGE:
            say(f"   Excel khi bắt đầu lượt nói {u['turn']} ({u['text']}): {u['ws_mb']} MB, {u['handles']} handle")
    (out / "ket-qua.json").write_text(json.dumps(
        {"passed": result.passed, "meta": meta,
         "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail, "blocking": c.blocking} for c in result.checks]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    say(("ĐẠT" if result.passed else "KHÔNG ĐẠT") + f". Kết quả: {out / 'ket-qua.md'}")
    for c in result.checks:
        say(f"  [{'x' if c.ok else ' '}] {c.name}: {c.detail}")
    return 0 if result.passed else 1


def add_visual(result, events, captures, out: Path) -> None:
    """Fold the picture checks into the result: one line per check, worst scene named, a marked PNG per failure."""
    try:
        from PIL import Image
        rows = visual.grade(events, captures, lambda p: Image.open(p).convert("RGB"))
    except Exception as exc:
        result.checks.append(checks.Check("Chấm bằng ảnh", False, f"không chấm được: {exc}", blocking=False))
        return
    if not captures:
        result.checks.append(checks.Check("Chấm bằng ảnh", False, "không chụp được cảnh nào", blocking=False))
        return
    shots = {c.beat_no: c for c in captures}
    by_name: dict = {}
    for name, ok, detail, blocking, beat_no, marks in rows:
        entry = by_name.setdefault(name, {"ok": True, "blocking": blocking, "fails": [], "n": 0})
        entry["n"] += 1
        if not ok:
            entry["ok"] = False
            entry["fails"].append(detail)
            cap = shots.get(beat_no)
            if cap and cap.shot:
                try:
                    visual.annotate(cap.shot, cap.origin, marks, out / f"loi-canh-{beat_no:02d}.png")
                except Exception:
                    pass
    for name, e in by_name.items():
        detail = "; ".join(e["fails"][:3]) + (f" (+{len(e['fails']) - 3})" if len(e["fails"]) > 3 else "") \
            if e["fails"] else f"{e['n']} cảnh đều đạt"
        result.checks.append(checks.Check(name, e["ok"], detail, blocking=e["blocking"]))


def close_between_laps(keep: set, quit_excel: bool = False) -> None:
    """Between two laps: quit Clicky and close, without saving, every workbook the lap opened.
    Workbooks that were open before lap 1 (only with --allow-other-workbooks) are left alone.
    quit_excel (--excel-moi-luot): also quit Excel, so the next lap starts the way a person starts a month,
    with a fresh Excel. Only when nothing was open before lap 1."""
    subprocess.run(["taskkill", "/IM", "Clicky.exe", "/F"], capture_output=True)
    try:
        import win32com.client
        excel = win32com.client.Dispatch("Excel.Application")
        for wb in list(excel.Workbooks):
            if wb.FullName not in keep:
                wb.Close(SaveChanges=False)
        if quit_excel and not keep:
            excel.DisplayAlerts = False
            excel.Quit()
            del excel
            end = time.monotonic() + 20
            while drivers.pids_of("EXCEL.EXE") and time.monotonic() < end:
                time.sleep(1)
            if drivers.pids_of("EXCEL.EXE"):
                say("   Excel chưa thoát sau 20 giây; lượt sau chạy với Excel cũ.")
    except Exception as exc:
        say(f"   Không đóng được file Excel của lượt trước: {exc}")
    time.sleep(3)


def _process_usage(pid: int):
    """(working set MB, handle count) of one process, by Win32 calls; (None, None) when it cannot be read."""
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None, None
    try:
        c = Counters()
        c.cb = ctypes.sizeof(Counters)
        ws = round(c.WorkingSetSize / 2 ** 20) if k32.K32GetProcessMemoryInfo(h, ctypes.byref(c), c.cb) else None
        n = wintypes.DWORD()
        handles = n.value if k32.GetProcessHandleCount(h, ctypes.byref(n)) else None
        return ws, handles
    finally:
        k32.CloseHandle(h)


def excel_state() -> dict:
    """Excel between two laps, counts only: processes, memory, handles, open workbooks, and how many of them are
    the robot's own copies and their Power Query connections. Workbook names are matched, never printed.
    Attaches to a running Excel only: it never starts one."""
    pids = sorted(drivers.pids_of("EXCEL.EXE"))
    state = {"excel": len(pids), "ws_mb": None, "handles": None, "workbooks": 0, "robot_copies": 0,
             "connections": 0, "queries": 0}
    for pid in pids:
        try:
            ws, handles = _process_usage(pid)
        except Exception:                              # noqa: BLE001 - not on Windows, or no access
            ws, handles = None, None
        if ws is not None:
            state["ws_mb"] = (state["ws_mb"] or 0) + ws
        if handles is not None:
            state["handles"] = (state["handles"] or 0) + handles
    if not pids:
        return state
    try:
        import win32com.client
        excel = win32com.client.GetActiveObject("Excel.Application")
        books = list(excel.Workbooks)
        state["workbooks"] = len(books)
        for wb in books:
            if COPY_TAG in wb.Name:
                state["robot_copies"] += 1
                state["connections"] += int(wb.Connections.Count)
                state["queries"] += int(wb.Queries.Count)
    except Exception as exc:                           # noqa: BLE001 - reported, never fatal
        state["error"] = type(exc).__name__
    return state


def state_line(when: str, state: dict) -> str:
    def v(key, unit=""):
        return "?" if state.get(key) is None else f"{state[key]}{unit}"
    return (f"   Excel {when}: {state.get('excel', 0)} tiến trình, {v('ws_mb', ' MB')}, {v('handles')} handle, "
            f"{state.get('workbooks', 0)} file mở (bản sao robot {state.get('robot_copies', 0)}, "
            f"kết nối {state.get('connections', 0)}, query {state.get('queries', 0)})"
            + (f", lỗi đọc: {state['error']}" if state.get("error") else ""))


def open_workbook_names() -> set:
    try:
        import win32com.client
        return {wb.FullName for wb in win32com.client.Dispatch("Excel.Application").Workbooks}
    except Exception:
        return set()


def run_laps(argv, runner=None, cleanup=None, before=None) -> int:
    """--lan N: the same case N times in a row; ĐẠT only when every lap is ĐẠT (pass^k).

    A customer runs the month close once and must get it right that once, so one lucky pass
    proves little. Each lap gets its own result folder; the summary names every lap that failed.
    Exit code: 1 if any lap is KHÔNG ĐẠT, else 2 if any lap could not run, else 0."""
    runner = runner or main
    cleanup = cleanup or close_between_laps
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lan", type=int, default=1)
    pre.add_argument("--out", default=None)
    pre.add_argument("--excel-moi-luot", action="store_true",
                     help="Thoát hẳn Excel giữa các lượt, như người dùng mở máy làm tháng mới")
    known, rest = pre.parse_known_args(argv)
    if known.lan <= 1:
        return runner(rest + (["--out", known.out] if known.out else []))
    keep = before() if before else open_workbook_names()
    codes, times, states = [], [], []

    def note(lap: int, when: str) -> None:
        if probe is None:
            return
        st = dict(probe(), lap=lap, when=when, t=time.strftime("%H:%M:%S"))
        states.append(st)
        say(state_line(f"{when} lượt {lap}", st))
    probe = excel_state if runner is main else None
    for i in range(1, known.lan + 1):
        say(f"===== Lượt {i}/{known.lan} =====")
        note(i, "trước")
        lap_argv = list(rest) + (["--out", str(Path(known.out) / f"lan-{i}")] if known.out else [])
        t0 = time.monotonic()
        try:
            code = runner(lap_argv)
        except SystemExit as exc:
            if isinstance(exc.code, str):
                say(exc.code)
            code = exc.code if isinstance(exc.code, int) else 2
        codes.append(code)
        times.append(time.monotonic() - t0)
        note(i, "sau")
        if i < known.lan:
            if known.excel_moi_luot and cleanup is close_between_laps:
                cleanup(keep, quit_excel=True)
            else:
                cleanup(keep)
    names = {0: "ĐẠT", 1: "KHÔNG ĐẠT", 2: "không chạy được"}
    passed = sum(c == 0 for c in codes)
    lines = [f"# Tổng hợp {known.lan} lượt: {'ĐẠT' if passed == known.lan else 'KHÔNG ĐẠT'} ({passed}/{known.lan} lượt đạt)",
             "", "Chỉ ĐẠT khi mọi lượt đều đạt: khách chạy một lần là phải đúng lần đó.", "",
             "| Lượt | Kết quả | Thời gian |", "| --- | --- | --- |"]
    lines += [f"| {i} | {names.get(c, str(c))} | {t:.0f} giây |" for i, (c, t) in enumerate(zip(codes, times), 1)]
    text = "\n".join(lines) + "\n"
    say(text)
    if known.out:
        Path(known.out).mkdir(parents=True, exist_ok=True)
        (Path(known.out) / "tong-hop.md").write_text(text, encoding="utf-8")
        if states:
            (Path(known.out) / "excel-giua-cac-luot.json").write_text(
                json.dumps(states, ensure_ascii=False, indent=1), encoding="utf-8")
    if any(c == 1 for c in codes):
        return 1
    return 2 if any(c != 0 for c in codes) else 0


def run() -> int:
    """A crash is 'could not run' (2), never confused with KHÔNG ĐẠT (1)."""
    try:
        return run_laps(sys.argv[1:])
    except SystemExit as exc:
        if isinstance(exc.code, str):
            say(exc.code)
            return 2
        return exc.code if isinstance(exc.code, int) else 2
    except Exception:
        import traceback
        traceback.print_exc()
        say("Robot bị lỗi giữa chừng (mã 2): đây là lỗi của robot, chưa chấm được Clicky.")
        return 2


if __name__ == "__main__":
    sys.exit(run())
