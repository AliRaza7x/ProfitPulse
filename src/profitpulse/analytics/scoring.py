"""Scorecards: branch performance score, product flags, supplier risk, inventory risk proxies.

Every score is a weighted combination of percentile ranks with weights read from
config/analytics.yaml, and every component is stored beside the total so a user
can see exactly why a branch scored what it did.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .detectors import SEVERITY_ORDER
from .explain import explain_stockout

_BRANCH_SCORE_COLS = {
    "gross_margin": "score_gross_margin",
    "discount_rate": "score_discount_rate",
    "return_rate_units": "score_return_rate",
    "shrink_rate": "score_shrink_rate",
    "growth_rate": "score_growth_rate",
}


def pct_rank(series: pd.Series, higher_is_better: bool = True) -> pd.Series:
    """Percentile rank scaled 0..1 where 1 is the best entity. Missing values score a neutral 0.5."""
    n = series.notna().sum()
    if n <= 1:
        return pd.Series(0.5, index=series.index)
    r = (series.rank(method="average") - 1) / (n - 1)
    r = r if higher_is_better else 1 - r
    return r.fillna(0.5)


def robust_component(series: pd.Series, higher_is_better: bool, min_scale: float, z_cap: float = 3.0) -> pd.Series:
    """Magnitude-aware 0..1 score: 0.5 at the peer median, 0 at z_cap robust SDs worse, 1 at z_cap better.

    Missing values score a neutral 0.5. Unlike a percentile rank, a gap of ten robust SDs
    costs more than a gap of one (up to the cap), so noise-level differences cannot outweigh a real outlier.
    """
    values = series.astype(float)
    if values.notna().sum() < 3:
        return pd.Series(0.5, index=series.index)
    med = values.median()
    scale = max(1.4826 * (values - med).abs().median(), min_scale)
    z = (values - med) / scale
    z = z if higher_is_better else -z
    return (0.5 + 0.5 * (z / z_cap).clip(-1, 1)).fillna(0.5)


def _anomaly_rollup(anomalies: pd.DataFrame, entity_type: str) -> pd.DataFrame:
    """Per entity: anomaly_count, max_anomaly_severity."""
    if anomalies.empty:
        return pd.DataFrame(columns=["entity_id", "anomaly_count", "max_anomaly_severity"])
    sub = anomalies[anomalies["entity_type"] == entity_type]
    if sub.empty:
        return pd.DataFrame(columns=["entity_id", "anomaly_count", "max_anomaly_severity"])
    rank = sub["severity"].map(SEVERITY_ORDER)
    g = sub.assign(_r=rank).groupby("entity_id")
    out = g.agg(anomaly_count=("anomaly_id", "count"), _r=("_r", "max")).reset_index()
    inv = {v: k for k, v in SEVERITY_ORDER.items()}
    out["max_anomaly_severity"] = out["_r"].map(inv)
    return out.drop(columns="_r")


def _risk_from_severity(sev: pd.Series) -> pd.Series:
    return sev.map({"critical": "high", "high": "high", "medium": "medium"}).fillna("low")


def score_branches(base: pd.DataFrame, anomalies: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    comps = cfg["branch_score"]["components"]
    bands = cfg["branch_score"]["bands"]
    z_cap = float(cfg["branch_score"].get("z_cap", 3.0))
    df = base.copy()
    total = pd.Series(0.0, index=df.index)
    for metric, spec in comps.items():
        comp = robust_component(df[metric], spec["higher_is_better"], spec["min_scale"], z_cap)
        df[_BRANCH_SCORE_COLS[metric]] = comp
        total += spec["weight"] * comp
    df["performance_score"] = (total * 100).round(1)
    df["performance_band"] = np.where(df["performance_score"] >= bands["strong"], "strong",
                                      np.where(df["performance_score"] >= bands["watch"], "watch", "weak"))
    df["performance_rank"] = df["performance_score"].rank(ascending=False, method="min").astype(int)
    roll = _anomaly_rollup(anomalies, "branch").rename(columns={"entity_id": "branch_id"})
    df = df.merge(roll, on="branch_id", how="left")
    df["anomaly_count"] = df["anomaly_count"].fillna(0).astype(int)
    df["operational_risk"] = _risk_from_severity(df["max_anomaly_severity"])
    return df


def score_suppliers(base: pd.DataFrame, anomalies: pd.DataFrame) -> pd.DataFrame:
    df = base.copy()
    roll = _anomaly_rollup(anomalies, "supplier").rename(columns={"entity_id": "supplier_id"})
    df = df.merge(roll, on="supplier_id", how="left")
    df["anomaly_count"] = df["anomaly_count"].fillna(0).astype(int)
    df["risk_level"] = _risk_from_severity(df["max_anomaly_severity"])
    return df.drop(columns=["max_anomaly_severity"])


def flag_products(base: pd.DataFrame, anomalies: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    pc = cfg["product"]
    df = base.copy()
    eligible = df["sale_events"] >= pc["min_sale_events"]

    df["margin_percentile"] = df.groupby("category")["gross_margin"].transform(lambda s: pct_rank(s, True))
    df["is_high_margin"] = eligible & (df["margin_percentile"] >= pc["high_margin_percentile"] / 100)
    df["is_low_margin"] = eligible & (df["margin_percentile"] <= pc["low_margin_percentile"] / 100)

    ret_cut = df.loc[eligible, "return_rate_units"].quantile(0.90)
    disc_cut = df.loc[eligible, "discount_rate"].quantile(0.90)
    df["is_high_return"] = eligible & (df["return_rate_units"] >= ret_cut)
    df["is_high_discount"] = eligible & (df["discount_rate"] >= disc_cut)
    df["is_declining"] = eligible & (df["growth_rate"] <= pc["declining_growth_threshold"])

    roll = _anomaly_rollup(anomalies, "product").rename(columns={"entity_id": "product_id"})
    df = df.merge(roll.drop(columns="max_anomaly_severity"), on="product_id", how="left")
    df["anomaly_count"] = df["anomaly_count"].fillna(0).astype(int)

    def reasons(r: pd.Series) -> list[str]:
        out = []
        if r["anomaly_count"] > 0:
            out.append("statistically abnormal vs peers (see anomalies)")
        if r["is_low_margin"]:
            out.append(f"margin {r['gross_margin']:.1%} is in the bottom decile of {r['category']}")
        if r["is_high_return"]:
            out.append(f"return rate {r['return_rate_units']:.1%} is in the top decile of all products")
        if r["is_high_discount"]:
            out.append(f"discount rate {r['discount_rate']:.1%} is in the top decile of all products")
        if r["is_declining"]:
            out.append(f"revenue fell {abs(r['growth_rate']):.0%} vs the previous period")
        return out

    rs = df.apply(reasons, axis=1)
    flag_count = df[["is_low_margin", "is_high_return", "is_high_discount", "is_declining"]].sum(axis=1)
    df["is_problematic"] = (df["anomaly_count"] > 0) | (flag_count >= 2)
    df["problem_reasons"] = [("; ".join(r) if (p and r) else None) for r, p in zip(rs, df["is_problematic"])]
    return df


def score_inventory(base: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Movement-based proxy for stockout risk. NOT a stock level: the source has none."""
    ic = cfg["inventory"]
    w = ic["stockout_proxy"]["weights"]
    bands = ic["stockout_proxy"]["bands"]
    wins = cfg["windows"]
    df = base.copy()

    df["pct_velocity"] = pct_rank(df["velocity_per_day"], higher_is_better=True)               # fast sellers deplete sooner
    df["pct_replenishment_gap"] = pct_rank(df["replenishment_ratio"], higher_is_better=False)  # least restocked = riskiest
    recency = df["days_since_last_purchase"].astype(float)
    df["pct_purchase_recency"] = pct_rank(recency.fillna(recency.max() if recency.notna().any() else 0), True)

    score = (w["velocity"] * df["pct_velocity"] + w["replenishment_gap"] * df["pct_replenishment_gap"]
             + w["purchase_recency"] * df["pct_purchase_recency"]) * 100
    df["stockout_risk_score"] = score.round(1)
    df["stockout_risk_level"] = np.where(score >= bands["high"], "high",
                                         np.where(score >= bands["elevated"], "elevated", "normal"))

    slow_cut = df.groupby("category")["velocity_per_day"].transform(
        lambda s: s.quantile(ic["slow_moving_percentile"] / 100))
    df["is_slow_moving"] = (df["velocity_per_day"] <= slow_cut) & df["velocity_per_day"].notna()
    no_sale = df["days_since_last_sale"].isna()
    df["is_dead_stock"] = no_sale | (df["days_since_last_sale"] > ic["dead_stock_days_no_sale"])
    median_vel = df["velocity_per_day"].median()
    stale = df["days_since_last_purchase"].fillna(10**6) >= ic["reorder_min_days_since_purchase"]
    df["reorder_flag"] = stale & (df["velocity_per_day"].fillna(0) >= median_vel)

    df["explanation"] = [
        explain_stockout(
            velocity=None if pd.isna(r.velocity_per_day) else float(r.velocity_per_day), vel_pct=float(r.pct_velocity),
            replenishment=None if pd.isna(r.replenishment_ratio) else float(r.replenishment_ratio),
            days_purchase=None if pd.isna(r.days_since_last_purchase) else int(r.days_since_last_purchase),
            velocity_days=wins["velocity_days"], window_days=wins["replenishment_days"])
        for r in df.itertuples()]
    return df
