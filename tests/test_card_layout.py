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


def test_pointer_rests_just_under_the_frame_not_on_figures():
    from ui.card_layout import pointer_spot
    x, y = pointer_spot((300, 200, 420, 222))
    assert 300 <= x <= 320 and 222 < y <= 230          # tip touches the bottom edge, near the left end
    _, y = pointer_spot((300, 1020, 420, 1040), SCREEN)
    assert y < 1020                                    # bottom of the screen: rests just above instead


def test_card_keeps_off_the_figures_when_it_can():
    filled = (0, 100, 1200, 700)                       # the sheet's values on screen
    anchor = (200, 300, 320, 322)                      # a range inside them
    x, y = place_card(anchor, (360, 140), SCREEN, filled)
    assert _overlaps((x, y, x + 360, y + 140), filled) is False and x >= 1200
    x, y = place_card((0, 560, 1900, 600), (360, 140), SCREEN, (0, 100, 1900, 600))   # report as wide as the screen
    assert y >= 600                                     # goes under the report, not over it
    full = (0, 0, 1920, 1040)                           # nowhere free: still never on the range itself
    x, y = place_card(anchor, (360, 140), SCREEN, full)
    assert not _overlaps((x, y, x + 360, y + 140), anchor)


def test_no_long_stem_across_the_sheet():
    from ui.card_layout import stem
    assert stem((200, 300, 320, 322), (1216, 300, 1576, 440)) == ()


def test_on_a_full_sheet_the_card_uses_a_gap_between_blocks_of_figures():
    # Two blocks of figures fill the screen side by side with an empty band of columns between them:
    # one box for the whole used range left nowhere to go, so the card used to land on figures.
    from ui.card_layout import overlap
    figures = [(0, 100, 700, 1000), (1100, 100, 1920, 1000)]
    anchor = (400, 500, 600, 522)
    x, y = place_card(anchor, (360, 140), SCREEN, figures)
    card = (x, y, x + 360, y + 140)
    assert overlap(card, figures) == 0 and not _overlaps(card, anchor)
    assert 700 <= x and x + 360 <= 1100                       # in the gap, next to the range


def test_figures_come_before_words_when_only_one_can_be_kept_clear():
    from ui.card_layout import overlap
    words = [(0, 0, 1920, 90), (0, 90, 300, 1040)]            # titles on top, labels down column A
    figures = [(300, 90, 1920, 1040)]                         # every other cell holds a figure
    anchor = (300, 400, 500, 422)
    x, y = place_card(anchor, (240, 120), SCREEN, figures, words)
    card = (x, y, x + 240, y + 120)
    assert overlap(card, figures) == 0                        # over the labels, never over a number
    assert overlap(card, words) > 0


def test_with_nowhere_free_the_card_covers_the_least_and_never_the_range():
    from ui.card_layout import overlap
    figures = [(0, 0, 1920, 600), (0, 600, 1000, 1040), (1000, 600, 1920, 900)]
    anchor = (500, 300, 600, 322)
    x, y = place_card(anchor, (360, 140), SCREEN, figures)
    card = (x, y, x + 360, y + 140)
    assert not _overlaps(card, anchor)
    assert overlap(card, figures) <= 360 * 140 * 0.5          # mostly over the strip left free


def test_button_card_moves_off_figures_and_off_the_explanation_card():
    from ui.card_layout import BOARD, free_spot, overlap
    BOARD.clear()
    corner = (1452, 838)                                      # its usual bottom-right place, 440 × 130
    assert free_spot(corner, (440, 130), SCREEN) == corner    # nothing there: it stays where it always is
    BOARD.show_sheet([(1200, 700, 1920, 1000)])
    BOARD.take("card1", (900, 300, 1260, 440))
    x, y = free_spot(corner, (440, 130), SCREEN, BOARD.figures + BOARD.others("choice"), BOARD.words)
    rect = (x, y, x + 440, y + 130)
    assert overlap(rect, BOARD.figures) == 0 and overlap(rect, BOARD.others("choice")) == 0
    BOARD.take("choice", rect)
    assert BOARD.others("card1") == [rect] and BOARD.others("choice") == [(900, 300, 1260, 440)]
    BOARD.release("card")
    assert BOARD.others("choice") == []
    BOARD.clear()
