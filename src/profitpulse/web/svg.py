"""Small dependency-free SVG chart builders used by the PDF report.

Same visual language as the live UI ("Slate" palette): slate blue for normal, brick for flagged,
ochre for medium, slate navy for text, hairline grid.
"""
from __future__ import annotations

from html import escape
from typing import Sequence

# Names kept for compatibility: TEAL = slate blue (normal), RASP = brick (risk), AMBER = ochre (watch).
INK, TEAL, RASP, AMBER, GRID, MUTED = "#16223A", "#3D5A84", "#C2513A", "#B47A22", "#E2E6ED", "#6C788D"
BRASS, TRACK, WASH = "#3D5A84", "#EEF1F5", "#E6ECF5"


def _fmt_axis(v: float) -> str:
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.1f}B"
    if a >= 1e6:
        return f"{v / 1e6:.0f}M" if a >= 1e7 else f"{v / 1e6:.1f}M"
    if a >= 1e3:
        return f"{v / 1e3:.0f}K"
    return f"{v:.0f}"


def line_chart(labels: Sequence[str], series: dict[str, Sequence[float]], colors: dict[str, str] | None = None,
               width: int = 720, height: int = 220, y_fmt=_fmt_axis, y_zero: bool = True) -> str:
    """Hairline grid, no axis box; the first series gets a faint area wash underneath."""
    colors = colors or {}
    pad_l, pad_r, pad_t, pad_b = 48, 10, 12, 26
    vals = [v for s in series.values() for v in s if v is not None]
    if not vals:
        return ""
    lo, hi = (0 if y_zero else min(vals)), max(vals)
    hi = hi * 1.08 or 1
    n = len(labels)
    x = lambda i: pad_l + (width - pad_l - pad_r) * (i / max(n - 1, 1))  # noqa: E731
    y = lambda v: pad_t + (height - pad_t - pad_b) * (1 - (v - lo) / (hi - lo))  # noqa: E731
    out = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img">']
    for i in range(5):
        gv = lo + (hi - lo) * i / 4
        out.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y(gv):.1f}" y2="{y(gv):.1f}" stroke="{GRID}" stroke-width="{0.9 if i == 0 else 0.5}"/>'
                   f'<text x="{pad_l - 8}" y="{y(gv) + 3:.1f}" text-anchor="end" font-size="8.5" fill="{MUTED}">{y_fmt(gv)}</text>')
    step = max(n // 6, 1)
    for i in range(0, n, step):
        out.append(f'<text x="{x(i):.1f}" y="{height - 8}" text-anchor="middle" font-size="8.5" fill="{MUTED}">{escape(labels[i])}</text>')
    for si, (name, s) in enumerate(series.items()):
        pts = [(x(i), y(v)) for i, v in enumerate(s) if v is not None]
        if not pts:
            continue
        col = colors.get(name, TEAL)
        line = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        if si == 0:
            base = y(lo)
            out.append(f'<polygon points="{pts[0][0]:.1f},{base:.1f} {line} {pts[-1][0]:.1f},{base:.1f}" fill="{col}" fill-opacity="0.08"/>')
        out.append(f'<polyline points="{line}" fill="none" stroke="{col}" stroke-width="1.7" stroke-linejoin="round" stroke-linecap="round"/>')
        out.append(f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="2.6" fill="{col}"/>')
    out.append("</svg>")
    return "".join(out)


def hbar_chart(items: Sequence[tuple[str, float]], width: int = 720, row_h: int = 24, color: str = TEAL,
               value_fmt=_fmt_axis, highlight: set[str] | None = None, label_w: int = 190) -> str:
    """Slim rounded bars on a faint full-width track, values in a fixed right-hand column."""
    if not items:
        return ""
    highlight = highlight or set()
    top = max(v for _, v in items) or 1
    height = row_h * len(items) + 6
    track = width - label_w - 84
    bh = 7
    out = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img">']
    for i, (label, v) in enumerate(items):
        yy = 3 + i * row_h
        cy = yy + row_h / 2
        w = max(track * (v / top), bh)
        c = RASP if label in highlight else color
        out.append(f'<text x="{label_w - 10}" y="{cy + 3:.1f}" text-anchor="end" font-size="9.5" fill="{INK}">{escape(label)}</text>'
                   f'<rect x="{label_w}" y="{cy - bh / 2:.1f}" width="{track}" height="{bh}" rx="{bh / 2}" fill="{TRACK}"/>'
                   f'<rect x="{label_w}" y="{cy - bh / 2:.1f}" width="{w:.1f}" height="{bh}" rx="{bh / 2}" fill="{c}"/>'
                   f'<text x="{label_w + track + 10}" y="{cy + 3:.1f}" font-size="9" font-weight="600" fill="{INK}">{value_fmt(v)}</text>')
    out.append("</svg>")
    return "".join(out)


def peer_strip(points: Sequence[dict], focus: str, median: float | None, fmt, width: int = 640, height: int = 66) -> str:
    """All peers as dots on one line; the flagged entity in brick; the peer median as a tick."""
    if not points:
        return ""
    vs = [p["value"] for p in points]
    lo, hi = min(vs), max(vs)
    span = (hi - lo) or 1
    pad = 56
    x = lambda v: pad + (width - 2 * pad) * ((v - lo) / span)  # noqa: E731
    clamp = lambda px: min(max(px, 84), width - 84)  # noqa: E731
    cy = 30
    out = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img">',
           f'<line x1="{pad}" x2="{width - pad}" y1="{cy}" y2="{cy}" stroke="{GRID}" stroke-width="1.4" stroke-linecap="round"/>']
    if median is not None:
        out.append(f'<line x1="{x(median):.1f}" x2="{x(median):.1f}" y1="{cy - 11}" y2="{cy + 11}" stroke="{INK}" stroke-width="1.4" stroke-linecap="round"/>'
                   f'<text x="{clamp(x(median)):.1f}" y="{cy + 25}" text-anchor="middle" font-size="8.5" fill="{MUTED}">peer median {fmt(median)}</text>')
    for p in sorted(points, key=lambda p: p["id"] == focus):
        is_focus = p["id"] == focus
        if is_focus:
            out.append(f'<circle cx="{x(p["value"]):.1f}" cy="{cy}" r="9.5" fill="{RASP}" fill-opacity="0.14"/>')
        out.append(f'<circle cx="{x(p["value"]):.1f}" cy="{cy}" r="{5.2 if is_focus else 3.2}" fill="{RASP if is_focus else TEAL}" '
                   f'fill-opacity="{1 if is_focus else 0.5}"/>')
        if is_focus:
            out.append(f'<text x="{clamp(x(p["value"])):.1f}" y="{cy - 13}" text-anchor="middle" font-size="9.5" font-weight="700" fill="{RASP}">{escape(str(p["id"]))} {fmt(p["value"])}</text>')
    out.append("</svg>")
    return "".join(out)


def score_bars(rows: Sequence[dict], width: int = 720, row_h: int = 19) -> str:
    """Horizontal 0-100 performance bars, coloured by band, on a faint track."""
    height = row_h * len(rows) + 6
    col = {"strong": TEAL, "watch": AMBER, "weak": RASP}
    out = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img">']
    bw = width - 220
    bh = 6
    for i, r in enumerate(rows):
        yy = 3 + i * row_h
        cy = yy + row_h / 2
        out.append(f'<text x="156" y="{cy + 3:.1f}" text-anchor="end" font-size="9.5" fill="{INK}">{escape(r["label"])}</text>'
                   f'<rect x="164" y="{cy - bh / 2:.1f}" width="{bw}" height="{bh}" fill="{TRACK}" rx="{bh / 2}"/>'
                   f'<rect x="164" y="{cy - bh / 2:.1f}" width="{max(bw * r["score"] / 100, bh):.1f}" height="{bh}" fill="{col.get(r["band"], TEAL)}" rx="{bh / 2}"/>'
                   f'<text x="{164 + bw + 10}" y="{cy + 3:.1f}" font-size="9" font-weight="600" fill="{INK}">{r["score"]:.0f}</text>')
    out.append("</svg>")
    return "".join(out)
