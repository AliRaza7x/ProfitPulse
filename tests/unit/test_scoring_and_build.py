from datetime import date

import numpy as np
import pandas as pd
import pytest

from profitpulse.analytics.build import build_kpis, build_leakage
from profitpulse.analytics.scoring import flag_products, pct_rank, score_branches, score_inventory, score_suppliers


def test_pct_rank_orientation_and_neutral_missing():
    s = pd.Series([0.1, 0.3, 0.2, np.nan])
    best_high = pct_rank(s, True)
    assert best_high.iloc[1] == 1.0 and best_high.iloc[0] == 0.0 and best_high.iloc[3] == 0.5
    best_low = pct_rank(s, False)
    assert best_low.iloc[0] == 1.0 and best_low.iloc[1] == 0.0


def branch_base():
    return pd.DataFrame({
        "branch_id": ["A", "B", "C", "D"], "city": list("wxyz"),
        "gross_margin": [0.30, 0.24, 0.24, 0.20], "discount_rate": [0.05, 0.06, 0.07, 0.22],
        "return_rate_units": [0.09, 0.10, 0.10, 0.11], "shrink_rate": [0.05, 0.06, 0.06, 0.10],
        "growth_rate": [0.05, 0.0, -0.01, -0.10]})


def anomalies_for(entity_type, entity_id, severity):
    return pd.DataFrame([{"anomaly_id": "x", "entity_type": entity_type, "entity_id": entity_id, "severity": severity}])


def test_branch_score_components_are_bounded_oriented_and_neutral_at_the_median(analytics_cfg):
    out = score_branches(branch_base(), pd.DataFrame(), analytics_cfg).set_index("branch_id")
    comps = ["score_gross_margin", "score_discount_rate", "score_return_rate", "score_shrink_rate", "score_growth_rate"]
    assert ((out[comps] >= 0) & (out[comps] <= 1)).all().all()
    assert out.loc["A", "score_gross_margin"] > 0.5 > out.loc["D", "score_gross_margin"]      # higher margin is better
    assert out.loc["A", "score_discount_rate"] > 0.5 > out.loc["D", "score_discount_rate"]    # lower discount is better
    assert out.loc["D", "score_discount_rate"] == 0.0                                          # 22% vs ~6%: capped worst
    assert out.loc["A", "performance_rank"] == 1 and out.loc["D", "performance_rank"] == 4
    assert out.loc["D", "performance_band"] == "weak" and out.loc["A", "performance_band"] == "strong"


def test_a_real_outlier_scores_lower_than_noise_level_differences(analytics_cfg):
    """The flaw that percentile ranks had: a catastrophic gap must cost more than marginal ones."""
    rows = []
    for i in range(14):
        rows.append({"branch_id": f"B{i:02d}", "city": "c", "gross_margin": 0.25, "discount_rate": 0.061 + i * 0.0001,
                     "return_rate_units": 0.100 + i * 0.0004, "shrink_rate": 0.15 + i * 0.0003, "growth_rate": -0.01 - i * 0.002})
    rows.append({"branch_id": "BAD", "city": "c", "gross_margin": 0.099, "discount_rate": 0.221,
                 "return_rate_units": 0.101, "shrink_rate": 0.147, "growth_rate": -0.012})
    out = score_branches(pd.DataFrame(rows), pd.DataFrame(), analytics_cfg).set_index("branch_id")
    assert out["performance_score"].idxmin() == "BAD"
    others = out.drop(index="BAD")["performance_score"]
    assert others.max() - others.min() < 12                          # near-identical branches stay close together
    assert out.loc["BAD", "performance_score"] < others.min() - 12   # the outlier is clearly separated


def test_branch_score_weights_come_from_config(analytics_cfg):
    base = {"higher_is_better": True, "min_scale": 0.005}
    comps = {k: {**v, "weight": 0.0} for k, v in analytics_cfg["branch_score"]["components"].items()}
    comps["gross_margin"]["weight"] = 1.0
    cfg = {**analytics_cfg, "branch_score": {**analytics_cfg["branch_score"], "components": comps}}
    out = score_branches(branch_base(), pd.DataFrame(), cfg).set_index("branch_id")
    # only margin counts: A (highest margin) beats B == C (typical), which beat D (lowest)
    assert out.loc["A", "performance_score"] > out.loc["B", "performance_score"] > out.loc["D", "performance_score"]
    assert out.loc["B", "performance_score"] == out.loc["C", "performance_score"]


def test_operational_risk_follows_worst_anomaly_severity(analytics_cfg):
    out = score_branches(branch_base(), anomalies_for("branch", "D", "critical"), analytics_cfg).set_index("branch_id")
    assert out.loc["D", "operational_risk"] == "high" and out.loc["D", "anomaly_count"] == 1
    assert out.loc["A", "operational_risk"] == "low"
    med = score_branches(branch_base(), anomalies_for("branch", "B", "medium"), analytics_cfg).set_index("branch_id")
    assert med.loc["B", "operational_risk"] == "medium"


def test_supplier_risk_level(analytics_cfg):
    base = pd.DataFrame({"supplier_id": ["S1", "S2"]})
    out = score_suppliers(base, anomalies_for("supplier", "S2", "critical")).set_index("supplier_id")
    assert out.loc["S2", "risk_level"] == "high" and out.loc["S1", "risk_level"] == "low"


def product_base(n=40):
    rng = np.random.default_rng(3)
    return pd.DataFrame({
        "product_id": [f"P{i:04d}" for i in range(n)], "category": ["Cat"] * n,
        "sale_events": 200, "gross_margin": np.linspace(0.05, 0.40, n),
        "return_rate_units": rng.normal(0.10, 0.02, n), "discount_rate": rng.normal(0.06, 0.01, n),
        "growth_rate": rng.normal(0.0, 0.05, n), "net_revenue": 1_000_000.0})


def test_product_flags_use_category_percentiles_and_explain_themselves(analytics_cfg):
    base = product_base()
    base.loc[0, ["return_rate_units", "growth_rate"]] = [0.30, -0.40]        # low margin + high return + declining
    out = flag_products(base, pd.DataFrame(), analytics_cfg).set_index("product_id")
    assert out.loc["P0000", "is_low_margin"] and out.loc["P0039", "is_high_margin"]
    assert out.loc["P0000", "is_high_return"] and out.loc["P0000", "is_declining"]
    assert out.loc["P0000", "is_problematic"]
    reasons = out.loc["P0000", "problem_reasons"]
    assert "bottom decile" in reasons and "return rate" in reasons and "fell 40%" in reasons
    assert pd.isna(out.loc["P0020", "problem_reasons"])


def test_low_volume_products_are_never_flagged(analytics_cfg):
    base = product_base()
    base["sale_events"] = 10
    out = flag_products(base, pd.DataFrame(), analytics_cfg)
    assert not out[["is_low_margin", "is_high_return", "is_declining"]].any().any()


def inv_base(n=30):
    return pd.DataFrame({
        "product_id": [f"P{i}" for i in range(n)], "category": ["C"] * n, "supplier_id": ["S"] * n,
        "velocity_per_day": np.linspace(0.1, 3.0, n), "velocity_change": 0.0,
        "replenishment_ratio": np.linspace(0.9, 0.05, n), "days_since_last_sale": 5,
        "days_since_last_purchase": np.linspace(5, 200, n).astype(int)})


def test_stockout_proxy_ranks_fast_unreplenished_stale_products_highest(analytics_cfg):
    out = score_inventory(inv_base(), analytics_cfg).set_index("product_id")
    assert out.loc["P29", "stockout_risk_score"] == 100.0 and out.loc["P29", "stockout_risk_level"] == "high"
    assert out.loc["P0", "stockout_risk_score"] == 0.0 and out.loc["P0", "stockout_risk_level"] == "normal"
    assert "units/day" in out.loc["P29", "explanation"] and "replenished" in out.loc["P29", "explanation"]
    assert out.loc["P29", "reorder_flag"] and not out.loc["P0", "reorder_flag"]


def test_dead_and_slow_moving_detection(analytics_cfg):
    base = inv_base()
    base.loc[5, "days_since_last_sale"] = 400
    base.loc[6, "days_since_last_sale"] = None            # never sold
    out = score_inventory(base, analytics_cfg).set_index("product_id")
    assert out.loc["P5", "is_dead_stock"] and out.loc["P6", "is_dead_stock"] and not out.loc["P7", "is_dead_stock"]
    assert out.loc["P0", "is_slow_moving"] and not out.loc["P29", "is_slow_moving"]


# ---- leakage and KPIs ---------------------------------------------------------
def anomaly_rows():
    mk = lambda aid, ltype, etype, eid, exp, m="PEER": {  # noqa: E731
        "anomaly_id": aid, "detection_method": m, "leakage_type": ltype, "entity_type": etype, "entity_id": eid,
        "entity_label": eid, "estimated_exposure": exp, "severity": "high"}
    return pd.DataFrame([
        mk("a1", "EXCESS_DISCOUNT", "branch", "BR07", 1_000_000.0),
        mk("a2", "INVENTORY_DISCREPANCY", "branch", "BR12", 300_000.0),
        mk("a3", "INVENTORY_DISCREPANCY", "branch", "BR12", 200_000.0),      # second check, same type: summed
        mk("a4", None, "branch", "BR03", 999.0),                              # no leakage type: excluded
        mk("a5", "EXCESS_DISCOUNT", "branch", "BR09", 5.0, m="TEMPORAL")])    # temporal: excluded


def test_leakage_sums_checks_of_same_type_and_excludes_non_leakage(analytics_cfg):
    products = pd.DataFrame({"product_id": ["P1"], "category": ["C"], "gross_margin": [0.1], "net_revenue": [1000.0],
                             "is_low_margin": [False]})
    out = build_leakage(anomaly_rows(), products, date(2024, 1, 1), date(2025, 1, 1))
    by = {(r.leakage_type, r.entity_id): r.exposure_amount for r in out.itertuples()}
    assert by == {("EXCESS_DISCOUNT", "BR07"): 1_000_000.0, ("INVENTORY_DISCREPANCY", "BR12"): 500_000.0}
    assert out.loc[out.entity_id == "BR07", "annualised_exposure"].iloc[0] == pytest.approx(1_000_000 / (366 / 365), rel=1e-3)
    assert out.loc[out.entity_id == "BR12", "source_anomaly_id"].iloc[0] == "a2;a3"


def test_low_margin_shortfall_is_measured_against_category_median():
    products = pd.DataFrame({
        "product_id": ["P1", "P2", "P3"], "category": ["C"] * 3, "gross_margin": [0.30, 0.30, 0.10],
        "net_revenue": [1000.0, 1000.0, 2000.0], "is_low_margin": [False, False, True]})
    out = build_leakage(pd.DataFrame(), products, date(2024, 1, 1), date(2025, 1, 1))
    assert len(out) == 1 and out.iloc[0].entity_id == "P3"
    assert out.iloc[0].exposure_amount == pytest.approx((0.30 - 0.10) * 2000.0)


def test_kpi_table_has_reconciling_headline_numbers():
    months = pd.DataFrame({
        "year_month": [f"2025-{m:02d}" for m in range(1, 13)] + [f"2026-{m:02d}" for m in range(1, 13)],
        "net_revenue": 100.0, "gross_profit": 25.0, "units_sold": 10, "gross_sales": 110.0, "discount_amount": 10.0,
        "return_units": 1, "return_value": 8.0, "return_profit_impact": -2.0, "damage_value": 1.0,
        "adjustment_value": 3.0, "purchase_spend": 70.0, "price_variance_amount": 0.5, "adjusted_gross_profit": 22.0})
    branches = pd.DataFrame({"performance_band": ["weak", "strong"]})
    products = pd.DataFrame({"is_problematic": [True, False]})
    suppliers = pd.DataFrame({"risk_level": ["high", "low"]})
    inv = pd.DataFrame({"stockout_risk_level": ["high", "normal"], "is_slow_moving": [True, False],
                        "is_dead_stock": [False, False], "reorder_flag": [True, False]})
    kpis = build_kpis(months, branches, products, suppliers, inv, anomaly_rows(), pd.DataFrame(
        {"leakage_type": ["EXCESS_DISCOUNT", "LOW_MARGIN_SHORTFALL"], "exposure_amount": [10.0, 99.0],
         "annualised_exposure": [5.0, 40.0]}), date(2026, 12, 31), date(2025, 1, 1)).set_index("kpi_key")
    assert kpis.loc["net_revenue", "value_numeric"] == 2400.0
    assert kpis.loc["gross_margin", "value_numeric"] == pytest.approx(0.25)
    assert kpis.loc["discount_rate", "value_numeric"] == pytest.approx(240 / 2640)
    assert kpis.loc["net_revenue_12m", "value_numeric"] == 1200.0
    assert kpis.loc["revenue_growth_yoy", "value_numeric"] == pytest.approx(0.0)
    assert kpis.loc["branches_weak", "value_numeric"] == 1 and kpis.loc["suppliers_at_risk", "value_numeric"] == 1
    assert kpis.loc["anomalies_high", "value_numeric"] == 5
    assert kpis.loc["leakage_total", "value_numeric"] == 10.0 and kpis.loc["leakage_annualised", "value_numeric"] == 5.0
    assert kpis.loc["margin_opportunity", "value_numeric"] == 99.0      # structural opportunity is kept apart
    assert kpis.loc["as_of_date", "value_text"] == "2026-12-31"
    assert kpis["sort_order"].is_unique
