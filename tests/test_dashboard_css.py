"""Guards on the stylesheet: nothing emitted unstyled, nothing undefined.

The wireframe shipped seventeen class names with no rule behind them and a
`var(--line)` that was never defined; both failures were silent. These tests
make them loud.
"""
import re

from flexrouter.dashboard.assets import STATIC_DIR

CSS = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
SRC = STATIC_DIR.parent

# Classes built at runtime from a value (cls=f"seg seg-{state}"), which the
# static scan below cannot see. Listed so their rules are still required.
DYNAMIC = {
    "seg-ok", "seg-part", "seg-bad", "seg-none",
    "state-ok", "state-warn", "state-bad",
    "cap-published", "cap-observed", "cap-guessed", "cap-manual", "cap-unknown",
    "status-ok", "status-warn", "status-bad", "status-idle", "status-none",
    "key-status",
}


def _emitted_classes() -> set[str]:
    found = set(DYNAMIC)
    for py in SRC.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for group in re.findall(r'cls=f?"([^"{}]+)"', text):
            found.update(group.split())
        for group in re.findall(r'class="([^"{}]+)"', text):
            found.update(group.split())
    return found


def test_every_emitted_class_has_a_rule():
    missing = sorted(c for c in _emitted_classes()
                     if not re.search(rf"\.{re.escape(c)}(?![\w-])", CSS))
    assert missing == [], f"classes with no CSS rule: {missing}"


def test_every_custom_property_used_is_defined():
    used = set(re.findall(r"var\((--[\w-]+)", CSS))
    defined = set(re.findall(r"(--[\w-]+)\s*:", CSS))
    assert sorted(used - defined) == []


def test_light_theme_redefines_every_colour_token():
    """Dark by default; light follows the OS or html[data-theme="light"].
    Both light blocks must redefine every colour the dark :root sets, or a
    light page ends up with dark-only ink on a light ground."""
    root = re.search(r":root\s*\{(.*?)\n\}", CSS, re.S).group(1)
    colours = {name for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", root)
               if value.strip().startswith(("#", "rgba(")) and not name.startswith("--s")}
    assert colours
    light_os = re.search(r'@media \(prefers-color-scheme: light\)\s*\{\s*:root:not\(\[data-theme="dark"\]\)\s*\{(.*?)\}', CSS, re.S).group(1)
    light_pinned = re.search(r':root\[data-theme="light"\]\s*\{(.*?)\}', CSS, re.S).group(1)
    for block in (light_os, light_pinned):
        assert sorted(c for c in colours if c + ":" not in block.replace(" :", ":")) == []


def test_brand_tokens_match_the_spec():
    for token, value in {"--green": "#34d399", "--blue": "#7dd3fc",
                         "--violet": "#a78bfa", "--ground": "#000000"}.items():
        assert re.search(rf"{token}\s*:\s*{value}", CSS), token


def test_motion_can_be_switched_off():
    assert 'html[data-motion="off"]' in CSS
    assert "prefers-reduced-motion" in CSS


def test_corners_are_square():
    radii = re.findall(r"border-radius\s*:\s*([^;]+);", CSS)
    too_round = [r for r in radii if r.strip() not in {"0", "2px", "50%"}]
    assert too_round == []
