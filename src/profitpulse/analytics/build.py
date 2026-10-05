"""Assemble leakage and headline-KPI tables from scored data."""
from __future__ import annotations

from datetime import date

import pandas as pd

LEAKAGE_BASIS = {
    "EXCESS_DISCOUNT": "Gap between the entity's discount rate and the peer median, applied to its gross (list-price) sales.",
    "EXCESS_RETURNS": "Gap between the entity's return rate and the peer median, applied to its net revenue.",
    "INVENTORY_DISCREPANCY": "Excess stock-adjustment/damage units vs the peer median, valued at cost. Adjustments carry no "
                             "direction in the source, so this is an upper bound (gains would reduce it).",
    "PROCUREMENT_OVERPAYMENT": "Purchase price paid above the product's standard (book) cost, summed over all purchases.",
    "LOW_MARGIN_SHORTFALL": "Pricing opportunity: margin shortfall vs the category median for bottom-decile products, applied to "
                            "their net revenue. Structural (every category has a bottom decile), so it is reported separately "
                            "from detected leakage.",
}
OPPORTUNITY_TYPES = {"LOW_MARGIN_SHORTFALL"}   # reported apart from anomaly-driven leakage


def build_leakage(anomalies: pd.DataFrame, products: pd.DataFrame, period_start: date, period_end: date) -> pd.DataFrame:
    """One row per (leakage type, entity). Overlaps between types are possible and documented."""
    years = max((period_end - period_start).days, 1) / 365.0
    rows = []
    if not anomalies.empty:
        peer = anomalies[(anomalies["detection_method"] == "PEER") & anomalies["leakage_type"].notna()
                         & (anomalies["estimated_exposure"].fillna(0) > 0)]
        # An entity can trip two checks that map to the same type (e.g. adjustment + damage): sum them.
        for (ltype, etype, eid), g in peer.groupby(["leakage_type", "entity_type", "entity_id"]):
            exposure = float(g["estimated_exposure"].sum())
            rows.append({"leakage_type": ltype, "entity_type": etype, "entity_id": eid,
                         "entity_label": g["entity_label"].iloc[0], "period_start": period_start,
                         "period_end": period_end, "exposure_amount": round(exposure, 2),
                         "annualised_exposure": round(exposure / years, 2),
                         "basis": LEAKAGE_BASIS[ltype], "source_anomaly_id": ";".join(g["anomaly_id"])})

    low = products[products["is_low_margin"] & products["gross_margin"].notna()].copy()
    if not low.empty:
        cat_median = products.groupby("category")["gross_margin"].median()
        low["shortfall"] = ((low["category"].map(cat_median) - low["gross_margin"]).clip(lower=0)
                            * low["net_revenue"].astype(float))
        for r in low[low["shortfall"] > 0].itertuples():
            rows.append({"leakage_type": "LOW_MARGIN_SHORTFALL", "entity_type": "product", "entity_id": r.product_id,
                         "entity_label": f"{r.product_id} ({r.category})", "period_start": period_start,
                         "period_end": period_end, "exposure_amount": round(float(r.shortfall), 2),
                         "annualised_exposure": round(float(r.shortfall) / years, 2),
                         "basis": LEAKAGE_BASIS["LOW_MARGIN_SHORTFALL"], "source_anomaly_id": None})
    cols = ["leakage_type", "entity_type", "entity_id", "entity_label", "period_start", "period_end",
            "exposure_amount", "annualised_exposure", "basis", "source_anomaly_id"]
    return pd.DataFrame(rows, columns=cols)


def build_kpis(company: pd.DataFrame, branches: pd.DataFrame, products: pd.DataFrame, suppliers: pd.DataFrame,
               inventory: pd.DataFrame, anomalies: pd.DataFrame, leakage: pd.DataFrame, as_of: date,
               period_start: date) -> pd.DataFrame:
    f = lambda c: float(company[c].astype(float).sum())  # noqa: E731
    net, gp, units = f("net_revenue"), f("gross_profit"), f("units_sold")
    last12 = company.sort_values("year_month").tail(12)
    prev12 = company.sort_values("year_month").iloc[-24:-12]
    g12 = lambda c: float(last12[c].astype(float).sum())  # noqa: E731
    yoy = (g12("net_revenue") / float(prev12["net_revenue"].astype(float).sum()) - 1) if len(prev12) == 12 else None
    sev_counts = anomalies["severity"].value_counts().to_dict() if not anomalies.empty else {}
    is_opp = leakage["leakage_type"].isin(OPPORTUNITY_TYPES) if not leakage.empty else pd.Series(dtype=bool)
    detected, opportunity = leakage[~is_opp], leakage[is_opp]

    items = [
        ("net_revenue", "Headline", "Net revenue (sales)", net, None, "currency"),
        ("gross_profit", "Headline", "Gross profit (sales)", gp, None, "currency"),
        ("gross_margin", "Headline", "Gross margin", gp / net if net else None, None, "percent"),
        ("units_sold", "Headline", "Units sold", units, None, "count"),
        ("net_revenue_12m", "Headline", "Net revenue, last 12 months", g12("net_revenue"), None, "currency"),
        ("revenue_growth_yoy", "Headline", "Revenue growth, last 12 months vs prior 12", yoy, None, "percent"),
        ("discount_rate", "Leakage", "Discount rate (value-weighted)", f("discount_amount") / f("gross_sales") if f("gross_sales") else None, None, "percent"),
        ("discount_amount", "Leakage", "Total discounts given", f("discount_amount"), None, "currency"),
        ("return_rate_units", "Leakage", "Return rate (units)", f("return_units") / units if units else None, None, "percent"),
        ("return_value", "Leakage", "Refund value", f("return_value"), None, "currency"),
        ("return_profit_impact", "Leakage", "Profit given back by returns", f("return_profit_impact"), None, "currency"),
        ("damage_value", "Leakage", "Damaged stock written off (at cost)", f("damage_value"), None, "currency"),
        ("adjustment_value", "Leakage", "Stock adjustments (at cost, unsigned)", f("adjustment_value"), None, "currency"),
        ("price_variance", "Leakage", "Purchase cost above standard", f("price_variance_amount"), None, "currency"),
        ("purchase_spend", "Procurement", "Purchase spend", f("purchase_spend"), None, "currency"),
        ("adjusted_gross_profit", "Headline", "Gross profit after returns and damage", f("adjusted_gross_profit"), None, "currency"),
        ("leakage_total", "Leakage", "Detected leakage exposure (anomaly-driven; findings may overlap)",
         float(detected["exposure_amount"].sum()) if not detected.empty else 0.0, None, "currency"),
        ("leakage_annualised", "Leakage", "Detected leakage per year",
         float(detected["annualised_exposure"].sum()) if not detected.empty else 0.0, None, "currency"),
        ("margin_opportunity", "Leakage", "Pricing opportunity in low-margin products",
         float(opportunity["exposure_amount"].sum()) if not opportunity.empty else 0.0, None, "currency"),
        ("leakage_findings", "Leakage", "Detected leakage findings", float(len(detected)), None, "count"),
        ("anomalies_total", "Risk", "Anomalies detected", float(len(anomalies)), None, "count"),
        ("anomalies_critical", "Risk", "Critical anomalies", float(sev_counts.get("critical", 0)), None, "count"),
        ("anomalies_high", "Risk", "High-severity anomalies", float(sev_counts.get("high", 0)), None, "count"),
        ("anomalies_medium", "Risk", "Medium-severity anomalies", float(sev_counts.get("medium", 0)), None, "count"),
        ("branches_weak", "Risk", "Branches in the weak performance band", float((branches["performance_band"] == "weak").sum()), None, "count"),
        ("products_problematic", "Risk", "Products flagged as problematic", float(products["is_problematic"].sum()), None, "count"),
        ("stockout_high", "Inventory", "Products at high stockout-risk proxy", float((inventory["stockout_risk_level"] == "high").sum()), None, "count"),
        ("slow_moving", "Inventory", "Slow-moving products", float(inventory["is_slow_moving"].sum()), None, "count"),
        ("dead_stock", "Inventory", "Dead-stock candidates", float(inventory["is_dead_stock"].sum()), None, "count"),
        ("reorder_flags", "Inventory", "Products flagged for reorder review", float(inventory["reorder_flag"].sum()), None, "count"),
        ("suppliers_at_risk", "Procurement", "Suppliers with medium+ risk", float((suppliers["risk_level"] != "low").sum()), None, "count"),
        ("as_of_date", "Meta", "Data as of", None, as_of.isoformat(), "text"),
        ("period_start", "Meta", "Data starts", None, period_start.isoformat(), "text"),
    ]
    return pd.DataFrame([
        {"kpi_key": k, "section": sec, "label": lbl, "value_numeric": v, "value_text": t, "unit": u, "sort_order": i}
        for i, (k, sec, lbl, v, t, u) in enumerate(items)])
