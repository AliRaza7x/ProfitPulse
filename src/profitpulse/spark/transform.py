"""Spark job: cleaned zone -> transformed zone (dimensions and facts).

The builders are pure DataFrame -> DataFrame functions so they can be unit-tested
on small fixtures. Business semantics (see docs/METRICS.md):

* Only SALE rows are revenue. `revenue`/`profit` on other event types are
  reported by the source but are not sales, so they are re-purposed per fact
  (refund value, write-off value, ...) and never summed into revenue.
* standard_cost  = median unit_cost on non-PURCHASE rows (book cost).
* list_price     = unit_price of the latest PRICE_CHANGE/SALE row.
* A return is non-restockable if its reason is in analytics.yaml `returns.non_restockable_reasons`.
"""
from __future__ import annotations

import json
import logging
import sys

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

from ..config import analytics_config
from ..db import tracked_run
from ..settings import get_settings
from .session import get_lake, get_spark

log = logging.getLogger("profitpulse.transform")

LINEAGE = ["kafka_topic", "kafka_partition", "kafka_offset"]
_D = "decimal(16,2)"


def _dominant(df: DataFrame, keys: list[str], attrs: list[str]) -> DataFrame:
    """One row per key carrying the most frequent attribute combination (ties: alphabetical)."""
    counts = df.groupBy(*keys, *attrs).count()
    w = Window.partitionBy(*keys).orderBy(F.desc("count"), *[F.asc(a) for a in attrs])
    return counts.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1").drop("_rn", "count")


def build_dim_date(events: DataFrame) -> DataFrame:
    bounds = events.agg(F.min("event_ts").alias("lo"), F.max("event_ts").alias("hi")).first()
    lo, hi = bounds["lo"], bounds["hi"]
    spark = events.sparkSession
    start, end = f"{lo.year}-01-01", f"{hi.year}-12-31"
    days = spark.sql(f"SELECT explode(sequence(DATE'{start}', DATE'{end}', INTERVAL 1 DAY)) AS full_date")
    d = F.col("full_date")
    return days.select(
        F.date_format(d, "yyyyMMdd").cast("int").alias("date_key"),
        d.alias("full_date"),
        F.year(d).cast("smallint").alias("year"),
        F.quarter(d).cast("smallint").alias("quarter"),
        F.month(d).cast("smallint").alias("month"),
        F.date_format(d, "MMMM").alias("month_name"),
        F.date_format(d, "yyyy-MM").alias("year_month"),
        F.weekofyear(d).cast("smallint").alias("iso_week"),
        (((F.dayofweek(d) + 5) % 7) + 1).cast("smallint").alias("day_of_week"),
        F.date_format(d, "EEEE").alias("day_name"),
        F.dayofweek(d).isin(1, 7).alias("is_weekend"),
    )


def build_dim_supplier(events: DataFrame) -> DataFrame:
    return events.groupBy("supplier_id").agg(
        F.to_date(F.min("event_ts")).alias("first_seen"), F.to_date(F.max("event_ts")).alias("last_seen"))


def build_dim_branch(events: DataFrame) -> DataFrame:
    seen = events.groupBy("branch_id").agg(
        F.to_date(F.min("event_ts")).alias("first_seen"), F.to_date(F.max("event_ts")).alias("last_seen"))
    return _dominant(events, ["branch_id"], ["city"]).join(seen, "branch_id")


def build_dim_product(events: DataFrame) -> DataFrame:
    attrs = _dominant(events, ["product_id"], ["category", "supplier_id"])
    non_purchase = (events.filter(F.col("event_type") != "PURCHASE").groupBy("product_id")
                    .agg(F.percentile_approx("unit_cost", 0.5, 10000).alias("book_cost")))
    purchase = (events.filter(F.col("event_type") == "PURCHASE").groupBy("product_id")
                .agg(F.percentile_approx("unit_cost", 0.5, 10000).alias("purchase_median")))
    pricing = events.filter(F.col("event_type").isin("PRICE_CHANGE", "SALE"))
    list_price = pricing.groupBy("product_id").agg(F.max_by("unit_price", "event_ts").alias("list_price"))
    seen = events.groupBy("product_id").agg(
        F.to_date(F.min("event_ts")).alias("first_seen"), F.to_date(F.max("event_ts")).alias("last_seen"))
    anyprice = events.groupBy("product_id").agg(F.max_by("unit_price", "event_ts").alias("any_price"))
    return (attrs.join(non_purchase, "product_id", "left").join(purchase, "product_id", "left")
            .join(list_price, "product_id", "left").join(anyprice, "product_id", "left").join(seen, "product_id")
            .select("product_id", "category", "supplier_id",
                    F.coalesce("list_price", "any_price").cast("decimal(12,2)").alias("list_price"),
                    F.coalesce("book_cost", "purchase_median").cast("decimal(12,2)").alias("standard_cost"),
                    "first_seen", "last_seen"))


def _base(events: DataFrame, event_type: str) -> DataFrame:
    return (events.filter(F.col("event_type") == event_type)
            .withColumn("date_key", F.date_format("event_ts", "yyyyMMdd").cast("int")))


def build_fact_sales(events: DataFrame) -> DataFrame:
    return (_base(events, "SALE").select(
        "event_id", "event_ts", "date_key", "branch_id", "product_id", "quantity", "unit_price", "unit_cost",
        "discount_pct",
        (F.col("quantity") * F.col("unit_price")).cast(_D).alias("gross_sales"),
        (F.col("quantity") * F.col("unit_price") - F.col("revenue")).cast(_D).alias("discount_amount"),
        F.col("revenue").alias("net_revenue"),
        F.col("purchase_cost").alias("cogs"),
        F.col("profit").alias("gross_profit"), *LINEAGE))


def build_fact_returns(events: DataFrame, non_restockable: list[str]) -> DataFrame:
    restockable = ~F.col("return_reason").isin(non_restockable)
    return (_base(events, "RETURN").select(
        "event_id", "event_ts", "date_key", "branch_id", "product_id", "quantity", "return_reason",
        F.col("revenue").alias("refund_value"),
        F.col("purchase_cost").alias("cost_value"),
        restockable.alias("restockable"),
        F.when(restockable, -(F.col("revenue") - F.col("purchase_cost"))).otherwise(-F.col("revenue"))
        .cast(_D).alias("profit_impact"), *LINEAGE))


def build_fact_purchases(events: DataFrame, dim_product: DataFrame) -> DataFrame:
    prod = dim_product.select("product_id", "standard_cost", F.col("list_price").alias("p_list_price"))
    return (_base(events, "PURCHASE").join(prod, "product_id", "left").select(
        "event_id", "event_ts", "date_key", "branch_id", "product_id", "supplier_id", "quantity", "unit_cost",
        "standard_cost",
        F.col("p_list_price").alias("list_price"),
        F.col("purchase_cost").alias("spend"),
        (F.col("quantity") * F.col("standard_cost")).cast(_D).alias("standard_spend"),
        (F.col("purchase_cost") - F.col("quantity") * F.col("standard_cost")).cast(_D).alias("price_variance_amount"),
        (F.col("unit_cost") / F.col("standard_cost") - 1).cast("decimal(10,6)").alias("price_variance_pct"),
        (F.col("unit_cost") > F.col("p_list_price")).alias("cost_above_list_price"), *LINEAGE))


def build_fact_inventory(events: DataFrame) -> DataFrame:
    inv = (events.filter(F.col("event_type").isin("STOCK_ADJUSTMENT", "DAMAGE"))
           .withColumn("date_key", F.date_format("event_ts", "yyyyMMdd").cast("int")))
    return inv.select(
        "event_id", "event_ts", "date_key", "branch_id", "product_id",
        F.col("event_type").alias("movement_type"),
        F.when(F.col("event_type") == "DAMAGE", "LOSS").otherwise("UNSIGNED").alias("direction"),
        "quantity", "unit_cost", F.col("purchase_cost").alias("value_at_cost"), *LINEAGE)


def build_fact_price_changes(events: DataFrame, dim_product: DataFrame) -> DataFrame:
    prod = dim_product.select("product_id", "standard_cost")
    return (_base(events, "PRICE_CHANGE").join(prod, "product_id", "left").select(
        "event_id", "event_ts", "date_key", "branch_id", "product_id",
        F.col("unit_price").alias("list_price"), "standard_cost",
        ((F.col("unit_price") - F.col("standard_cost")) / F.col("unit_price")).cast("decimal(10,6)").alias("list_margin_pct"),
        *LINEAGE))


def run() -> dict:
    s = get_settings()
    cfg = analytics_config()
    lake = get_lake(s)
    with tracked_run("transform") as rh:
        spark = get_spark("profitpulse-transform", s)
        events = spark.read.parquet(lake.cleaned("events"))
        dim_product = build_dim_product(events).cache()

        tables = {
            "dim_date": build_dim_date(events),
            "dim_supplier": build_dim_supplier(events),
            "dim_branch": build_dim_branch(events),
            "dim_product": dim_product,
            "fact_sales": build_fact_sales(events),
            "fact_returns": build_fact_returns(events, cfg["returns"]["non_restockable_reasons"]),
            "fact_purchases": build_fact_purchases(events, dim_product),
            "fact_inventory_movements": build_fact_inventory(events),
            "fact_price_changes": build_fact_price_changes(events, dim_product),
        }
        counts = {}
        for name, df in tables.items():
            df.write.mode("overwrite").parquet(lake.transformed(name))
            counts[name] = spark.read.parquet(lake.transformed(name)).count()

        fact_total = sum(n for k, n in counts.items() if k.startswith("fact_"))
        rh.rows_in = events.count()
        rh.rows_out = fact_total
        rh.details = {"table_rows": counts}
        if rh.rows_in != fact_total:
            raise RuntimeError(f"Every accepted event must land in exactly one fact: events={rh.rows_in}, facts={fact_total}")
        log.info("transform: %s", counts)
        return counts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    print(json.dumps(run()))
    sys.exit(0)
