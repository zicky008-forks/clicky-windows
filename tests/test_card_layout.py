"""Explanation cards sit beside the range they explain, on screen, and never on top of it."""
from ui.card_layout import NO_BUBBLE, place_card, reading_seconds, tone_of

SCREEN = (0, 0, 1920, 1040)


def _overlaps(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def test_card_goes_below_the_range_when_there_is_room():
    x, y = place_card((300, 200, 420, 222), (360, 140), SCREEN)
    assert (x, y) == (300, 238)


def test_card_moves_above_or_beside_near_the_screen_edges():
    x, y = place_card((300, 950, 420, 972), (360, 140), SCREEN)          # bottom row: above
    assert y + 140 <= 950
    x, y = place_card((1700, 100, 1900, 1000), (360, 140), SCREEN)       # tall range at the right edge: left of it
    assert x + 360 <= 1700
    for anchor in ((300, 200, 420, 222), (300, 950, 420, 972), (1700, 100, 1900, 1000), (10, 10, 1900, 1030)):
        x, y = place_card(anchor, (360, 140), SCREEN)
        assert SCREEN[0] <= x and x + 360 <= SCREEN[2] and SCREEN[1] <= y and y + 140 <= SCREEN[3]


def test_card_never_covers_the_range_when_any_side_fits():
    for anchor in ((300, 200, 420, 222), (300, 950, 420, 972), (1700, 100, 1900, 1000), (5, 400, 200, 430)):
        x, y = place_card(anchor, (360, 140), SCREEN)
        assert not _overlaps((x, y, x + 360, y + 140), anchor)


def test_second_monitor_coordinates():
    x, y = place_card((2300, 300, 2400, 320), (360, 140), (1920, 0, 3200, 1024))
    assert 1920 <= x and x + 360 <= 3200 and y == 336


def test_reading_time_and_tones():
    assert reading_seconds("Đã khớp.") == 2.5
    assert 5 < reading_seconds("Báo cáo trong Excel đang thiếu 5,42 tỷ so với sổ cái của tháng này nhé") < 7
    assert reading_seconds("chữ " * 200) == 9.0
    assert tone_of("bad") == "bad" and tone_of("") == "info" and tone_of("weird") == "info"
    assert NO_BUBBLE == "\u2063"


def test_figure_rolls_from_old_to_new_and_lands_exactly():
    from ui.card_layout import ROLL_S, roll_amount
    seen = [roll_amount(-5_420_000_000, 0, t / 10) for t in range(0, 15)]
    assert seen[0] == -5_420_000_000 and roll_amount(-5_420_000_000, 0, ROLL_S) == 0
    assert all(a <= b for a, b in zip(seen, seen[1:]))            # moves one way only, no overshoot
    assert seen[3] > -5_420_000_000 / 2                            # eases out: most of the move happens early


def test_stem_ties_the_card_to_its_range():
    from ui.card_layout import stem
    anchor = (300, 200, 420, 222)
    x, y = place_card(anchor, (360, 140), SCREEN)
    x1, y1, x2, y2 = stem(anchor, (x, y, x + 360, y + 140))
    assert y1 == y and y2 == 222 and 300 <= x2 <= 420              # from the card's top edge up to the cells
    assert stem(anchor, (300, 222, 660, 362)) == ()               # touching: nothing to draw
    x1, y1, x2, y2 = stem((1700, 100, 1900, 1000), (1324, 100, 1684, 240))
    assert (x1, x2) == (1684, 1700) and 100 <= y2 <= 1000          # card on the left: line to the range's left edge


def test_pointer_rests_beside_the_range_not_on_it():
    from ui.card_layout import pointer_spot
    x, y = pointer_spot((300, 200, 420, 222))
    assert x < 300 and 200 <= y <= 222
    x, _ = pointer_spot((0, 200, 420, 222))
    assert x > 420
