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
