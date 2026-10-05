"""Spark job: raw zone -> cleaned zone (validation + quarantine).

Recomputes over every registered raw batch, so results are deterministic and the
duplicate-detection rule sees the complete history. Outputs:

  lake/cleaned/events     accepted, typed events (including rows with warnings)
  lake/cleaned/failures   one row per (message, violated rule)
  quarantine.dq_failures  the same failure log, in PostgreSQL (replaced each run)
  ops.dq_results          per-rule counts for this run

Invariant checked at the end: raw rows == accepted rows + rejected rows.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from ..config import rules_config
from ..db import merge_from_staging, record_dq_result, tracked_run, transaction
from ..settings import get_settings
from ..validation.rules import evaluate, failure_log, parse_raw
from .ingest import registered_batches
from .load import to_staging
from .session import get_lake, get_spark

log = logging.getLogger("profitpulse.clean")

ACCEPTED_COLUMNS = [
    "event_id", "event_ts", "event_type", "branch_id", "city", "product_id", "category", "supplier_id",
    "quantity", "unit_cost", "unit_price", "discount_pct", "revenue", "purchase_cost", "profit", "event_value",
    "return_reason", "kafka_topic", "kafka_partition", "kafka_offset", "kafka_timestamp", "has_warning",
]


def accepted_events(evaluated: DataFrame) -> DataFrame:
    """Typed projection of the rows that passed every REJECT rule."""
    money = lambda c: F.col(f"t_{c}").cast("decimal(16,2)").alias(c)  # noqa: E731
    return (evaluated.filter(~F.col("is_rejected"))
            .select(
                "event_id",
                F.col("t_event_ts").alias("event_ts"),
                "event_type", "branch_id", "city", "product_id", "category", "supplier_id",
                F.col("t_quantity").alias("quantity"),
                money("unit_cost"), money("unit_price"),
                F.col("t_discount_pct").cast("decimal(7,4)").alias("discount_pct"),
                money("revenue"), money("purchase_cost"), money("profit"), money("event_value"),
                "return_reason",
                F.col("topic").alias("kafka_topic"), "kafka_partition", "kafka_offset", "kafka_timestamp",
                "has_warning"))


def run() -> dict:
    s = get_settings()
    cfg = rules_config()
    lake = get_lake(s)
    with tracked_run("clean") as rh:
        batches = registered_batches()
        if not batches:
            raise RuntimeError("No ingested batches found: run the ingest step first.")
        spark = get_spark("profitpulse-clean", s)
        raw = spark.read.parquet(*[lake.raw(b) for b in batches])
        evaluated = evaluate(parse_raw(raw), cfg, datetime.utcnow()).cache()

        accepted = accepted_events(evaluated)
        accepted.write.mode("overwrite").parquet(lake.cleaned("events"))
        failures = failure_log(evaluated, cfg).withColumn("run_id", F.lit(rh.run_id))
        failures.write.mode("overwrite").parquet(lake.cleaned("failures"))

        total = evaluated.count()
        rejected = evaluated.filter("is_rejected").count()
        warned = evaluated.filter("has_warning AND NOT is_rejected").count()
        accepted_n = spark.read.parquet(lake.cleaned("events")).count()
        by_rule = {r["rule_code"]: (r["severity"], r["n"]) for r in
                   failures.groupBy("rule_code", "severity").agg(F.count("*").alias("n")).collect()}

        with transaction(s) as conn:
            staged = to_staging(failures.select(
                "run_id", "event_id", "rule_code", "severity", "disposition", "detail", "raw_payload",
                "kafka_topic", "kafka_partition", "kafka_offset"), "dq_failures", s)
            merge_from_staging(conn, "quarantine.dq_failures", staged, "replace")
            for rule, severity in cfg["severity"].items():
                sev, n = by_rule.get(rule, (severity, 0))
                status = "PASS" if n == 0 else ("FAIL" if sev == "REJECT" else "WARN")
                record_dq_result(conn, rh.run_id, "clean", rule, "WARNING", status, observed=n, expected=0,
                                 message=f"{n} message(s) {'rejected' if sev == 'REJECT' else 'flagged'} by {rule}")
            balanced = (total == accepted_n + rejected)
            record_dq_result(conn, rh.run_id, "clean", "RAW_EQUALS_ACCEPTED_PLUS_REJECTED", "CRITICAL",
                             "PASS" if balanced else "FAIL", observed=total, expected=accepted_n + rejected,
                             message=f"raw={total} accepted={accepted_n} rejected={rejected}")
        if not balanced:
            raise RuntimeError(f"Row accounting broke: raw={total}, accepted={accepted_n}, rejected={rejected}")

        rh.rows_in, rh.rows_out, rh.rows_rejected = total, accepted_n, rejected
        rh.details = {"batches": len(batches), "warnings_only": warned,
                      "rejections_by_rule": {k: v[1] for k, v in by_rule.items() if v[0] == "REJECT"},
                      "warnings_by_rule": {k: v[1] for k, v in by_rule.items() if v[0] != "REJECT"}}
        log.info("clean: raw=%d accepted=%d rejected=%d warned=%d", total, accepted_n, rejected, warned)
        return {"raw": total, "accepted": accepted_n, "rejected": rejected, "warned": warned}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    print(json.dumps(run()))
    sys.exit(0)
