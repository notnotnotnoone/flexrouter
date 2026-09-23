import pytest

from flexrouter.dashboard import ui


def test_box_escapes_its_title_and_keeps_its_body():
    html = ui.box("<x>", "<p>ok</p>", sub="s&t")
    assert "&lt;x&gt;" in html
    assert "<p>ok</p>" in html
    assert "s&amp;t" in html
    assert html.startswith('<section class="box')


def test_box_passes_data_attributes_through():
    html = ui.box("t", "", **{"data-box": "chart", "data-enter": ""})
    assert 'data-box="chart"' in html
    assert 'data-enter=""' in html


def test_stat_carries_a_key_and_the_raw_value_for_change_detection():
    html = ui.stat("Requests", "1,284", key="requests", note="vs prev")
    assert 'data-stat="requests"' in html
    assert 'data-value="1,284"' in html
    assert "vs prev" in html


def test_stat_escapes_its_value():
    assert "&lt;b&gt;" in ui.stat("x", "<b>", key="k")


@pytest.mark.parametrize("fraction,on,level", [
    (0.0, 0, "ok"), (0.5, 10, "ok"), (0.8, 16, "warn"),
    (0.99, 20, "warn"), (1.0, 20, "full"), (1.7, 20, "full"), (-1, 0, "ok"),
])
def test_meter_fills_cells_and_picks_a_level(fraction, on, level):
    html = ui.meter(fraction)
    assert html.count('<i class="on"></i>') == on
    assert html.count("<i") == 20
    assert f'data-level="{level}"' in html


def test_a_small_but_nonzero_fraction_still_lights_one_cell():
    # 1% of a cap used is not "nothing used"; a meter showing zero would lie.
    assert ui.meter(0.01).count('<i class="on"></i>') == 1


def test_meter_with_no_value_claims_nothing():
    html = ui.meter(None)
    assert 'data-level="none"' in html
    assert "aria-valuenow" not in html
    assert '<i class="on">' not in html


@pytest.mark.parametrize("state,text", [
    ("ok", "OK"), ("warn", "ATTENTION"), ("bad", "BROKEN"), ("idle", "IDLE"),
    ("mystery", "IDLE"),
])
def test_status_never_relies_on_colour_alone(state, text):
    assert text in ui.status(state)


def test_icon_refers_to_the_sprite_and_rejects_unknown_names():
    assert '<use href="#i-gauge"/>' in ui.icon("gauge")
    with pytest.raises(KeyError):
        ui.icon("nope")


def test_sprite_defines_every_icon_once():
    sprite = ui.sprite()
    for name in ui.ICONS:
        assert sprite.count(f'id="i-{name}"') == 1


def test_button_is_a_link_when_it_navigates():
    assert ui.button("Go", href="/x").startswith('<a class="btn')
    assert ui.button("Do").startswith('<button type="button" class="btn')


def test_button_escapes_label_and_href():
    html = ui.button("<x>", href='/a"b')
    assert "&lt;x&gt;" in html and "&quot;" in html


def test_empty_state_escapes_its_message():
    assert "&lt;" in ui.empty("<nothing>")
