"""analytics_pipeline: warehouse -> KPIs, scorecards, anomalies, leakage -> BI tables -> report snapshot.

    build_features (Spark) -> detect_and_score (explainable stats) -> publish_analytics
                                                                          -> render_report_snapshot

Triggered when the warehouse load succeeds (WAREHOUSE asset). The last step asks the
web service to render the PDF/XLSX business report into the shared reports volume,
which is the "scheduled reporting" output.
"""
from __future__ import annotations

import os
import urllib.request
from datetime import datetime

from _common import ANALYTICS, DEFAULT_ARGS, WAREHOUSE, stage
from airflow.sdk import DAG, task

with DAG(
    dag_id="analytics_pipeline",
    description="Spark features, anomaly detection with explanations, scorecards, leakage, report snapshot",
    start_date=datetime(2026, 1, 1),
    schedule=[WAREHOUSE],
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["profitpulse", "spark", "analytics"],
) as dag:
    features = stage("build_features", "features")
    detect = stage("detect_and_score", "analytics")
    publish = stage("publish_analytics", "publish-analytics", outlets=[ANALYTICS])

    @task(retries=3)
    def render_report_snapshot() -> dict:
        """Ask the web service to render and archive the current PDF + XLSX report."""
        url = os.environ.get("PP_WEB_INTERNAL_URL", "http://web:8000") + "/api/reports/snapshot"
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:           # noqa: S310 (internal service)
            return {"status": resp.status, "body": resp.read().decode()[:500]}

    features >> detect >> publish >> render_report_snapshot()
