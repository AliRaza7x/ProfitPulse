"""Fault injection for exercising the validation layer.

Faults are ADDITIVE: the producer sends the untouched original and, with some
probability, an extra corrupted message derived from it. Source totals therefore
stay intact and every injected fault is expected to land in quarantine under a
known rule code, which the tests and the end-to-end run assert.
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from typing import Any, Callable

# Injected fault ids start here: well outside the source dataset's id range.
FAULT_ID_BASE = 9_000_000


@dataclass(frozen=True)
class Fault:
    name: str
    expected_rule: str
    apply: Callable[[dict[str, Any], str], dict[str, Any] | bytes]
    applies_to: tuple[str, ...] | None = None     # event types the fault is meaningful for (None = any)


def _with(event: dict, **changes: Any) -> dict:
    out = copy.deepcopy(event)
    out.update(changes)
    return out


def _fresh_id(new_id: str) -> dict[str, str]:
    return {"event_id": new_id}


FAULTS: list[Fault] = [
    Fault("missing_event_id", "MISSING_EVENT_ID", lambda e, i: _with(e, event_id=None)),
    Fault("bad_event_id_format", "INVALID_EVENT_ID_FORMAT", lambda e, i: _with(e, event_id=f"X-{i}")),
    Fault("duplicate_event", "DUPLICATE_EVENT_ID", lambda e, i: copy.deepcopy(e)),
    Fault("unknown_event_type", "INVALID_EVENT_TYPE", lambda e, i: _with(e, **_fresh_id(i), event_type="REFUND")),
    Fault("negative_quantity", "INVALID_QUANTITY", lambda e, i: _with(e, **_fresh_id(i), quantity=-3)),
    Fault("zero_quantity", "INVALID_QUANTITY", lambda e, i: _with(e, **_fresh_id(i), quantity=0)),
    Fault("fractional_quantity", "INVALID_QUANTITY", lambda e, i: _with(e, **_fresh_id(i), quantity=2.5)),
    Fault("bad_branch_id", "INVALID_BRANCH_ID", lambda e, i: _with(e, **_fresh_id(i), branch_id="BRANCH-7")),
    Fault("bad_product_id", "INVALID_PRODUCT_ID", lambda e, i: _with(e, **_fresh_id(i), product_id="PROD1")),
    Fault("bad_supplier_id", "INVALID_SUPPLIER_ID", lambda e, i: _with(e, **_fresh_id(i), supplier_id="S-9")),
    Fault("zero_unit_price", "INVALID_UNIT_PRICE", lambda e, i: _with(e, **_fresh_id(i), unit_price=0.0)),
    Fault("negative_unit_cost", "INVALID_UNIT_COST", lambda e, i: _with(e, **_fresh_id(i), unit_cost=-12.5)),
    Fault("discount_over_100pct", "INVALID_DISCOUNT", lambda e, i: _with(e, **_fresh_id(i), discount_pct=1.4)),
    Fault("future_timestamp", "TIMESTAMP_OUT_OF_RANGE",
          lambda e, i: _with(e, **_fresh_id(i), event_timestamp="2099-01-01 00:00:00")),
    Fault("unparseable_timestamp", "INVALID_TIMESTAMP",
          lambda e, i: _with(e, **_fresh_id(i), event_timestamp="yesterday-ish")),
    Fault("revenue_mismatch", "REVENUE_MISMATCH",
          lambda e, i: _with(e, **_fresh_id(i), revenue=round((e["revenue"] or 0) * 1.5 + 100, 2)), ("SALE",)),
    Fault("purchase_cost_mismatch", "PURCHASE_COST_MISMATCH",
          lambda e, i: _with(e, **_fresh_id(i), purchase_cost=round((e["purchase_cost"] or 0) * 0.5, 2))),
    Fault("profit_mismatch", "PROFIT_MISMATCH", lambda e, i: _with(e, **_fresh_id(i), profit=round((e["profit"] or 0) + 500, 2))),
    Fault("missing_return_reason", "INVALID_RETURN_REASON",
          lambda e, i: _with(e, **_fresh_id(i), return_reason=None), ("RETURN",)),
    Fault("unknown_return_reason", "INVALID_RETURN_REASON",
          lambda e, i: _with(e, **_fresh_id(i), return_reason="Felt like it"), ("RETURN",)),
    Fault("missing_city", "MISSING_REQUIRED_FIELD", lambda e, i: _with(e, **_fresh_id(i), city=None)),
    Fault("category_conflict", "PRODUCT_ATTRIBUTE_CONFLICT",
          lambda e, i: _with(e, **_fresh_id(i), category="Imaginary Category")),
    Fault("city_conflict", "BRANCH_CITY_CONFLICT", lambda e, i: _with(e, **_fresh_id(i), city="Atlantis")),
    Fault("malformed_json", "MALFORMED_PAYLOAD", lambda e, i: b'{"event_id": "' + i.encode() + b'", "quantity": '),
]

# Faults that depend on the original being delivered first (duplicate detection).
ORDER_DEPENDENT = {"duplicate_event"}


class FaultInjector:
    def __init__(self, rate: float, seed: int = 42, fault_names: list[str] | None = None) -> None:
        if not 0.0 <= rate <= 1.0:
            raise ValueError("corrupt rate must be within [0, 1]")
        self.rate = rate
        self.rng = random.Random(seed)
        selected = [f for f in FAULTS if not fault_names or f.name in fault_names]
        if not selected:
            raise ValueError(f"No matching faults in {fault_names}")
        self.faults = selected
        self.counter = 0
        self.injected: dict[str, int] = {}

    def maybe_fault(self, event: dict[str, Any]) -> tuple[Fault, dict[str, Any] | bytes] | None:
        """Return an extra faulty message for `event`, or None."""
        if self.rate <= 0 or self.rng.random() >= self.rate:
            return None
        usable = [f for f in self.faults if f.applies_to is None or event.get("event_type") in f.applies_to]
        fault = self.rng.choice(usable)
        self.counter += 1
        new_id = f"EVT{FAULT_ID_BASE + self.counter:07d}"
        self.injected[fault.name] = self.injected.get(fault.name, 0) + 1
        return fault, fault.apply(event, new_id)

    def expected_rejections(self) -> dict[str, int]:
        """Expected quarantine rows per rule code for everything injected so far."""
        by_name = {f.name: f for f in FAULTS}
        out: dict[str, int] = {}
        for name, n in self.injected.items():
            rule = by_name[name].expected_rule
            out[rule] = out.get(rule, 0) + n
        return out
