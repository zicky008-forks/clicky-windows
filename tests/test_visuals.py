"""The three graphics (leader tags, sticky notes, profit bridge): engine figures only, laid out off the numbers."""
import asyncio
import json
from decimal import Decimal

import pytest

from engine import visuals
from engine.checks import Issue
from engine.monthly_report import Figure, Variance
from ui.card_layout import bridge_bars, bridge_size, leaders_layout, notes_layout, overlap

SCREEN = (0, 0, 1920, 1040)
D = Decimal


def _selling(extra=()):
    sources = [("6411", "Chi phí nhân viên", 31, "dr", D(760_000_000)),
               ("6417", "Chi phí dịch vụ mua ngoài", 32, "dr", D(420_000_000)),
               ("6418", "Chi phí bằng tiền khác", 33, "dr", D(200_000_000)), *extra]
    return Figure("selling", "Chi phí bán hàng", D(1_380_000_000), sources)


# ── numbers ───────────────────────────────────────────────────────────────────

def test_leader_tags_are_the_figure_s_own_accounts_and_add_up_to_it():
    tags = visuals.leader_tags(_selling())
    assert [t["account"] for t in tags["tags"]] == ["6411", "6417", "6418"]          # biggest first
    assert sum(t["amount"] for t in tags["tags"]) == tags["figure"] == 1_380_000_000 and tags["ok"]
    assert tags["check"] == "3 tài khoản cộng lại đúng 1,38 tỷ"
    assert visuals.leader_sentence(tags).startswith("Chi phí bán hàng 1,38 tỷ đến từ 3 tài khoản")
    json.dumps(tags)                                                                   # plain JSON for the scene log


def test_tags_that_do_not_add_up_are_said_in_red_not_hidden():
    figure = _selling()
    figure.value = D(1_387_500_000)
    tags = visuals.leader_tags(figure)
    assert not tags["ok"] and "lệch 7,5 triệu" in tags["check"]
    assert "Cảnh báo" in visuals.leader_sentence(tags)


def test_one_account_on_two_rows_is_one_tag():
    tags = visuals.leader_tags(_selling([("6418", "Chi phí bằng tiền khác", 34, "dr", D(0))]))
    assert len(tags["tags"]) == 3


def test_the_line_opened_up_is_a_split_cost_that_moved_most():
    single = Figure("cogs", "Giá vốn hàng bán", D(5_800_000_000), [("632", "Giá vốn", 20, "dr", D(5_800_000_000))])
    admin = Figure("admin", "Chi phí QLDN", D(690_000_000), [("6421", "a", 40, "dr", D(520_000_000)),
                                                             ("6428", "b", 41, "dr", D(170_000_000))])
    moved = [Variance("cogs", "", D(0), D(0), []), Variance("selling", "", D(1380), D(1150), []),
             Variance("admin", "", D(690), D(660), [])]
    assert visuals.figure_to_explain([single, _selling(), admin], moved).key == "selling"
    assert visuals.figure_to_explain([single], moved) is None                       # nothing split: no wires


def test_bridge_steps_add_up_to_the_real_profit():
    bridge = visuals.profit_bridge(D(556_000_000), [("Hàng trả lại ghi Nợ 511", D(-120_000_000))], D(436_000_000))
    assert [b["text"] for b in bridge["bars"]] == ["556 triệu", "−120 triệu", "436 triệu"] and bridge["ok"]
    assert bridge["bars"][0]["amount"] + bridge["bars"][1]["amount"] == bridge["bars"][-1]["amount"]
    assert visuals.bridge_sentence(bridge) == ("Từ 556 triệu trên báo cáo xuống 436 triệu thực: "
                                               "trừ 120 triệu hàng trả lại ghi Nợ 511.")
    assert not visuals.profit_bridge(D(556), [("x", D(-100))], D(436))["ok"]
    assert all(len(b["label"].split()) <= 6 for b in bridge["bars"])                 # each column read at a glance


class _Line:
    def __init__(self, account, name, row, close_dr=0, close_cr=0):
        self.account, self.name, self.row = account, name, row
        self.close_dr, self.close_cr = D(close_dr), D(close_cr)


class _TB:
    lines = [_Line("111", "Tiền mặt", 7, close_cr=195_000_000)]


def test_every_trap_gets_a_note_and_one_story_shares_one():
    issues = [Issue("error", "wrong_side", "TK 111 Tiền mặt dư Có 195.000.000: ...", 7),
              Issue("error", "closing_mismatch", "Thu nhập kết chuyển ...", 37),
              Issue("error", "profit_tie", "Lợi nhuận ... lệch +120.000.000", 18),
              Issue("error", "recurring_missing", "Kỳ này chưa thấy khấu hao ... TK 214 ...", 0,
                    "khấu hao tài sản cố định", D(45_000_000)),
              Issue("warning", "text_amount", "[TB_T09] Dòng 9: số tiền đang ở dạng chữ", 0)]
    notes = visuals.report_notes(issues, _TB(), D(120_000_000), {"returns": 21, "pat": 20})
    codes = [c for n in notes for c in n["codes"]]
    assert {"wrong_side", "closing_mismatch", "profit_tie", "recurring_missing", "text_amount"} <= set(codes)
    returns = notes[0]
    assert returns["warn"] == "120 triệu hàng bán bị trả lại đang ghi Nợ TK 511" and returns["line"] == 21
    assert returns["action"] == "Chuyển sang TK 521 trước khi gửi sếp"
    cash = next(n for n in notes if "wrong_side" in n["codes"])
    assert cash["warn"] == "TK 111 tiền mặt âm 195 triệu" and cash["line"] is None
    assert any("khấu hao" in n["warn"] and "45 triệu" in n["warn"] for n in notes)
    assert notes[-1]["warn"] == "Sổ kỳ trước còn 1 lỗi"                                # last period: one note
    assert all(len(n["warn"]) <= 70 and n["action"] for n in notes)


def test_without_returns_the_closing_traps_have_their_own_notes():
    issues = [Issue("error", "closing_mismatch", "x", 37), Issue("error", "profit_tie", "y", 18)]
    notes = visuals.report_notes(issues, _TB(), D(0), {"net_rev": 7, "pat": 20})
    assert [n["line"] for n in notes] == [7, 20]


def test_cause_notes_name_each_cause_with_its_size():
    rows = [["Đọc nhầm ngày tháng", 334, D(2_900_000_000), "", "..."], ["Xoá trùng nhầm", 27, D(182_200_000), "", "..."],
            ["Phần lệch chưa rõ nguyên nhân", "", D(5_000_000), "", "..."]]
    notes = visuals.cause_notes(rows)
    assert notes[0]["warn"] == "Đọc nhầm ngày tháng: 334 chứng từ · 2,9 tỷ"
    assert notes[1]["warn"] == "Xoá trùng nhầm: 27 chứng từ · 182,2 triệu"
    assert notes[2]["action"].startswith("Anh xem bảng chi tiết") and [n["row"] for n in notes] == [0, 1, 2]


# ── layout ────────────────────────────────────────────────────────────────────

def test_tags_sit_off_the_figures_and_every_wire_reaches_its_tag():
    figures = [(0, 100, 900, 700)]
    anchor = (40, 400, 520, 422)
    lay = leaders_layout(anchor, 3, SCREEN, figures)
    assert all(overlap(t, figures) == 0 for t in lay["tags"] + [lay["check"]])
    assert len(lay["wires"]) == 3
    for tag, ((x1, y1), (xf, yf), (x2, y2)) in zip(lay["tags"], lay["wires"]):
        assert (x1, y1) == (anchor[2], anchor[3]) and yf == anchor[3]                   # along the gridline under it
        assert all(not (f[0] < xf and x1 < f[2] and f[1] < yf < f[3]) or yf == anchor[3] for f in figures)
        assert tag[0] == x2 and tag[1] <= y2 <= tag[3]                                  # lands on its own tag


def test_notes_never_stack_or_cover_figures_or_each_other_s_range():
    figures = [(0, 100, 900, 700)]
    anchors = [(0, 400, 900, 422), (0, 100, 900, 140), (0, 600, 900, 622)]
    rects = notes_layout(anchors, [96, 96, 96], SCREEN, figures)
    for i, r in enumerate(rects):
        assert overlap(r, figures) == 0 and all(overlap(r, [a]) == 0 for a in anchors)
        assert all(overlap(r, [o]) == 0 for o in rects[:i])


def test_bridge_bars_float_from_the_running_total():
    bars = bridge_bars([556, -120, 436], [True, False, True], (0, 0, 400, 150))
    first, step, last = bars
    assert first[1] == step[1] == 0 and first[3] == 150                               # the step hangs from 556
    assert step[3] == pytest.approx(last[1])                                            # and ends where 436 stands
    assert last[3] == 150 and bridge_size(3)[0] > 3 * 88


# ── play: the graphics are drawn, logged, and do not need the AI ──────────────

class _Sig:
    def __init__(self, log, name):
        self.log, self.name = log, name

    def emit(self, *a):
        self.log.append((self.name, a))


class _Excel:
    def activate(self, sheet):
        pass

    def screen_rect(self, ref, reveal):
        row = int("".join(c for c in ref.split("!")[-1].split(":")[0] if c.isdigit()) or 1)
        return (100.0, 40.0 + 20 * row, 600.0, 60.0 + 20 * row)

    def figure_boxes(self):
        return {"numbers": [(300.0, 100.0, 600.0, 600.0)], "text": [(100.0, 100.0, 300.0, 600.0)]}


class _Manager:
    def __init__(self):
        self.log = []
        self._excel = _Excel()
        for n in ("sig_clear_drawings", "sig_response_chunk", "sig_draw", "sig_point_hold",
                  "sig_point_at", "sig_point_release", "sig_choices", "sig_guided"):
            setattr(self, n, _Sig(self.log, n))

    def _physical_to_logical_bbox(self, rect):
        return rect

    async def _reply_local(self, text):
        self.log.append(("say", (text,)))
        return True


def test_play_draws_each_graphic_with_engine_figures_and_logs_them(monkeypatch, tmp_path):
    from office import month_close as mc
    import scene_log
    monkeypatch.setattr(mc, "BEAT_GAP_S", 0)
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(tmp_path / "scene.jsonl"))
    tags = visuals.leader_tags(_selling())
    bridge = visuals.profit_bridge(D(556_000_000), [("Hàng trả lại ghi Nợ 511", D(-120_000_000))], D(436_000_000))
    notes = [{"codes": ["wrong_side"], "warn": "TK 111 âm", "action": "Kiểm phiếu thu", "ref": "'BC thang'!A1:G2"},
             {"codes": ["returns_in_revenue"], "warn": "120 triệu", "action": "Chuyển 521", "ref": "'BC thang'!A21:G21"}]
    beats = [mc.Beat("dây", "'BC thang'!A12:C12", graphic={"kind": "leaders", "figure_ref": "'BC thang'!C12", **tags}),
             mc.Beat("cầu", "'BC thang'!A20:G23", graphic={"kind": "bridge", **bridge}),
             mc.Beat("giấy", "'BC thang'!A1:G23", graphic={"kind": "notes", "notes": notes, "plain": True})]
    m = _Manager()
    asyncio.run(mc.play(m, beats))
    drawn = [a[0] for n, a in m.log if n == "sig_draw"]
    kinds = [d["kind"] for d in drawn]
    assert kinds == ["spotlight", "leaders", "spotlight", "bridge", "notes"]           # notes: no dimming
    leaders = drawn[1]
    assert leaders["anchor"] == [100.0, 280.0, 600.0, 300.0] and leaders["avoid"] == [[300.0, 100.0, 600.0, 600.0]]
    assert [n["anchor"] for n in drawn[4]["notes"]] == [[100.0, 60.0, 600.0, 80.0], [100.0, 460.0, 600.0, 480.0]]
    logged = [json.loads(l) for l in (tmp_path / "scene.jsonl").read_text(encoding="utf-8").splitlines()]
    graphics = [e["graphic"] for e in logged if e["event"] == "beat"]
    assert graphics[0]["sum"] == graphics[0]["figure"] == 1_380_000_000
    assert graphics[1]["bars"][-1]["amount"] == 436_000_000 and graphics[2]["notes"][0]["codes"] == ["wrong_side"]
    scene_log.record  # the log stays the robot's record


def test_overlay_paints_each_graphic_and_reports_where_it_landed(monkeypatch, tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    import time
    monkeypatch.setenv("CLICKY_SCENE_LOG", str(tmp_path / "scene.jsonl"))
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.card_layout import BOARD
    from ui.overlay import CursorOverlay
    BOARD.clear()
    ov = CursorOverlay()
    ov.setGeometry(0, 0, 800, 800)
    figures = [[0, 100, 240, 780]]
    tags = visuals.leader_tags(_selling())
    bridge = visuals.profit_bridge(D(556_000_000), [("Hàng trả lại ghi Nợ 511", D(-120_000_000))], D(436_000_000))
    ov.add_shape({"kind": "leaders", "anchor": [20, 300, 240, 322], "avoid": figures, **tags})
    ov.add_shape({"kind": "bridge", "anchor": [20, 500, 240, 560], "avoid": figures, **bridge})
    ov.add_shape({"kind": "notes", "avoid": figures,
                  "notes": [{"warn": "TK 111 tiền mặt âm 195 triệu", "action": "Kiểm phiếu thu", "anchor": [0, 100, 240, 120]}]})
    for shape in ov._annotations:
        shape["start"] = time.monotonic() - 5                    # everything already arrived
    ov.grab()
    events = [json.loads(l) for l in (tmp_path / "scene.jsonl").read_text(encoding="utf-8").splitlines()]
    placed = {e["kind"]: e for e in events if e["event"] == "graphic"}
    assert set(placed) == {"leaders", "bridge", "notes"}
    assert all(e["covers_figures"] == 0 and not e["covers_range"] for e in placed.values())
    assert placed["leaders"]["ok"] and placed["leaders"]["tags"][0] == "6411 760 triệu"
    rects = [r for e in placed.values() for r in e["rects"]]
    assert len(BOARD.others()) >= 3                              # the button card will keep off them
    ov.clear_annotations()
    assert BOARD.others() == [] and rects
    BOARD.clear()
