"""data_quality_pipeline: independent post-load gates over the pipeline's results.

Runs after BOTH the warehouse load and the analytics publish have completed
(schedule on two assets = both must have updated). It reconciles Kafka -> raw ->
cleaned/quarantined -> warehouse -> analytics, checks integrity and financial
identities, and records each result in ops.dq_results. A critical failure fails
the run, which is what the Pipeline Health page and any alerting hook on.
"""
from __future__ import annotations

from datetime import datetime

from _common import ANALYTICS, DEFAULT_ARGS, WAREHOUSE
from airflow.sdk import DAG, task

with DAG(
    dag_id="data_quality_pipeline",
    description="Row reconciliation, integrity and financial checksums across every layer",
    start_date=datetime(2026, 1, 1),
    schedule=[WAREHOUSE, ANALYTICS],
    catchup=False,
    max_active_runs=1,
    default_args={**DEFAULT_ARGS, "retries": 0},
    tags=["profitpulse", "data-quality"],
) as dag:

    @task
    def warehouse_checks() -> dict:
        """Kafka -> raw -> cleaned -> warehouse reconciliation, integrity, financial checksums."""
        from profitpulse.quality import checks
        return checks.run("warehouse")        # records the run, stores every result, raises on a critical failure

    @task
    def analytics_checks() -> dict:
        """Analytics totals must equal the warehouse; every anomaly must carry an explanation."""
        from profitpulse.quality import checks
        return checks.run("analytics")

    warehouse_checks() >> analytics_checks()
