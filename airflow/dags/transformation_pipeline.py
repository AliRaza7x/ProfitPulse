"""transformation_pipeline: raw zone -> validated events -> dimensions/facts -> PostgreSQL core.

    validate_and_clean -> build_dimensions_and_facts -> load_warehouse

Triggered whenever ingestion lands new raw data (RAW_EVENTS asset). Rejected
messages are never dropped: they go to quarantine.dq_failures with the raw payload.
A successful load updates the WAREHOUSE asset, which starts analytics_pipeline
and data_quality_pipeline.
"""
from __future__ import annotations

from datetime import datetime

from _common import DEFAULT_ARGS, RAW_EVENTS, WAREHOUSE, stage
from airflow.sdk import DAG

with DAG(
    dag_id="transformation_pipeline",
    description="Spark: validate/clean (quarantine rejects), build star schema, load PostgreSQL",
    start_date=datetime(2026, 1, 1),
    schedule=[RAW_EVENTS],
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["profitpulse", "spark", "transformation"],
) as dag:
    clean = stage("validate_and_clean", "clean")
    transform = stage("build_dimensions_and_facts", "transform")
    load = stage("load_warehouse", "load", outlets=[WAREHOUSE])
    clean >> transform >> load
