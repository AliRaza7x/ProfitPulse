"""Post-load data-quality gates (the `data_quality_pipeline`).

These checks look at the *results* of the pipeline, independently of the code that
produced them: Kafka vs raw, raw vs cleaned+quarantined, cleaned vs warehouse,
warehouse vs analytics, plus integrity and financial identities. Each outcome is
written to ops.dq_results; any CRITICAL failure raises so the Airflow task fails.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..db import fetch_all, record_dq_result, transaction
from ..settings import get_settings

log = logging.getLogger("profitpulse.quality")

FACT_TABLES = ["fact_sales", "fact_returns", "fact_purchases", "fact_inventory_movements", "fact_price_changes"]


@dataclass
class Result:
    layer: str
    name: str
    severity: str          # CRITICAL | WARNING | INFO
    status: str            # PASS | FAIL | WARN
    observed: float | None = None
    expected: float | None = None
    message: str = ""


def _one(sql: str, params=None):
    return fetch_all(sql, params)[0]


def _latest_run(stage: str) -> dict | None:
    rows = fetch_all("SELECT * FROM ops.pipeline_runs WHERE stage=%s AND status='SUCCESS' ORDER BY started_at DESC LIMIT 1", (stage,))
    return rows[0] if rows else None


def _compare(layer, name, observed, expected, severity="CRITICAL", tol=0.0, msg="") -> Result:
    ok = abs((observed or 0) - (expected or 0)) <= tol
    return Result(layer, name, severity, "PASS" if ok else ("FAIL" if severity == "CRITICAL" else "WARN"),
                  float(observed or 0), float(expected or 0), msg or f"observed {observed} vs expected {expected}")


def warehouse_checks() -> list[Result]:
    out: list[Result] = []
    s = get_settings()
    layer = "warehouse"

    # 1. Kafka -> raw: every committed offset range is in the registry, nothing missing.
    row = _one("SELECT COALESCE(SUM(messages),0) AS landed FROM ops.ingest_batches")
    off = _one("SELECT COALESCE(SUM(next_offset),0) AS committed FROM ops.kafka_offsets")
    out.append(_compare(layer, "KAFKA_OFFSETS_EQUAL_RAW_MESSAGES", row["landed"], off["committed"],
                        msg=f"raw messages landed={row['landed']}, offsets committed={off['committed']}"))

    # 2. Raw accounting: raw = accepted + rejected
    clean = _latest_run("clean")
    if clean:
        out.append(_compare(layer, "RAW_EQUALS_CLEAN_PLUS_REJECTED", clean["rows_in"],
                            (clean["rows_out"] or 0) + (clean["rows_rejected"] or 0)))
        out.append(_compare(layer, "RAW_MESSAGES_EQUAL_KAFKA", row["landed"], clean["rows_in"],
                            msg="messages processed by the clean stage equal messages landed from Kafka"))
        rej = _one("SELECT COUNT(DISTINCT (kafka_topic, kafka_partition, kafka_offset)) AS n FROM quarantine.dq_failures WHERE disposition='REJECTED'")
        out.append(_compare(layer, "QUARANTINE_HOLDS_EVERY_REJECT", rej["n"], clean["rows_rejected"],
                            msg="every rejected message is stored verbatim in quarantine.dq_failures"))
    else:
        out.append(Result(layer, "CLEAN_RUN_EXISTS", "CRITICAL", "FAIL", message="no successful clean run recorded"))

    # 3. Warehouse mirrors the cleaned data
    facts = sum(_one(f"SELECT COUNT(*) AS n FROM core.{t}")["n"] for t in FACT_TABLES)
    if clean:
        out.append(_compare(layer, "WAREHOUSE_FACTS_EQUAL_ACCEPTED_EVENTS", facts, clean["rows_out"],
                            msg=f"fact rows={facts}, accepted events={clean['rows_out']}"))

    # 4. An event appears in exactly one fact table
    dup = _one("SELECT COUNT(*) AS n FROM (SELECT event_id FROM (" + " UNION ALL ".join(
        f"SELECT event_id FROM core.{t}" for t in FACT_TABLES) + ") u GROUP BY event_id HAVING COUNT(*) > 1) d")
    out.append(_compare(layer, "EVENT_IN_ONE_FACT_ONLY", dup["n"], 0))

    # 5. Referential integrity (constraints enforce it; this proves nothing was disabled)
    orphans = 0
    for t in FACT_TABLES:
        orphans += _one(f"SELECT COUNT(*) AS n FROM core.{t} f LEFT JOIN core.dim_branch b ON b.branch_id=f.branch_id "
                        f"LEFT JOIN core.dim_product p ON p.product_id=f.product_id WHERE b.branch_id IS NULL OR p.product_id IS NULL")["n"]
    out.append(_compare(layer, "NO_ORPHAN_FACTS", orphans, 0))

    # 6. Financial identities in the warehouse
    gp = _one("SELECT COALESCE(MAX(ABS(net_revenue - cogs - gross_profit)),0) AS d FROM core.fact_sales")
    out.append(_compare(layer, "SALES_PROFIT_IDENTITY", float(gp["d"]), 0.0, tol=0.02,
                        msg="max |net_revenue - cogs - gross_profit| per sale"))
    neg = _one("SELECT COUNT(*) AS n FROM core.fact_sales WHERE net_revenue <= 0 OR quantity <= 0")
    out.append(_compare(layer, "SALES_ARE_POSITIVE", neg["n"], 0))

    # 7. Independent revenue checksum: warehouse vs the cleaned Parquet zone
    try:
        import pyarrow.dataset as ds
        table = ds.dataset(str(s.lake_dir / "cleaned" / "events"), format="parquet").to_table(
            columns=["event_type", "revenue", "profit"])
        df = table.to_pandas()
        sales = df[df["event_type"] == "SALE"]
        lake_rev, lake_profit = float(sales["revenue"].astype(float).sum()), float(sales["profit"].astype(float).sum())
        wh = _one("SELECT COALESCE(SUM(net_revenue),0) AS r, COALESCE(SUM(gross_profit),0) AS p FROM core.fact_sales")
        out.append(_compare(layer, "SALES_REVENUE_CHECKSUM_LAKE_VS_WAREHOUSE", float(wh["r"]), lake_rev, tol=0.5,
                            msg=f"warehouse={wh['r']} lake={lake_rev:.2f}"))
        out.append(_compare(layer, "SALES_PROFIT_CHECKSUM_LAKE_VS_WAREHOUSE", float(wh["p"]), lake_profit, tol=0.5))
    except Exception as exc:  # lake not mounted (e.g. checks run elsewhere)
        out.append(Result(layer, "SALES_REVENUE_CHECKSUM_LAKE_VS_WAREHOUSE", "WARNING", "WARN", message=f"skipped: {exc}"))

    # 8. Freshness (informational)
    for stage in ("ingest", "clean", "transform", "load"):
        run = _latest_run(stage)
        stale = (run is None) or (datetime.now(timezone.utc) - run["finished_at"] > timedelta(days=2))
        out.append(Result(layer, f"FRESH_{stage.upper()}_RUN", "INFO", "WARN" if stale else "PASS",
                          message=f"last successful {stage}: {run['finished_at'] if run else 'never'}"))
    return out


def analytics_checks() -> list[Result]:
    layer = "analytics"
    out: list[Result] = []
    core = _one("SELECT COALESCE(SUM(net_revenue),0) AS r, COALESCE(SUM(gross_profit),0) AS p, COUNT(*) AS n FROM core.fact_sales")
    kpi = {r["kpi_key"]: r["value_numeric"] for r in fetch_all("SELECT kpi_key, value_numeric FROM analytics.kpi_summary")}
    out.append(_compare(layer, "KPI_REVENUE_EQUALS_WAREHOUSE", kpi.get("net_revenue"), float(core["r"]), tol=1.0))
    out.append(_compare(layer, "KPI_PROFIT_EQUALS_WAREHOUSE", kpi.get("gross_profit"), float(core["p"]), tol=1.0))
    monthly = _one("SELECT COALESCE(SUM(net_revenue),0) AS r FROM analytics.company_monthly")
    out.append(_compare(layer, "MONTHLY_SERIES_EQUALS_WAREHOUSE", float(monthly["r"]), float(core["r"]), tol=1.0))
    branch = _one("SELECT COALESCE(SUM(net_revenue),0) AS r FROM analytics.branch_scorecard")
    out.append(_compare(layer, "BRANCH_SCORECARD_EQUALS_WAREHOUSE", float(branch["r"]), float(core["r"]), tol=1.0))
    unexplained = _one("SELECT COUNT(*) AS n FROM analytics.anomalies WHERE explanation IS NULL OR length(explanation) < 40")["n"]
    out.append(_compare(layer, "EVERY_ANOMALY_HAS_AN_EXPLANATION", unexplained, 0))
    nulls = _one("SELECT COUNT(*) AS n FROM analytics.branch_scorecard WHERE performance_score IS NULL")["n"]
    out.append(_compare(layer, "BRANCH_SCORES_COMPLETE", nulls, 0))
    leak = _one("SELECT COALESCE(SUM(exposure_amount),0) AS e FROM analytics.revenue_leakage")["e"]
    out.append(Result(layer, "LEAKAGE_EXPOSURE_RECORDED", "INFO", "PASS", float(leak), None,
                      f"identified exposure {float(leak):,.0f}"))
    return out


def persist(results: list[Result], run_id: str | None = None) -> None:
    with transaction() as conn:
        for r in results:
            record_dq_result(conn, run_id, r.layer, r.name, r.severity, r.status, r.observed, r.expected, r.message)


def run(scope: str = "all") -> dict:
    from ..db import tracked_run
    with tracked_run("dq") as rh:
        results: list[Result] = []
        if scope in ("all", "warehouse"):
            results += warehouse_checks()
        if scope in ("all", "analytics"):
            results += analytics_checks()
        persist(results, rh.run_id)
        failed = [r for r in results if r.status == "FAIL" and r.severity == "CRITICAL"]
        rh.rows_in = len(results)
        rh.rows_out = sum(1 for r in results if r.status == "PASS")
        rh.rows_rejected = len(failed)
        rh.details = {"checks": len(results), "failed": [r.name for r in failed],
                      "warnings": [r.name for r in results if r.status == "WARN"]}
        for r in results:
            log.info("%-8s %-45s %s  %s", r.status, r.name, r.layer, r.message)
        if failed:
            raise RuntimeError("Critical data-quality checks failed: " + ", ".join(f"{r.name} ({r.message})" for r in failed))
        return {"checks": len(results), "passed": rh.rows_out, "warnings": len(rh.details["warnings"])}


if __name__ == "__main__":
    import json
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    print(json.dumps(run(sys.argv[1] if len(sys.argv) > 1 else "all")))
