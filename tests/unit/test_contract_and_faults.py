import json

import pytest
from helpers import make_event

from profitpulse import contract
from profitpulse.kafka.faults import FAULT_ID_BASE, FAULTS, FaultInjector


def test_csv_row_is_typed_and_empty_reason_becomes_null():
    row = {c: "" for c in contract.EVENT_COLUMNS}
    row.update(event_id="EVT0000001", quantity="4", unit_cost="12.5", discount_pct="0.0", return_reason="")
    ev = contract.csv_row_to_event(row)
    assert ev["quantity"] == 4 and isinstance(ev["quantity"], int)
    assert ev["unit_cost"] == 12.5 and ev["discount_pct"] == 0.0
    assert ev["return_reason"] is None and ev["city"] is None


def test_unconvertible_values_pass_through_for_the_validator_to_judge():
    row = {c: "x" for c in contract.EVENT_COLUMNS}
    row["quantity"], row["unit_price"] = "four", "n/a"
    ev = contract.csv_row_to_event(row)
    assert ev["quantity"] == "four" and ev["unit_price"] == "n/a"


@pytest.mark.parametrize("etype,topic", [
    ("SALE", "sales"), ("PURCHASE", "purchases"), ("RETURN", "returns"),
    ("STOCK_ADJUSTMENT", "inventory"), ("DAMAGE", "inventory"), ("PRICE_CHANGE", "price_changes"),
    ("REFUND", "dead_letter"), (None, "dead_letter"), (42, "dead_letter")])
def test_routing(etype, topic):
    assert contract.route(etype) == topic


def test_every_routed_topic_is_declared():
    declared = {t.short_name for t in contract.TOPICS}
    assert set(contract.ROUTING.values()) | {contract.DEAD_LETTER} == declared


def test_encode_is_compact_json_with_schema_version_and_branch_key():
    ev = make_event(1)
    payload = json.loads(contract.encode(ev))
    assert payload["schema_version"] == contract.SCHEMA_VERSION and payload["event_id"] == "EVT0000001"
    assert contract.message_key(ev) == b"BR01"
    assert contract.message_key({"branch_id": None}) is None


def test_fault_injection_is_deterministic_for_a_seed():
    def run(seed):
        inj = FaultInjector(0.3, seed=seed)
        out = [inj.maybe_fault(make_event(i)) for i in range(200)]
        return [(f.name, p if isinstance(p, bytes) else p["event_id"]) for f, p in filter(None, out)]
    assert run(5) == run(5)
    assert run(5) != run(6)


def test_fault_rate_zero_never_injects_and_rate_one_always_does():
    assert all(FaultInjector(0.0).maybe_fault(make_event(i)) is None for i in range(50))
    inj = FaultInjector(1.0, seed=1)
    assert all(inj.maybe_fault(make_event(i)) is not None for i in range(50))
    assert sum(inj.injected.values()) == 50


def test_injected_ids_never_collide_with_source_ids():
    inj = FaultInjector(1.0, seed=3, fault_names=["negative_quantity"])
    _, payload = inj.maybe_fault(make_event(1))
    assert payload["event_id"] == f"EVT{FAULT_ID_BASE + 1:07d}"
    assert int(payload["event_id"][3:]) > 300_000


def test_expected_rejections_follow_the_injected_faults():
    inj = FaultInjector(1.0, seed=2, fault_names=["negative_quantity", "zero_quantity", "future_timestamp"])
    for i in range(30):
        inj.maybe_fault(make_event(i))
    expected = inj.expected_rejections()
    assert sum(expected.values()) == 30
    assert set(expected) <= {"INVALID_QUANTITY", "TIMESTAMP_OUT_OF_RANGE"}


def test_unknown_fault_name_is_an_error():
    with pytest.raises(ValueError):
        FaultInjector(0.5, fault_names=["no_such_fault"])


def test_all_faults_have_unique_names_and_a_known_rule(rules_cfg):
    names = [f.name for f in FAULTS]
    assert len(names) == len(set(names))
    assert {f.expected_rule for f in FAULTS} <= set(rules_cfg["severity"])
