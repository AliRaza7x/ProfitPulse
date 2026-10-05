"""Analytics job: Spark feature tables -> anomalies, scorecards, leakage, KPIs.

The inputs are small per-entity tables (15 branches, 1,000 products, 80 suppliers),
so this stage runs in pandas where the logic is explainable and unit-tested. The
heavy lifting over the fact tables already happened in Spark (spark/features.py).
Output Parquet goes to lake/analytics/<table>; spark/publish.py loads PostgreSQL.
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from ..config import analytics_config
from ..db import tracked_run
from ..settings import get_settings
from .build import build_kpis, build_leakage
from .detectors import detect_peer, detect_temporal
from .scoring import flag_products, score_branches, score_inventory, score_suppliers

log = logging.getLogger("profitpulse.analytics")

ANOMALY_COLUMNS = {
    "anomaly_id": "string", "as_of_date": "object", "detection_method": "string", "check_id": "string",
    "entity_type": "string", "entity_id": "string", "entity_label": "string", "metric": "string",
    "direction": "string", "observed_value": "float64", "baseline_value": "float64", "baseline_spread": "float64",
    "robust_z": "float64", "severity": "string", "sample_size": "Int64", "estimated_exposure": "float64",
    "annualised_exposure": "float64", "leakage_type": "string", "period_start": "object", "period_end": "object",
    "explanation": "string", "evidence": "string",
}


def _d(value) -> date:
    return value.date() if isinstance(value, (pd.Timestamp, datetime)) else value


def _read(lake_dir: Path, name: str) -> pd.DataFrame:
    """Read a Spark-written table. Spark DECIMAL columns arrive as Decimal objects: use floats for stats."""
    df = pd.read_parquet(lake_dir / "analytics" / name)
    for col in df.columns:
        if df[col].dtype == object:
            first = df[col].dropna().head(1)
            if len(first) and isinstance(first.iloc[0], Decimal):
                df[col] = df[col].astype(float)
    return df


def _write(lake_dir: Path, name: str, df: pd.DataFrame) -> None:
    target = lake_dir / "analytics" / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    df.to_parquet(target / "part-0.parquet", index=False)


def detect_all(branch_base: pd.DataFrame, product_base: pd.DataFrame, supplier_base: pd.DataFrame,
               branch_monthly: pd.DataFrame, supplier_monthly: pd.DataFrame, cfg: dict,
               period_start: date, as_of: date, currency: str) -> pd.DataFrame:
    """Run every configured peer and temporal check."""
    a, w = cfg["anomaly"], cfg["windows"]
    frames = {"branch": branch_base, "product": product_base, "supplier": supplier_base}
    monthly = {"branch": branch_monthly, "supplier": supplier_monthly}
    labels = {str(r.branch_id): f"{r.branch_id} ({r.city})" for r in branch_base.itertuples()}

    found: list[dict] = []
    for check in a["peer_checks"]:
        found += detect_peer(frames[check["entity"]], check, a["severity_z"], period_start, as_of, as_of, currency)
    for check in a["temporal_checks"]:
        found += detect_temporal(monthly[check["entity"]], check, w["temporal_recent_months"],
                                 w["temporal_baseline_months"], as_of, labels if check["entity"] == "branch" else None)
    df = pd.DataFrame(found, columns=list(ANOMALY_COLUMNS))
    df["evidence"] = df["evidence"].map(lambda e: json.dumps(e, default=float) if isinstance(e, dict) else "{}")
    return df.astype({k: v for k, v in ANOMALY_COLUMNS.items() if v != "object"})


def run() -> dict:
    s = get_settings()
    cfg = analytics_config()
    lake = s.lake_dir
    currency = cfg.get("currency", s.currency)
    with tracked_run("analytics") as rh:
        branch_base, product_base = _read(lake, "branch_base"), _read(lake, "product_base")
        supplier_base, inventory_base = _read(lake, "supplier_base"), _read(lake, "inventory_base")
        branch_monthly, supplier_monthly = _read(lake, "branch_monthly"), _read(lake, "supplier_monthly")
        company = _read(lake, "company_monthly")

        as_of = _d(inventory_base["as_of_date"].iloc[0])
        period_start = min(_d(v) for v in branch_base["period_start"])

        anomalies = detect_all(branch_base, product_base, supplier_base, branch_monthly, supplier_monthly,
                               cfg, period_start, as_of, currency)
        branches = score_branches(branch_base, anomalies, cfg)
        suppliers = score_suppliers(supplier_base, anomalies)
        products = flag_products(product_base, anomalies, cfg)
        inventory = score_inventory(inventory_base, cfg)
        leakage = build_leakage(anomalies, products, period_start, as_of)
        kpis = build_kpis(company, branches, products, suppliers, inventory, anomalies, leakage, as_of, period_start)

        outputs = {"branch_scorecard": branches, "product_scorecard": products, "supplier_scorecard": suppliers,
                   "inventory_risk": inventory, "anomalies": anomalies, "revenue_leakage": leakage, "kpi_summary": kpis}
        for name, df in outputs.items():
            _write(lake, name, df)

        rh.rows_out = int(sum(len(d) for d in outputs.values()))
        by_sev = anomalies["severity"].value_counts().to_dict()
        rh.details = {"as_of_date": as_of.isoformat(), "anomalies": len(anomalies), "by_severity": by_sev,
                      "leakage_findings": len(leakage),
                      "leakage_exposure": round(float(leakage["exposure_amount"].sum()), 2) if len(leakage) else 0.0}
        log.info("analytics: %d anomalies %s, %d leakage findings", len(anomalies), by_sev, len(leakage))
        return {"as_of_date": as_of.isoformat(), "anomalies": len(anomalies), **{k: len(v) for k, v in outputs.items()}}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    print(json.dumps(run()))
    sys.exit(0)
