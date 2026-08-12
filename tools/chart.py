"""Static SVG stacked-area token-history chart.

Follows the project's dataviz skill: documented categorical palette slots
1 (blue, system message) and 2 (orange, built-in tools), 2px boundary
lines, a 2px surface-color gap between stacked layers, solid hairline
gridlines (never dashed), a legend for the two series, and dashed *event*
reference lines (major-version boundaries) which are a distinct role from
axis gridlines.
"""
from __future__ import annotations

from datetime import datetime, timezone

WIDTH = 1900
HEIGHT = 650
MARGIN_LEFT = 100
MARGIN_RIGHT = 40
MARGIN_TOP = 70
MARGIN_BOTTOM = 70

PLOT_LEFT = MARGIN_LEFT
PLOT_RIGHT = WIDTH - MARGIN_RIGHT
PLOT_TOP = MARGIN_TOP
PLOT_BOTTOM = HEIGHT - MARGIN_BOTTOM

PALETTE = {
    "light": {
        "surface": None,  # transparent — blends with the GitHub light page
        "text_primary": "#0b0b0b",
        "text_secondary": "#52514e",
        "text_muted": "#898781",
        "gridline": "#e1e0d9",
        "baseline": "#c3c2b7",
        "series_system": "#2a78d6",  # categorical slot 1 (blue)
        "series_tools": "#eb6834",  # categorical slot 2 (orange)
        "boundary_line": "#898781",
        "callout_marker": "#0b0b0b",
    },
    "dark": {
        "surface": "#1a1a19",
        "text_primary": "#ffffff",
        "text_secondary": "#c3c2b7",
        "text_muted": "#898781",
        "gridline": "#2c2c2a",
        "baseline": "#383835",
        "series_system": "#3987e5",  # categorical slot 1, dark step
        "series_tools": "#d95926",  # categorical slot 2, dark step
        "boundary_line": "#898781",
        "callout_marker": "#ffffff",
    },
}

FONT_STACK = "system-ui, -apple-system, 'Segoe UI', sans-serif"

AREA_OPACITY = 0.13
GAP_PX = 2  # surface-color gap between the two stacked layers


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _nice_y_max(peak: float) -> int:
    step = 10000
    if peak <= 0:
        return step
    return int((peak // step) + 1) * step


def _version_major(version: str):
    import re

    m = re.match(r"^(\d+)", version.strip())
    if m:
        return int(m.group(1))
    return version


def render_chart_svg(
    harness_label: str,
    points,  # list of dicts: {date: datetime, version: str, system: int, tools: int}
    callouts,  # list of dicts: {date: datetime, value: int, label: str, detail: str}
    mode: str = "light",
) -> str:
    assert mode in ("light", "dark")
    c = PALETTE[mode]

    if len(points) == 0:
        return _empty_svg(harness_label, mode, c)

    points = sorted(points, key=lambda p: p["date"])

    t0 = points[0]["date"].timestamp()
    t1 = points[-1]["date"].timestamp()
    span = t1 - t0

    def xscale(dt: datetime) -> float:
        if span <= 0:
            return (PLOT_LEFT + PLOT_RIGHT) / 2
        frac = (dt.timestamp() - t0) / span
        return PLOT_LEFT + frac * (PLOT_RIGHT - PLOT_LEFT)

    peak = max(p["system"] + p["tools"] for p in points)
    y_max = _nice_y_max(peak)

    def yscale(v: float) -> float:
        frac = v / y_max if y_max else 0
        return PLOT_BOTTOM - frac * (PLOT_BOTTOM - PLOT_TOP)

    parts = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" role="img" '
        f'aria-labelledby="chartTitle chartDesc" font-family="{FONT_STACK}">'
    )
    parts.append(f"<title id=\"chartTitle\">{_esc(harness_label)} token history</title>")
    parts.append(
        "<desc id=\"chartDesc\">Stacked area chart of system message tokens and "
        "aggregate built-in tool definition tokens across captured "
        f"{_esc(harness_label)} versions, by capture date.</desc>"
    )

    if c["surface"]:
        parts.append(f'<rect x="0" y="0" width="{WIDTH}" height="{HEIGHT}" fill="{c["surface"]}"/>')

    # ---- gridlines + y ticks -------------------------------------------------
    gridline_step = 10000
    y = 0
    while y <= y_max:
        py = yscale(y)
        parts.append(
            f'<line x1="{PLOT_LEFT}" y1="{py:.1f}" x2="{PLOT_RIGHT}" y2="{py:.1f}" '
            f'stroke="{c["gridline"]}" stroke-width="1"/>'
        )
        label = "0" if y == 0 else f"{y // 1000}k"
        parts.append(
            f'<text x="{PLOT_LEFT - 12}" y="{py + 4:.1f}" font-size="15" '
            f'fill="{c["text_muted"]}" text-anchor="end">{label}</text>'
        )
        y += gridline_step

    # y-axis "TOKENS" label, rotated
    parts.append(
        f'<text x="24" y="{(PLOT_TOP + PLOT_BOTTOM) / 2:.1f}" font-size="14" '
        f'fill="{c["text_muted"]}" text-anchor="middle" letter-spacing="1.5" '
        f'transform="rotate(-90 24 {(PLOT_TOP + PLOT_BOTTOM) / 2:.1f})">TOKENS</text>'
    )

    # baseline + axis
    parts.append(
        f'<line x1="{PLOT_LEFT}" y1="{PLOT_BOTTOM}" x2="{PLOT_RIGHT}" y2="{PLOT_BOTTOM}" '
        f'stroke="{c["baseline"]}" stroke-width="1"/>'
    )

    # ---- x ticks (dates) ------------------------------------------------------
    n_ticks = min(7, len(points))
    if n_ticks < 2:
        tick_fracs = [0.0]
    else:
        tick_fracs = [i / (n_ticks - 1) for i in range(n_ticks)]
    for frac in tick_fracs:
        ts = t0 + frac * span
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        px = PLOT_LEFT + frac * (PLOT_RIGHT - PLOT_LEFT)
        parts.append(
            f'<text x="{px:.1f}" y="{PLOT_BOTTOM + 26}" font-size="14" '
            f'fill="{c["text_muted"]}" text-anchor="middle">{dt.strftime("%b %Y")}</text>'
        )

    # ---- major-version boundary lines -----------------------------------------
    # Skip the very first plotted point: it's where the series starts, not a
    # transition into a new major, so it isn't a meaningful boundary to mark.
    last_major = _version_major(points[0]["version"])
    for p in points[1:]:
        major = _version_major(p["version"])
        if major != last_major:
            px = xscale(p["date"])
            parts.append(
                f'<line x1="{px:.1f}" y1="{PLOT_TOP}" x2="{px:.1f}" y2="{PLOT_BOTTOM}" '
                f'stroke="{c["boundary_line"]}" stroke-width="1" stroke-dasharray="5,4" opacity="0.55"/>'
            )
            parts.append(
                f'<text x="{px + 6:.1f}" y="{PLOT_TOP - 8}" font-size="14" '
                f'fill="{c["text_secondary"]}">v{_esc(major)}.0</text>'
            )
            last_major = major

    # ---- stacked areas ----------------------------------------------------------
    xs = [xscale(p["date"]) for p in points]
    y_sys = [yscale(p["system"]) for p in points]
    y_comb = [yscale(p["system"] + p["tools"]) for p in points]
    baseline_y = yscale(0)

    half_gap = GAP_PX / 2.0

    # system (bottom) area: shrink its top edge down by half the gap. No
    # boundary stroke here — the seam between the two stacked layers is
    # separated by the surface-color gap itself, never a border (dataviz
    # skill: "never draw a border around a mark to separate it").
    sys_top = [v + half_gap for v in y_sys]
    d_sys = _area_path(xs, sys_top, baseline_y)
    parts.append(f'<path d="{d_sys}" fill="{c["series_system"]}" fill-opacity="{AREA_OPACITY}" stroke="none"/>')

    # tools (top) area: shrink its bottom edge up by half the gap, leaving a
    # true surface-color gap between the two fills.
    tools_bottom = [v - half_gap for v in y_sys]
    d_tools = _band_path(xs, y_comb, tools_bottom)
    parts.append(f'<path d="{d_tools}" fill="{c["series_tools"]}" fill-opacity="{AREA_OPACITY}" stroke="none"/>')

    # Top edge of the whole stack traces combined tokens — this is not a
    # separator between two marks, it's the cumulative curve, so a stroke
    # here is the normal area-chart treatment.
    parts.append(
        f'<path d="{_line_path(xs, y_comb)}" fill="none" stroke="{c["series_tools"]}" '
        f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
    )

    # ---- callouts -----------------------------------------------------------------
    for co in callouts:
        px = xscale(co["date"])
        py = yscale(co["value"])
        ring = c["surface"] or "#fcfcfb"
        parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="6" fill="{ring}"/>')
        parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4.5" fill="{c["callout_marker"]}"/>')
        label_y = max(py - 34, PLOT_TOP + 16)
        anchor = "start"
        text_x = px + 10
        if text_x > PLOT_RIGHT - 260:
            anchor = "end"
            text_x = px - 10
        parts.append(
            f'<line x1="{px:.1f}" y1="{py - 6:.1f}" x2="{px:.1f}" y2="{label_y + 6:.1f}" '
            f'stroke="{c["text_muted"]}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{text_x:.1f}" y="{label_y:.1f}" font-size="15" font-weight="600" '
            f'fill="{c["text_primary"]}" text-anchor="{anchor}">{_esc(co["label"])}</text>'
        )
        parts.append(
            f'<text x="{text_x:.1f}" y="{label_y + 18:.1f}" font-size="13" '
            f'fill="{c["text_secondary"]}" text-anchor="{anchor}">{_esc(co["detail"])}</text>'
        )

    # ---- legend (top-left) ---------------------------------------------------------
    legend_y = 28
    parts.append(
        f'<line x1="{PLOT_LEFT}" y1="{legend_y}" x2="{PLOT_LEFT + 24}" y2="{legend_y}" '
        f'stroke="{c["series_system"]}" stroke-width="3" stroke-linecap="round"/>'
    )
    parts.append(
        f'<text x="{PLOT_LEFT + 32}" y="{legend_y + 5}" font-size="15" fill="{c["text_secondary"]}">System message</text>'
    )
    legend2_x = PLOT_LEFT + 220
    parts.append(
        f'<line x1="{legend2_x}" y1="{legend_y}" x2="{legend2_x + 24}" y2="{legend_y}" '
        f'stroke="{c["series_tools"]}" stroke-width="3" stroke-linecap="round"/>'
    )
    parts.append(
        f'<text x="{legend2_x + 32}" y="{legend_y + 5}" font-size="15" '
        f'fill="{c["text_secondary"]}">Built-in tools (aggregate)</text>'
    )

    parts.append("</svg>")
    return "\n".join(parts)


def _area_path(xs, ys_top, baseline_y) -> str:
    d = [f"M {xs[0]:.1f} {baseline_y:.1f}"]
    for x, y in zip(xs, ys_top):
        d.append(f"L {x:.1f} {y:.1f}")
    d.append(f"L {xs[-1]:.1f} {baseline_y:.1f}")
    d.append("Z")
    return " ".join(d)


def _band_path(xs, ys_top, ys_bottom) -> str:
    d = [f"M {xs[0]:.1f} {ys_bottom[0]:.1f}"]
    for x, y in zip(xs, ys_top):
        d.append(f"L {x:.1f} {y:.1f}")
    for x, y in reversed(list(zip(xs, ys_bottom))):
        d.append(f"L {x:.1f} {y:.1f}")
    d.append("Z")
    return " ".join(d)


def _line_path(xs, ys) -> str:
    d = [f"M {xs[0]:.1f} {ys[0]:.1f}"]
    for x, y in zip(xs[1:], ys[1:]):
        d.append(f"L {x:.1f} {y:.1f}")
    return " ".join(d)


def _empty_svg(harness_label: str, mode: str, c) -> str:
    bg = f'<rect x="0" y="0" width="{WIDTH}" height="{HEIGHT}" fill="{c["surface"]}"/>' if c["surface"] else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="{_esc(harness_label)} token history, no data yet" '
        f'font-family="{FONT_STACK}">{bg}'
        f'<text x="{WIDTH / 2}" y="{HEIGHT / 2}" font-size="18" fill="{c["text_muted"]}" '
        f'text-anchor="middle">No token measurements captured yet.</text></svg>'
    )
