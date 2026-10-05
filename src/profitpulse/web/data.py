"""Read-only data access for the web UI and the report builders.

Connects as the read-only role. One function per dashboard page; every function
returns plain JSON-serialisable structures so the UI, the PDF and the XLSX all
draw from exactly the same numbers.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg2
import psycopg2.extras
import psycopg2.pool

_pool: psycopg2.pool.SimpleConnectionPool | None = None


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.SimpleConnectionPool(
            1, 6,
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            dbname=os.environ.get("POSTGRES_DB", "profitpulse"),
            user=os.environ.get("PP_READER_USER", "pp_reader"),
            password=os.environ.get("PP_READER_PASSWORD", ""),
            options="-c default_transaction_read_only=on -c statement_timeout=20000",
        )
    return _pool


def _clean(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def q(sql: str, params: tuple | None = None) -> list[dict[str, Any]]:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [{k: _clean(v) for k, v in r.items()} for r in cur.fetchall()]
        conn.rollback()
        return rows
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def currency() -> str:
    return os.environ.get("PP_CURRENCY", "PKR")


# ---------------------------------------------------------------- shared pieces
def kpis() -> dict[str, dict[str, Any]]:
    rows = q("SELECT kpi_key, section, label, value_numeric, value_text, unit FROM analytics.kpi_summary ORDER BY sort_order")
    return {r["kpi_key"]: r for r in rows}


def ready() -> bool:
    return bool(q("SELECT 1 FROM analytics.kpi_summary LIMIT 1"))


def anomalies(entity_type: str | None = None, limit: int | None = None, leakage_only: bool = False) -> list[dict]:
    where, params = [], []
    if entity_type:
        where.append("entity_type = %s")
        params.append(entity_type)
    if leakage_only:
        where.append("leakage_type IS NOT NULL")
    sql = ("SELECT anomaly_id, detection_method, check_id, entity_type, entity_id, entity_label, metric, direction, "
           "observed_value, baseline_value, baseline_spread, robust_z, severity, sample_size, estimated_exposure, "
           "annualised_exposure, leakage_type, explanation, evidence FROM analytics.anomalies "
           + ("WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY CASE severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 ELSE 3 END, "
             "estimated_exposure DESC NULLS LAST" + (f" LIMIT {int(limit)}" if limit else ""))
    return q(sql, tuple(params))


def peer_strip(check_id: str, entity_id: str) -> dict[str, Any] | None:
    """All peers' values for the check that flagged `entity_id`: the data behind the peer-strip chart."""
    from ..config import analytics_config
    check = next((c for c in analytics_config()["anomaly"]["peer_checks"] if c["id"] == check_id), None)
    if check is None:
        return None
    table = {"branch": "branch_scorecard", "product": "product_scorecard", "supplier": "supplier_scorecard"}[check["entity"]]
    idc = f"{check['entity']}_id"
    metric = check["metric"]
    if metric not in {"discount_rate", "return_rate_units", "adjustment_avg_qty", "damage_rate", "gross_margin", "ppv_pct"}:
        return None
    rows = q(f"SELECT {idc} AS id, {metric} AS value, {check['n_col']} AS n FROM analytics.{table} "
             f"WHERE {metric} IS NOT NULL AND {check['n_col']} >= %s", (check["min_n"],))
    return {"entity": check["entity"], "metric": metric, "points": rows, "focus": entity_id}


# ---------------------------------------------------------------- pages
def overview() -> dict[str, Any]:
    k = kpis()
    top = anomalies(leakage_only=True, limit=4)
    for a in top:
        a["strip"] = peer_strip(a["check_id"], a["entity_id"]) if a["detection_method"] == "PEER" else None
    return {
        "currency": currency(),
        "kpis": k,
        "monthly": q("SELECT year_month, net_revenue, gross_profit, gross_margin, discount_rate, return_rate_units, "
                     "adjusted_gross_profit FROM analytics.company_monthly ORDER BY year_month"),
        "leakage_by_type": q("SELECT leakage_type, findings, exposure_amount, annualised_exposure "
                             "FROM analytics.v_leakage_by_type ORDER BY exposure_amount DESC"),
        "findings": top,
        "counts": {
            "anomalies": {r["severity"]: r["n"] for r in q("SELECT severity, COUNT(*) AS n FROM analytics.anomalies GROUP BY 1")},
            "by_page": nav_counts(),
        },
    }


def nav_counts() -> dict[str, int]:
    rows = q("""
        SELECT 'leakage' AS page, COUNT(*) AS n FROM analytics.revenue_leakage WHERE leakage_type <> 'LOW_MARGIN_SHORTFALL'
        UNION ALL SELECT 'inventory', COUNT(*) FROM analytics.inventory_risk WHERE stockout_risk_level = 'high' OR reorder_flag
        UNION ALL SELECT 'branches', COUNT(*) FROM analytics.anomalies WHERE entity_type = 'branch'
        UNION ALL SELECT 'suppliers', COUNT(*) FROM analytics.anomalies WHERE entity_type = 'supplier'
        UNION ALL SELECT 'products', COUNT(*) FROM analytics.product_scorecard WHERE is_problematic
        UNION ALL SELECT 'health', COUNT(*) FROM ops.dq_results WHERE status = 'FAIL'
            AND checked_at > now() - interval '30 days'""")
    return {r["page"]: r["n"] for r in rows}


def leakage() -> dict[str, Any]:
    return {
        "currency": currency(),
        "by_type": q("SELECT leakage_type, findings, exposure_amount, annualised_exposure FROM analytics.v_leakage_by_type "
                     "ORDER BY exposure_amount DESC"),
        "items": q("SELECT l.leakage_type, l.entity_type, l.entity_id, l.entity_label, l.exposure_amount, l.annualised_exposure, "
                   "l.basis, l.source_anomaly_id, a.severity, a.explanation, a.check_id, a.detection_method, a.metric, "
                   "a.baseline_value, a.observed_value, a.entity_type AS anomaly_entity_type "
                   "FROM analytics.revenue_leakage l LEFT JOIN analytics.anomalies a "
                   "ON a.anomaly_id = split_part(l.source_anomaly_id, ';', 1) ORDER BY l.exposure_amount DESC"),
        "kpis": {k: v for k, v in kpis().items() if v["section"] == "Leakage"},
    }


def inventory() -> dict[str, Any]:
    return {
        "currency": currency(),
        "kpis": {k: v for k, v in kpis().items() if v["section"] == "Inventory"},
        "movement": q("SELECT year_month, units_purchased, units_sold, units_returned, units_damaged, units_adjusted, "
                      "replenishment_ratio FROM analytics.inventory_movement_monthly ORDER BY year_month"),
        "risk_top": q("SELECT product_id, category, supplier_id, velocity_per_day, replenishment_ratio, days_since_last_purchase, "
                      "days_since_last_sale, stockout_risk_score, stockout_risk_level, reorder_flag, is_slow_moving, "
                      "is_dead_stock, explanation FROM analytics.inventory_risk ORDER BY stockout_risk_score DESC LIMIT 25"),
        "levels": q("SELECT stockout_risk_level AS level, COUNT(*) AS n FROM analytics.inventory_risk GROUP BY 1"),
        "slow": q("SELECT product_id, category, velocity_per_day, units_sold_recent, days_since_last_sale "
                  "FROM analytics.inventory_risk WHERE is_slow_moving ORDER BY velocity_per_day NULLS FIRST LIMIT 15"),
        "branch_category": q("SELECT branch_id, category, units_sold, units_purchased, replenishment_ratio, shrink_rate, "
                             "damage_units, adjustment_units, adjustment_value FROM analytics.inventory_branch_category"),
        "as_of": q("SELECT MAX(as_of_date) AS d FROM analytics.inventory_risk")[0]["d"],
    }


def branches() -> dict[str, Any]:
    return {
        "currency": currency(),
        "scorecards": q("SELECT * FROM analytics.branch_scorecard ORDER BY performance_rank"),
        "monthly": q("SELECT branch_id, year_month, net_revenue, gross_margin, discount_rate, return_rate_units "
                     "FROM analytics.branch_monthly ORDER BY branch_id, year_month"),
        "anomalies": anomalies("branch"),
        "weights": _branch_weights(),
    }


def _branch_weights() -> dict[str, Any]:
    from ..config import analytics_config
    cfg = analytics_config()["branch_score"]
    return {"components": cfg["components"], "bands": cfg["bands"]}


def suppliers() -> dict[str, Any]:
    return {
        "currency": currency(),
        "scorecards": q("SELECT * FROM analytics.supplier_scorecard ORDER BY ppv_amount DESC, purchase_spend DESC"),
        "monthly": q("SELECT supplier_id, year_month, ppv_pct, purchase_spend FROM analytics.supplier_monthly "
                     "WHERE supplier_id IN (SELECT supplier_id FROM analytics.supplier_scorecard WHERE anomaly_count > 0) "
                     "ORDER BY supplier_id, year_month"),
        "anomalies": anomalies("supplier"),
    }


def products() -> dict[str, Any]:
    cols = ("product_id, category, supplier_id, net_revenue, gross_margin, discount_rate, return_rate_units, growth_rate, "
            "sale_events, margin_percentile, problem_reasons, anomaly_count")
    seg = lambda where, order, n=12: q(f"SELECT {cols} FROM analytics.product_scorecard WHERE {where} ORDER BY {order} LIMIT {n}")  # noqa: E731
    return {
        "currency": currency(),
        "high_margin": seg("is_high_margin", "gross_margin DESC"),
        "low_margin": seg("is_low_margin", "gross_margin ASC"),
        "high_return": seg("is_high_return", "return_rate_units DESC"),
        "high_discount": seg("is_high_discount", "discount_rate DESC"),
        "declining": seg("is_declining", "growth_rate ASC"),
        "problematic": seg("is_problematic", "anomaly_count DESC, net_revenue DESC", 25),
        "category": q("SELECT category, COUNT(*) AS products, SUM(net_revenue) AS net_revenue, SUM(gross_profit) / NULLIF(SUM(net_revenue),0) AS gross_margin, "
                      "SUM(return_units)::float / NULLIF(SUM(units_sold),0) AS return_rate "
                      "FROM analytics.product_scorecard GROUP BY category ORDER BY net_revenue DESC"),
        "counts": q("SELECT COUNT(*) FILTER (WHERE is_high_margin) AS high_margin, COUNT(*) FILTER (WHERE is_low_margin) AS low_margin, "
                    "COUNT(*) FILTER (WHERE is_high_return) AS high_return, COUNT(*) FILTER (WHERE is_high_discount) AS high_discount, "
                    "COUNT(*) FILTER (WHERE is_declining) AS declining, COUNT(*) FILTER (WHERE is_problematic) AS problematic, "
                    "COUNT(*) AS total FROM analytics.product_scorecard")[0],
        "anomalies": anomalies("product"),
    }


def pipeline_health() -> dict[str, Any]:
    runs = q("SELECT stage, status, started_at, finished_at, duration_seconds, rows_in, rows_out, rows_rejected, error "
             "FROM ops.v_pipeline_health")
    order = ["publish", "ingest", "clean", "transform", "load", "features", "analytics", "publish_analytics", "dq"]
    runs.sort(key=lambda r: order.index(r["stage"]) if r["stage"] in order else 99)
    ingest = int(q("SELECT COALESCE(SUM(messages),0) AS n FROM ops.ingest_batches")[0]["n"])
    clean = q("SELECT rows_in, rows_out, rows_rejected FROM ops.pipeline_runs WHERE stage='clean' AND status='SUCCESS' "
              "ORDER BY started_at DESC LIMIT 1")
    facts = q("SELECT (SELECT COUNT(*) FROM core.fact_sales) + (SELECT COUNT(*) FROM core.fact_returns) + "
              "(SELECT COUNT(*) FROM core.fact_purchases) + (SELECT COUNT(*) FROM core.fact_inventory_movements) + "
              "(SELECT COUNT(*) FROM core.fact_price_changes) AS n")[0]["n"]
    published = int(q("SELECT COALESCE(SUM(rows_out),0) AS n FROM ops.pipeline_runs WHERE stage='publish' AND status='SUCCESS'")[0]["n"])
    checks = q("SELECT DISTINCT ON (layer, check_name) layer, check_name, severity, status, observed, expected, message, checked_at "
               "FROM ops.dq_results WHERE layer IN ('warehouse','analytics') ORDER BY layer, check_name, checked_at DESC")
    rules = q("SELECT rule_code, severity, disposition, failures FROM quarantine.v_failure_summary ORDER BY failures DESC")
    total_s = q("SELECT COALESCE(SUM(EXTRACT(EPOCH FROM (finished_at - started_at))),0) AS s FROM ops.v_pipeline_health "
                "WHERE stage IN ('ingest','clean','transform','load','features','analytics','publish_analytics')")[0]["s"]
    return {
        "runs": runs,
        "flow": {"published": published, "landed": ingest, "accepted": clean[0]["rows_out"] if clean else None,
                 "rejected": clean[0]["rows_rejected"] if clean else None, "stored": facts},
        "checks": checks, "rules": rules, "processing_seconds": total_s,
        "quarantine_samples": q("SELECT event_id, rule_code, detail, kafka_topic, disposition FROM quarantine.dq_failures "
                                "WHERE disposition = 'REJECTED' ORDER BY failure_id LIMIT 8"),
    }
