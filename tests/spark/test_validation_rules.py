"""Data-quality rules: every injectable fault must be quarantined under its expected rule."""
from __future__ import annotations

from datetime import datetime

import pytest
from helpers import RAW_SCHEMA, clean_stream, make_event, raw_row, rows_from

from profitpulse.kafka.faults import FAULTS
from profitpulse.validation.rules import evaluate, failure_log, parse_raw

pytestmark = pytest.mark.spark
NOW = datetime(2026, 10, 5, 12, 0, 0)


def run_rules(spark, rows, cfg):
    raw = spark.createDataFrame(rows, RAW_SCHEMA)
    return evaluate(parse_raw(raw), cfg, NOW)


def codes_by_event(df):
    return {r["event_id"]: {f["rule_code"] for f in r["failures"]} for r in df.select("event_id", "failures").collect()}


def test_clean_stream_has_no_rejections_or_warnings(spark, rules_cfg):
    out = run_rules(spark, rows_from(clean_stream()), rules_cfg)
    assert out.filter("is_rejected").count() == 0
    assert out.filter("has_warning").count() == 0
    assert out.count() == 24


@pytest.fixture(scope="module")
def all_faults_run(spark, rules_cfg):
    """One Spark evaluation containing a clean stream plus one faulty message per fault.

    Returns {fault name: (is_rejected, [failure rule codes], number of rejected messages overall)}.
    Faults are located by Kafka offset, since some (e.g. missing_event_id) have no usable id.
    """
    stream = clean_stream()
    extra_by_type: dict[str, dict] = {}          # one shared victim per event type (distinct event_ids)
    victims = {}
    for f in FAULTS:
        wanted = (f.applies_to or ("SALE",))[0]
        if wanted == "SALE":
            victims[f.name] = stream[0]
        else:                                    # fault only meaningful for another type (e.g. RETURN)
            extra_by_type.setdefault(wanted, make_event(500 + len(extra_by_type), event_type=wanted,
                                                        product="P0001", category="Cat0", supplier="SUP001"))
            victims[f.name] = extra_by_type[wanted]
    base = stream + list(extra_by_type.values())
    messages = [f.apply(victims[f.name], f"EVT{9000001 + i}") for i, f in enumerate(FAULTS)]
    out = run_rules(spark, rows_from(base + messages), rules_cfg)
    first_fault_offset = len(base)
    log = failure_log(out, rules_cfg).collect()
    rejected_offsets = {r["kafka_offset"] for r in out.filter("is_rejected").select("kafka_offset").collect()}
    result = {}
    for i, f in enumerate(FAULTS):
        offset = first_fault_offset + i
        codes = {r["rule_code"] for r in log if r["kafka_offset"] == offset and r["disposition"] == "REJECTED"}
        result[f.name] = (offset in rejected_offsets, codes)
    result["_rejected_total"] = len(rejected_offsets)
    result["_faults"] = len(FAULTS)
    return result


@pytest.mark.parametrize("fault", FAULTS, ids=lambda f: f.name)
def test_each_fault_is_rejected_under_its_expected_rule(all_faults_run, fault):
    rejected, codes = all_faults_run[fault.name]
    assert rejected, f"{fault.name} was not rejected"
    assert fault.expected_rule in codes, f"{fault.name}: expected {fault.expected_rule}, got {sorted(codes)}"


def test_only_the_injected_faults_are_rejected(all_faults_run):
    """Clean traffic in the same stream must be untouched: rejections == faults injected."""
    assert all_faults_run["_rejected_total"] == all_faults_run["_faults"]


def test_rejected_rows_keep_the_raw_payload(spark, rules_cfg):
    stream = clean_stream()
    bad = make_event(900, qty=-1, product="P0001", category="Cat0", supplier="SUP001")
    out = run_rules(spark, rows_from(stream + [bad]), rules_cfg)
    log = failure_log(out, rules_cfg).filter("rule_code = 'INVALID_QUANTITY'").collect()
    assert len(log) == 1
    assert '"quantity":-1' in log[0]["raw_payload"]
    assert log[0]["kafka_topic"] == "pp.sales"


def test_first_valid_copy_wins_duplicates_even_if_it_arrives_second(spark, rules_cfg):
    """An invalid early copy must not cause the valid retry to be discarded as a duplicate."""
    stream = clean_stream()
    good = make_event(700, product="P0001", category="Cat0", supplier="SUP001")
    broken = dict(good, quantity=-5)                       # same event_id, invalid
    rows = rows_from(stream + [broken, good, dict(good)])  # invalid, valid, exact duplicate
    out = run_rules(spark, rows, rules_cfg)
    mine = [r for r in out.filter("event_id = 'EVT0000700'").orderBy("kafka_offset").collect()]
    verdicts = [(r["is_rejected"], sorted(f["rule_code"] for f in r["failures"])) for r in mine]
    assert verdicts[0][0] is True and "INVALID_QUANTITY" in verdicts[0][1]
    assert verdicts[1] == (False, [])                      # valid copy accepted
    assert verdicts[2][0] is True and "DUPLICATE_EVENT_ID" in verdicts[2][1]


def test_rounding_noise_in_source_data_is_not_rejected(spark, rules_cfg):
    """Real source rows carry cent-rounding drift (observed up to 0.005 x quantity); they must pass."""
    stream = clean_stream()
    e = make_event(800, event_type="PURCHASE", product="P0001", category="Cat0", supplier="SUP001",
                   qty=7, unit_cost=1234.57)
    e["purchase_cost"] = round(e["purchase_cost"] + 0.03, 2)
    e["profit"] = round(e["revenue"] - e["purchase_cost"], 2)
    e["event_value"] = e["purchase_cost"]
    out = run_rules(spark, rows_from(stream + [e]), rules_cfg)
    assert out.filter("event_id = 'EVT0000800'").first()["is_rejected"] is False


def test_warnings_are_recorded_but_rows_are_accepted(spark, rules_cfg):
    stream = clean_stream()
    thin = make_event(600, event_type="PURCHASE", product="P0001", category="Cat0", supplier="SUP001",
                      unit_cost=200.0, unit_price=150.0)    # bought above list price
    out = run_rules(spark, rows_from(stream + [thin]), rules_cfg)
    row = out.filter("event_id = 'EVT0000600'").first()
    assert row["is_rejected"] is False and row["has_warning"] is True
    log = failure_log(out, rules_cfg).filter("event_id = 'EVT0000600'").collect()
    assert [(r["rule_code"], r["disposition"]) for r in log] == [("NEGATIVE_LIST_MARGIN", "ACCEPTED_WITH_WARNING")]


def test_event_routed_to_wrong_topic_is_a_warning(spark, rules_cfg):
    stream = clean_stream()
    misrouted = raw_row(make_event(650, product="P0001", category="Cat0", supplier="SUP001"), 99, topic="pp.returns")
    out = run_rules(spark, rows_from(stream) + [misrouted], rules_cfg)
    codes = codes_by_event(out)
    assert codes["EVT0000650"] == {"TOPIC_ROUTE_MISMATCH"}


def test_row_count_is_preserved(spark, rules_cfg):
    stream = clean_stream()
    faulty = [f.apply(stream[0], f"EVT90000{i:02d}") for i, f in enumerate(FAULTS)]
    rows = rows_from(stream + faulty)
    assert run_rules(spark, rows, rules_cfg).count() == len(rows)
