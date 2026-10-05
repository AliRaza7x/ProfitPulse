"""ingestion_pipeline: source events -> Kafka -> raw zone.

    check_infrastructure -> decide_publish -+-> replay_source_events -+-> kafka_to_raw -> reconcile_ingest
                                            +-> skip_replay ---------+

Replaying the CSV stands in for an external event source; set `publish_source`
to false when events already arrive in Kafka some other way. The raw landing is
incremental: only offsets not yet committed in ops.kafka_offsets are read.
Success of `kafka_to_raw` triggers transformation_pipeline (via the RAW_EVENTS asset).
"""
from __future__ import annotations

from datetime import datetime

from _common import DEFAULT_ARGS, RAW_EVENTS, stage
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG, Param, task

with DAG(
    dag_id="ingestion_pipeline",
    description="CSV replay -> Kafka -> raw Parquet zone, with offset tracking and reconciliation",
    start_date=datetime(2026, 1, 1),
    schedule=None,                      # triggered manually / by an external scheduler
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["profitpulse", "ingestion"],
    params={
        "publish_source": Param(True, type="boolean", description="Replay the source CSV into Kafka first"),
        "rate": Param(0, type="number", minimum=0, description="Events per second; 0 = batch replay"),
        "limit": Param(0, type="integer", minimum=0, description="Publish only the first N events; 0 = all"),
        "corrupt_rate": Param(0.0, type="number", minimum=0, maximum=1,
                              description="Extra faulty messages per event (quarantine demonstration)"),
        "faults_only": Param(False, type="boolean",
                             description="Publish only the injected faults (bad data arriving later); needs corrupt_rate > 0"),
    },
) as dag:

    @task
    def check_infrastructure() -> dict:
        """Fail early, with a readable message, if Kafka or PostgreSQL is unreachable or unmigrated."""
        from profitpulse.db import fetch_all
        from profitpulse.kafka.admin import ensure_topics, wait_for_broker

        wait_for_broker(timeout_s=30)
        topics = ensure_topics()
        tables = fetch_all("SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema IN ('core','ops','quarantine','analytics')")
        if tables[0]["n"] < 10:
            raise RuntimeError("Database schema is missing: run `python -m profitpulse migrate` first.")
        return {"topics": topics}

    @task.branch
    def decide_publish(**context) -> str:
        return "replay_source_events" if context["params"]["publish_source"] else "skip_replay"

    replay = stage(
        "replay_source_events",
        "publish --record-run --rate {{ params.rate }} "
        "{% if params.limit %}--limit {{ params.limit }}{% endif %} "
        "--corrupt-rate {{ params.corrupt_rate }} {% if params.faults_only %}--faults-only{% endif %}",
    )
    skip = EmptyOperator(task_id="skip_replay")
    to_raw = stage("kafka_to_raw", "ingest", trigger_rule="none_failed_min_one_success", outlets=[RAW_EVENTS])

    @task
    def reconcile_ingest() -> dict:
        """Every message Kafka holds below the committed offsets must be in a registered raw batch."""
        from profitpulse.db import fetch_all
        from profitpulse.kafka.admin import watermarks

        committed = {(r["topic"], r["partition_id"]): r["next_offset"] for r in fetch_all("SELECT * FROM ops.kafka_offsets")}
        landed = fetch_all("SELECT COALESCE(SUM(messages),0) AS n FROM ops.ingest_batches")[0]["n"]
        lag = sum(max(w.high - committed.get((w.topic, w.partition), w.low), 0) for w in watermarks())
        expected = sum(committed.get((w.topic, w.partition), w.low) - w.low for w in watermarks())
        if landed != expected:
            raise RuntimeError(f"Raw zone holds {landed} messages but committed offsets imply {expected}")
        return {"messages_in_raw": landed, "unconsumed_lag": lag}

    check = check_infrastructure()
    decide = decide_publish()
    check >> decide >> [replay, skip] >> to_raw >> reconcile_ingest()
