"""Spark -> PostgreSQL loading helpers.

Spark writes a DataFrame to `staging.<name>` over JDBC; `db.merge_from_staging`
then moves it into the real table inside the caller's transaction (typed,
idempotent, atomic across tables).
"""
from __future__ import annotations

from pyspark.sql import DataFrame

from ..settings import Settings, get_settings
from .session import jdbc_options


def to_staging(df: DataFrame, name: str, settings: Settings | None = None) -> str:
    """Overwrite staging.<name> with `df`. Returns the qualified table name."""
    qualified = f"staging.{name}"
    (df.write.format("jdbc").options(**jdbc_options(settings or get_settings()))
     .option("dbtable", qualified).option("truncate", "true").mode("overwrite").save())
    return qualified
