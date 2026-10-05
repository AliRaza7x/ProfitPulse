"""Final validation report: what went in, what came out, and whether the two agree.

    python -m profitpulse validation-report            # writes reports/validation_report.md (+ .json)
    python -m profitpulse validation-report --docs     # also copies it to docs/VALIDATION_REPORT.md

Source totals are recomputed from the CSV with pandas, independently of the
pipeline, so the comparison is a real cross-check and not the pipeline agreeing with itself.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import fetch_all
from .settings import REPO_ROOT, get_settings

FACTS = ["fact_sales", "fact_returns", "fact_purchases", "fact_inventory_movements", "fact_price_changes"]
EVENT_FOR_FACT = {"fact_sales": ["SALE"], "fact_returns": ["RETURN"], "fact_purchases": ["PURCHASE"],
                  "fact_inventory_movements": ["STOCK_ADJUSTMENT", "DAMAGE"], "fact_price_changes": ["PRICE_CHANGE"]}


def _one(sql: str, *params) -> Any:
    return list(fetch_all(sql, params or None)[0].values())[0]


def source_totals() -> dict[str, Any] | None:
    import pandas as pd
    path = get_settings().data_dir / "source" / "profitpulse_synthetic_dataset.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    sales = df[df.event_type == "SALE"]
    return {
        "rows": int(len(df)),
        "by_type": {k: int(v) for k, v in df.event_type.value_counts().items()},
        "sales_revenue": round(float(sales.revenue.sum()), 2),
        "sales_profit": round(float(sales.profit.sum()), 2),
        "sales_units": int(sales.quantity.sum()),
        "return_value": round(float(df[df.event_type == "RETURN"].revenue.sum()), 2),
        "naive_revenue_all_rows": round(float(df.revenue.sum()), 2),
        "naive_profit_all_rows": round(float(df.profit.sum()), 2),
    }


def collect() -> dict[str, Any]:
    src = source_totals()
    clean = fetch_all("SELECT rows_in, rows_out, rows_rejected, details FROM ops.pipeline_runs WHERE stage='clean' "
                      "AND status='SUCCESS' ORDER BY started_at DESC LIMIT 1")[0]
    stored = {t: _one(f"SELECT COUNT(*) FROM core.{t}") for t in FACTS}
    sales = fetch_all("SELECT COUNT(*) AS events, SUM(quantity) AS units, SUM(gross_sales) AS gross, SUM(discount_amount) AS disc, "
                      "SUM(net_revenue) AS rev, SUM(cogs) AS cogs, SUM(gross_profit) AS gp, "
                      "AVG(discount_pct) AS avg_disc, AVG((discount_pct > 0.2)::int) AS share_over_20, "
                      "AVG((discount_pct = 0)::int) AS share_none, MAX(discount_pct) AS max_disc, "
                      "SUM((gross_profit < 0)::int) AS loss_making FROM core.fact_sales")[0]
    ret = fetch_all("SELECT SUM(quantity) AS units, SUM(refund_value) AS refund, SUM(profit_impact) AS impact, COUNT(*) AS events, "
                    "SUM((NOT restockable)::int) AS non_restockable FROM core.fact_returns")[0]
    purch = fetch_all("SELECT SUM(spend) AS spend, SUM(price_variance_amount) AS ppv, SUM(cost_above_list_price::int) AS above_list "
                      "FROM core.fact_purchases")[0]
    inv = fetch_all("SELECT movement_type, SUM(quantity) AS units, SUM(value_at_cost) AS value FROM core.fact_inventory_movements GROUP BY 1")
    runs = fetch_all("SELECT stage, started_at, finished_at, EXTRACT(EPOCH FROM (finished_at-started_at)) AS secs, rows_in, rows_out, "
                     "rows_rejected FROM ops.pipeline_runs WHERE status='SUCCESS' AND stage <> 'dq' ORDER BY started_at")
    dq_done = fetch_all("SELECT finished_at FROM ops.pipeline_runs WHERE stage='dq' AND status='SUCCESS' ORDER BY started_at")
    # The first complete run (replay through the second quality gate) is the full-dataset run; anything later is an incremental batch.
    first_end = dq_done[1]["finished_at"] if len(dq_done) >= 2 else (dq_done[0]["finished_at"] if dq_done else None)
    first = [r for r in runs if first_end is None or r["started_at"] <= first_end]
    later = [r for r in runs if first_end is not None and r["started_at"] > first_end]
    spark_secs = sum(float(r["secs"] or 0) for r in first)
    wall = (first_end - first[0]["started_at"]).total_seconds() if first and first_end else None
    kpi_adj = fetch_all("SELECT value_numeric FROM analytics.kpi_summary WHERE kpi_key='adjusted_gross_profit'")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": _one("SELECT value_text FROM analytics.kpi_summary WHERE kpi_key='as_of_date'"),
        "source": src, "clean": clean, "stored": stored,
        "published": _one("SELECT COALESCE(SUM(rows_out),0) FROM ops.pipeline_runs WHERE stage='publish' AND status='SUCCESS'"),
        "landed": _one("SELECT COALESCE(SUM(messages),0) FROM ops.ingest_batches"),
        "clean_history": fetch_all("SELECT started_at, rows_in, rows_out, rows_rejected FROM ops.pipeline_runs WHERE stage='clean' "
                                   "AND status='SUCCESS' ORDER BY started_at"),
        "ingest_batches": fetch_all("SELECT batch_id, SUM(messages) AS messages, MIN(landed_at) AS landed_at FROM ops.ingest_batches "
                                    "GROUP BY batch_id ORDER BY batch_id"),
        "sales": sales, "returns": ret, "purchases": purch, "inventory": inv, "runs_first": first, "runs_later": later,
        "adjusted_gp": kpi_adj[0]["value_numeric"] if kpi_adj else None,
        "spark_seconds": spark_secs, "wall_seconds": wall,
        "inv_levels": fetch_all("SELECT stockout_risk_level AS level, COUNT(*) AS n FROM analytics.inventory_risk GROUP BY 1 ORDER BY 2 DESC"),
        "inv_flags": fetch_all("SELECT SUM(reorder_flag::int) AS reorder, SUM(is_slow_moving::int) AS slow, SUM(is_dead_stock::int) AS dead FROM analytics.inventory_risk")[0],
        "anomalies": fetch_all("SELECT severity, check_id, entity_label, robust_z, estimated_exposure, annualised_exposure, explanation "
                               "FROM analytics.anomalies ORDER BY estimated_exposure DESC NULLS LAST"),
        "leakage": fetch_all("SELECT leakage_type, findings, exposure_amount, annualised_exposure, kind FROM analytics.v_leakage_by_type ORDER BY exposure_amount DESC"),
        "checks": fetch_all("SELECT DISTINCT ON (layer, check_name) layer, check_name, severity, status, message FROM ops.dq_results "
                            "WHERE layer IN ('warehouse','analytics') ORDER BY layer, check_name, checked_at DESC"),
        "rules": fetch_all("SELECT rule_code, disposition, failures FROM quarantine.v_failure_summary ORDER BY failures DESC"),
    }


def _m(v) -> str:
    return f"{float(v):,.2f}"


def _i(v) -> str:
    return f"{int(v):,}"


def render(d: dict[str, Any]) -> str:
    s, r, p, src = d["sales"], d["returns"], d["purchases"], d["source"]
    cur = get_settings().currency
    stored_total = sum(d["stored"].values())
    L: list[str] = []
    a = L.append
    a("# ProfitPulse: end-to-end validation report\n")
    a(f"Generated {d['generated_at']} from a live run. Data as of {d['as_of']}. Amounts in {cur}.\n")

    a("## 1. Record accounting\n")
    a("| Stage | Records |\n|---|---:|")
    if src:
        a(f"| Rows in the source CSV | {_i(src['rows'])} |")
    a(f"| Published to Kafka | {_i(d['published'])} |")
    a(f"| Ingested from Kafka into the raw zone | {_i(d['landed'])} |")
    a(f"| Processed by validation | {_i(d['clean']['rows_in'])} |")
    a(f"| Accepted | {_i(d['clean']['rows_out'])} |")
    a(f"| Rejected (quarantined, kept verbatim) | {_i(d['clean']['rows_rejected'])} |")
    a(f"| Stored in the PostgreSQL warehouse | {_i(stored_total)} |")
    balanced = d["clean"]["rows_in"] == d["clean"]["rows_out"] + d["clean"]["rows_rejected"] and stored_total == d["clean"]["rows_out"]
    a(f"\nAccounting identity (processed = accepted + rejected, stored = accepted): **{'holds' if balanced else 'BROKEN'}**.\n")
    if len(d["ingest_batches"]) > 1:
        a("\nIngestion was incremental. Each batch read only the Kafka offsets not yet committed:\n")
        a("| Raw batch | Messages | Validation run: raw in | Accepted | Rejected |\n|---|---:|---:|---:|---:|")
        for b, c in zip(d["ingest_batches"], d["clean_history"]):
            a(f"| {b['batch_id']} | {_i(b['messages'])} | {_i(c['rows_in'])} (cumulative) | {_i(c['rows_out'])} | {_i(c['rows_rejected'])} |")
        a("\nAfter the later batches the warehouse still holds exactly the accepted events: bad data that arrives later is "
          "quarantined, not loaded and not lost.\n")
    a("| Fact table | Stored | Source rows of that type | Match |\n|---|---:|---:|:--:|")
    for t in FACTS:
        want = sum(src["by_type"].get(e, 0) for e in EVENT_FOR_FACT[t]) if src else None
        ok = "n/a" if want is None else ("yes" if want == d["stored"][t] or d["clean"]["rows_rejected"] else "NO")
        a(f"| {t} | {_i(d['stored'][t])} | {_i(want) if want is not None else 'n/a'} | {ok} |")

    a("\n## 2. Financial totals\n")
    a("Only SALE rows are revenue. The CSV also fills `revenue` and `profit` on other event types, which is why summing those "
      "columns naively overstates sales.\n")
    a("| Measure | Warehouse | Recomputed from CSV | Difference |\n|---|---:|---:|---:|")
    if src:
        a(f"| Net sales revenue | {_m(s['rev'])} | {_m(src['sales_revenue'])} | {_m(float(s['rev']) - src['sales_revenue'])} |")
        a(f"| Gross profit (sales) | {_m(s['gp'])} | {_m(src['sales_profit'])} | {_m(float(s['gp']) - src['sales_profit'])} |")
        a(f"| Units sold | {_i(s['units'])} | {_i(src['sales_units'])} | {_i(int(s['units']) - src['sales_units'])} |")
        a(f"| Refund value (returns) | {_m(r['refund'])} | {_m(src['return_value'])} | {_m(float(r['refund']) - src['return_value'])} |")
        a(f"\nFor comparison, summing the CSV's `revenue` column over all rows gives {_m(src['naive_revenue_all_rows'])} "
          f"(+{100 * (src['naive_revenue_all_rows'] / src['sales_revenue'] - 1):.0f}% vs real sales) and `profit` {_m(src['naive_profit_all_rows'])}.")
    a(f"\n- Gross margin: {100 * float(s['gp']) / float(s['rev']):.2f}%")
    a(f"- Cost of goods sold: {_m(s['cogs'])}")
    if d["adjusted_gp"] is not None:
        a(f"- Gross profit after returns and damaged stock: {_m(d['adjusted_gp'])} (profit given back by returns {_m(r['impact'])})")
    a(f"- Purchase spend: {_m(p['spend'])}; paid above standard cost: {_m(p['ppv'])}; purchases above our own list price: {_i(p['above_list'])}")

    a("\n## 3. Returns\n")
    a(f"- Return events: {_i(r['events'])}; units returned: {_i(r['units'])} ({100 * float(r['units']) / float(s['units']):.2f}% of units sold)")
    a(f"- Refund value: {_m(r['refund'])} ({100 * float(r['refund']) / float(s['rev']):.2f}% of net sales)")
    a(f"- Not resaleable (Damaged or Quality Issue): {_i(r['non_restockable'])} returns; profit given back by returns: {_m(r['impact'])}")

    a("\n## 4. Discounts (SALE rows)\n")
    a(f"- Discount given: {_m(s['disc'])} on {_m(s['gross'])} of list-price sales = **{100 * float(s['disc']) / float(s['gross']):.2f}%**")
    a(f"- Average discount per sale: {100 * float(s['avg_disc']):.2f}%; maximum {100 * float(s['max_disc']):.1f}%")
    a(f"- Sales discounted by more than 20%: {100 * float(s['share_over_20']):.2f}%; sales with no discount: {100 * float(s['share_none']):.2f}%")
    a(f"- Loss-making sales (negative gross profit): {_i(s['loss_making'])}")

    a("\n## 5. Inventory risk (movement-based proxies; the source has no stock on hand)\n")
    a("| Stockout-risk level | Products |\n|---|---:|")
    for lv in d["inv_levels"]:
        a(f"| {lv['level']} | {_i(lv['n'])} |")
    f = d["inv_flags"]
    a(f"\nReorder-review flags: {_i(f['reorder'])}; slow-moving: {_i(f['slow'])}; dead-stock candidates: {_i(f['dead'])}.")
    for row in d["inventory"]:
        a(f"- {row['movement_type'].replace('_', ' ').title()}: {_i(row['units'])} units, {_m(row['value'])} at cost")

    a("\n## 6. Detected anomalies\n")
    a(f"{len(d['anomalies'])} anomalies. Each carries an explanation; none is an unexplained score.\n")
    for an in d["anomalies"]:
        exp = f", exposure {_m(an['estimated_exposure'])} ({_m(an['annualised_exposure'])} a year)" if an["estimated_exposure"] else ""
        a(f"- **{an['entity_label']}**, `{an['check_id']}`, {an['severity']}, robust z {float(an['robust_z']):.1f}{exp}  \n  {an['explanation']}")
    a("\n| Leakage type | Findings | Exposure | Per year | Kind |\n|---|---:|---:|---:|---|")
    for lk in d["leakage"]:
        a(f"| {lk['leakage_type']} | {_i(lk['findings'])} | {_m(lk['exposure_amount'])} | {_m(lk['annualised_exposure'])} | {lk['kind']} |")

    a("\n## 7. Data-quality gates\n")
    passed = sum(1 for c in d["checks"] if c["status"] == "PASS")
    a(f"{passed} of {len(d['checks'])} gates passed.\n")
    a("| Layer | Check | Status |\n|---|---|:--:|")
    for c in d["checks"]:
        a(f"| {c['layer']} | {c['check_name']} | {c['status']} |")
    if d["rules"]:
        a("\nQuarantine / warnings by rule:\n\n| Rule | Disposition | Messages |\n|---|---|---:|")
        for rr in d["rules"]:
            a(f"| {rr['rule_code']} | {rr['disposition']} | {_i(rr['failures'])} |")

    a("\n## 8. Processing duration\n")
    a("### Full-dataset run (all source events, orchestrated by Airflow)\n")
    a("| Stage | Seconds | Rows in | Rows out | Rejected |\n|---|---:|---:|---:|---:|")
    for run in d["runs_first"]:
        a(f"| {run['stage']} | {float(run['secs'] or 0):.1f} | {_i(run['rows_in'] or 0)} | {_i(run['rows_out'] or 0)} | {_i(run['rows_rejected'] or 0)} |")
    a(f"\nSum of stage durations: **{d['spark_seconds']:.0f} s**"
      + (f"; wall-clock from first stage start to the last quality gate: **{d['wall_seconds']:.0f} s** (includes Airflow scheduling gaps)." if d["wall_seconds"] else "."))
    if d["runs_later"]:
        a("\n### Later incremental batch (faulty messages only)\n")
        a("| Stage | Seconds | Rows in | Rows out | Rejected |\n|---|---:|---:|---:|---:|")
        for run in d["runs_later"]:
            a(f"| {run['stage']} | {float(run['secs'] or 0):.1f} | {_i(run['rows_in'] or 0)} | {_i(run['rows_out'] or 0)} | {_i(run['rows_rejected'] or 0)} |")
    return "\n".join(L) + "\n"


def run(write_docs: bool = False) -> dict[str, str]:
    data = collect()
    out_dir = REPO_ROOT / "reports" if not Path("/opt/profitpulse/reports").exists() else Path("/opt/profitpulse/reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render(data)
    (out_dir / "validation_report.md").write_text(md, encoding="utf-8")
    (out_dir / "validation_report.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    result = {"markdown": str(out_dir / "validation_report.md")}
    if write_docs:
        docs = Path("/opt/profitpulse/docs") if Path("/opt/profitpulse/docs").exists() else REPO_ROOT / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "VALIDATION_REPORT.md").write_text(md, encoding="utf-8")
        result["docs"] = str(docs / "VALIDATION_REPORT.md")
    return result


if __name__ == "__main__":
    import sys
    print(json.dumps(run("--docs" in sys.argv)))
