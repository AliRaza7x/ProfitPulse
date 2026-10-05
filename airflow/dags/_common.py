"""Shared building blocks for the ProfitPulse DAGs."""
from __future__ import annotations

from datetime import timedelta

from airflow.sdk import Asset
from airflow.providers.standard.operators.bash import BashOperator

# Data assets connect the DAGs: a DAG scheduled on an asset starts when the
# upstream task that declares it as an outlet succeeds.
RAW_EVENTS = Asset("profitpulse://lake/raw/events")
WAREHOUSE = Asset("profitpulse://postgres/core")
ANALYTICS = Asset("profitpulse://postgres/analytics")

DEFAULT_ARGS = {
    "owner": "profitpulse",
    "retries": 1,
    "retry_delay": timedelta(minutes=1),
}


def stage(task_id: str, command: str, **kwargs) -> BashOperator:
    """Run one pipeline stage (its own process, so Spark's JVM is isolated and logs are per task)."""
    return BashOperator(
        task_id=task_id,
        bash_command=f"cd /opt/profitpulse && python -m profitpulse {command}",
        **kwargs,
    )
