"""The return detector must find a genuine return anomaly, and must not invent one in the untouched data."""
from datetime import date

import pandas as pd
import pytest

from profitpulse.analytics.detectors import detect_peer
from profitpulse.config import analytics_config
from profitpulse.settings import get_settings
from profitpulse.variant import VARIANT_ID_BASE, inject_returns

PRODUCT = "P0932"


@pytest.fixture(scope="module")
def source():
    path = get_settings().data_dir / "source" / "profitpulse_synthetic_dataset.csv"
    if not path.exists():
        pytest.skip("source CSV not present")
    return pd.read_csv(path)


def product_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Per-product sale events and return rate, computed in pandas (independent of the Spark pipeline)."""
    sale = df[df.event_type == "SALE"].groupby("product_id").agg(sale_events=("event_id", "size"), units_sold=("quantity", "sum"),
                                                                 net_revenue=("revenue", "sum"))
    ret = df[df.event_type == "RETURN"].groupby("product_id").quantity.sum().rename("return_units")
    m = sale.join(ret).fillna({"return_units": 0}).reset_index()
    m["return_rate_units"] = m.return_units / m.units_sold
    return m


def run_check(metrics: pd.DataFrame) -> list[dict]:
    check = next(c for c in analytics_config()["anomaly"]["peer_checks"] if c["id"] == "product_return_rate")
    return detect_peer(metrics, check, analytics_config()["anomaly"]["severity_z"],
                       date(2024, 1, 1), date(2026, 9, 30), date(2026, 9, 30), "PKR")


def test_untouched_data_does_not_flag_the_named_product(source):
    flagged = {a["entity_id"] for a in run_check(product_metrics(source))}
    assert PRODUCT not in flagged                      # its returns are statistically ordinary in the supplied file


def test_injected_return_anomaly_is_found_by_the_detector(source):
    variant = inject_returns(source, PRODUCT, multiplier=3.5)
    flagged = run_check(product_metrics(variant))
    assert PRODUCT in {a["entity_id"] for a in flagged}
    found = next(a for a in flagged if a["entity_id"] == PRODUCT)
    assert found["severity"] in {"high", "critical"} and "return rate" in found["explanation"]
    assert found["estimated_exposure"] > 0


def test_variant_leaves_source_untouched_and_keeps_rows_valid(source):
    before = len(source)
    variant = inject_returns(source, PRODUCT)
    assert len(source) == before and len(variant) > before
    extra = variant[variant.event_id.str[3:].astype(int) >= VARIANT_ID_BASE]
    assert (extra.event_type == "RETURN").all() and (extra.product_id == PRODUCT).all()
    assert extra.return_reason.isin(["Quality Issue", "Damaged", "Wrong Item"]).all()
    assert (extra.profit - (extra.revenue - extra.purchase_cost)).abs().max() < 0.02       # identities preserved
    assert variant.event_id.is_unique
