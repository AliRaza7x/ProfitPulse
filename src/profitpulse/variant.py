"""Create a derived copy of the dataset with a known return anomaly injected.

The supplied CSV does not contain the brief's "abnormal returns" pattern for the named product (see docs/DATA_PROFILE.md).
This builds a variant where a chosen product genuinely has an excess of returns, so the return detector can be tested
against a real positive. The source file is never modified; the output goes to data/derived/.

    python scripts/make_variant_dataset.py --product P0932 --multiplier 3.5
"""
from __future__ import annotations

import numpy as np
import pandas as pd

VARIANT_ID_BASE = 5_000_000          # variant event ids start well clear of the source range (1..300,000)
REASONS = ["Quality Issue", "Damaged", "Wrong Item"]


def inject_returns(df: pd.DataFrame, product: str, multiplier: float = 3.5, seed: int = 11) -> pd.DataFrame:
    """Return `df` plus extra RETURN rows for `product`, so its return count is about `multiplier` x its original.

    Each extra return is derived from one of the product's real sales (same branch and economics, a few days later),
    so every financial identity in the validation rules still holds.
    """
    rng = np.random.default_rng(seed)
    sales = df[(df.product_id == product) & (df.event_type == "SALE")]
    returns = df[(df.product_id == product) & (df.event_type == "RETURN")]
    if sales.empty:
        raise ValueError(f"No SALE rows for product {product}")
    n_extra = max(int(round(len(returns) * (multiplier - 1))), 1)
    picks = sales.sample(n=n_extra, replace=n_extra > len(sales), random_state=int(rng.integers(1 << 31))).copy()
    last = pd.to_datetime(df.event_timestamp).max()
    delay = pd.to_timedelta(rng.integers(24, 24 * 14, size=len(picks)), unit="h")
    picks["event_timestamp"] = (pd.to_datetime(picks.event_timestamp) + delay).clip(upper=last).dt.strftime("%Y-%m-%d %H:%M:%S")
    picks["event_type"] = "RETURN"
    picks["return_reason"] = rng.choice(REASONS, size=len(picks), p=[0.5, 0.3, 0.2])
    picks["event_id"] = [f"EVT{VARIANT_ID_BASE + i:07d}" for i in range(len(picks))]
    return pd.concat([df, picks[df.columns]], ignore_index=True)
