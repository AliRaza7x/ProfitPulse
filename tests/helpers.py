"""Builders for valid events and raw Kafka rows used across the tests."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from profitpulse import contract

BASE_TS = datetime(2025, 3, 1, 8, 0, 0)

# Schema of the raw zone written by spark/ingest.py
RAW_SCHEMA = ("topic string, kafka_partition int, kafka_offset bigint, kafka_timestamp timestamp_ntz, "
              "key string, value string")


def make_event(i: int = 1, event_type: str = "SALE", branch: str = "BR01", city: str = "Karachi",
               product: str = "P0001", category: str = "Grocery", supplier: str = "SUP001", qty: int = 2,
               unit_cost: float = 100.0, unit_price: float = 150.0, discount: float = 0.10,
               hours: int | None = None, reason: str | None = None) -> dict[str, Any]:
    """A fully consistent event: every financial identity holds exactly."""
    revenue = round(qty * unit_price * (1 - discount), 2)
    purchase_cost = round(qty * unit_cost, 2)
    profit = round(revenue - purchase_cost, 2)
    event_value = {"PURCHASE": purchase_cost, "PRICE_CHANGE": unit_price}.get(event_type, revenue)
    if event_type == "RETURN" and reason is None:
        reason = "Damaged"
    ts = BASE_TS + timedelta(hours=i if hours is None else hours)
    return {
        "event_id": f"EVT{i:07d}", "event_timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"), "event_type": event_type,
        "branch_id": branch, "city": city, "product_id": product, "category": category, "supplier_id": supplier,
        "quantity": qty, "unit_cost": unit_cost, "unit_price": unit_price, "discount_pct": discount,
        "revenue": revenue, "purchase_cost": purchase_cost, "profit": profit, "event_value": event_value,
        "return_reason": reason,
    }


def raw_row(payload: dict[str, Any] | bytes, offset: int, topic: str | None = None, partition: int = 0,
            ts: datetime | None = None, prefix: str = "pp") -> dict[str, Any]:
    """A raw-zone row as produced by the ingest job."""
    if isinstance(payload, bytes):
        value, short = payload.decode("utf-8"), contract.DEAD_LETTER
    else:
        value, short = contract.encode(payload).decode("utf-8"), contract.route(payload.get("event_type"))
    return {"topic": topic or f"{prefix}.{short}", "kafka_partition": partition, "kafka_offset": offset,
            "kafka_timestamp": ts or (BASE_TS + timedelta(seconds=offset)), "key": None, "value": value}


def clean_stream(n_products: int = 3, per_product: int = 8) -> list[dict[str, Any]]:
    """Valid events spread over several products/branches, so master-data dominance is well defined."""
    events, i = [], 1
    for p in range(n_products):
        for k in range(per_product):
            branch = "BR01" if k % 2 == 0 else "BR02"
            city = "Karachi" if branch == "BR01" else "Lahore"
            events.append(make_event(i, product=f"P{p + 1:04d}", category=f"Cat{p}", supplier=f"SUP{p + 1:03d}",
                                     branch=branch, city=city, qty=1 + (k % 5)))
            i += 1
    return events


def rows_from(events: list[dict[str, Any] | bytes]) -> list[dict[str, Any]]:
    return [raw_row(e, offset) for offset, e in enumerate(events)]


def dump(event: dict[str, Any]) -> str:
    return json.dumps(event)
