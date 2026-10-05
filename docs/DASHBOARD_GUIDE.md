# BI dashboard guide (Power BI or any SQL-capable tool)

The built-in web UI (`http://localhost:18000`) covers these pages, and every one reads from tables you can query directly. This guide
describes the seven recommended pages and which tables feed them.

## Connecting

| Setting | Value |
|---|---|
| Server | `localhost` |
| Port | `15432` (the host port from `.env`; PostgreSQL inside the container is 5432) |
| Database | `profitpulse` |
| User | `pp_reader` (read-only; password is `PP_READER_PASSWORD` in your `.env`) |
| Mode | Import for the `analytics` schema (small); DirectQuery for `core` if you want row-level drill-through |

The reader role can read `core`, `analytics`, `ops` and `quarantine`. It cannot write and cannot see `staging`.

## Pages

### 1. Executive overview
**Tables:** `analytics.kpi_summary` (single-value cards), `analytics.company_monthly` (trend), `analytics.v_leakage_by_type`, `analytics.v_anomaly_feed`.
**Visuals:** a one-sentence headline (a text card driven by `kpi_summary`), cards for net sales, gross profit and margin, discount rate, return rate,
leakage per year; a line chart of monthly net sales and gross profit; a stacked bar of detected leakage by type; a table of the largest anomalies
showing the `explanation` column verbatim.
**Tip:** keep `LOW_MARGIN_SHORTFALL` apart from the others (`kind = 'opportunity'` in `v_leakage_by_type`); adding it to detected leakage overstates it.

### 2. Revenue leakage
**Tables:** `analytics.revenue_leakage`, `analytics.anomalies`.
**Visuals:** bars of exposure by type and entity; a table with `entity_label`, `exposure_amount`, `annualised_exposure` and `explanation`; a dot plot of
peer values per check (take `analytics.branch_scorecard` / `supplier_scorecard` / `product_scorecard`, plot the metric named in `anomalies.metric` for all entities and
highlight the flagged `entity_id`).

### 3. Inventory intelligence
**Tables:** `analytics.inventory_risk`, `analytics.inventory_branch_category`, `analytics.inventory_movement_monthly`.
**Visuals:** units purchased vs sold by month; a branch x category matrix of `replenishment_ratio` (conditional colour); a ranked table by `stockout_risk_score` with the `explanation`.
**Caveat to show on the page:** these are movement-based proxies, because the source holds no stock on hand.

### 4. Branch performance
**Tables:** `analytics.branch_scorecard`, `analytics.branch_monthly`.
**Visuals:** scatter of `gross_margin` vs `discount_rate` sized by `net_revenue`; a ranked scorecard with `performance_score`, `performance_band` and the five `score_*` component columns (a
stacked bar shows exactly where points were lost); monthly trend of any branch against the peer median.

### 5. Supplier intelligence
**Tables:** `analytics.supplier_scorecard`, `analytics.supplier_monthly`.
**Visuals:** bars of `ppv_amount` (spend above standard cost); table with `ppv_pct`, `share_above_standard`, `purchases_above_list`, `cadence_cv`, `product_return_rate`, `risk_level`;
monthly `ppv_pct` for flagged suppliers.

### 6. Product intelligence
**Tables:** `analytics.product_scorecard`.
**Visuals:** slicer on the flags (`is_low_margin`, `is_high_margin`, `is_high_return`, `is_high_discount`, `is_declining`, `is_problematic`); table with `problem_reasons`; category
summary with margin and return rate.

### 7. Data quality and pipeline health
**Tables:** `ops.v_pipeline_health`, `ops.dq_results`, `quarantine.v_failure_summary`, `quarantine.dq_failures`, `ops.pipeline_runs`.
**Visuals:** the funnel published → read → accepted → quarantined → stored (all must reconcile); stage durations; the latest status of each quality gate; quarantine rows by rule with a drill-through to
the raw payload.

## Starter measures (DAX)

```
Discount Rate = DIVIDE(SUM(v_sales_detail[discount_amount]), SUM(v_sales_detail[gross_sales]))
Gross Margin  = DIVIDE(SUM(v_sales_detail[gross_profit]), SUM(v_sales_detail[net_revenue]))
Detected Leakage per Year =
    CALCULATE(SUM(v_leakage_by_type[annualised_exposure]), v_leakage_by_type[kind] = "detected")
```

Use `analytics.v_sales_detail` (sales joined to date, branch and product) when you want to slice sales freely. It queries `core` directly,
so prefer DirectQuery or a filtered import.
