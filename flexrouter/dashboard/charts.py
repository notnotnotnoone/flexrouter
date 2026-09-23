"""Charts, drawn as inline SVG by Python.

No charting library and no JavaScript: a chart here is a string of SVG that
goes into the page like any other markup, so it is server-rendered with
everything else, testable by the same `TestClient` call, and cannot get out
of step with the numbers beside it - see the Stage 8 roadmap, rulings R2/R3.

Every function in this module takes numbers and returns SVG. None of them
take text from outside, so none of them escape anything: labels come from
the clock (`"14:00"`) and colours from the fixed table below. Callers that
want to name a series do it in HTML around the chart, where `render.esc`
applies.

Colour is assigned by position and never cycled. Six slots exist; a seventh
series would silently repeat a colour, so callers fold the tail into one
"other" band rather than asking for more.
"""
from __future__ import annotations

SERIES_COLORS = [
    "var(--s1)", "var(--s2)", "var(--s3)",
    "var(--s4)", "var(--s5)", "var(--s6)",
]
IDLE_COLOR = "var(--s0)"

MAX_SERIES = len(SERIES_COLORS)


def color_for(index: int) -> str:
    """The colour of the series at `index`, or the idle grey past the end."""
    if 0 <= index < MAX_SERIES:
        return SERIES_COLORS[index]
    return IDLE_COLOR


def nice_max(value: int) -> int:
    """A round number at or just above `value`, for the top of an axis.

    Axis labels people can read are 1, 2 or 5 followed by zeros. Anything
    else - a top gridline at 137 - makes the reader do arithmetic to work
    out what a band is worth.
    """
    if value <= 0:
        return 1
    step = 1
    while step * 10 <= value:
        step *= 10
    for mult in (1, 2, 5, 10):
        if step * mult >= value:
            return step * mult
    return step * 10


def _n(value: float) -> str:
    """A coordinate, short enough not to bloat the page."""
    return f"{value:.1f}".rstrip("0").rstrip(".")


def stacked_hours(series: list[dict], labels: list[str],
                  fails: list[int]) -> str:
    """The Overview's main chart: requests per hour, stacked by provider,
    with a lane underneath counting the ones that failed outright.

    `series` is `[{"values": [...]}, ...]`, already in the order the caller
    wants stacked and coloured - biggest at the bottom. Every list must be
    the same length as `labels`.

    The failure lane is a separate scale on purpose. Failures are usually a
    rounding error next to traffic, and stacking them into the same axis
    would hide the thing the reader most needs to see.
    """
    n = len(labels)
    if n < 2:
        return ""

    W, X0, X1 = 880, 46, 868
    Y0, Y1 = 16, 196          # stacked area
    F0, F1 = 216, 244         # failure lane
    LABEL_Y = 264
    H = 286

    step = (X1 - X0) / (n - 1)
    x = [X0 + i * step for i in range(n)]

    totals = [sum(s["values"][i] for s in series) for i in range(n)]
    top = nice_max(max(totals) if totals else 0)

    def y(v: float) -> float:
        return Y1 - (v / top) * (Y1 - Y0)

    out = [f'<svg class="chart" viewBox="0 0 {W} {H}" '
           f'preserveAspectRatio="xMidYMid meet" role="img">']

    # Gridlines, and a label on every one of them.
    for k in range(5):
        v = top * k / 4
        gy = y(v)
        cls = "chart-axis" if k == 0 else "chart-grid"
        out.append(f'<line class="{cls}" x1="{X0}" x2="{X1}" '
                   f'y1="{_n(gy)}" y2="{_n(gy)}"/>')
        out.append(f'<text class="chart-tick" x="{X0 - 7}" y="{_n(gy + 3.3)}" '
                   f'text-anchor="end">{int(round(v))}</text>')

    # Bands, bottom up. Each one is redrawn over the last, then its own top
    # edge is stroked in the panel colour so neighbouring fills never touch -
    # two saturated colours meeting at a hairline read as a third colour.
    cum = [0.0] * n
    for idx, s in enumerate(series):
        vals = s["values"]
        lower = list(cum)
        upper = [lower[i] + vals[i] for i in range(n)]
        pts = [f"{_n(x[i])} {_n(y(upper[i]))}" for i in range(n)]
        back = [f"{_n(x[i])} {_n(y(lower[i]))}" for i in range(n - 1, -1, -1)]
        out.append(
            f'<path d="M {" L ".join(pts)} L {" L ".join(back)} Z" '
            f'fill="{color_for(idx)}" fill-opacity="0.94"/>'
        )
        out.append(f'<path class="chart-sep" d="M {" L ".join(pts)}"/>')
        cum = upper

    # Failure lane.
    out.append(f'<text class="chart-tick" x="{X0 - 7}" y="{F0 + 9}" '
               f'text-anchor="end">fail</text>')
    out.append(f'<line class="chart-axis" x1="{X0}" x2="{X1}" '
               f'y1="{F1}" y2="{F1}"/>')
    fail_top = max(fails) if fails else 0
    bar_w = min(16.0, step * 0.72)
    for i, f in enumerate(fails):
        if f <= 0:
            h = 1.5
            fill = "var(--wash)"
        else:
            h = max(3.0, (f / fail_top) * (F1 - F0))
            fill = "var(--bad)"
        out.append(
            f'<rect x="{_n(x[i] - bar_w / 2)}" y="{_n(F1 - h)}" '
            f'width="{_n(bar_w)}" height="{_n(h)}" '
            f'rx="{2 if h > 4 else 0.75}" fill="{fill}"/>'
        )

    # Hour labels, thinned so they never collide at any window width.
    every = max(1, n // 8)
    for i, label in enumerate(labels):
        if i % every:
            continue
        out.append(f'<text class="chart-tick" x="{_n(x[i])}" y="{LABEL_Y}" '
                   f'text-anchor="middle">{label}</text>')

    out.append("</svg>")
    return "".join(out)


def sparkline(values: list[int], color: str,
              width: int = 104, height: int = 22) -> str:
    """One provider's traffic, small enough to sit in a table cell.

    Drawn against its own maximum, not the busiest provider's: the shape of
    a quiet provider's day is the point, and scaling it against a busy one
    would flatten every row but the top one into a straight line. The column
    header says so, and the request count sits in the next column for the
    magnitude the shape deliberately drops.
    """
    n = len(values)
    if n < 2:
        return ""
    peak = max(values)
    if peak <= 0:
        return (f'<svg class="spark" viewBox="0 0 {width} {height}" '
                f'preserveAspectRatio="none" aria-hidden="true">'
                f'<line x1="0" x2="{width}" y1="{height - 1}" y2="{height - 1}" '
                f'stroke="var(--rule-2)" stroke-width="1"/></svg>')

    step = width / (n - 1)
    floor_y = height - 1.0
    span = height - 3.0
    pts = [f"{_n(i * step)} {_n(floor_y - (v / peak) * span)}"
           for i, v in enumerate(values)]
    line = " L ".join(pts)
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="none" aria-hidden="true">'
        f'<path d="M {line} L {width} {height} L 0 {height} Z" '
        f'fill="{color}" fill-opacity="0.16"/>'
        f'<path d="M {line}" fill="none" stroke="{color}" '
        f'stroke-width="1.5" vector-effect="non-scaling-stroke"/>'
        f"</svg>"
    )
