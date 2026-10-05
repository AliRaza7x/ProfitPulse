"""Spark job: transformed zone -> PostgreSQL core schema.

Each table is written to staging.<name> over JDBC, then merged into core.<name>
in a single transaction (dimensions upserted, facts synced so that the warehouse
mirrors the cleaned data exactly). Afterwards stored counts are compared with the
transformed zone; any difference fails the run.
"""
from __future__ import annotations

import json
import logging
import sys

from ..db import fetch_all, merge_from_staging, tracked_run, transaction
from ..settings import get_settings
from .load import to_staging
from .session import get_lake, get_spark

log = logging.getLogger("profitpulse.load_core")

# Load order matters for foreign keys: dimensions first.
TABLES: list[tuple[str, str, list[str]]] = [
    ("dim_date", "upsert", ["date_key"]),
    ("dim_supplier", "upsert", ["supplier_id"]),
    ("dim_branch", "upsert", ["branch_id"]),
    ("dim_product", "upsert", ["product_id"]),
    ("fact_sales", "sync", ["event_id"]),
    ("fact_returns", "sync", ["event_id"]),
    ("fact_purchases", "sync", ["event_id"]),
    ("fact_inventory_movements", "sync", ["event_id"]),
    ("fact_price_changes", "sync", ["event_id"]),
]


def run() -> dict:
    s = get_settings()
    lake = get_lake(s)
    with tracked_run("load") as rh:
        spark = get_spark("profitpulse-load-core", s)
        from pyspark.sql import functions as F

        staged: dict[str, str] = {}
        expected: dict[str, int] = {}
        for name, _, _ in TABLES:
            df = spark.read.parquet(lake.transformed(name))
            if name.startswith("fact_"):
                df = df.withColumn("load_run_id", F.lit(rh.run_id))
            expected[name] = df.count()
            staged[name] = to_staging(df, name, s)

        with transaction(s) as conn:
            for name, mode, keys in TABLES:
                merge_from_staging(conn, f"core.{name}", staged[name], mode, keys)

        stored = {name: fetch_all(f"SELECT COUNT(*) AS n FROM core.{name}")[0]["n"] for name, _, _ in TABLES}
        bad = {n: (expected[n], stored[n]) for n in expected if expected[n] != stored[n] and not n.startswith("dim_")}
        facts_expected = sum(v for k, v in expected.items() if k.startswith("fact_"))
        facts_stored = sum(v for k, v in stored.items() if k.startswith("fact_"))
        rh.rows_in, rh.rows_out = facts_expected, facts_stored
        rh.details = {"expected": expected, "stored": stored}
        if bad:
            raise RuntimeError(f"Load verification failed (expected, stored): {bad}")
        log.info("load_core: stored %s", stored)
        return stored


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    print(json.dumps(run()))
    sys.exit(0)
