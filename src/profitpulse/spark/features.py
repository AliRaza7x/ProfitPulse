"""Spark job: core warehouse -> analytical feature tables.

This is where Spark earns its keep: wide aggregations over every fact table
(per branch, product, supplier, month, branch x category) plus window functions
(supplier purchase cadence). Output goes to lake/analytics/ as Parquet:

  final tables   company_monthly, branch_monthly, supplier_monthly,
                 inventory_movement_monthly, inventory_branch_category
  base tables    branch_base, product_base, supplier_base, inventory_base
                 (consumed by the Python detectors/scorers, which are
                 explainable and unit-tested, then published to PostgreSQL)

The SQL builders take a SparkSession with the core tables registered as temp
views, so they can be tested on small fixtures. Reads come from PostgreSQL
(core.*), not the lake: analytics only ever sees data that passed the load checks.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date

from pyspark.sql import DataFrame, SparkSession

from ..config import analytics_config
from ..db import tracked_run
from ..settings import get_settings
from .session import get_lake, get_spark, jdbc_options

log = logging.getLogger("profitpulse.features")

CORE_TABLES = ["dim_date", "dim_branch", "dim_product", "dim_supplier", "fact_sales", "fact_returns",
               "fact_purchases", "fact_inventory_movements", "fact_price_changes"]


def register_core_views(spark: SparkSession, as_of: date | None = None) -> None:
    """Expose core.* as temp views named like the tables. Facts are cut off at `as_of`."""
    s = get_settings()
    for t in CORE_TABLES:
        df = (spark.read.format("jdbc").options(**jdbc_options(s)).option("dbtable", f"core.{t}")
              .option("fetchsize", "20000").load())
        if as_of and t.startswith("fact_"):
            df = df.filter(f"to_date(event_ts) <= DATE'{as_of.isoformat()}'")
        df.createOrReplaceTempView(t)


def resolve_as_of(spark: SparkSession, configured: str | None) -> date:
    if configured:
        return date.fromisoformat(str(configured))
    row = spark.sql("""
        SELECT MAX(d) AS d FROM (
          SELECT MAX(to_date(event_ts)) d FROM fact_sales UNION ALL SELECT MAX(to_date(event_ts)) FROM fact_returns
          UNION ALL SELECT MAX(to_date(event_ts)) FROM fact_purchases
          UNION ALL SELECT MAX(to_date(event_ts)) FROM fact_inventory_movements)""").first()
    return row["d"]


def register_movements(spark: SparkSession) -> None:
    """One narrow table of every stock-relevant event: (branch, product, ts, kind, quantity, value)."""
    spark.sql("""
        SELECT branch_id, product_id, event_ts, 'SALE' AS kind, quantity, net_revenue AS value FROM fact_sales
        UNION ALL SELECT branch_id, product_id, event_ts, 'PURCHASE', quantity, spend FROM fact_purchases
        UNION ALL SELECT branch_id, product_id, event_ts, 'RETURN', quantity, refund_value FROM fact_returns
        UNION ALL SELECT branch_id, product_id, event_ts, movement_type, quantity, value_at_cost FROM fact_inventory_movements
    """).createOrReplaceTempView("movements")


# --------------------------------------------------------------------------- SQL pieces
_SALES = """COUNT(*) AS sale_events, SUM(quantity) AS units_sold, SUM(gross_sales) AS gross_sales,
            SUM(net_revenue) AS net_revenue, SUM(cogs) AS cogs, SUM(gross_profit) AS gross_profit,
            SUM(discount_amount) AS discount_amount"""


def _growth(as_of: date, months: int) -> str:
    d = f"DATE'{as_of.isoformat()}'"
    recent = f"to_date(event_ts) > add_months({d}, -{months})"
    prior = f"to_date(event_ts) > add_months({d}, -{2 * months}) AND to_date(event_ts) <= add_months({d}, -{months})"
    return (f"SUM(CASE WHEN {recent} THEN net_revenue END) AS revenue_recent, "
            f"SUM(CASE WHEN {prior} THEN net_revenue END) AS revenue_prior")


def _entity_aggregates(key: str, as_of: date, growth_months: int) -> str:
    """Per-entity subqueries shared by branch and product metrics."""
    return f"""
      LEFT JOIN (SELECT {key}, {_SALES}, {_growth(as_of, growth_months)} FROM fact_sales GROUP BY {key}) s ON s.{key} = e.{key}
      LEFT JOIN (SELECT {key}, COUNT(*) AS return_events, SUM(quantity) AS return_units, SUM(refund_value) AS return_value,
                        SUM(profit_impact) AS return_profit_impact FROM fact_returns GROUP BY {key}) r ON r.{key} = e.{key}
      LEFT JOIN (SELECT {key},
                        SUM(CASE WHEN movement_type='DAMAGE' THEN quantity ELSE 0 END) AS damage_units,
                        SUM(CASE WHEN movement_type='DAMAGE' THEN value_at_cost ELSE 0 END) AS damage_value,
                        SUM(CASE WHEN movement_type='STOCK_ADJUSTMENT' THEN 1 ELSE 0 END) AS adjustment_events,
                        SUM(CASE WHEN movement_type='STOCK_ADJUSTMENT' THEN quantity ELSE 0 END) AS adjustment_units,
                        SUM(CASE WHEN movement_type='STOCK_ADJUSTMENT' THEN value_at_cost ELSE 0 END) AS adjustment_value
                 FROM fact_inventory_movements GROUP BY {key}) i ON i.{key} = e.{key}
    """


def _z(col: str, typ: str = "bigint") -> str:
    return f"CAST(COALESCE({col}, 0) AS {typ})"


_COMMON_SELECT = f"""
      {_z('s.sale_events')} AS sale_events, {_z('s.units_sold')} AS units_sold,
      {_z('s.gross_sales','decimal(18,2)')} AS gross_sales, {_z('s.net_revenue','decimal(18,2)')} AS net_revenue,
      {_z('s.cogs','decimal(18,2)')} AS cogs, {_z('s.gross_profit','decimal(18,2)')} AS gross_profit,
      {_z('s.discount_amount','decimal(18,2)')} AS discount_amount,
      {_z('r.return_events')} AS return_events, {_z('r.return_units')} AS return_units,
      {_z('r.return_value','decimal(18,2)')} AS return_value,
      {_z('i.damage_units')} AS damage_units
"""


def build_branch_base(spark: SparkSession, as_of: date, growth_months: int) -> DataFrame:
    return spark.sql(f"""
      SELECT e.branch_id, e.city, DATE(e.first_seen) AS period_start, DATE'{as_of.isoformat()}' AS period_end,
        {_COMMON_SELECT},
        {_z('r.return_profit_impact','decimal(18,2)')} AS return_profit_impact,
        {_z('i.damage_value','decimal(18,2)')} AS damage_value,
        {_z('i.adjustment_events')} AS adjustment_events, {_z('i.adjustment_units')} AS adjustment_units,
        {_z('i.adjustment_value','decimal(18,2)')} AS adjustment_value,
        CAST(COALESCE(i.adjustment_events * try_divide(i.adjustment_value, i.adjustment_units), 0) AS decimal(18,2)) AS adjustment_event_cost,
        {_z('p.purchase_units')} AS purchase_units, {_z('p.purchase_spend','decimal(18,2)')} AS purchase_spend,
        CAST(COALESCE(s.gross_profit,0) + COALESCE(r.return_profit_impact,0) - COALESCE(i.damage_value,0) AS decimal(18,2)) AS adjusted_gross_profit,
        try_divide(s.gross_profit, s.net_revenue) AS gross_margin,
        try_divide(s.discount_amount, s.gross_sales) AS discount_rate,
        try_divide(r.return_units, s.units_sold) AS return_rate_units,
        try_divide(r.return_value, s.net_revenue) AS return_rate_value,
        try_divide(i.damage_units, s.units_sold) AS damage_rate,
        try_divide(i.adjustment_units, i.adjustment_events) AS adjustment_avg_qty,
        try_divide(COALESCE(i.damage_units,0) + COALESCE(i.adjustment_units,0), s.units_sold) AS shrink_rate,
        try_divide(s.revenue_recent, s.revenue_prior) - 1 AS growth_rate
      FROM dim_branch e
      {_entity_aggregates('branch_id', as_of, growth_months)}
      LEFT JOIN (SELECT branch_id, SUM(quantity) AS purchase_units, SUM(spend) AS purchase_spend
                 FROM fact_purchases GROUP BY branch_id) p ON p.branch_id = e.branch_id
    """)


def build_product_base(spark: SparkSession, as_of: date, growth_months: int) -> DataFrame:
    return spark.sql(f"""
      SELECT e.product_id, e.category, e.supplier_id, e.list_price, e.standard_cost,
        {_COMMON_SELECT},
        {_z('i.adjustment_units')} AS adjustment_units,
        CAST(s.revenue_recent AS decimal(18,2)) AS revenue_recent, CAST(s.revenue_prior AS decimal(18,2)) AS revenue_prior,
        try_divide(s.gross_profit, s.net_revenue) AS gross_margin,
        try_divide(s.discount_amount, s.gross_sales) AS discount_rate,
        try_divide(r.return_units, s.units_sold) AS return_rate_units,
        try_divide(i.damage_units, s.units_sold) AS damage_rate,
        try_divide(s.revenue_recent, s.revenue_prior) - 1 AS growth_rate
      FROM dim_product e
      {_entity_aggregates('product_id', as_of, growth_months)}
    """)


def build_supplier_base(spark: SparkSession) -> DataFrame:
    return spark.sql("""
      WITH p AS (
        SELECT supplier_id, COUNT(*) AS purchase_events, SUM(quantity) AS purchase_units, SUM(spend) AS purchase_spend,
               SUM(standard_spend) AS purchase_standard_spend, SUM(price_variance_amount) AS ppv_amount,
               AVG(CASE WHEN price_variance_pct > 0.0001 THEN 1.0 ELSE 0.0 END) AS share_above_standard,
               SUM(CASE WHEN cost_above_list_price THEN 1 ELSE 0 END) AS purchases_above_list,
               MIN(to_date(event_ts)) AS first_purchase, MAX(to_date(event_ts)) AS last_purchase
        FROM fact_purchases GROUP BY supplier_id),
      gaps AS (
        SELECT supplier_id, try_divide(STDDEV_SAMP(gap_days), AVG(gap_days)) AS cadence_cv
        FROM (SELECT supplier_id, datediff(to_date(event_ts), to_date(LAG(event_ts) OVER (PARTITION BY supplier_id ORDER BY event_ts, event_id))) AS gap_days
              FROM fact_purchases)
        WHERE gap_days IS NOT NULL GROUP BY supplier_id),
      sold AS (SELECT dp.supplier_id, SUM(f.quantity) AS units_sold
               FROM fact_sales f JOIN dim_product dp ON dp.product_id = f.product_id GROUP BY dp.supplier_id),
      ret  AS (SELECT dp.supplier_id, SUM(f.quantity) AS return_units
               FROM fact_returns f JOIN dim_product dp ON dp.product_id = f.product_id GROUP BY dp.supplier_id),
      dmg  AS (SELECT dp.supplier_id, SUM(f.quantity) AS damage_units
               FROM fact_inventory_movements f JOIN dim_product dp ON dp.product_id = f.product_id
               WHERE f.movement_type = 'DAMAGE' GROUP BY dp.supplier_id),
      cnt  AS (SELECT supplier_id, COUNT(*) AS products_supplied FROM dim_product GROUP BY supplier_id)
      SELECT sp.supplier_id,
             CAST(COALESCE(cnt.products_supplied, 0) AS int) AS products_supplied,
             CAST(COALESCE(p.purchase_events, 0) AS bigint) AS purchase_events,
             CAST(COALESCE(p.purchase_units, 0) AS bigint) AS purchase_units,
             CAST(COALESCE(p.purchase_spend, 0) AS decimal(18,2)) AS purchase_spend,
             CAST(COALESCE(p.purchase_standard_spend, 0) AS decimal(18,2)) AS purchase_standard_spend,
             CAST(COALESCE(p.ppv_amount, 0) AS decimal(18,2)) AS ppv_amount,
             try_divide(p.ppv_amount, p.purchase_standard_spend) AS ppv_pct,
             p.share_above_standard,
             CAST(COALESCE(p.purchases_above_list, 0) AS int) AS purchases_above_list,
             gaps.cadence_cv,
             try_divide(ret.return_units, sold.units_sold) AS product_return_rate,
             try_divide(dmg.damage_units, sold.units_sold) AS product_damage_rate,
             p.first_purchase, p.last_purchase
      FROM dim_supplier sp
      LEFT JOIN p ON p.supplier_id = sp.supplier_id LEFT JOIN gaps ON gaps.supplier_id = sp.supplier_id
      LEFT JOIN sold ON sold.supplier_id = sp.supplier_id LEFT JOIN ret ON ret.supplier_id = sp.supplier_id
      LEFT JOIN dmg ON dmg.supplier_id = sp.supplier_id LEFT JOIN cnt ON cnt.supplier_id = sp.supplier_id
    """)


def build_company_monthly(spark: SparkSession) -> DataFrame:
    return spark.sql("""
      WITH months AS (
        SELECT DISTINCT date_format(event_ts, 'yyyy-MM') AS year_month, to_date(date_format(event_ts, 'yyyy-MM-01')) AS month_start
        FROM fact_sales),
      s AS (SELECT date_format(event_ts,'yyyy-MM') ym, COUNT(*) sale_events, SUM(quantity) units_sold, SUM(gross_sales) gross_sales,
                   SUM(net_revenue) net_revenue, SUM(cogs) cogs, SUM(gross_profit) gross_profit, SUM(discount_amount) discount_amount
            FROM fact_sales GROUP BY 1),
      r AS (SELECT date_format(event_ts,'yyyy-MM') ym, SUM(quantity) return_units, SUM(refund_value) return_value,
                   SUM(profit_impact) return_profit_impact FROM fact_returns GROUP BY 1),
      i AS (SELECT date_format(event_ts,'yyyy-MM') ym,
                   SUM(CASE WHEN movement_type='DAMAGE' THEN value_at_cost ELSE 0 END) damage_value,
                   SUM(CASE WHEN movement_type='STOCK_ADJUSTMENT' THEN value_at_cost ELSE 0 END) adjustment_value
            FROM fact_inventory_movements GROUP BY 1),
      p AS (SELECT date_format(event_ts,'yyyy-MM') ym, SUM(spend) purchase_spend, SUM(price_variance_amount) price_variance_amount
            FROM fact_purchases GROUP BY 1)
      SELECT m.year_month, m.month_start,
        CAST(COALESCE(s.sale_events,0) AS bigint) sale_events, CAST(COALESCE(s.units_sold,0) AS bigint) units_sold,
        CAST(COALESCE(s.gross_sales,0) AS decimal(18,2)) gross_sales, CAST(COALESCE(s.net_revenue,0) AS decimal(18,2)) net_revenue,
        CAST(COALESCE(s.cogs,0) AS decimal(18,2)) cogs, CAST(COALESCE(s.gross_profit,0) AS decimal(18,2)) gross_profit,
        CAST(COALESCE(s.discount_amount,0) AS decimal(18,2)) discount_amount,
        CAST(COALESCE(r.return_units,0) AS bigint) return_units, CAST(COALESCE(r.return_value,0) AS decimal(18,2)) return_value,
        CAST(COALESCE(r.return_profit_impact,0) AS decimal(18,2)) return_profit_impact,
        CAST(COALESCE(i.damage_value,0) AS decimal(18,2)) damage_value, CAST(COALESCE(i.adjustment_value,0) AS decimal(18,2)) adjustment_value,
        CAST(COALESCE(p.purchase_spend,0) AS decimal(18,2)) purchase_spend,
        CAST(COALESCE(p.price_variance_amount,0) AS decimal(18,2)) price_variance_amount,
        CAST(COALESCE(s.gross_profit,0) + COALESCE(r.return_profit_impact,0) - COALESCE(i.damage_value,0) AS decimal(18,2)) adjusted_gross_profit,
        try_divide(s.gross_profit, s.net_revenue) gross_margin, try_divide(s.discount_amount, s.gross_sales) discount_rate,
        try_divide(r.return_units, s.units_sold) return_rate_units
      FROM months m LEFT JOIN s ON s.ym = m.year_month LEFT JOIN r ON r.ym = m.year_month
      LEFT JOIN i ON i.ym = m.year_month LEFT JOIN p ON p.ym = m.year_month
      ORDER BY m.year_month
    """)


def build_branch_monthly(spark: SparkSession) -> DataFrame:
    return spark.sql("""
      WITH s AS (SELECT branch_id, date_format(event_ts,'yyyy-MM') ym, COUNT(*) sale_events, SUM(quantity) units_sold,
                        SUM(gross_sales) gross_sales, SUM(net_revenue) net_revenue, SUM(cogs) cogs, SUM(gross_profit) gross_profit,
                        SUM(discount_amount) discount_amount FROM fact_sales GROUP BY 1, 2),
           r AS (SELECT branch_id, date_format(event_ts,'yyyy-MM') ym, SUM(quantity) return_units, SUM(refund_value) return_value
                 FROM fact_returns GROUP BY 1, 2),
           i AS (SELECT branch_id, date_format(event_ts,'yyyy-MM') ym,
                        SUM(CASE WHEN movement_type='DAMAGE' THEN quantity ELSE 0 END) damage_units,
                        SUM(CASE WHEN movement_type='STOCK_ADJUSTMENT' THEN quantity ELSE 0 END) adjustment_units
                 FROM fact_inventory_movements GROUP BY 1, 2),
           p AS (SELECT branch_id, date_format(event_ts,'yyyy-MM') ym, SUM(spend) purchase_spend FROM fact_purchases GROUP BY 1, 2)
      SELECT s.branch_id, s.ym AS year_month, to_date(concat(s.ym, '-01')) AS month_start,
             CAST(s.sale_events AS bigint) sale_events, CAST(s.units_sold AS bigint) units_sold,
             CAST(s.gross_sales AS decimal(18,2)) gross_sales, CAST(s.net_revenue AS decimal(18,2)) net_revenue,
             CAST(s.cogs AS decimal(18,2)) cogs, CAST(s.gross_profit AS decimal(18,2)) gross_profit,
             CAST(s.discount_amount AS decimal(18,2)) discount_amount,
             CAST(COALESCE(r.return_units,0) AS bigint) return_units, CAST(COALESCE(r.return_value,0) AS decimal(18,2)) return_value,
             CAST(COALESCE(i.damage_units,0) AS bigint) damage_units, CAST(COALESCE(i.adjustment_units,0) AS bigint) adjustment_units,
             CAST(COALESCE(p.purchase_spend,0) AS decimal(18,2)) purchase_spend,
             try_divide(s.gross_profit, s.net_revenue) gross_margin, try_divide(s.discount_amount, s.gross_sales) discount_rate,
             try_divide(r.return_units, s.units_sold) return_rate_units
      FROM s LEFT JOIN r ON r.branch_id = s.branch_id AND r.ym = s.ym
             LEFT JOIN i ON i.branch_id = s.branch_id AND i.ym = s.ym
             LEFT JOIN p ON p.branch_id = s.branch_id AND p.ym = s.ym
    """)


def build_supplier_monthly(spark: SparkSession) -> DataFrame:
    return spark.sql("""
      SELECT supplier_id, date_format(event_ts,'yyyy-MM') AS year_month,
             to_date(concat(date_format(event_ts,'yyyy-MM'), '-01')) AS month_start,
             COUNT(*) AS purchase_events, SUM(quantity) AS purchase_units,
             CAST(SUM(spend) AS decimal(18,2)) AS purchase_spend,
             CAST(SUM(standard_spend) AS decimal(18,2)) AS purchase_standard_spend,
             CAST(SUM(price_variance_amount) AS decimal(18,2)) AS ppv_amount,
             try_divide(SUM(price_variance_amount), SUM(standard_spend)) AS ppv_pct
      FROM fact_purchases GROUP BY supplier_id, date_format(event_ts,'yyyy-MM')
    """)


def build_inventory_movement_monthly(spark: SparkSession) -> DataFrame:
    return spark.sql("""
      SELECT date_format(event_ts,'yyyy-MM') AS year_month, to_date(date_format(event_ts,'yyyy-MM-01')) AS month_start,
             CAST(SUM(CASE WHEN kind='PURCHASE' THEN quantity ELSE 0 END) AS bigint) AS units_purchased,
             CAST(SUM(CASE WHEN kind='SALE' THEN quantity ELSE 0 END) AS bigint) AS units_sold,
             CAST(SUM(CASE WHEN kind='RETURN' THEN quantity ELSE 0 END) AS bigint) AS units_returned,
             CAST(SUM(CASE WHEN kind='DAMAGE' THEN quantity ELSE 0 END) AS bigint) AS units_damaged,
             CAST(SUM(CASE WHEN kind='STOCK_ADJUSTMENT' THEN quantity ELSE 0 END) AS bigint) AS units_adjusted,
             try_divide(SUM(CASE WHEN kind='PURCHASE' THEN quantity END), SUM(CASE WHEN kind='SALE' THEN quantity END)) AS replenishment_ratio
      FROM movements GROUP BY 1, 2
    """)


def build_inventory_base(spark: SparkSession, as_of: date, velocity_days: int, replenishment_days: int) -> DataFrame:
    d = f"DATE'{as_of.isoformat()}'"
    v, r = velocity_days, replenishment_days

    def window(kind: str, days: int, offset: int = 0) -> str:
        hi = f"AND to_date(event_ts) <= date_sub({d}, {offset})" if offset else ""
        return (f"SUM(CASE WHEN kind='{kind}' AND to_date(event_ts) > date_sub({d}, {days + offset}) {hi} "
                f"THEN quantity ELSE 0 END)")

    return spark.sql(f"""
      WITH m AS (
        SELECT product_id,
          CAST({window('SALE', v)} AS bigint) AS units_sold_recent,
          CAST({window('SALE', v, v)} AS bigint) AS units_sold_prior,
          CAST({window('SALE', r)} AS bigint) AS units_sold_window,
          CAST({window('PURCHASE', r)} AS bigint) AS units_purchased_window,
          CAST({window('DAMAGE', v)} AS bigint) AS damage_units_recent,
          MAX(CASE WHEN kind='SALE' THEN to_date(event_ts) END) AS last_sale,
          MAX(CASE WHEN kind='PURCHASE' THEN to_date(event_ts) END) AS last_purchase
        FROM movements GROUP BY product_id)
      SELECT p.product_id, p.category, p.supplier_id, {d} AS as_of_date,
        CAST(COALESCE(m.units_sold_recent,0) AS bigint) AS units_sold_recent,
        CAST(COALESCE(m.units_sold_prior,0) AS bigint) AS units_sold_prior,
        try_divide(m.units_sold_recent, {v}) AS velocity_per_day,
        try_divide(m.units_sold_recent, m.units_sold_prior) - 1 AS velocity_change,
        CAST(COALESCE(m.units_sold_window,0) AS bigint) AS units_sold_window,
        CAST(COALESCE(m.units_purchased_window,0) AS bigint) AS units_purchased_window,
        try_divide(m.units_purchased_window, m.units_sold_window) AS replenishment_ratio,
        datediff({d}, m.last_sale) AS days_since_last_sale,
        datediff({d}, m.last_purchase) AS days_since_last_purchase,
        CAST(COALESCE(m.damage_units_recent,0) AS bigint) AS damage_units_recent
      FROM dim_product p LEFT JOIN m ON m.product_id = p.product_id
    """)


def build_inventory_branch_category(spark: SparkSession, as_of: date, replenishment_days: int) -> DataFrame:
    d = f"DATE'{as_of.isoformat()}'"
    inwin = f"to_date(m.event_ts) > date_sub({d}, {replenishment_days})"
    return spark.sql(f"""
      SELECT m.branch_id, p.category, {d} AS as_of_date,
        CAST(SUM(CASE WHEN m.kind='SALE' AND {inwin} THEN m.quantity ELSE 0 END) AS bigint) AS units_sold,
        CAST(SUM(CASE WHEN m.kind='PURCHASE' AND {inwin} THEN m.quantity ELSE 0 END) AS bigint) AS units_purchased,
        try_divide(SUM(CASE WHEN m.kind='PURCHASE' AND {inwin} THEN m.quantity END),
                   SUM(CASE WHEN m.kind='SALE' AND {inwin} THEN m.quantity END)) AS replenishment_ratio,
        CAST(SUM(CASE WHEN m.kind='DAMAGE' AND {inwin} THEN m.quantity ELSE 0 END) AS bigint) AS damage_units,
        CAST(SUM(CASE WHEN m.kind='STOCK_ADJUSTMENT' AND {inwin} THEN m.quantity ELSE 0 END) AS bigint) AS adjustment_units,
        CAST(SUM(CASE WHEN m.kind='STOCK_ADJUSTMENT' AND {inwin} THEN m.value ELSE 0 END) AS decimal(18,2)) AS adjustment_value,
        try_divide(SUM(CASE WHEN m.kind IN ('DAMAGE','STOCK_ADJUSTMENT') AND {inwin} THEN m.quantity END),
                   SUM(CASE WHEN m.kind='SALE' AND {inwin} THEN m.quantity END)) AS shrink_rate,
        datediff({d}, MAX(CASE WHEN m.kind='PURCHASE' THEN to_date(m.event_ts) END)) AS days_since_last_purchase
      FROM movements m JOIN dim_product p ON p.product_id = m.product_id
      GROUP BY m.branch_id, p.category
    """)


def run() -> dict:
    s = get_settings()
    cfg = analytics_config()
    lake = get_lake(s)
    w = cfg["windows"]
    with tracked_run("features") as rh:
        spark = get_spark("profitpulse-features", s)
        configured = cfg.get("as_of_date")
        register_core_views(spark, date.fromisoformat(str(configured)) if configured else None)
        as_of = resolve_as_of(spark, configured)
        register_movements(spark)

        outputs = {
            "company_monthly": build_company_monthly(spark),
            "branch_monthly": build_branch_monthly(spark),
            "supplier_monthly": build_supplier_monthly(spark),
            "inventory_movement_monthly": build_inventory_movement_monthly(spark),
            "inventory_branch_category": build_inventory_branch_category(spark, as_of, w["replenishment_days"]),
            "branch_base": build_branch_base(spark, as_of, w["growth_months"]),
            "product_base": build_product_base(spark, as_of, w["growth_months"]),
            "supplier_base": build_supplier_base(spark),
            "inventory_base": build_inventory_base(spark, as_of, w["velocity_days"], w["replenishment_days"]),
        }
        counts = {}
        for name, df in outputs.items():
            df.write.mode("overwrite").parquet(lake.analytics(name))
            counts[name] = spark.read.parquet(lake.analytics(name)).count()
        rh.rows_out = sum(counts.values())
        rh.details = {"as_of_date": as_of.isoformat(), "tables": counts}
        log.info("features as_of=%s %s", as_of, counts)
        return {"as_of_date": as_of.isoformat(), **counts}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    print(json.dumps(run()))
    sys.exit(0)
