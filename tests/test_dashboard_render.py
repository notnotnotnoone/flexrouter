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


def test_text_escapes_and_joins_its_parts():
    assert render.text("<b>", "x", "</b>") == "&lt;b&gt;x&lt;/b&gt;"


def test_text_escapes_every_part_not_only_the_first():
    # A prior bug class this guards: escaping only parts[0] would let markup
    # in a later argument through untouched.
    assert render.text("safe", "<script>bad</script>") == \
        "safe&lt;script&gt;bad&lt;/script&gt;"


def test_areas_lists_all_nine_in_menu_order():
    slugs = [slug for slug, _, _, _ in render.AREAS]
    assert slugs == [
        "overview", "providers", "models", "buckets",
        "requests", "playground", "broken", "brain", "allowance", "settings",
    ]


def test_page_marks_the_current_area():
    html = render.page("Overview", "overview", "<p>hi</p>")
    assert 'aria-current="page"' in html
    assert html.count('aria-current="page"') == 1


def test_page_links_every_area():
    html = render.page("Overview", "overview", "")
    for slug, _, _, _ in render.AREAS:
        expected = 'href="/"' if slug == "overview" else f'href="/{slug}"'
        assert expected in html, slug


def test_page_escapes_its_title():
    html = render.page('<script>', "overview", "")
    # The page loads real scripts, so look at the title itself.
    assert "<title><script>" not in html
    assert "<title>&lt;script&gt;" in html


def test_page_is_a_complete_document():
    html = render.page("Overview", "overview", "<p>hi</p>")
    assert html.startswith("<!doctype html>")
    assert "</html>" in html
    assert "<p>hi</p>" in html


def test_page_links_the_new_assets_not_the_wireframe():
    html = render.page("Overview", "overview", "<p>hi</p>")
    assert "/wire.css" not in html and "/wire.js" not in html
    assert "/static/app.css?v=" in html
    assert "/static/app.js?v=" in html
    assert "/static/vendor/htmx.min.js?v=" in html


def test_page_carries_the_motion_preference(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    (tmp_path / "dashboard.json").write_text('{"motion": "off"}')
    assert 'data-motion="off"' in render.page("Overview", "overview", "")


def test_page_shows_the_wordmark_and_a_badge():
    html = render.page("Overview", "overview", "", badges={"broken": 2})
    assert 'class="wm-router"' in html
    assert 'class="nav-badge"' in html and ">2<" in html


def test_every_menu_entry_has_an_icon():
    html = render.page("Overview", "overview", "")
    for _, _, _, icon in render.AREAS:
        assert f'href="#i-{icon}"' in html
