"""How the robot works Clicky's own windows, the way a person would: type into the chat box, press buttons.

Two interchangeable drivers:
- AgentDesktop: the agent-desktop CLI (Vercel Labs, Apache-2.0), headless UI Automation by ref.
- Uia: the `uiautomation` Python package Clicky already ships with. The fallback when
  agent-desktop is missing or blocked by the company's antivirus.

Both find controls by their accessible name ("Tin nhắn cho Clicky", "Gửi", "Sửa giúp tôi").
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time


class DriverError(RuntimeError):
    pass


def iter_nodes(tree):
    """Every node of an agent-desktop snapshot tree, depth first."""
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            yield node
            stack.extend(reversed(node.get("children") or []))
        elif isinstance(node, list):
            stack.extend(reversed(node))


def find_ref(tree, name: str):
    """Ref of the first actionable node whose name equals `name`, else whose name contains it."""
    nodes = [n for n in iter_nodes(tree) if n.get("ref_id")]
    for n in nodes:
        if (n.get("name") or "").strip() == name:
            return n["ref_id"]
    for n in nodes:
        if name.casefold() in (n.get("name") or "").casefold():
            return n["ref_id"]
    return None


def window_ids(data) -> list:
    """Window ids anywhere in a list-windows (or AMBIGUOUS_TARGET) envelope."""
    found = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("id", "window_id") and isinstance(v, str) and v not in found:
                    found.append(v)
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    return found


def pids_of(image: str) -> set:
    """Process ids running `image` (e.g. Clicky.exe), from tasklist."""
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return parse_tasklist(out)


def parse_tasklist(csv_text: str) -> set:
    import csv
    import io
    pids = set()
    for row in csv.reader(io.StringIO(csv_text)):
        if len(row) >= 2 and row[1].strip().isdigit():
            pids.add(int(row[1]))
    return pids


class AgentDesktop:
    label = "agent-desktop"

    def __init__(self, exe: str = "agent-desktop", app: str = "Clicky"):
        path = shutil.which(exe) or exe
        self.exe, self.app = path, app
        self.version = self._run("version").get("data")

    def _run(self, *args, timeout: float = 30):
        try:
            done = subprocess.run([self.exe, *args], capture_output=True, text=True, encoding="utf-8",
                                  timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DriverError(f"agent-desktop không chạy được: {exc}") from exc
        try:
            env = json.loads(done.stdout)
        except ValueError as exc:
            raise DriverError(f"agent-desktop trả về không phải JSON: {done.stdout[:200]} {done.stderr[:200]}") from exc
        if not env.get("ok"):
            err = env.get("error") or {}
            raise DriverError(f"{err.get('code')}: {err.get('message')}")
        return env

    def _find(self, name: str):
        try:
            wins = window_ids(self._run("list-windows", "--app", self.app).get("data"))
        except DriverError:
            wins = []
        for win in wins or [None]:
            args = ["snapshot", "--app", self.app, "-i", "--compact", "--timeout-ms", "5000"]
            if win:
                args += ["--window-id", win]
            try:
                snap = self._run(*args, timeout=20)
            except DriverError:
                continue
            ref = find_ref((snap.get("data") or {}).get("tree"), name)
            if ref:
                return ref
        return None

    def wait_for(self, name: str, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._find(name):
                return True
            time.sleep(1.0)
        return False

    def set_text(self, name: str, text: str) -> None:
        ref = self._find(name)
        if not ref:
            raise DriverError(f"Không thấy ô \"{name}\"")
        self._run("set-value", ref, text)

    def press(self, name: str) -> None:
        ref = self._find(name)
        if not ref:
            raise DriverError(f"Không thấy nút \"{name}\"")
        self._run("click", ref)


class Uia:
    label = "uiautomation"

    def __init__(self, image: str = "Clicky.exe"):
        try:
            import uiautomation as auto
        except ImportError as exc:
            raise DriverError("Thiếu thư viện uiautomation (pip install uiautomation)") from exc
        self.auto, self.image = auto, image
        self.version = getattr(auto, "VERSION", "?")

    def _find(self, name: str):
        auto = self.auto
        pids = pids_of(self.image)
        for top in auto.GetRootControl().GetChildren():
            if top.ProcessId not in pids:
                continue
            hit = top.Control(searchDepth=15, Name=name)
            if hit.Exists(0, 0):
                return hit
        return None

    def wait_for(self, name: str, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._find(name):
                return True
            time.sleep(1.0)
        return False

    def set_text(self, name: str, text: str) -> None:
        ctl = self._find(name)
        if ctl is None:
            raise DriverError(f"Không thấy ô \"{name}\"")
        pattern = ctl.GetPattern(self.auto.PatternId.ValuePattern)
        if pattern is None:
            raise DriverError(f"Ô \"{name}\" không nhận chữ (không có ValuePattern)")
        pattern.SetValue(text)

    def press(self, name: str) -> None:
        ctl = self._find(name)
        if ctl is None:
            raise DriverError(f"Không thấy nút \"{name}\"")
        # Control(Name=...) returns the generic Control class, which has no GetInvokePattern(): ask for the pattern by id.
        pattern = ctl.GetPattern(self.auto.PatternId.InvokePattern)
        if pattern is None:
            raise DriverError(f"Nút \"{name}\" không bấm được (không có InvokePattern)")
        pattern.Invoke()
