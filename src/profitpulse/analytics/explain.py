"""Plain-English explanations for findings.

Every anomaly leaves this module with a sentence a business user can act on:
what is unusual, compared with what, how strong the evidence is, and what it
costs. No bare scores.
"""
from __future__ import annotations

from typing import Any

METRICS: dict[str, dict[str, str]] = {
    "discount_rate": {"label": "discount rate", "kind": "pct", "about": "of list value given away as discount"},
    "return_rate_units": {"label": "return rate", "kind": "pct", "about": "of units sold come back as returns"},
    "adjustment_avg_qty": {"label": "average stock-adjustment size", "kind": "units", "about": "units per adjustment"},
    "damage_rate": {"label": "damage rate", "kind": "pct", "about": "of units sold are written off as damaged"},
    "gross_margin": {"label": "gross margin", "kind": "pct", "about": "of net revenue is gross profit"},
    "ppv_pct": {"label": "purchase cost versus standard cost", "kind": "pct_signed", "about": "paid above book cost"},
}
PEER_NOUN = {"branch": "branches", "product": "products", "supplier": "suppliers"}
UNIT_NOUN = {"sale_events": "sales", "adjustment_events": "stock adjustments", "purchase_events": "purchases"}


def fmt_value(metric: str, value: float) -> str:
    kind = METRICS.get(metric, {}).get("kind", "number")
    if kind == "pct":
        return f"{value:.1%}"
    if kind == "pct_signed":
        return f"{value:+.1%}"
    if kind == "units":
        return f"{value:.1f} units"
    return f"{value:,.2f}"


def fmt_gap(metric: str, gap: float, direction: str) -> str:
    """`gap` is the (positive) size of the difference in the 'worse' direction."""
    kind = METRICS.get(metric, {}).get("kind", "number")
    word = "higher" if direction == "high" else "lower"
    if kind in ("pct", "pct_signed"):
        return f"{gap * 100:.1f} percentage points {word}"
    if kind == "units":
        return f"{gap:.1f} units {word}"
    return f"{gap:,.2f} {word}"


def money(currency: str, amount: float) -> str:
    return f"{currency} {amount:,.0f}"


def explain_peer(*, label: str, metric: str, direction: str, observed: float, baseline: float, gap: float,
                 z: float, severity: str, n: int, n_col: str, peers: int, entity: str,
                 exposure: float | None, annualised: float | None, years: float, currency: str) -> str:
    spec = METRICS.get(metric, {"label": metric})
    parts = [
        f"{label}: {spec['label']} is {fmt_value(metric, observed)} against a peer median of "
        f"{fmt_value(metric, baseline)} across {peers} {PEER_NOUN.get(entity, 'peers')} "
        f"({fmt_gap(metric, gap, direction)}; robust z = {z:.1f}, {severity} severity).",
        f"Based on {n:,} {UNIT_NOUN.get(n_col, 'events')}.",
    ]
    if exposure is not None and exposure > 0:
        parts.append(
            f"Estimated exposure: {money(currency, exposure)} over {years:.1f} years"
            + (f" (about {money(currency, annualised)} a year)." if annualised else "."))
    return " ".join(parts)


def explain_temporal(*, label: str, metric: str, direction: str, recent: float, baseline: float, spread: float,
                     gap: float, z: float, severity: str, recent_months: int, baseline_months: int) -> str:
    spec = METRICS.get(metric, {"label": metric})
    return (f"{label}: {spec['label']} averaged {fmt_value(metric, recent)} over the last {recent_months} months, "
            f"versus {fmt_value(metric, baseline)} (typical monthly variation {fmt_value(metric, spread)}) in the "
            f"{baseline_months} months before: {fmt_gap(metric, gap, direction)} than its own history "
            f"(z = {z:.1f}, {severity} severity).")


def explain_stockout(*, velocity: float | None, vel_pct: float, replenishment: float | None, days_purchase: int | None,
                     velocity_days: int, window_days: int) -> str:
    bits = []
    if velocity is not None:
        bits.append(f"sells {velocity:.1f} units/day over the last {velocity_days} days (faster than {vel_pct:.0%} of products)")
    if replenishment is not None:
        bits.append(f"only {replenishment:.0%} of units sold in the last {window_days} days were replenished by purchases")
    if days_purchase is not None:
        bits.append(f"last purchase was {days_purchase} days ago")
    return "; ".join(bits).capitalize() + "." if bits else "Insufficient movement data."


def explain_product_flags(reasons: list[str]) -> str:
    return "; ".join(reasons)


def describe_param(d: dict[str, Any]) -> str:  # tiny helper used in docs/tests
    return ", ".join(f"{k}={v}" for k, v in d.items())
