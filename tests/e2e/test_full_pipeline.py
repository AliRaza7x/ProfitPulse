"""End-to-end acceptance: the warehouse and analytics must agree with the raw CSV.

Run after a complete pipeline run on the unmodified 300k dataset (no fault injection):
    scripts/run_stages.sh   (or the Airflow DAGs)   then   pytest -m e2e

Expected values are recomputed from the CSV with pandas here, independently of the
pipeline. Nothing about specific branches/suppliers is hard-coded: the test finds the
statistical outliers itself and checks the detector agrees.
"""
from __future__ import annotations

import pandas as pd
import pytest

from profitpulse.db import fetch_all
from profitpulse.settings import get_settings

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def csv():
    path = get_settings().data_dir / "source" / "profitpulse_synthetic_dataset.csv"
    if not path.exists():
        pytest.skip("source CSV not present")
    return pd.read_csv(path, parse_dates=["event_timestamp"])


@pytest.fixture(scope="module", autouse=True)
def pipeline_has_run(db):
    n = fetch_all("SELECT COUNT(*) AS n FROM core.fact_sales")[0]["n"]
    if n == 0:
        pytest.skip("pipeline has not been run")


def scalar(sql, *params):
    return list(fetch_all(sql, params)[0].values())[0]


FACTS = ["fact_sales", "fact_returns", "fact_purchases", "fact_inventory_movements", "fact_price_changes"]


@pytest.fixture(scope="module")
def stored_ids():
    sql = " UNION ALL ".join(f"SELECT event_id FROM core.{t}" for t in FACTS)
    return {r["event_id"] for r in fetch_all(sql)}


@pytest.fixture(scope="module")
def rejected_ids():
    return {r["event_id"] for r in fetch_all("SELECT DISTINCT event_id FROM quarantine.dq_failures "
                                             "WHERE disposition='REJECTED' AND event_id IS NOT NULL")}


def test_every_source_event_is_stored_or_quarantined_and_nothing_is_invented(csv, stored_ids, rejected_ids):
    """No silent loss, no phantom rows. (Injected fault messages that duplicate a source id are
    rejected as duplicates while the original stays stored, so a source id can be in both sets.)"""
    source_ids = set(csv.event_id)
    assert len(csv) == 300_000 and len(source_ids) == 300_000
    assert source_ids - stored_ids <= rejected_ids, "source events vanished without a quarantine record"
    assert stored_ids <= source_ids, "the warehouse holds events that are not in the source"
    assert len(stored_ids) == scalar("SELECT COUNT(*) FROM core.fact_sales") + sum(
        scalar(f"SELECT COUNT(*) FROM core.{t}") for t in FACTS[1:])        # each event in exactly one fact row


def test_clean_stage_accounting_identity_holds():
    clean = fetch_all("SELECT rows_in, rows_out, rows_rejected FROM ops.pipeline_runs WHERE stage='clean' "
                      "AND status='SUCCESS' ORDER BY started_at DESC LIMIT 1")[0]
    assert clean["rows_in"] == clean["rows_out"] + clean["rows_rejected"]
    assert clean["rows_out"] == sum(scalar(f"SELECT COUNT(*) FROM core.{t}") for t in FACTS)


@pytest.mark.parametrize("event_type,table", [("SALE", "fact_sales"), ("RETURN", "fact_returns"),
                                              ("PURCHASE", "fact_purchases"), ("PRICE_CHANGE", "fact_price_changes")])
def test_row_counts_by_event_type(csv, stored_ids, event_type, table):
    expected = int(csv[csv.event_type == event_type].event_id.isin(stored_ids).sum())
    assert scalar(f"SELECT COUNT(*) FROM core.{table}") == expected


def test_sales_revenue_profit_and_units_match_csv_to_the_cent(csv, stored_ids):
    sales = csv[(csv.event_type == "SALE") & csv.event_id.isin(stored_ids)]
    row = fetch_all("SELECT SUM(net_revenue) AS r, SUM(gross_profit) AS p, SUM(quantity) AS q FROM core.fact_sales")[0]
    assert float(row["r"]) == pytest.approx(round(sales.revenue.sum(), 2), abs=0.01)
    assert float(row["p"]) == pytest.approx(round(sales.profit.sum(), 2), abs=0.01)
    assert int(row["q"]) == int(sales.quantity.sum())


def test_only_sales_count_as_revenue(csv):
    """The source's `revenue` column is populated on every event type; summing it naively overstates sales by ~49%."""
    naive = csv.revenue.sum()
    kpi = fetch_all("SELECT value_numeric FROM analytics.kpi_summary WHERE kpi_key='net_revenue'")[0]["value_numeric"]
    assert kpi < naive * 0.70
    assert kpi == pytest.approx(float(scalar("SELECT SUM(net_revenue) FROM core.fact_sales")), abs=1.0)


def test_per_branch_revenue_matches_csv(csv, stored_ids):
    expected = csv[(csv.event_type == "SALE") & csv.event_id.isin(stored_ids)].groupby("branch_id").revenue.sum().round(2)
    got = {r["branch_id"]: float(r["net_revenue"]) for r in fetch_all("SELECT branch_id, net_revenue FROM analytics.branch_scorecard")}
    assert set(got) == set(expected.index)
    for branch, value in expected.items():
        assert got[branch] == pytest.approx(value, abs=0.01)


# ---- the detector must agree with an independent statistical look at the data ----
def _robust_z(s: pd.Series) -> pd.Series:
    med = s.median()
    mad = (s - med).abs().median()
    return (s - med) / max(1.4826 * mad, 1e-9)


def test_the_most_discounting_branch_is_flagged_without_being_told(csv):
    sales = csv[csv.event_type == "SALE"].assign(gross=lambda d: d.quantity * d.unit_price)
    g = sales.groupby("branch_id").agg(gross=("gross", "sum"), net=("revenue", "sum"))
    rate = 1 - g.net / g.gross
    outlier = _robust_z(rate).idxmax()
    flagged = {r["entity_id"] for r in fetch_all("SELECT entity_id FROM analytics.anomalies WHERE check_id='branch_discount_rate'")}
    assert flagged == {outlier}


def test_the_largest_adjustment_branch_is_flagged_without_being_told(csv):
    adj = csv[csv.event_type == "STOCK_ADJUSTMENT"].groupby("branch_id").quantity.mean()
    outlier = _robust_z(adj).idxmax()
    flagged = {r["entity_id"] for r in fetch_all("SELECT entity_id FROM analytics.anomalies WHERE check_id='branch_adjustment_qty'")}
    assert flagged == {outlier}


def test_supplier_overpayment_matches_an_independent_calculation(csv):
    std = csv[csv.event_type != "PURCHASE"].groupby("product_id").unit_cost.median()
    p = csv[csv.event_type == "PURCHASE"].copy()
    p["over"] = (p.unit_cost - p.product_id.map(std)) * p.quantity
    by_supplier = p.groupby("supplier_id").over.sum()
    worst = by_supplier.idxmax()
    rows = fetch_all("SELECT entity_id, estimated_exposure FROM analytics.anomalies WHERE check_id='supplier_price_variance'")
    assert [r["entity_id"] for r in rows] == [worst]
    assert float(rows[0]["estimated_exposure"]) == pytest.approx(by_supplier[worst], rel=0.001)


def test_a_normal_product_is_not_flagged_for_returns(csv):
    """Guard against false positives: no product-level return anomaly may be a statistically ordinary product."""
    flagged = fetch_all("SELECT entity_id, robust_z FROM analytics.anomalies WHERE check_id='product_return_rate'")
    assert all(float(r["robust_z"]) >= 4.5 for r in flagged)
    assert len(flagged) <= 2                                # ~1000 products: chance findings must stay near zero


def test_every_anomaly_has_a_plain_english_explanation():
    rows = fetch_all("SELECT explanation FROM analytics.anomalies")
    assert rows and all(len(r["explanation"]) > 60 and "median" in r["explanation"] or "history" in r["explanation"] for r in rows)


def test_quarantine_lost_nothing():
    clean = fetch_all("SELECT rows_rejected FROM ops.pipeline_runs WHERE stage='clean' AND status='SUCCESS' "
                      "ORDER BY started_at DESC LIMIT 1")[0]["rows_rejected"]
    stored = scalar("SELECT COUNT(DISTINCT (kafka_topic, kafka_partition, kafka_offset)) FROM quarantine.dq_failures "
                    "WHERE disposition='REJECTED'")
    assert stored == clean


def test_all_data_quality_gates_passed():
    failures = fetch_all("SELECT check_name, message FROM ops.dq_results WHERE status='FAIL' AND severity='CRITICAL' "
                         "AND checked_at = (SELECT MAX(checked_at) FROM ops.dq_results r2 WHERE r2.check_name = ops.dq_results.check_name)")
    assert failures == []
