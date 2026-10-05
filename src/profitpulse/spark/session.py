"""SparkSession factory and lake layout.

The same code runs in local mode (default) and on a cluster: only SPARK_MASTER
changes. Zones, in order of refinement:

    raw          exact Kafka messages + lineage (append-only batches)
    cleaned      parsed, typed, validated events (and the rejected set)
    transformed  dimensions and facts, ready to load
    analytics    KPI / score / anomaly tables, ready to load
"""
from __future__ import annotations

import glob
from pathlib import Path

from pyspark.sql import SparkSession

from ..settings import Settings, get_settings


def get_spark(app_name: str, settings: Settings | None = None, **extra_conf: str) -> SparkSession:
    s = settings or get_settings()
    jars = sorted(glob.glob(str(s.extra_jars_dir / "*.jar")))
    builder = (
        SparkSession.builder.appName(app_name)
        .master(s.spark_master)
        .config("spark.driver.memory", s.spark_driver_memory)
        .config("spark.ui.enabled", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.timestampType", "TIMESTAMP_NTZ")     # source timestamps carry no zone
        .config("spark.sql.shuffle.partitions", "16")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
        .config("spark.driver.extraJavaOptions", "-Duser.timezone=UTC")
    )
    if jars:
        builder = builder.config("spark.jars", ",".join(jars))
    for key, value in extra_conf.items():
        builder = builder.config(key.replace("__", "."), value)
    spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    return spark


class Lake:
    """Filesystem layout of the Parquet zones."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def raw(self, batch_id: str | None = None) -> str:
        base = self.root / "raw" / "events"
        return str(base / f"ingest_batch={batch_id}") if batch_id else str(base)

    def cleaned(self, name: str) -> str:
        return str(self.root / "cleaned" / name)

    def transformed(self, table: str) -> str:
        return str(self.root / "transformed" / table)

    def analytics(self, table: str) -> str:
        return str(self.root / "analytics" / table)


def get_lake(settings: Settings | None = None) -> Lake:
    return Lake((settings or get_settings()).lake_dir)


def jdbc_options(settings: Settings | None = None) -> dict[str, str]:
    s = settings or get_settings()
    return {
        "url": s.jdbc_url,
        "user": s.pg_user,
        "password": s.pg_password,
        "driver": "org.postgresql.Driver",
        "batchsize": "10000",
        "reWriteBatchedInserts": "true",
    }
