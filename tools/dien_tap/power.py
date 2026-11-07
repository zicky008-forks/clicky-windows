"""Is this PC able to give a fair timing? Power source, battery, power plan and CPU speed, read without admin.

DN-LAP-231, 07/11 evening: on battery at 11%, Balanced plan, CPU at ~67% of its top speed, and every turn of
Case 01 ran 2-3x slower on two different builds. A timing taken like that says nothing about Clicky, so the
robot records the power state with every run and voids the timing gates (shown, not failed) when it is off.
"""
from __future__ import annotations

import re
import subprocess

MIN_CPU_PCT = 90          # if even the fastest sample of a run is below this, the clock was capped: void


def _ac_battery():
    import ctypes

    class Status(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
    s = Status()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return None, None
    ac = {0: False, 1: True}.get(s.ACLineStatus)
    pct = None if s.BatteryLifePercent == 255 else int(s.BatteryLifePercent)
    return ac, pct


def _plan():
    out = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True, timeout=10).stdout
    m = re.search(r"\(([^)]+)\)\s*$", out.strip())
    return m.group(1) if m else (out.strip() or None)


def parse_typeperf(text: str):
    """Mean of the samples in typeperf's CSV output ("time","value" lines), or None."""
    vals = []
    for line in (text or "").splitlines():
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) >= 2:
            try:
                vals.append(float(parts[-1]))
            except ValueError:
                continue
    return round(sum(vals) / len(vals)) if vals else None


def _cpu_pct():
    out = subprocess.run(["typeperf", r"\Processor Information(_Total)\% Processor Performance", "-sc", "3"],
                         capture_output=True, text=True, timeout=20).stdout
    return parse_typeperf(out)


def snapshot() -> dict:
    """{"ac", "battery_pct", "plan", "cpu_pct"}; any part that cannot be read is None."""
    snap = {"ac": None, "battery_pct": None, "plan": None, "cpu_pct": None}
    for key, read in (("ac", None), ("plan", _plan), ("cpu_pct", _cpu_pct)):
        try:
            if key == "ac":
                snap["ac"], snap["battery_pct"] = _ac_battery()
            else:
                snap[key] = read()
        except Exception:                      # noqa: BLE001 - not Windows, or the counter is missing
            pass
    return snap


class CpuWatch:
    """CPU speed sampled every few seconds while the run goes on. An idle CPU on Balanced runs slow on purpose,
    so one idle sample says little; the fastest sample of the run shows whether the clock could go to full
    speed when Clicky and Excel were busy."""

    def __init__(self, every: float = 5.0):
        import threading
        self.samples: list = []
        self._stop = threading.Event()
        self._every = every
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                v = _cpu_pct()
                if v is not None:
                    self.samples.append(v)
            except Exception:                  # noqa: BLE001 - no counter on this PC
                return
            self._stop.wait(self._every)

    def start(self):
        self._thread.start()
        return self

    def stop(self) -> list:
        self._stop.set()
        self._thread.join(timeout=25)
        return list(self.samples)


def worst(*snaps, cpu_samples=None) -> dict:
    """Combine the power samples taken before and after a run (on battery if any was) with the CPU speed seen
    during it (the fastest sample; without samples, the fastest snapshot)."""
    snaps = [s for s in snaps if s]
    if not snaps:
        return {}
    acs = [s.get("ac") for s in snaps if s.get("ac") is not None]
    cpus = list(cpu_samples or []) or [s.get("cpu_pct") for s in snaps if s.get("cpu_pct") is not None]
    pcts = [s.get("battery_pct") for s in snaps if s.get("battery_pct") is not None]
    return {"ac": (all(acs) if acs else None), "battery_pct": (min(pcts) if pcts else None),
            "plan": snaps[-1].get("plan") or snaps[0].get("plan"), "cpu_pct": (max(cpus) if cpus else None)}


def void_reason(snap: dict, min_cpu: int = MIN_CPU_PCT):
    """Why a timing taken in this state does not count, or None when it does (or cannot be told)."""
    why = []
    if snap.get("ac") is False:
        pct = snap.get("battery_pct")
        why.append("máy chạy pin" + (f" ({pct}%)" if pct is not None else ""))
    if snap.get("cpu_pct") is not None and snap["cpu_pct"] < min_cpu:
        why.append(f"CPU nhanh nhất chỉ {snap['cpu_pct']}% tốc độ tối đa")
    return ", ".join(why) or None


def describe(snap: dict) -> str:
    if not snap:
        return "không đọc được"
    src = {True: "cắm sạc", False: "chạy pin", None: "không rõ nguồn"}[snap.get("ac")]
    bits = [src + (f" {snap['battery_pct']}%" if snap.get("battery_pct") is not None else "")]
    if snap.get("plan"):
        bits.append(f"chế độ nguồn {snap['plan']}")
    if snap.get("cpu_pct") is not None:
        bits.append(f"CPU {snap['cpu_pct']}% tốc độ tối đa")
    return ", ".join(bits)
