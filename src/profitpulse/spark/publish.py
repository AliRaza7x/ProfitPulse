"""Spark job: analytics outputs (Parquet) -> PostgreSQL analytics schema.

Every table is staged first, then all targets are replaced inside ONE transaction,
so dashboards never see a half-refreshed set of tables.
"""
from __future__ import annotations

import json
import logging
import sys

from ..db import fetch_all, merge_from_staging, tracked_run, transaction
from ..settings import get_settings
from .load import to_staging
from .session import get_lake, get_spark

log = logging.getLogger("profitpulse.publish")

TABLES = [
    "company_monthly", "branch_monthly", "supplier_monthly", "inventory_movement_monthly",
    "inventory_branch_category", "branch_scorecard", "product_scorecard", "supplier_scorecard",
    "inventory_risk", "anomalies", "revenue_leakage", "kpi_summary",
]


def _target_columns(table: str) -> list[str]:
    rows = fetch_all(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='analytics' AND table_name=%s "
        "ORDER BY ordinal_position", (table,))
    return [r["column_name"] for r in rows]


def run() -> dict:
    s = get_settings()
    lake = get_lake(s)
    with tracked_run("publish_analytics") as rh:
        spark = get_spark("profitpulse-publish", s)
        staged, expected, dropped = {}, {}, {}
        for t in TABLES:
            df = spark.read.parquet(lake.analytics(t))
            wanted = _target_columns(t)
            extra = [c for c in df.columns if c not in wanted]
            if extra:
                dropped[t] = extra
                df = df.select(*[c for c in df.columns if c in wanted])
            expected[t] = df.count()
            staged[t] = to_staging(df, f"analytics_{t}", s)
        with transaction(s) as conn:
            for t in TABLES:
                merge_from_staging(conn, f"analytics.{t}", staged[t], "replace")
        stored = {t: fetch_all(f"SELECT COUNT(*) AS n FROM analytics.{t}")[0]["n"] for t in TABLES}
        bad = {t: (expected[t], stored[t]) for t in TABLES if expected[t] != stored[t]}
        rh.rows_in, rh.rows_out = sum(expected.values()), sum(stored.values())
        rh.details = {"stored": stored, "columns_not_published": dropped}
        if bad:
            raise RuntimeError(f"Analytics publish verification failed (expected, stored): {bad}")
        return stored


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    print(json.dumps(run()))
    sys.exit(0)
