"""A small, hand-computable event set shared by the transform and feature tests.

As-of date for the feature tests: 2025-06-30.  Expected values are worked out in the
test modules, so every assertion can be verified with a calculator.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

EVENT_SCHEMA = (
    "event_id string, event_ts timestamp_ntz, event_type string, branch_id string, city string, product_id string, "
    "category string, supplier_id string, quantity int, unit_cost decimal(16,2), unit_price decimal(16,2), "
    "discount_pct decimal(7,4), revenue decimal(16,2), purchase_cost decimal(16,2), profit decimal(16,2), "
    "event_value decimal(16,2), return_reason string, kafka_topic string, kafka_partition int, kafka_offset bigint, "
    "kafka_timestamp timestamp_ntz, has_warning boolean"
)

_n = 0


def ev(ts: str, etype: str, branch: str, product: str, qty: int, cost: float, price: float, disc: float = 0.0,
       reason: str | None = None, revenue: float | None = None) -> tuple:
    """Build one typed event row. Revenue defaults to qty x price x (1 - disc); other columns follow the source rules."""
    global _n
    _n += 1
    rev = round(qty * price * (1 - disc), 2) if revenue is None else revenue
    pc = round(qty * cost, 2)
    city = {"BR01": "Karachi", "BR02": "Lahore"}[branch]
    cat, sup = {"P1": ("Grocery", "SUP1"), "P2": ("Apparel", "SUP2")}[product]
    value = {"PURCHASE": pc, "PRICE_CHANGE": price}.get(etype, rev)
    d = lambda x: Decimal(str(x))  # noqa: E731
    return (f"EVT{_n:07d}", datetime.fromisoformat(ts), etype, branch, city, product, cat, sup, qty, d(cost), d(price),
            d(disc), d(rev), d(pc), d(round(rev - pc, 2)), d(value), reason, f"pp.{etype.lower()}", 0, _n,
            datetime.fromisoformat(ts), False)


def fixture_events() -> list[tuple]:
    return [
        # BR01 sales: S1 and S2 fall in the recent growth window, S3 in the prior one
        ev("2025-06-20 10:00:00", "SALE", "BR01", "P1", 2, 100, 150, 0.10),      # gross 300 net 270 cogs 200
        ev("2025-05-10 10:00:00", "SALE", "BR01", "P1", 4, 100, 150, 0.0),       # gross 600 net 600 cogs 400
        ev("2024-09-01 10:00:00", "SALE", "BR01", "P2", 1, 300, 400, 0.50),      # gross 400 net 200 cogs 300
        ev("2025-06-15 10:00:00", "SALE", "BR02", "P2", 2, 300, 400, 0.25),      # gross 800 net 600 cogs 600
        # returns: one non-restockable (Damaged), one restockable (Wrong Item)
        ev("2025-06-21 10:00:00", "RETURN", "BR01", "P1", 1, 100, 150, 0.10, reason="Damaged", revenue=135.0),
        ev("2025-06-22 10:00:00", "RETURN", "BR02", "P2", 1, 300, 400, 0.0, reason="Wrong Item", revenue=380.0),
        # inventory
        ev("2025-06-01 10:00:00", "DAMAGE", "BR01", "P1", 2, 100, 150, 0.0, revenue=300.0),
        ev("2025-06-02 10:00:00", "STOCK_ADJUSTMENT", "BR01", "P1", 5, 100, 150, 0.0, revenue=750.0),
        # purchases from SUP1 at above-standard prices (standard cost is 100)
        ev("2025-06-10 10:00:00", "PURCHASE", "BR01", "P1", 10, 120, 150),       # spend 1200, std 1000
        ev("2025-06-20 10:00:00", "PURCHASE", "BR01", "P1", 5, 160, 150),        # spend 800, std 500, above list price
        # price observation
        ev("2025-06-25 10:00:00", "PRICE_CHANGE", "BR01", "P1", 1, 100, 150),
    ]
