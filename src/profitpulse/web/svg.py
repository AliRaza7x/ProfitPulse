"""Small dependency-free SVG chart builders used by the PDF report.

Same visual language as the live UI: teal for normal, raspberry for flagged,
amber for medium, ink for text, thin grid.
"""
from __future__ import annotations

from html import escape
from typing import Sequence

INK, TEAL, RASP, AMBER, GRID, MUTED = "#16232C", "#0F7B6C", "#CF2E5E", "#D99A0B", "#D5DCD8", "#6B7A82"


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
    colors = colors or {}
    pad_l, pad_r, pad_t, pad_b = 52, 14, 12, 26
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
        out.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y(gv):.1f}" y2="{y(gv):.1f}" stroke="{GRID}" stroke-width="0.6"/>'
                   f'<text x="{pad_l - 6}" y="{y(gv) + 3:.1f}" text-anchor="end" font-size="9" fill="{MUTED}">{y_fmt(gv)}</text>')
    step = max(n // 6, 1)
    for i in range(0, n, step):
        out.append(f'<text x="{x(i):.1f}" y="{height - 8}" text-anchor="middle" font-size="9" fill="{MUTED}">{escape(labels[i])}</text>')
    for name, s in series.items():
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(s) if v is not None)
        out.append(f'<polyline points="{pts}" fill="none" stroke="{colors.get(name, TEAL)}" stroke-width="1.8" stroke-linejoin="round"/>')
    out.append("</svg>")
    return "".join(out)


def hbar_chart(items: Sequence[tuple[str, float]], width: int = 720, row_h: int = 26, color: str = TEAL,
               value_fmt=_fmt_axis, highlight: set[str] | None = None, label_w: int = 190) -> str:
    if not items:
        return ""
    highlight = highlight or set()
    top = max(v for _, v in items) or 1
    height = row_h * len(items) + 8
    out = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img">']
    for i, (label, v) in enumerate(items):
        yy = 4 + i * row_h
        w = (width - label_w - 90) * (v / top)
        c = RASP if label in highlight else color
        out.append(f'<text x="{label_w - 8}" y="{yy + row_h / 2 + 3:.1f}" text-anchor="end" font-size="10.5" fill="{INK}">{escape(label)}</text>'
                   f'<rect x="{label_w}" y="{yy + 5}" width="{max(w, 1):.1f}" height="{row_h - 12}" rx="1.5" fill="{c}"/>'
                   f'<text x="{label_w + w + 6:.1f}" y="{yy + row_h / 2 + 3:.1f}" font-size="10" fill="{MUTED}">{value_fmt(v)}</text>')
    out.append("</svg>")
    return "".join(out)


def peer_strip(points: Sequence[dict], focus: str, median: float | None, fmt, width: int = 640, height: int = 66) -> str:
    """All peers as dots on one line; the flagged entity in raspberry; the peer median as a tick."""
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
           f'<line x1="{pad}" x2="{width - pad}" y1="{cy}" y2="{cy}" stroke="{GRID}" stroke-width="1"/>']
    if median is not None:
        out.append(f'<line x1="{x(median):.1f}" x2="{x(median):.1f}" y1="{cy - 12}" y2="{cy + 12}" stroke="{INK}" stroke-width="1.4"/>'
                   f'<text x="{clamp(x(median)):.1f}" y="{cy + 26}" text-anchor="middle" font-size="9" fill="{MUTED}">peer median {fmt(median)}</text>')
    for p in sorted(points, key=lambda p: p["id"] == focus):
        is_focus = p["id"] == focus
        out.append(f'<circle cx="{x(p["value"]):.1f}" cy="{cy}" r="{6 if is_focus else 3.6}" fill="{RASP if is_focus else TEAL}" '
                   f'fill-opacity="{1 if is_focus else 0.55}"/>')
        if is_focus:
            out.append(f'<text x="{clamp(x(p["value"])):.1f}" y="{cy - 12}" text-anchor="middle" font-size="10" font-weight="700" fill="{RASP}">{escape(str(p["id"]))} {fmt(p["value"])}</text>')
    out.append("</svg>")
    return "".join(out)


def score_bars(rows: Sequence[dict], width: int = 720, row_h: int = 19) -> str:
    """Horizontal 0-100 performance bars, coloured by band."""
    height = row_h * len(rows) + 6
    col = {"strong": TEAL, "watch": AMBER, "weak": RASP}
    out = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img">']
    bw = width - 220
    for i, r in enumerate(rows):
        yy = 3 + i * row_h
        out.append(f'<text x="156" y="{yy + 15}" text-anchor="end" font-size="10.5" fill="{INK}">{escape(r["label"])}</text>'
                   f'<rect x="164" y="{yy + 4}" width="{bw}" height="{row_h - 10}" fill="{GRID}" fill-opacity="0.5" rx="1.5"/>'
                   f'<rect x="164" y="{yy + 4}" width="{bw * r["score"] / 100:.1f}" height="{row_h - 10}" fill="{col.get(r["band"], TEAL)}" rx="1.5"/>'
                   f'<text x="{164 + bw + 8}" y="{yy + 15}" font-size="10" fill="{MUTED}">{r["score"]:.0f}</text>')
    out.append("</svg>")
    return "".join(out)
