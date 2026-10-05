"""Anomaly detection: finds injected outliers without being told their IDs, and explains them."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from profitpulse.analytics.detectors import detect_peer, detect_temporal, robust_baseline, severity_for

PERIOD = (date(2024, 1, 1), date(2026, 9, 30))
THRESHOLDS = {"medium": 3.5, "high": 6.0, "critical": 10.0}


def branches(n=15, outlier=None, base=0.062, jitter=0.002, outlier_value=0.22, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        rate = base + rng.normal(0, jitter)
        if outlier is not None and i == outlier:
            rate = outlier_value
        rows.append({"branch_id": f"BR{i + 1:02d}", "city": f"City{i}", "discount_rate": rate,
                     "sale_events": 13000, "gross_sales": 100_000_000.0})
    return pd.DataFrame(rows)


CHECK = {"id": "branch_discount_rate", "entity": "branch", "metric": "discount_rate", "direction": "high",
         "min_scale": 0.005, "min_abs_effect": 0.02, "min_n": 200, "n_col": "sale_events",
         "exposure_base": "gross_sales", "leakage_type": "EXCESS_DISCOUNT"}


def run_check(df, check=CHECK):
    return detect_peer(df, check, THRESHOLDS, PERIOD[0], PERIOD[1], PERIOD[1], "PKR")


def test_robust_baseline_is_not_dragged_by_the_outlier():
    values = np.array([0.06, 0.061, 0.062, 0.063, 0.064, 0.22])
    median, scale = robust_baseline(values, min_scale=0.005)
    assert 0.061 < median < 0.064 and scale < 0.01          # a mean/std baseline would put 0.22 within reach


def test_scale_is_floored_so_constant_peers_do_not_produce_infinite_z():
    median, scale = robust_baseline(np.zeros(10), min_scale=0.005)
    assert median == 0 and scale == 0.005


def test_outlier_branch_is_found_by_data_not_by_name():
    df = branches(outlier=6)
    found = run_check(df)
    assert [a["entity_id"] for a in found] == ["BR07"]
    a = found[0]
    assert a["severity"] == "critical" and a["robust_z"] > 10
    assert a["observed_value"] == pytest.approx(0.22) and a["baseline_value"] == pytest.approx(0.062, abs=0.005)


def test_detection_follows_the_data_when_the_outlier_moves():
    assert [a["entity_id"] for a in run_check(branches(outlier=2))] == ["BR03"]
    assert [a["entity_id"] for a in run_check(branches(outlier=11))] == ["BR12"]


def test_no_outlier_no_findings():
    assert run_check(branches(outlier=None)) == []


def test_statistically_odd_but_practically_small_gaps_are_ignored():
    df = branches(outlier=4, base=0.062, jitter=0.0005, outlier_value=0.075)   # large z, 1.3pt gap < 2pt floor
    assert run_check(df) == []


def test_small_samples_are_excluded():
    df = branches(outlier=4)
    df["sale_events"] = 50
    assert run_check(df) == []


def test_exposure_is_gap_times_base_and_annualised():
    a = run_check(branches(outlier=0))[0]
    gap = a["observed_value"] - a["baseline_value"]
    assert a["estimated_exposure"] == pytest.approx(gap * 100_000_000.0, rel=1e-6)
    years = (PERIOD[1] - PERIOD[0]).days / 365
    assert a["annualised_exposure"] == pytest.approx(a["estimated_exposure"] / years, rel=1e-3)


def test_explanation_is_plain_english_with_evidence():
    text = run_check(branches(outlier=6))[0]["explanation"]
    for needle in ["BR07 (City6)", "discount rate", "22.0%", "peer median", "robust z", "critical",
                   "13,000 sales", "PKR", "a year"]:
        assert needle in text, f"{needle!r} missing from: {text}"


def test_low_direction_flags_unusually_low_values():
    df = branches(outlier=1, outlier_value=0.0)
    df = df.rename(columns={"discount_rate": "gross_margin"})
    df["gross_margin"] = 0.24 + (df["gross_margin"] - 0.062)
    df.loc[1, "gross_margin"] = 0.10
    check = dict(CHECK, id="m", metric="gross_margin", direction="low", exposure_base="gross_sales")
    found = run_check(df, check)
    assert [a["entity_id"] for a in found] == ["BR02"]
    assert "lower" in found[0]["explanation"]


def test_severity_thresholds():
    assert severity_for(3.4, THRESHOLDS) is None
    assert severity_for(3.5, THRESHOLDS) == "medium"
    assert severity_for(6.0, THRESHOLDS) == "high"
    assert severity_for(12, THRESHOLDS) == "critical"


# ---- temporal ---------------------------------------------------------------
def monthly(entity="BR01", shift_from=None, shift=0.0, n=24, seed=1):
    rng = np.random.default_rng(seed)
    months = pd.period_range("2024-10", periods=n, freq="M").astype(str)
    vals = 0.06 + rng.normal(0, 0.003, n)
    if shift_from is not None:
        vals[shift_from:] += shift
    return pd.DataFrame({"branch_id": entity, "year_month": months, "discount_rate": vals})


TCHECK = {"id": "branch_discount_shift", "entity": "branch", "metric": "discount_rate", "direction": "high",
          "min_abs_effect": 0.02, "min_std": 0.005, "z_threshold": 4.0}


def test_temporal_detects_a_recent_regime_change():
    found = detect_temporal(monthly(shift_from=21, shift=0.08), TCHECK, 3, 12, date(2026, 9, 30))
    assert len(found) == 1 and found[0]["detection_method"] == "TEMPORAL"
    assert found[0]["observed_value"] > found[0]["baseline_value"] + 0.05
    assert "last 3 months" in found[0]["explanation"] and "own history" in found[0]["explanation"]


def test_temporal_ignores_stable_series_and_a_persistent_high_level():
    assert detect_temporal(monthly(), TCHECK, 3, 12, date(2026, 9, 30)) == []
    persistent = monthly(shift_from=0, shift=0.16)      # always high: a peer problem, not a change
    assert detect_temporal(persistent, TCHECK, 3, 12, date(2026, 9, 30)) == []


def test_temporal_needs_enough_history():
    assert detect_temporal(monthly(n=8, shift_from=5, shift=0.1), TCHECK, 3, 12, date(2026, 9, 30)) == []
