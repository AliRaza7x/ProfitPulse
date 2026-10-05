"""Turn analytic results into plain sentences (UI headline, report executive summary)."""
from __future__ import annotations

from typing import Any

from ..analytics.explain import METRICS, fmt_value

TYPE_LABELS = {
    "EXCESS_DISCOUNT": "Excess discounting",
    "EXCESS_RETURNS": "Excess returns",
    "INVENTORY_DISCREPANCY": "Inventory discrepancies",
    "PROCUREMENT_OVERPAYMENT": "Procurement overpayment",
    "LOW_MARGIN_SHORTFALL": "Low-margin pricing opportunity",
}


def short(n: float | None, currency: str = "PKR") -> str:
    """12,345,678 -> 'PKR 12.3M'."""
    if n is None:
        return "n/a"
    a = abs(n)
    sign = "-" if n < 0 else ""
    if a >= 1e9:
        return f"{sign}{currency} {a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}{currency} {a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{sign}{currency} {a / 1e3:.0f}K"
    return f"{sign}{currency} {a:,.0f}"


def finding_phrase(a: dict[str, Any]) -> str:
    """One clause describing a finding, e.g. 'discount rate of 22.1% against a peer median of 6.1%'."""
    spec = METRICS.get(a["metric"], {"label": a["metric"]})
    return (f"the {spec['label']} is {fmt_value(a['metric'], a['observed_value'])} against a peer median of "
            f"{fmt_value(a['metric'], a['baseline_value'])}"
            if a["detection_method"] == "PEER" else
            f"the {spec['label']} shifted to {fmt_value(a['metric'], a['observed_value'])} from "
            f"{fmt_value(a['metric'], a['baseline_value'])}")


def headline(overview: dict[str, Any]) -> dict[str, str]:
    """Lead sentence + supporting sentence for the overview page."""
    cur = overview["currency"]
    k = overview["kpis"]
    total = (k.get("leakage_total") or {}).get("value_numeric") or 0
    annual = (k.get("leakage_annualised") or {}).get("value_numeric") or 0
    n = int((k.get("leakage_findings") or {}).get("value_numeric") or 0)
    findings = overview["findings"]
    if not n or not findings:
        return {"lead": "No leakage findings stand out from peers right now.",
                "support": "Every branch, supplier and product is within its normal range."}
    lead = (f"{_count(n)} finding{'s' if n != 1 else ''} put an estimated {short(total, cur)} at risk, "
            f"about {short(annual, cur)} a year.")
    top = findings[0]
    support = f"The largest is {top['entity_label']}, where {finding_phrase(top)}."
    return {"lead": lead, "support": support}


def _count(n: int) -> str:
    words = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"]
    return words[n] if n < len(words) else str(n)


def executive_summary(ov: dict[str, Any], branches: dict, suppliers: dict, products: dict,
                      inventory: dict, health: dict) -> list[str]:
    """Paragraphs for the report's opening page."""
    cur, k = ov["currency"], ov["kpis"]
    v = lambda key: (k.get(key) or {}).get("value_numeric")  # noqa: E731
    period = f"{(k.get('period_start') or {}).get('value_text', '?')} and {(k.get('as_of_date') or {}).get('value_text', '?')}"
    out = [
        f"Between {period}, the business recorded {short(v('net_revenue'), cur)} of net sales and "
        f"{short(v('gross_profit'), cur)} of gross profit, a margin of {v('gross_margin'):.1%}. "
        f"The last twelve months brought {short(v('net_revenue_12m'), cur)}"
        + (f", {v('revenue_growth_yoy'):+.1%} against the twelve months before." if v("revenue_growth_yoy") is not None else ".")]
    h = headline(ov)
    out.append(f"{h['lead']} {h['support']}")

    by_type = [t for t in ov["leakage_by_type"] if t["leakage_type"] != "LOW_MARGIN_SHORTFALL"]
    if by_type:
        parts = [f"{TYPE_LABELS.get(t['leakage_type'], t['leakage_type']).lower()} ({short(t['exposure_amount'], cur)})" for t in by_type]
        out.append("By type: " + ", ".join(parts) + ". Findings can overlap, so treat the total as an estimate of "
                   "exposure, not of recoverable cash.")
    opp = v("margin_opportunity")
    if opp:
        out.append(f"Separately, {products['counts']['low_margin']} products sit in the bottom margin decile of their category. "
                   f"Bringing them to the category median would be worth about {short(opp, cur)} over the period. "
                   f"This is a pricing opportunity, not detected leakage.")
    weak = [b for b in branches["scorecards"] if b["performance_band"] == "weak"]
    best = branches["scorecards"][0] if branches["scorecards"] else None
    if best:
        out.append(f"Branch performance: {best['branch_id']} ({best['city']}) ranks first with a score of {best['performance_score']:.0f}/100. "
                   + (f"{len(weak)} branch{'es are' if len(weak) != 1 else ' is'} in the weak band: "
                      + ", ".join(f"{b['branch_id']} ({b['performance_score']:.0f})" for b in weak) + "." if weak else
                      "No branch is in the weak band."))
    flow = {k_: (int(v_) if v_ is not None else None) for k_, v_ in health["flow"].items()}
    out.append(f"Pipeline: {flow['landed']:,} events were read from Kafka, {flow['accepted']:,} passed validation and "
               f"{flow['rejected']:,} were quarantined; {flow['stored']:,} are stored in the warehouse. "
               f"All {sum(1 for c in health['checks'] if c['status'] == 'PASS')} reconciliation checks that apply passed."
               if flow["accepted"] is not None else "Pipeline statistics are not available yet.")
    return out


LIMITATIONS = [
    "The source data has no stock-on-hand or opening balances. Stockout risk, slow-moving and dead-stock flags are "
    "movement-based proxies (sales velocity, purchase coverage, days since last purchase), not stock levels.",
    "Stock adjustments carry no sign, so inventory-discrepancy exposure is an upper bound that treats every excess "
    "adjustment as a loss.",
    "Only SALE rows are treated as revenue. The source also fills revenue and profit columns on purchases, returns, "
    "damages and adjustments; those are not sales and are excluded from every revenue figure.",
    "Standard cost is the median unit cost on non-purchase events. Purchase-price variance is measured against it.",
    "Anomalies are statistical flags with explanations, not accusations. Each needs a person to investigate the cause.",
    "Currency is assumed to be PKR (configurable); the source does not state it. Timestamps carry no time zone.",
]
