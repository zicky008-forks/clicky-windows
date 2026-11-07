from screen.capture import ScreenShot, physical_to_logical


def _monitor(index, left, width, scale, logical_left):
    return ScreenShot(index=index, width=0, height=0, base64_jpeg="", physical_width=width, physical_height=1080,
                      physical_left=left, physical_top=0, dpi_scale=scale, logical_left=logical_left, logical_top=0)


def test_box_maps_through_the_monitor_that_holds_it():
    shots = [_monitor(1, 0, 1920, 1.0, 0), _monitor(2, 1920, 2880, 1.5, 1920)]
    assert physical_to_logical((100, 50, 200, 70), shots) == (100, 50, 200, 70)
    assert physical_to_logical((1920 + 300, 150, 1920 + 600, 180), shots) == (2120, 100, 2320, 120)


def test_no_monitors_means_nothing_to_draw():
    assert physical_to_logical((0, 0, 1, 1), []) is None
