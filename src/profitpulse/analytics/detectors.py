"""Explainable anomaly detection: robust peer comparison and change-vs-history.

Peer check   robust z = (x - median(peers)) / max(1.4826 * MAD(peers), min_scale)
             A finding needs z above the severity threshold AND a practically
             meaningful gap (min_abs_effect) AND enough observations (min_n).
Temporal     mean of the most recent months vs the mean/std of the baseline
             months before them, z = diff / (max(std, min_std) / sqrt(recent)).

Median/MAD are used instead of mean/std so that the anomaly itself cannot drag
the baseline toward itself (a 22% discount rate would inflate a mean/std baseline
and mask its own signal). Nothing here is hard-coded to an entity.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from .explain import explain_peer, explain_temporal

ID_COL = {"branch": "branch_id", "product": "product_id", "supplier": "supplier_id"}
SEVERITY_ORDER = {"medium": 1, "high": 2, "critical": 3}


def robust_baseline(values: np.ndarray, min_scale: float) -> tuple[float, float]:
    """(median, scale) where scale = 1.4826 * MAD, floored at min_scale."""
    med = float(np.median(values))
    mad = float(np.median(np.abs(values - med)))
    return med, max(1.4826 * mad, min_scale)


def severity_for(z: float, thresholds: dict[str, float]) -> str | None:
    if z >= thresholds["critical"]:
        return "critical"
    if z >= thresholds["high"]:
        return "high"
    if z >= thresholds["medium"]:
        return "medium"
    return None


def entity_label(entity: str, row: pd.Series) -> str:
    key = row[ID_COL[entity]]
    if entity == "branch" and "city" in row and pd.notna(row["city"]):
        return f"{key} ({row['city']})"
    if entity == "product" and "category" in row and pd.notna(row["category"]):
        return f"{key} ({row['category']})"
    return str(key)


def detect_peer(df: pd.DataFrame, check: dict[str, Any], default_thresholds: dict[str, float],
                period_start: date, period_end: date, as_of: date, currency: str) -> list[dict[str, Any]]:
    """Run one peer check over an entity metrics table. Returns anomaly records."""
    entity, metric, direction = check["entity"], check["metric"], check["direction"]
    thresholds = check.get("severity_z", default_thresholds)
    idc = ID_COL[entity]

    usable = df[df[metric].notna() & (df[check["n_col"]] >= check["min_n"])]
    if len(usable) < 5:                                  # too few peers for a meaningful baseline
        return []
    values = usable[metric].astype(float).to_numpy()
    median, scale = robust_baseline(values, check["min_scale"])
    years = max((period_end - period_start).days, 1) / 365.0

    out: list[dict[str, Any]] = []
    for _, row in usable.iterrows():
        x = float(row[metric])
        gap = (x - median) if direction == "high" else (median - x)
        z = gap / scale
        severity = severity_for(z, thresholds)
        if severity is None or gap < check["min_abs_effect"]:
            continue
        base = float(row[check["exposure_base"]]) if check.get("exposure_base") in row and pd.notna(row[check["exposure_base"]]) else None
        exposure = round(gap * base, 2) if base is not None else None
        annualised = round(exposure / years, 2) if exposure is not None else None
        label = entity_label(entity, row)
        n = int(row[check["n_col"]])
        out.append({
            "anomaly_id": f"{check['id']}:{row[idc]}",
            "as_of_date": as_of,
            "detection_method": "PEER",
            "check_id": check["id"],
            "entity_type": entity,
            "entity_id": str(row[idc]),
            "entity_label": label,
            "metric": metric,
            "direction": direction,
            "observed_value": x,
            "baseline_value": median,
            "baseline_spread": scale,
            "robust_z": round(z, 2),
            "severity": severity,
            "sample_size": n,
            "estimated_exposure": exposure,
            "annualised_exposure": annualised,
            "leakage_type": check.get("leakage_type"),
            "period_start": period_start,
            "period_end": period_end,
            "explanation": explain_peer(
                label=label, metric=metric, direction=direction, observed=x, baseline=median, gap=gap, z=z,
                severity=severity, n=n, n_col=check["n_col"], peers=len(usable), entity=entity,
                exposure=exposure, annualised=annualised, years=years, currency=currency),
            "evidence": {"peer_median": median, "peer_scale": scale, "peers": int(len(usable)),
                         "gap": gap, "exposure_base_column": check.get("exposure_base"), "exposure_base": base},
        })
    return out


def detect_temporal(monthly: pd.DataFrame, check: dict[str, Any], recent_months: int, baseline_months: int,
                    as_of: date, labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Compare each entity's recent months against its own preceding baseline."""
    entity, metric, direction = check["entity"], check["metric"], check["direction"]
    idc = ID_COL[entity]
    out: list[dict[str, Any]] = []
    for key, grp in monthly.dropna(subset=[metric]).sort_values("year_month").groupby(idc):
        series = grp[metric].astype(float).to_numpy()
        if len(series) < recent_months + max(6, baseline_months // 2):
            continue
        recent = series[-recent_months:]
        base = series[-(recent_months + baseline_months):-recent_months]
        base_mean, base_std = float(base.mean()), float(base.std(ddof=1)) if len(base) > 1 else 0.0
        recent_mean = float(recent.mean())
        gap = (recent_mean - base_mean) if direction == "high" else (base_mean - recent_mean)
        spread = max(base_std, check["min_std"])
        z = gap / (spread / math.sqrt(len(recent)))
        if z < check["z_threshold"] or gap < check["min_abs_effect"]:
            continue
        threshold = check["z_threshold"]
        severity = "critical" if z >= 2.5 * threshold else "high" if z >= 1.5 * threshold else "medium"
        label = (labels or {}).get(str(key), str(key))
        out.append({
            "anomaly_id": f"{check['id']}:{key}",
            "as_of_date": as_of,
            "detection_method": "TEMPORAL",
            "check_id": check["id"],
            "entity_type": entity,
            "entity_id": str(key),
            "entity_label": label,
            "metric": metric,
            "direction": direction,
            "observed_value": recent_mean,
            "baseline_value": base_mean,
            "baseline_spread": spread,
            "robust_z": round(z, 2),
            "severity": severity,
            "sample_size": int(len(series)),
            "estimated_exposure": None,
            "annualised_exposure": None,
            "leakage_type": None,
            "period_start": None,
            "period_end": None,
            "explanation": explain_temporal(
                label=label, metric=metric, direction=direction, recent=recent_mean, baseline=base_mean,
                spread=spread, gap=gap, z=z, severity=severity, recent_months=recent_months,
                baseline_months=len(base)),
            "evidence": {"recent_months": recent_months, "baseline_months": int(len(base)), "gap": gap},
        })
    return out


def worst_severity(severities: list[str]) -> str | None:
    return max(severities, key=lambda s: SEVERITY_ORDER[s]) if severities else None
