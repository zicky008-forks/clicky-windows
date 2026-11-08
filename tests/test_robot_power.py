"""Timings taken on a throttled PC (on battery, CPU under 90% of top speed) are shown but never pass or fail."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))

import checks          # noqa: E402
import layout_checks   # noqa: E402
import power           # noqa: E402


def test_void_reason_names_battery_and_slow_cpu():
    # DN-LAP-231 on 07/11 at 19:55.
    evening = {"ac": False, "battery_pct": 11, "plan": "Balanced", "cpu_pct": 67}
    why = power.void_reason(evening)
    assert why == "máy chạy pin (11%), CPU nhanh nhất chỉ 67% tốc độ tối đa"
    assert power.void_reason({"ac": True, "battery_pct": 100, "plan": "Balanced", "cpu_pct": 104}) is None
    assert power.void_reason({"ac": None, "cpu_pct": None}) is None            # unknown: not voided
    assert "cắm sạc" in power.describe({"ac": True, "battery_pct": 80, "plan": "Balanced", "cpu_pct": 99})


def test_worst_of_before_and_after():
    w = power.worst({"ac": True, "battery_pct": 40, "plan": "Balanced", "cpu_pct": 41},
                    {"ac": False, "battery_pct": 35, "plan": "Balanced", "cpu_pct": 38}, cpu_samples=[40, 67, 52])
    assert w == {"ac": False, "battery_pct": 35, "plan": "Balanced", "cpu_pct": 67, "boot_s": None}
    # An idle Balanced CPU is slow on purpose: the fastest sample of the run is what counts.
    busy = power.worst({"ac": True, "cpu_pct": 35}, cpu_samples=[35, 112, 60])
    assert busy["cpu_pct"] == 112 and power.void_reason(busy) is None
    assert power.worst() == {}


def test_parse_typeperf():
    out = ('\n"(PDH-CSV 4.0)","\\\\DN\\Processor Information(_Total)\\% Processor Performance"\n'
           '"11/07/2026 19:55:01.123","66.9"\n"11/07/2026 19:55:02.123","67.4"\n'
           '"11/07/2026 19:55:03.123","66.7"\nExiting, please wait...\n')
    assert power.parse_typeperf(out) == 67
    assert power.parse_typeperf("") is None


def test_latency_gates_are_void_on_a_throttled_pc():
    events = [{"event": "turn", "text": "Đúng rồi, làm tiếp"},
              {"event": "latency", "source": "chat", "mode": "code", "label": "Đúng rồi, làm tiếp",
               "first_feedback": 0, "first_caption": 0, "ready": 20422}]
    spec = {"layout": {"latency": {"first_feedback_ms": 1000, "ready_ms": 5000, "first_excel_ready_ms": 8000}}}
    timing = {c.name for c in layout_checks.latency_checks(events, spec["layout"]["latency"])}
    hard = [c for c in layout_checks.evaluate(events, spec) if c.name in timing]
    assert any(not c.ok and c.blocking for c in hard)
    every = layout_checks.evaluate(events, spec, void="máy chạy pin (11%)")
    soft = [c for c in every if c.name in timing]
    assert soft and all(not c.blocking for c in soft)
    assert all(c.detail.startswith("KHÔNG TÍNH (máy chạy pin (11%)): ") for c in soft)
    assert all(c.blocking for c in every if c.name not in timing)    # the layout lines still count
    res = checks.Result(soft)
    assert res.passed                                            # void, not failed
    md = checks.report_md(res, {"case": "01", "power_text": "chạy pin 11%", "timing_void": "máy chạy pin (11%)"})
    assert "Thời gian đo được không tính vì máy chạy pin (11%)." in md


def test_boot_time_is_kept_from_the_start_and_described():
    w = power.worst({"ac": True, "cpu_pct": 120, "boot_s": 300}, {"ac": True, "cpu_pct": 110, "boot_s": 500})
    assert w["boot_s"] == 300                          # how long the PC had been up when the run started
    assert "máy mở được 5 phút" in power.describe(w)
