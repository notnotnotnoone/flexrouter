# tests/test_dashboard_charts.py
"""The Overview's charts are SVG built by Python, so they are testable the
same way every other rendered string in the dashboard is."""
import re

from flexrouter.dashboard import charts


def test_a_round_number_tops_the_axis():
    # A gridline at 137 makes the reader do arithmetic to value a band.
    assert charts.nice_max(137) == 200
    assert charts.nice_max(46) == 50
    assert charts.nice_max(7) == 10
    assert charts.nice_max(100) == 100


def test_an_empty_axis_still_has_a_top():
    # Dividing by the maximum is how every y coordinate is found, so a
    # window with no traffic must not hand the chart a zero.
    assert charts.nice_max(0) == 1
    assert charts.nice_max(-5) == 1


def test_colours_are_positional_and_never_cycle():
    assert charts.color_for(0) != charts.color_for(1)
    # Past the last slot it goes grey rather than repeating slot one, which
    # would silently claim two providers are the same one.
    assert charts.color_for(charts.MAX_SERIES) == charts.IDLE_COLOR
    assert charts.color_for(999) == charts.IDLE_COLOR


def _chart():
    return charts.stacked_hours(
        [{"values": [1, 4, 2]}, {"values": [0, 2, 6]}],
        ["01:00", "02:00", "03:00"],
        [0, 1, 0],
    )


def test_the_chart_is_one_self_contained_svg():
    svg = _chart()
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert svg.count("<svg") == 1
    # Nothing external: no script, no image, no stylesheet link.
    assert "<script" not in svg and "http" not in svg


def test_every_series_is_drawn_and_separated():
    svg = _chart()
    assert svg.count("var(--s1)") == 1
    assert svg.count("var(--s2)") == 1
    # The hairline between two fills, so neighbouring colours never touch.
    assert 'class="chart-sep"' in svg


def test_every_gridline_carries_its_own_label():
    svg = _chart()
    lines = svg.count('class="chart-grid"') + svg.count('class="chart-axis"')
    ticks = len(re.findall(r'class="chart-tick"', svg))
    # 5 gridlines each labelled, plus the failure lane's axis and its label,
    # plus the hour labels along the bottom.
    assert lines >= 5 and ticks >= 5


def test_a_chart_needs_at_least_two_points():
    assert charts.stacked_hours([{"values": [1]}], ["01:00"], [0]) == ""
    assert charts.stacked_hours([], [], []) == ""


def test_marks_stay_inside_the_drawing():
    svg = _chart()
    for value in re.findall(r'y="(-?[\d.]+)"', svg):
        assert -1 <= float(value) <= 286, value
    for value in re.findall(r'x="(-?[\d.]+)"', svg):
        assert -1 <= float(value) <= 880, value


def test_a_sparkline_scales_to_its_own_peak():
    # A quiet provider's shape is the point; scaling it against a busy one
    # would flatten every row but the busiest into a flat line.
    quiet = charts.sparkline([0, 1, 0, 2], "var(--s1)")
    busy = charts.sparkline([0, 100, 0, 200], "var(--s1)")
    assert quiet.count(" L ") == busy.count(" L ")
    # Same shape, same geometry, different magnitudes.
    assert re.sub(r'[\d.]+', "", quiet) == re.sub(r'[\d.]+', "", busy)


def test_a_sparkline_with_no_traffic_is_a_flat_rule():
    svg = charts.sparkline([0, 0, 0], "var(--s1)")
    assert "<line" in svg and "<path" not in svg


def test_a_sparkline_needs_at_least_two_points():
    assert charts.sparkline([5], "var(--s1)") == ""
