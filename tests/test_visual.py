"""The robot's picture checks: frame on the cell, card and pointer off the figures, ring on screen."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dien_tap"))

import checks    # noqa: E402
import visual    # noqa: E402

# 150 % display: logical box → physical rect is ×1.5.
BOX = [100.0, 200.0, 300.0, 220.0]
CELL = [150.0, 300.0, 450.0, 330.0]
REF = "'Doi chieu'!C7:E7"
LEFT_FIGURE = ("B7", [60.0, 300.0, 148.0, 330.0], 8_400_000_000)    # the figure next to the explained range
BELOW_FIGURE = ("C9", [150.0, 360.0, 450.0, 390.0], 197_500_000)
SCREEN = [0.0, 0.0, 1920.0, 1080.0]


def frame_image(rect, shift=0, size=(800, 600)):
    img = Image.new("RGB", size, (255, 255, 255))
    pad = visual.FRAME_PAD * 1.5
    l, t, r, b = rect[0] - pad + shift, rect[1] - pad, rect[2] + pad + shift, rect[3] + pad
    ImageDraw.Draw(img).rectangle([l, t, r, b], outline=visual.TONE_RGB["bad"], width=3)
    return img


def events(point=None, card=None):
    ev = [
        {"t": 1.0, "event": "turn", "text": "làm báo cáo tháng"},
        {"t": 2.0, "event": "locate", "ref": REF, "excel_rect": CELL, "box": BOX},
        {"t": 2.1, "event": "beat", "i": 0, "ref": REF, "box": BOX, "title": "Lệch"},
        {"t": 2.2, "event": "draw", "shape": {"kind": "spotlight", "x1": 100, "y1": 200, "x2": 300, "y2": 220,
                                              "tone": "bad"}},
    ]
    if card:
        ev.append({"t": 2.3, "event": "card", "rect": card, "anchor": BOX, "title": "Lệch"})
    if point:
        ev.append({"t": 2.4, "event": "point", "x": point[0], "y": point[1], "label": ""})
    return ev


def capture(shot="", cells=(LEFT_FIGURE, BELOW_FIGURE), screen=SCREEN):
    target = ("C7", CELL, -5_420_000_000)
    return visual.Capture(beat_no=0, shot=shot, robot_rect=list(CELL), cells=[target, *cells], screen=list(screen),
                          shown=True, shown_detail="Excel hiện rõ ở ô")


def by_name(rows):
    return {r[0]: r for r in rows}


def test_mapping_round_trip():
    m = visual.mapping(CELL, BOX)
    assert visual.to_physical(BOX, m) == CELL
    assert visual.point_physical(100, 210, m) == (150.0, 315.0)


def test_frame_found_on_the_cell_and_offset_measured():
    found, off = visual.frame_offsets(frame_image(CELL), [CELL[0] - 7.5, CELL[1] - 7.5, CELL[2] + 7.5, CELL[3] + 7.5],
                                      visual.TONE_RGB["bad"])
    assert found >= 0.9 and off <= 1.5
    found, off = visual.frame_offsets(frame_image(CELL, shift=8),
                                      [CELL[0] - 7.5, CELL[1] - 7.5, CELL[2] + 7.5, CELL[3] + 7.5],
                                      visual.TONE_RGB["bad"])
    assert off > visual.MAX_FRAME_ERR


def test_well_placed_scene_passes_every_picture_check(tmp_path):
    shot = tmp_path / "canh.png"
    frame_image(CELL).save(shot)
    # Pointer tip on the frame's top edge, card in the empty space to the right.
    rows = visual.grade(events(point=(200.0, 195.0), card=[310.0, 200.0, 500.0, 260.0]),
                        [capture(str(shot))], lambda p: Image.open(p).convert("RGB"))
    names = by_name(rows)
    assert all(r[1] for r in rows), [r for r in rows if not r[1]]
    for want in ("Clicky tính đúng vị trí ô (so với robot tự hỏi Excel)", "Khung vẽ thật nằm đúng ô trên màn hình",
                 "Thẻ không che con số", "Con trỏ chạm mép khung", "Con trỏ và vòng tròn không che số khác",
                 "Vòng tròn nằm trọn trong màn hình"):
        assert want in names


def test_run_3_pointer_beside_the_frame_fails():
    # Clicky's pointer_spot today: 14 logical px left of the range, ring over the neighbouring figure.
    rows = by_name(visual.grade(events(point=(86.0, 210.0)), [capture()]))
    assert not rows["Con trỏ chạm mép khung"][1]
    assert "cách khung" in rows["Con trỏ chạm mép khung"][2]
    assert not rows["Con trỏ và vòng tròn không che số khác"][1]
    assert "B7" in rows["Con trỏ và vòng tròn không che số khác"][2]


def test_card_over_figures_or_its_own_range_fails():
    rows = by_name(visual.grade(events(card=[100.0, 228.0, 300.0, 270.0]), [capture()]))
    assert not rows["Thẻ không che con số"][1] and "C9" in rows["Thẻ không che con số"][2]
    rows = by_name(visual.grade(events(card=[150.0, 205.0, 350.0, 260.0]), [capture()]))
    assert "chính vùng" in rows["Thẻ không che con số"][2]


def test_ring_cut_at_screen_edge_fails():
    edge = [10.0, 300.0, 300.0, 330.0]
    ev = events(point=(4.0, 210.0))
    ev[1]["excel_rect"] = edge
    ev[1]["box"] = ev[2]["box"] = [6.67, 200.0, 200.0, 220.0]
    cap = visual.Capture(beat_no=0, robot_rect=list(edge), cells=[], screen=SCREEN, shown=True)
    rows = by_name(visual.grade(ev, [cap]))
    assert not rows["Vòng tròn nằm trọn trong màn hình"][1]


def test_clicky_rect_disagreeing_with_excel_fails():
    cap = capture()
    cap.robot_rect = [CELL[0] + 20, CELL[1], CELL[2] + 20, CELL[3]]
    rows = by_name(visual.grade(events(), [cap]))
    assert not rows["Clicky tính đúng vị trí ô (so với robot tự hỏi Excel)"][1]


def test_rehearse_folds_picture_checks_into_the_result(tmp_path):
    import rehearse
    shot = tmp_path / "canh.png"
    frame_image(CELL).save(shot)
    res = checks.Result([checks.Check("Clicky nhận lệnh và bắt đầu dẫn", True)])
    rehearse.add_visual(res, events(point=(86.0, 210.0), card=[310.0, 200.0, 500.0, 260.0]),
                        [capture(str(shot))], tmp_path)
    failed = [c.name for c in res.checks if not c.ok]
    assert "Con trỏ chạm mép khung" in failed and not res.passed
    assert (tmp_path / "loi-canh-00.png").exists()


def test_no_capture_is_reported_but_does_not_fail_the_run(tmp_path):
    import rehearse
    res = checks.Result([checks.Check("x", True)])
    rehearse.add_visual(res, events(), [], tmp_path)
    assert res.passed and res.checks[-1].name == "Chấm bằng ảnh"


def test_robot_knows_the_products_no_bubble_marker():
    from ui.card_layout import NO_BUBBLE
    assert visual.NO_BUBBLE == NO_BUBBLE


def test_new_pointer_spot_under_the_frame_passes():
    from ui.card_layout import NO_BUBBLE, pointer_spot
    x, y = pointer_spot(BOX)
    ev = events(card=[310.0, 200.0, 500.0, 260.0])
    ev.append({"t": 2.4, "event": "point", "x": x, "y": y, "label": NO_BUBBLE})
    rows = visual.grade(ev, [capture()])
    assert all(r[1] for r in rows), [r for r in rows if not r[1]]


def test_watcher_can_be_stopped_and_joined(tmp_path):
    # Regression: an attribute named _stop shadowed Thread._stop, so join() raised TypeError on Python 3.12.
    w = visual.Watcher(tmp_path / "scene.jsonl", tmp_path)
    w.run = lambda: w._halt.wait(5)
    w.start()
    w.stop()
    w.join(timeout=5)
    assert not w.is_alive()


def test_sticky_notes_scene_is_not_asked_for_a_frame(tmp_path):
    """Run (a), 07/11: the notes scene lights no range on purpose, so 'no frame found' was a false note."""
    shot = tmp_path / "giay-nho.png"
    Image.new("RGB", (800, 600), (255, 255, 255)).save(shot)
    ev = events()
    ev[2]["graphic"] = {"kind": "notes", "notes": [{"codes": ["returns_in_revenue"]}]}
    del ev[3]                                   # no spotlight is drawn for a notes scene
    names = by_name(visual.grade(ev, [capture(str(shot))], lambda p: Image.open(p).convert("RGB")))
    assert "Thấy khung vẽ trên ảnh chụp" not in names
    assert "Khung vẽ thật nằm đúng ô trên màn hình" not in names
    plain = by_name(visual.grade(events(), [capture(str(shot))], lambda p: Image.open(p).convert("RGB")))
    assert not plain["Thấy khung vẽ trên ảnh chụp"][1]     # an ordinary scene with no frame is still noted


# ── Is Excel really what a person sees? (user's PC, 15/11: frames drawn over another app passed) ─────────

EXCEL = {"hwnd": 11, "pid": 100, "rect": [0.0, 0.0, 1920.0, 1040.0], "exe": "EXCEL.EXE"}
CLICKY = {"hwnd": 22, "pid": 200, "rect": [0.0, 0.0, 1920.0, 1080.0], "exe": "Clicky.exe"}
OTHER = {"hwnd": 33, "pid": 300, "rect": [0.0, 0.0, 1920.0, 1040.0], "exe": "claude.exe"}


def test_excel_shown_when_only_clicky_overlay_is_above():
    ok, detail = visual.excel_shown([CLICKY, EXCEL], CELL, 11, skip_pids={200})
    assert ok, detail


def test_other_app_on_top_fails_and_names_only_the_program():
    ok, detail = visual.excel_shown([CLICKY, OTHER, EXCEL], CELL, 11, skip_pids={200})
    assert not ok
    assert "claude.exe" in detail


def test_click_through_overlays_and_cloaked_windows_do_not_count_as_cover():
    ghost = dict(OTHER, click_through=True)
    hidden = dict(OTHER, hwnd=34, cloaked=True)
    assert visual.excel_shown([ghost, hidden, EXCEL], CELL, 11)[0]


def test_partly_covered_cell_fails():
    side = dict(OTHER, rect=[0.0, 0.0, CELL[0] + 20, 1040.0])      # covers the cell's left corners only
    assert not visual.excel_shown([side, EXCEL], CELL, 11)[0]


def test_minimised_excel_fails():
    assert not visual.excel_shown([dict(EXCEL, minimized=True)], CELL, 11)[0]


def test_frame_on_cell_over_another_app_is_not_a_pass():
    img = frame_image(CELL)
    cap = capture(shot="x.png")
    cap.shown, cap.shown_detail = False, "Excel bị cửa sổ claude.exe che ở ô này"
    rows = by_name(visual.grade(events(), [cap], lambda p: img))
    assert rows["Excel thật sự hiện ở ô đang chỉ (không bị cửa sổ khác che)"][1] is False
    assert "Khung vẽ thật nằm đúng ô trên màn hình" not in rows


def test_unread_window_state_is_a_failure_not_a_skip():
    cap = capture()
    cap.shown = None
    rows = by_name(visual.grade(events(), [cap]))
    assert rows["Excel thật sự hiện ở ô đang chỉ (không bị cửa sổ khác che)"][1] is False


def _cover_capture(shot_img, after_img=None):
    cap = capture(shot="ok.png")
    cap.cover = {"shot": "cover.png", "origin": (0, 0), "rect": [0.0, 0.0, 800.0, 600.0],
                 "after_shot": "after.png" if after_img else None}
    pics = {"ok.png": frame_image(CELL), "cover.png": shot_img, "after.png": after_img}
    return cap, (lambda p: pics[p])


CARD = [100.0, 240.0, 200.0, 260.0]          # logical → physical [150, 360, 300, 390]


def test_cover_passes_when_nothing_of_clicky_shows_on_the_cover():
    blank = Image.new("RGB", (800, 600), (255, 255, 255))
    cap, opener = _cover_capture(blank, after_img=frame_image(CELL))
    ev = events(card=CARD) + [{"t": 3, "event": "overlay_hidden", "reason": "focus"},
                              {"t": 4, "event": "overlay_shown", "reason": "focus"}]
    rows = by_name(visual.grade(ev, [cap], opener))
    assert rows["Che Excel bằng cửa sổ khác: Clicky ẩn thẻ, khung, con trỏ"][1], rows
    assert rows["Clicky ghi nhận bị che (overlay_hidden)"][1]
    assert rows["Mở lại Excel: Clicky hiện lại và chạy tiếp"][1]


def test_cover_fails_when_frame_or_card_stay_on_top():
    leaky = frame_image(CELL)
    ImageDraw.Draw(leaky).rectangle([150, 360, 300, 390], fill=(30, 30, 30))
    cap, opener = _cover_capture(leaky)
    rows = by_name(visual.grade(events(card=CARD), [cap], opener))
    row = rows["Che Excel bằng cửa sổ khác: Clicky ẩn thẻ, khung, con trỏ"]
    assert row[1] is False and row[3] is True
    assert "khung vẫn hiện" in row[2] and "thẻ vẫn hiện" in row[2]


def test_cover_that_robot_could_not_raise_fails_closed():
    cap, opener = _cover_capture(Image.new("RGB", (800, 600), (255, 255, 255)))
    cap.cover["error"] = "cửa sổ che không lên trước được (đang trước: EXCEL.EXE)"
    rows = by_name(visual.grade(events(), [cap], opener))
    assert rows["Che Excel bằng cửa sổ khác: Clicky ẩn thẻ, khung, con trỏ"][1] is False


def test_cover_script_is_valid_python():
    import ast
    ast.parse(visual.COVER_SCRIPT)
