"""Financial calculations in the transform layer and the Spark feature SQL, against hand-computed numbers."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fixtures import EVENT_SCHEMA, fixture_events

from profitpulse.spark import features as F
from profitpulse.spark import transform as T

pytestmark = pytest.mark.spark
AS_OF = date(2025, 6, 30)


@pytest.fixture(scope="module")
def events(spark):
    return spark.createDataFrame(fixture_events(), EVENT_SCHEMA)


@pytest.fixture(scope="module")
def tables(spark, events, analytics_cfg):
    """Build dims + facts, expose them as the core views the feature SQL expects."""
    dim_product = T.build_dim_product(events)
    built = {
        "dim_date": T.build_dim_date(events), "dim_supplier": T.build_dim_supplier(events),
        "dim_branch": T.build_dim_branch(events), "dim_product": dim_product,
        "fact_sales": T.build_fact_sales(events),
        "fact_returns": T.build_fact_returns(events, analytics_cfg["returns"]["non_restockable_reasons"]),
        "fact_purchases": T.build_fact_purchases(events, dim_product),
        "fact_inventory_movements": T.build_fact_inventory(events),
        "fact_price_changes": T.build_fact_price_changes(events, dim_product),
    }
    for name, df in built.items():
        df.createOrReplaceTempView(name)
    F.register_movements(spark)
    return built


def one(df, **where):
    rows = df.collect()
    for k, v in where.items():
        rows = [r for r in rows if r[k] == v]
    assert len(rows) == 1, f"expected one row for {where}, got {len(rows)}"
    return rows[0].asDict()


# ------------------------------------------------------------------ transform
def test_every_event_lands_in_exactly_one_fact(tables, events):
    facts = sum(tables[t].count() for t in tables if t.startswith("fact_"))
    assert facts == events.count() == 11


def test_sales_fact_discount_and_profit_arithmetic(tables):
    s = {r["quantity"] * 1000 + int(r["gross_sales"]): r.asDict() for r in tables["fact_sales"].collect()}
    first = next(r for r in s.values() if r["gross_sales"] == Decimal("300.00"))
    assert (first["net_revenue"], first["discount_amount"], first["cogs"], first["gross_profit"]) == \
           (Decimal("270.00"), Decimal("30.00"), Decimal("200.00"), Decimal("70.00"))
    loss = next(r for r in s.values() if r["gross_sales"] == Decimal("400.00"))
    assert loss["gross_profit"] == Decimal("-100.00")           # a 50% discount sells below cost
    assert all(r["net_revenue"] - r["cogs"] == r["gross_profit"] for r in s.values())


def test_returns_split_restockable_and_non_restockable(tables):
    damaged = one(tables["fact_returns"], return_reason="Damaged")
    assert damaged["restockable"] is False and damaged["profit_impact"] == Decimal("-135.00")   # full refund lost
    wrong = one(tables["fact_returns"], return_reason="Wrong Item")
    assert wrong["restockable"] is True and wrong["profit_impact"] == Decimal("-80.00")         # -(380 - 300)


def test_purchase_price_variance_against_standard_cost(tables):
    rows = sorted(tables["fact_purchases"].collect(), key=lambda r: r["quantity"])
    p5, p10 = rows[0].asDict(), rows[1].asDict()
    assert p10["standard_cost"] == Decimal("100.00") and p10["price_variance_amount"] == Decimal("200.00")
    assert p10["price_variance_pct"] == Decimal("0.200000") and p10["cost_above_list_price"] is False
    assert p5["price_variance_amount"] == Decimal("300.00") and p5["cost_above_list_price"] is True


def test_inventory_movements_keep_direction_honest(tables):
    dmg = one(tables["fact_inventory_movements"], movement_type="DAMAGE")
    adj = one(tables["fact_inventory_movements"], movement_type="STOCK_ADJUSTMENT")
    assert dmg["direction"] == "LOSS" and adj["direction"] == "UNSIGNED"
    assert adj["value_at_cost"] == Decimal("500.00")


def test_dim_product_standard_cost_ignores_purchase_prices(tables):
    p1 = one(tables["dim_product"], product_id="P1")
    assert p1["standard_cost"] == Decimal("100.00")             # not pulled up by the 120/160 purchases
    assert p1["list_price"] == Decimal("150.00") and p1["supplier_id"] == "SUP1" and p1["category"] == "Grocery"


def test_dim_date_covers_whole_years_with_correct_attributes(tables):
    d = tables["dim_date"]
    assert d.count() == 366 + 365                                # 2024 (leap) and 2025
    monday = one(d, full_date=date(2025, 3, 3))
    assert monday["day_of_week"] == 1 and monday["is_weekend"] is False and monday["month_name"] == "March"
    assert monday["year_month"] == "2025-03" and monday["date_key"] == 20250303
    assert one(d, full_date=date(2025, 3, 8))["is_weekend"] is True


# ------------------------------------------------------------------ features
@pytest.fixture(scope="module")
def branch_base(spark, tables):
    return F.build_branch_base(spark, AS_OF, growth_months=6)


def test_branch_metrics_match_hand_calculation(branch_base):
    b = one(branch_base, branch_id="BR01")
    assert (b["sale_events"], b["units_sold"]) == (3, 7)
    assert (b["gross_sales"], b["net_revenue"], b["cogs"], b["gross_profit"]) == \
           (Decimal("1300.00"), Decimal("1070.00"), Decimal("900.00"), Decimal("170.00"))
    assert float(b["discount_rate"]) == pytest.approx(230 / 1300)
    assert float(b["gross_margin"]) == pytest.approx(170 / 1070)
    assert float(b["return_rate_units"]) == pytest.approx(1 / 7) and b["return_value"] == Decimal("135.00")
    assert b["damage_units"] == 2 and float(b["adjustment_avg_qty"]) == pytest.approx(5.0)
    assert b["adjustment_event_cost"] == Decimal("100.00")        # 1 event x (500 / 5 units)
    assert float(b["shrink_rate"]) == pytest.approx((2 + 5) / 7)
    assert b["adjusted_gross_profit"] == Decimal("-165.00")        # 170 - 135 refund loss - 200 damage
    assert float(b["growth_rate"]) == pytest.approx(870 / 200 - 1)        # recent 270+600 vs prior 200


def test_missing_activity_gives_zero_sums_and_null_ratios_not_errors(spark, tables):
    """BR02 has no damage or adjustments: sums are 0, and ratios whose denominator is 0 are NULL (ANSI mode would raise)."""
    br = one(F.build_branch_base(spark, AS_OF, 6), branch_id="BR02")
    assert br["damage_units"] == 0 and br["adjustment_events"] == 0 and br["return_units"] == 1
    assert br["adjustment_avg_qty"] is None            # 0 units / 0 events
    assert br["shrink_rate"] == 0.0
    assert br["growth_rate"] is None                    # no sales in the prior window


def test_supplier_price_variance(spark, tables):
    sb = F.build_supplier_base(spark)
    s1 = one(sb, supplier_id="SUP1")
    assert (s1["purchase_events"], s1["purchase_units"]) == (2, 15)
    assert (s1["purchase_spend"], s1["purchase_standard_spend"], s1["ppv_amount"]) == \
           (Decimal("2000.00"), Decimal("1500.00"), Decimal("500.00"))
    assert float(s1["ppv_pct"]) == pytest.approx(500 / 1500) and s1["share_above_standard"] == 1.0
    assert s1["purchases_above_list"] == 1
    assert s1["cadence_cv"] is None                      # a single gap between purchases: spread is undefined
    s2 = one(sb, supplier_id="SUP2")
    assert s2["purchase_events"] == 0 and s2["ppv_pct"] is None


def test_monthly_series_reconcile_to_facts(spark, tables):
    company = F.build_company_monthly(spark).collect()
    assert sum(r["net_revenue"] for r in company) == Decimal("1670.00")
    june = next(r for r in company if r["year_month"] == "2025-06")
    assert june["net_revenue"] == Decimal("870.00")        # BR01 270 + BR02 600 (the 600 sale in May is excluded)
    branch = F.build_branch_monthly(spark).collect()
    assert sum(r["net_revenue"] for r in branch) == Decimal("1670.00")


def test_inventory_windows_and_replenishment(spark, tables):
    inv = F.build_inventory_base(spark, AS_OF, velocity_days=90, replenishment_days=180)
    p1 = one(inv, product_id="P1")
    assert p1["units_sold_recent"] == 6 and float(p1["velocity_per_day"]) == pytest.approx(6 / 90)
    assert p1["units_sold_prior"] == 0 and p1["velocity_change"] is None
    assert p1["units_sold_window"] == 6 and p1["units_purchased_window"] == 15
    assert float(p1["replenishment_ratio"]) == pytest.approx(2.5)
    assert p1["days_since_last_sale"] == 10 and p1["days_since_last_purchase"] == 10
    assert p1["damage_units_recent"] == 2
    p2 = one(inv, product_id="P2")
    assert p2["replenishment_ratio"] == 0.0 and p2["days_since_last_purchase"] is None
    assert p2["days_since_last_sale"] == 15


def test_movement_table_covers_every_stock_event(spark, tables):
    assert spark.table("movements").count() == 10                 # 11 events minus the price observation
