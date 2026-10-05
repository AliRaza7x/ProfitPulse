"""Data-quality rules, expressed as Spark column expressions.

Pipeline:  raw Kafka rows --parse_raw--> string columns --evaluate--> typed columns
plus a `failures` array<struct<rule_code, detail>> and `is_rejected`.

Rules never delete anything: the caller splits the result into accepted rows and
a failure log (REJECT and WARN), so every row is accounted for. Severity per rule
comes from config/rules.yaml.

All parsing uses try_cast / try_to_timestamp: Spark 4 runs in ANSI mode, where a
plain cast of a bad value would abort the whole job instead of flagging the row.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F
from pyspark.sql import types as T

from ..contract import EVENT_COLUMNS, ROUTING

_PARSE_SCHEMA = T.StructType(
    [T.StructField(c, T.StringType()) for c in EVENT_COLUMNS + ["schema_version", "_corrupt"]])
_TS_FORMATS = ["yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd'T'HH:mm:ss"]
_NUMERIC_PARSE = "decimal(20,4)"

REQUIRED_FIELDS = [
    "event_timestamp", "branch_id", "city", "product_id", "category", "supplier_id", "quantity",
    "unit_cost", "unit_price", "discount_pct", "revenue", "purchase_cost", "profit", "event_value",
]


@dataclass(frozen=True)
class Rule:
    code: str
    description: str
    violated: Callable[["Ctx"], Column]       # True where the rule is broken
    detail: Callable[["Ctx"], Column]         # human-readable evidence for the failure log


class Ctx:
    """Column handles and config shared by all rules."""

    def __init__(self, cfg: dict[str, Any], now: datetime) -> None:
        self.cfg = cfg
        self.now = now
        self.abs_tol = float(cfg["financial"]["abs_tolerance"])
        self.rel_tol = float(cfg["financial"]["rel_tolerance"])
        self.limits = cfg["limits"]
        self.malformed = F.col("malformed")
        self.ok = ~F.col("malformed")           # rules other than MALFORMED_PAYLOAD only apply to parseable messages

    @staticmethod
    def present(col: str) -> Column:
        return F.col(col).isNotNull()

    def tol_qty(self, expected: Column) -> Column:
        """Tolerance for quantity x unit-value formulas: absolute floor, relative band,
        and a cent-rounding allowance per unit (unit prices are stored to 2 dp)."""
        return F.greatest(F.lit(self.abs_tol), F.abs(expected) * F.lit(self.rel_tol),
                          F.col("t_quantity") * F.lit(0.0051))


# --------------------------------------------------------------------------- parsing
def parse_raw(raw: DataFrame) -> DataFrame:
    """Raw zone (topic, kafka_partition, kafka_offset, kafka_timestamp, value) -> string fields.

    Adds `malformed` (value is not a JSON object). Empty strings become null.
    """
    # PERMISSIVE from_json yields an all-null struct (not NULL) for corrupt input, so the
    # raw text is captured in a corrupt-record column to tell "bad JSON" from "empty event".
    parsed = raw.withColumn("_p", F.from_json(F.col("value"), _PARSE_SCHEMA, {
        "mode": "PERMISSIVE", "columnNameOfCorruptRecord": "_corrupt"}))
    cols = [F.when(F.trim(F.col("_p")[c]) == "", F.lit(None)).otherwise(F.col("_p")[c]).alias(c)
            for c in EVENT_COLUMNS]
    malformed = F.col("_p").isNull() | F.col("_p")["_corrupt"].isNotNull()
    return parsed.select("*", *cols).withColumn("malformed", malformed).drop("_p")


def _typed(df: DataFrame, ctx: Ctx) -> DataFrame:
    ts = F.coalesce(*[F.try_to_timestamp(F.col("event_timestamp"), F.lit(f)) for f in _TS_FORMATS])
    out = df.withColumn("t_event_ts", ts)
    for c in ["unit_cost", "unit_price", "discount_pct", "revenue", "purchase_cost", "profit", "event_value"]:
        out = out.withColumn(f"t_{c}", F.col(c).try_cast(_NUMERIC_PARSE))
    qty_dec = F.col("quantity").try_cast(_NUMERIC_PARSE)
    out = out.withColumn("t_quantity_raw", qty_dec)
    out = out.withColumn("t_quantity", F.when(qty_dec == F.floor(qty_dec), qty_dec.cast("int")))
    return out


# --------------------------------------------------------------------------- rules
def _all_numeric_parsed(*names: str) -> Column:
    cond = F.lit(True)
    for n in names:
        cond = cond & F.col(f"t_{n}").isNotNull()
    return cond


def _build_rules(ctx: Ctx) -> list[Rule]:
    cfg = ctx.cfg
    pat = cfg["id_patterns"]
    valid_types = cfg["event_types"]
    reasons = cfg["return_reasons"]
    earliest = F.lit(cfg["timestamp"]["earliest"]).cast("timestamp_ntz")
    latest = F.lit(ctx.now.replace(microsecond=0).isoformat(sep=" ")).cast("timestamp_ntz") + \
        F.expr(f"INTERVAL {int(cfg['timestamp']['future_tolerance_days'])} DAYS")
    ok = ctx.ok
    et = F.col("event_type")
    val = lambda c: F.coalesce(F.col(c), F.lit("<null>"))  # noqa: E731

    def bad_id(col: str, pattern: str) -> Column:
        return ok & F.col(col).isNotNull() & ~F.col(col).rlike(pattern)

    missing_list = F.concat_ws(",", *[F.when(F.col(c).isNull(), F.lit(c)) for c in REQUIRED_FIELDS])

    expected_value = (
        F.when(et == "PURCHASE", F.col("t_purchase_cost"))
        .when(et == "PRICE_CHANGE", F.col("t_unit_price"))
        .otherwise(F.col("t_revenue"))
    )

    rules = [
        Rule("MALFORMED_PAYLOAD", "Message is not a JSON object",
             lambda c: c.malformed,
             lambda c: F.concat(F.lit("value="), F.coalesce(F.substring(F.col("value"), 1, 120), F.lit("<null>")))),
        Rule("MISSING_EVENT_ID", "event_id is missing",
             lambda c: ok & F.col("event_id").isNull(), lambda c: F.lit("event_id is null")),
        Rule("INVALID_EVENT_ID_FORMAT", "event_id does not match the expected pattern",
             lambda c: bad_id("event_id", pat["event_id"]), lambda c: F.concat(F.lit("event_id="), val("event_id"))),
        Rule("INVALID_EVENT_TYPE", "event_type is not one of the supported types",
             lambda c: ok & ~F.coalesce(et.isin(valid_types), F.lit(False)),
             lambda c: F.concat(F.lit("event_type="), val("event_type"))),
        Rule("MISSING_REQUIRED_FIELD", "A required field is missing",
             lambda c: ok & (missing_list != ""), lambda c: F.concat(F.lit("missing: "), missing_list)),
        Rule("INVALID_TIMESTAMP", "event_timestamp cannot be parsed",
             lambda c: ok & F.col("event_timestamp").isNotNull() & F.col("t_event_ts").isNull(),
             lambda c: F.concat(F.lit("event_timestamp="), val("event_timestamp"))),
        Rule("TIMESTAMP_OUT_OF_RANGE", "event_timestamp is before the supported start or in the future",
             lambda c: ok & F.col("t_event_ts").isNotNull() & ((F.col("t_event_ts") < earliest) | (F.col("t_event_ts") > latest)),
             lambda c: F.concat(F.lit("event_timestamp="), val("event_timestamp"))),
        Rule("INVALID_BRANCH_ID", "branch_id does not match the expected pattern",
             lambda c: bad_id("branch_id", pat["branch_id"]), lambda c: F.concat(F.lit("branch_id="), val("branch_id"))),
        Rule("INVALID_PRODUCT_ID", "product_id does not match the expected pattern",
             lambda c: bad_id("product_id", pat["product_id"]), lambda c: F.concat(F.lit("product_id="), val("product_id"))),
        Rule("INVALID_SUPPLIER_ID", "supplier_id does not match the expected pattern",
             lambda c: bad_id("supplier_id", pat["supplier_id"]), lambda c: F.concat(F.lit("supplier_id="), val("supplier_id"))),
        Rule("INVALID_QUANTITY", "quantity must be a positive whole number within limits",
             lambda c: ok & F.col("quantity").isNotNull() & (
                 F.col("t_quantity").isNull() | (F.col("t_quantity") <= 0) | (F.col("t_quantity") > int(ctx.limits["quantity_max"]))),
             lambda c: F.concat(F.lit("quantity="), val("quantity"))),
        Rule("INVALID_UNIT_COST", "unit_cost must be a positive number",
             lambda c: ok & F.col("unit_cost").isNotNull() & (F.col("t_unit_cost").isNull() | (F.col("t_unit_cost") <= 0)),
             lambda c: F.concat(F.lit("unit_cost="), val("unit_cost"))),
        Rule("INVALID_UNIT_PRICE", "unit_price must be a positive number within limits",
             lambda c: ok & F.col("unit_price").isNotNull() & (
                 F.col("t_unit_price").isNull() | (F.col("t_unit_price") <= 0) | (F.col("t_unit_price") > float(ctx.limits["unit_price_max"]))),
             lambda c: F.concat(F.lit("unit_price="), val("unit_price"))),
        Rule("INVALID_DISCOUNT", "discount_pct must be between 0 and the configured maximum",
             lambda c: ok & F.col("discount_pct").isNotNull() & (
                 F.col("t_discount_pct").isNull() | (F.col("t_discount_pct") < 0) | (F.col("t_discount_pct") > float(ctx.limits["discount_max"]))),
             lambda c: F.concat(F.lit("discount_pct="), val("discount_pct"))),
        Rule("INVALID_RETURN_REASON", "RETURN events need a known return_reason",
             lambda c: ok & (et == "RETURN") & (F.col("return_reason").isNull() | ~F.col("return_reason").isin(reasons)),
             lambda c: F.concat(F.lit("return_reason="), val("return_reason"))),
        Rule("PURCHASE_COST_MISMATCH", "purchase_cost does not equal quantity x unit_cost",
             lambda c: ok & _all_numeric_parsed("quantity", "unit_cost", "purchase_cost") &
             (F.abs(F.col("t_purchase_cost") - F.col("t_quantity") * F.col("t_unit_cost")) >
              c.tol_qty(F.col("t_quantity") * F.col("t_unit_cost"))),
             lambda c: F.concat(F.lit("purchase_cost="), val("purchase_cost"), F.lit(" expected "),
                                (F.col("t_quantity") * F.col("t_unit_cost")).cast("string"))),
        Rule("REVENUE_MISMATCH", "SALE revenue does not equal quantity x unit_price x (1 - discount)",
             lambda c: ok & (et == "SALE") & _all_numeric_parsed("quantity", "unit_price", "discount_pct", "revenue") &
             (F.abs(F.col("t_revenue") - F.col("t_quantity") * F.col("t_unit_price") * (1 - F.col("t_discount_pct"))) >
              c.tol_qty(F.col("t_quantity") * F.col("t_unit_price") * (1 - F.col("t_discount_pct")))),
             lambda c: F.concat(F.lit("revenue="), val("revenue"), F.lit(" expected "),
                                (F.col("t_quantity") * F.col("t_unit_price") * (1 - F.col("t_discount_pct"))).cast("string"))),
        Rule("PROFIT_MISMATCH", "profit does not equal revenue - purchase_cost",
             lambda c: ok & _all_numeric_parsed("revenue", "purchase_cost", "profit") &
             (F.abs(F.col("t_profit") - (F.col("t_revenue") - F.col("t_purchase_cost"))) > c.abs_tol),
             lambda c: F.concat(F.lit("profit="), val("profit"), F.lit(" expected "),
                                (F.col("t_revenue") - F.col("t_purchase_cost")).cast("string"))),
        Rule("EVENT_VALUE_MISMATCH", "event_value does not match the value implied by event_type",
             lambda c: ok & F.coalesce(et.isin(valid_types), F.lit(False)) & F.col("t_event_value").isNotNull() &
             expected_value.isNotNull() & (F.abs(F.col("t_event_value") - expected_value) > c.abs_tol),
             lambda c: F.concat(F.lit("event_value="), val("event_value"), F.lit(" expected "), expected_value.cast("string"))),
        Rule("PRODUCT_ATTRIBUTE_CONFLICT", "category/supplier disagree with the product's established values",
             lambda c: ok & F.col("_product_conflict"),
             lambda c: F.concat(F.lit("product="), val("product_id"), F.lit(" category="), val("category"),
                                F.lit(" supplier="), val("supplier_id"))),
        Rule("BRANCH_CITY_CONFLICT", "city disagrees with the branch's established city",
             lambda c: ok & F.col("_branch_conflict"),
             lambda c: F.concat(F.lit("branch="), val("branch_id"), F.lit(" city="), val("city"))),
        Rule("TOPIC_ROUTE_MISMATCH", "Event arrived on a topic that does not match its event_type",
             lambda c: ok & F.col("topic_short").isNotNull() & F.coalesce(et.isin(valid_types), F.lit(False)) &
             (F.col("topic_short") != F.col("_expected_topic")),
             lambda c: F.concat(F.lit("topic="), F.col("topic_short"), F.lit(" event_type="), val("event_type"))),
        Rule("UNEXPECTED_RETURN_REASON", "return_reason is populated on a non-RETURN event",
             lambda c: ok & F.coalesce(et.isin(valid_types), F.lit(False)) & (et != "RETURN") & F.col("return_reason").isNotNull(),
             lambda c: F.concat(F.lit("return_reason="), val("return_reason"), F.lit(" on "), val("event_type"))),
        Rule("NEGATIVE_LIST_MARGIN", "unit_price is below unit_cost (sold or bought at a negative list margin)",
             lambda c: ok & _all_numeric_parsed("unit_price", "unit_cost") & (F.col("t_unit_price") < F.col("t_unit_cost")),
             lambda c: F.concat(F.lit("unit_price="), val("unit_price"), F.lit(" unit_cost="), val("unit_cost"))),
        Rule("POSSIBLE_DUPLICATE_BUSINESS_KEY", "Same branch/product/timestamp/type/quantity as an earlier event",
             lambda c: ok & F.col("_biz_dup"),
             lambda c: F.concat(F.lit("business key repeated for event_id="), val("event_id"))),
    ]
    return rules


# --------------------------------------------------------------------------- evaluation
def _dominant_flag(df: DataFrame, keys: list[str], attrs: list[str], min_agreement: float, out: str) -> DataFrame:
    """Flag rows whose `attrs` differ from the dominant combination for `keys`."""
    usable = df.filter(~F.col("malformed") & F.expr(" AND ".join(f"{c} IS NOT NULL" for c in keys + attrs)))
    combo = usable.groupBy(*keys, *attrs).count()
    totals = combo.groupBy(*keys).agg(F.sum("count").alias("_total"))
    w = Window.partitionBy(*keys).orderBy(F.desc("count"), *[F.asc(a) for a in attrs])
    dominant = (combo.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1")
                .join(totals, keys)
                .withColumn("_share", F.col("count") / F.col("_total"))
                .filter(F.col("_share") >= min_agreement)
                .select(*keys, *[F.col(a).alias(f"_dom_{a}") for a in attrs]))
    joined = df.join(dominant, keys, "left")
    differs = F.lit(False)
    for a in attrs:
        differs = differs | (F.col(a).isNotNull() & F.col(f"_dom_{a}").isNotNull() & (F.col(a) != F.col(f"_dom_{a}")))
    return joined.withColumn(out, F.coalesce(differs, F.lit(False))).drop(*[f"_dom_{a}" for a in attrs])


def evaluate(parsed: DataFrame, cfg: dict[str, Any], now: datetime | None = None) -> DataFrame:
    """Apply every rule. Returns the input plus typed `t_*` columns, `failures`,
    `is_rejected` and `has_warning`. Row count is unchanged."""
    now = now or datetime.utcnow()
    ctx = Ctx(cfg, now)

    df = _typed(parsed, ctx)
    df = df.withColumn("topic_short", F.regexp_extract(F.col("topic"), r"^[^.]*\.(.*)$", 1))
    df = df.withColumn("topic_short", F.when(F.col("topic_short") == "", None).otherwise(F.col("topic_short")))
    route_map = F.create_map(*[x for kv in ROUTING.items() for x in (F.lit(kv[0]), F.lit(kv[1]))])
    df = df.withColumn("_expected_topic", route_map[F.col("event_type")])

    agree = float(cfg["dimension_consistency"]["min_agreement"])
    df = _dominant_flag(df, ["product_id"], ["category", "supplier_id"], agree, "_product_conflict")
    df = _dominant_flag(df, ["branch_id"], ["city"], agree, "_branch_conflict")
    df = df.withColumn("_biz_dup", F.lit(False))

    rules = _build_rules(ctx)
    severity = cfg["severity"]
    unknown = {r.code for r in rules} - set(severity)
    if unknown:
        raise ValueError(f"rules without a severity in rules.yaml: {sorted(unknown)}")
    reject_codes = [r.code for r in rules if severity[r.code] == "REJECT"]

    def failure(rule: Rule) -> Column:
        return F.when(rule.violated(ctx), F.struct(F.lit(rule.code).alias("rule_code"), rule.detail(ctx).alias("detail")))

    def assemble(subset: list[Rule]) -> Column:
        return F.array_compact(F.array(*[failure(r) for r in subset]))

    order = [F.col("kafka_timestamp"), F.col("kafka_partition"), F.col("kafka_offset")]

    # Pass 1: every rule that does not depend on the other rows' verdicts.
    df = df.withColumn("failures", assemble(rules))
    df = df.withColumn("_rejected_p1", F.exists("failures", lambda x: x["rule_code"].isin(reject_codes)))

    # Pass 2: duplicate detection, among rows that survived pass 1, first copy wins.
    # (Winner is chosen from valid rows only, so an invalid first copy cannot
    # cause a valid retry to be thrown away.)
    # Candidates are ordered first inside each window, so ranks count candidates only:
    # a rejected earlier copy must not occupy rank 1.
    df = df.withColumn("_cand", (~F.col("_rejected_p1")) & F.col("event_id").isNotNull())
    w_id = Window.partitionBy("event_id").orderBy(F.col("_cand").desc(), *order)
    df = df.withColumn("_id_rank", F.when(F.col("_cand"), F.row_number().over(w_id)))
    biz_key = ["branch_id", "product_id", "event_timestamp", "event_type", "quantity"]
    df = df.withColumn("_cand_biz", F.col("_cand") & (F.col("_id_rank") == 1))
    w_biz = Window.partitionBy(*biz_key).orderBy(F.col("_cand_biz").desc(), *order)
    df = df.withColumn("_biz_rank", F.when(F.col("_cand_biz"), F.row_number().over(w_biz)))
    df = df.withColumn("_biz_dup", F.coalesce(F.col("_biz_rank") > 1, F.lit(False)))

    dup_rule = Rule("DUPLICATE_EVENT_ID", "event_id was already received", lambda c: F.col("_id_rank") > 1,
                    lambda c: F.concat(F.lit("event_id seen earlier: "), F.col("event_id")))
    biz_rule = next(r for r in rules if r.code == "POSSIBLE_DUPLICATE_BUSINESS_KEY")
    df = df.withColumn("failures", F.concat(
        F.col("failures"),
        F.array_compact(F.array(failure(dup_rule))),
        F.array_compact(F.array(failure(biz_rule))),
    ))
    # biz_rule was already evaluated (always False) in pass 1; the pass-2 evaluation above is the real one.
    reject_codes = reject_codes + ["DUPLICATE_EVENT_ID"]
    df = df.withColumn("is_rejected", F.exists("failures", lambda x: x["rule_code"].isin(reject_codes)))
    df = df.withColumn("has_warning", F.exists("failures", lambda x: ~x["rule_code"].isin(reject_codes)))
    return df.drop("_rejected_p1", "_cand", "_cand_biz", "_id_rank", "_biz_rank", "_biz_dup", "_product_conflict",
                   "_branch_conflict", "_expected_topic")


def failure_log(evaluated: DataFrame, cfg: dict[str, Any]) -> DataFrame:
    """One row per (message, violated rule), with severity and disposition."""
    sev = dict(cfg["severity"])
    sev["DUPLICATE_EVENT_ID"] = cfg["severity"].get("DUPLICATE_EVENT_ID", "REJECT")
    sev_map = F.create_map(*[x for kv in sev.items() for x in (F.lit(kv[0]), F.lit(kv[1]))])
    exploded = (evaluated.filter(F.size("failures") > 0)
                .select("event_id", F.col("value").alias("raw_payload"),
                        F.col("topic").alias("kafka_topic"), "kafka_partition", "kafka_offset",
                        F.explode("failures").alias("f")))
    return (exploded
            .withColumn("rule_code", F.col("f.rule_code")).withColumn("detail", F.col("f.detail")).drop("f")
            .withColumn("severity", sev_map[F.col("rule_code")])
            .withColumn("disposition", F.when(F.col("severity") == "REJECT", "REJECTED").otherwise("ACCEPTED_WITH_WARNING"))
            .withColumn("raw_payload", F.coalesce(F.col("raw_payload"), F.lit(""))))
