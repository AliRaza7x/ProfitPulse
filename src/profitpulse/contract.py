"""The event contract shared by producer, Spark jobs and tests.

A message is a flat JSON object with the 17 source columns plus `schema_version`.
Numbers are real JSON numbers; an absent return_reason is null.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1

EVENT_COLUMNS: list[str] = [
    "event_id", "event_timestamp", "event_type", "branch_id", "city", "product_id", "category",
    "supplier_id", "quantity", "unit_cost", "unit_price", "discount_pct", "revenue",
    "purchase_cost", "profit", "event_value", "return_reason",
]
INT_COLUMNS = {"quantity"}
FLOAT_COLUMNS = {"unit_cost", "unit_price", "discount_pct", "revenue", "purchase_cost", "profit", "event_value"}

# event_type -> short topic name
ROUTING: dict[str, str] = {
    "SALE": "sales",
    "PURCHASE": "purchases",
    "RETURN": "returns",
    "STOCK_ADJUSTMENT": "inventory",
    "DAMAGE": "inventory",
    "PRICE_CHANGE": "price_changes",
}
DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class TopicSpec:
    short_name: str
    partitions: int


# sales carries ~68% of volume, so it is the only multi-partition topic.
TOPICS: list[TopicSpec] = [
    TopicSpec("sales", 3),
    TopicSpec("purchases", 1),
    TopicSpec("returns", 1),
    TopicSpec("inventory", 1),
    TopicSpec("price_changes", 1),
    TopicSpec(DEAD_LETTER, 1),
]


def route(event_type: Any) -> str:
    """Short topic name for an event type; unknown types go to the dead-letter topic."""
    return ROUTING.get(event_type, DEAD_LETTER) if isinstance(event_type, str) else DEAD_LETTER


def csv_row_to_event(row: dict[str, str]) -> dict[str, Any]:
    """Convert a CSV row (all strings) into a typed event dict.

    Values that cannot be converted are passed through unchanged so that the
    validation layer, not the producer, decides what is invalid.
    """
    event: dict[str, Any] = {}
    for col in EVENT_COLUMNS:
        raw = row.get(col)
        if raw is None or raw == "":
            event[col] = None
        elif col in INT_COLUMNS:
            event[col] = _try(int, raw)
        elif col in FLOAT_COLUMNS:
            event[col] = _try(float, raw)
        else:
            event[col] = raw
    return event


def _try(fn, raw: str):
    try:
        return fn(raw)
    except ValueError:
        return raw


def encode(event: dict[str, Any]) -> bytes:
    return json.dumps({"schema_version": SCHEMA_VERSION, **event}, separators=(",", ":")).encode("utf-8")


def message_key(event: dict[str, Any]) -> bytes | None:
    """Messages are keyed by branch so each branch's events stay ordered within a partition."""
    branch = event.get("branch_id")
    return branch.encode("utf-8") if isinstance(branch, str) and branch else None
