from flexrouter.dashboard import render


def test_esc_escapes_angle_brackets_and_quotes():
    assert render.esc('<a href="x">&') == "&lt;a href=&quot;x&quot;&gt;&amp;"


def test_esc_handles_non_strings():
    assert render.esc(42) == "42"
    assert render.esc(None) == ""


def test_attrs_skips_none_and_false():
    out = render.attrs({"id": "a", "hidden": False, "title": None})
    assert out == ' id="a"'


def test_attrs_renders_true_as_bare_attribute():
    assert render.attrs({"hidden": True}) == " hidden"


def test_attrs_escapes_values():
    assert render.attrs({"title": 'he said "hi"'}) == ' title="he said &quot;hi&quot;"'


def test_tag_maps_cls_to_class():
    assert render.tag("p", "hi", cls="note") == '<p class="note">hi</p>'


def test_tag_does_not_escape_its_body():
    # The body is already-rendered HTML; callers escape their own text.
    assert render.tag("div", "<b>x</b>") == "<div><b>x</b></div>"


def test_areas_lists_all_nine_in_menu_order():
    slugs = [slug for slug, _, _ in render.AREAS]
    assert slugs == [
        "overview", "providers", "models", "buckets",
        "requests", "broken", "brain", "allowance", "settings",
    ]


def test_page_marks_the_current_area():
    html = render.page("Overview", "overview", "<p>hi</p>")
    assert 'aria-current="page"' in html
    assert html.count('aria-current="page"') == 1


def test_page_links_every_area():
    html = render.page("Overview", "overview", "")
    for slug, _, _ in render.AREAS:
        expected = 'href="/"' if slug == "overview" else f'href="/{slug}"'
        assert expected in html, slug


def test_page_escapes_its_title():
    html = render.page('<script>', "overview", "")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_page_is_a_complete_document():
    html = render.page("Overview", "overview", "<p>hi</p>")
    assert html.startswith("<!doctype html>")
    assert "</html>" in html
    assert "<p>hi</p>" in html
