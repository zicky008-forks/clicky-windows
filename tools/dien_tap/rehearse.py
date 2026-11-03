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

HERE = Path(__file__).resolve().parent
COMPOSER = "Tin nhắn cho Clicky"
SEND = "Gửi"


def say(text: str) -> None:
    print(text, flush=True)


def find_clicky() -> str | None:
    base = os.environ.get("LOCALAPPDATA", "")
    hits = sorted(glob.glob(os.path.join(base, "*", "current", "Clicky.exe")), key=os.path.getmtime, reverse=True)
    return hits[0] if hits else None


class Clock:
    def __init__(self):
        self.t0 = self.last = time.monotonic()
        self.steps = []

    def lap(self, name: str) -> None:
        now = time.monotonic()
        self.steps.append((name, now - self.last))
        self.last = now


def wait_log(log: Path, done, timeout: float, quiet: float = 0.0) -> list:
    """Poll the scene log until done(events) holds (then `quiet` seconds with no new event), or time runs out."""
    end = time.monotonic() + timeout
    events = []
    while time.monotonic() < end:
        events = checks.read_log(log)
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


def answer(driver, log: Path, part: str, timeout: float, question: str = "") -> str | None:
    """Wait for a choice whose label contains `part` (and question contains `question`), press it; the label or None."""
    def pick(ev):
        for e in ev:
            if e.get("event") == "choices" and question.casefold() in (e.get("question") or "").casefold():
                for lab in e.get("labels") or []:
                    if part.casefold() in lab.casefold():
                        return lab
        return None
    label = pick(wait_log(log, lambda ev: pick(ev) is not None, timeout))
    if label and driver.wait_for(label, 20):
        driver.press(label)
        return label
    return None


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


def open_workbook(source: Path, allow_others: bool, zoom: int | None = None):
    import win32com.client
    excel = win32com.client.Dispatch("Excel.Application")
    others = [wb.Name for wb in excel.Workbooks]
    if others and not allow_others:
        raise SystemExit(f"Excel đang mở file khác: {', '.join(others)}. Đóng hết rồi chạy lại "
                         "(diễn tập chỉ dùng số giả lập).")
    stamp = time.strftime("%m%d_%H%M%S")
    copy = source.with_name(f"{source.stem}_dientap_{stamp}{source.suffix}")
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


def sheet_values(wb, max_rows: int = 200, max_cols: int = 12) -> dict:
    out = {}
    for ws in wb.Worksheets:
        rng = ws.Range(ws.Cells(1, 1), ws.Cells(max_rows, max_cols))
        values = rng.Value2 or ()
        out[ws.Name] = [list(r) for r in values]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Diễn tập trọn hành trình báo cáo tháng bằng robot.")
    ap.add_argument("--workbook", required=True, help="File Excel trống của case, nằm trong thư mục dữ liệu tháng")
    ap.add_argument("--expected", default=str(HERE / "case01_expected.json"), help="Đáp án cần đạt (JSON)")
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
    ap.add_argument("--tamper", default=None, metavar="SHEET!CELL=DELTA",
                    help="Việc 3b: nhờ Clicky (móc thử CLICKY_TEST_TAMPER) cộng DELTA vào ô đó ngay sau khi lưu; "
                         "chấm Clicky có phát hiện đúng ô và đúng số lệch không")
    ap.add_argument("--egress-log", default=None,
                    help="Nhật ký gửi đi của Clicky (mặc định %%LOCALAPPDATA%%\\Clicky\\gui-di.jsonl)")
    args = ap.parse_args(argv)

    expected = json.loads(Path(args.expected).read_text(encoding="utf-8"))
    workbook = Path(args.workbook).resolve()
    out = Path(args.out) if args.out else workbook.parent / "dien-tap" / time.strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    log = out / "scene.jsonl"
    clock = Clock()
    stalled = []

    if drivers.pids_of("Clicky.exe"):
        say("Clicky đang chạy. Thoát Clicky (khay hệ thống → Thoát) rồi chạy lại, để robot mở bản có nhật ký cảnh.")
        return 2
    exe = args.clicky or find_clicky()
    if not exe or not Path(exe).exists():
        say("Không tìm thấy Clicky.exe. Truyền --clicky \"<đường dẫn>\".")
        return 2

    say(f"1/6 Mở bản sao {workbook.name} trong Excel")
    excel, wb, copy = open_workbook(workbook, args.allow_other_workbooks, args.zoom)
    baseline = sheet_values(wb) if "intake" in expected else None
    egress = Path(args.egress_log) if args.egress_log else Path(os.environ.get("LOCALAPPDATA", "")) / "Clicky" / "gui-di.jsonl"
    egress_start = egress.stat().st_size if egress.exists() else 0
    if args.local_only:
        expected = dict(expected, egress=dict(expected.get("egress") or {}, local_only=True))
    if args.tamper:
        ref, _, delta = args.tamper.partition("=")
        expected = dict(expected, tamper={"cell": ref, "delta": float(delta)})
        expected.pop("verify", None)            # a clean re-read is not expected on this run
    hash_before = file_hash(copy)
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
    if "intake" in expected:
        confirm = expected["intake"].get("confirm_label", "Đúng rồi, làm tiếp")
        asked = lambda ev: any(confirm in (e.get("labels") or []) for e in ev if e.get("event") == "choices")  # noqa: E731
        if asked(wait_log(log, asked, expected.get("first_stage_seconds", 240))):
            untouched = sheet_values(wb) == baseline and file_hash(copy) == hash_before
            say(f"   Phiếu nhận file: bấm \"{confirm}\"")
            if driver.wait_for(confirm, 20):
                driver.press(confirm)
        else:
            say("   Không thấy phiếu nhận file.")
    for part in (expected.get("roles") or {}).get("choose", []):
        got = answer(driver, log, part, expected.get("first_stage_seconds", 240),
                     expected["roles"].get("ask_contains", ""))
        say(f"   Chọn nguồn: {got}" if got else f"   Clicky không hỏi chọn \"{part}\"")
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
    target = expected.get("final_title", "")
    finished = lambda ev: any(target and target in (e.get("title") or "") for e in ev if e.get("event") == "beat") \
        or (not target and len(ev) > 0)  # noqa: E731
    limit = expected.get("second_stage_seconds", 300)
    done_events = wait_log(log, finished, limit, quiet=6.0)
    clock.lap("Chặng 2–3: sửa và lập báo cáo")
    if not finished(done_events):
        stalled.append(("Chặng 2–3", clock.steps[-1][1]))

    say("6/6 Đọc số trong file và chấm")
    sheets = sheet_values(wb)
    events = checks.read_log(log)
    result = checks.evaluate(events, sheets, expected)
    result.checks.append(checks.stage_check(stalled))
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
    if watcher is not None:
        watcher.stop()
        watcher.join(timeout=10)
        add_visual(result, events, watcher.captures, out)
    meta = {"case": expected.get("case", ""), "clicky": exe, "when": time.strftime("%d/%m/%Y %H:%M"),
            "driver": f"{driver.label} {driver.version}", "steps": clock.steps, "workbook": str(copy),
            "zoom": args.zoom or "mặc định", "network": network,
            "mode": "local-only" if args.local_only else ("tamper " + args.tamper if args.tamper else "thường")}
    (out / "ket-qua.md").write_text(checks.report_md(result, meta), encoding="utf-8")
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


def run() -> int:
    """A crash is 'could not run' (2), never confused with KHÔNG ĐẠT (1)."""
    try:
        return main()
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
