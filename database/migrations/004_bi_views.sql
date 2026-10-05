-- Views intended for Power BI / any SQL-speaking BI tool and for the web UI.
-- Stable names, business-friendly columns, no joins required by the consumer.

CREATE OR REPLACE VIEW analytics.v_executive_overview AS
SELECT s.kpi_key, s.section, s.label, s.value_numeric, s.value_text, s.unit, s.sort_order
FROM analytics.kpi_summary s;

CREATE OR REPLACE VIEW analytics.v_leakage_by_type AS
SELECT leakage_type,
       COUNT(*)                         AS findings,
       SUM(exposure_amount)             AS exposure_amount,
       SUM(annualised_exposure)         AS annualised_exposure
FROM analytics.revenue_leakage
GROUP BY leakage_type;

CREATE OR REPLACE VIEW analytics.v_anomaly_feed AS
SELECT a.anomaly_id, a.severity, a.entity_type, a.entity_id, a.entity_label, a.check_id,
       a.detection_method, a.metric, a.observed_value, a.baseline_value, a.robust_z,
       a.estimated_exposure, a.annualised_exposure, a.explanation, a.as_of_date
FROM analytics.anomalies a
ORDER BY CASE a.severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 ELSE 3 END,
         a.estimated_exposure DESC NULLS LAST;

CREATE OR REPLACE VIEW analytics.v_sales_detail AS
SELECT f.event_id, f.event_ts, d.full_date, d.year_month, d.year, d.quarter,
       f.branch_id, b.city, f.product_id, p.category, p.supplier_id,
       f.quantity, f.unit_price, f.discount_pct, f.gross_sales, f.discount_amount,
       f.net_revenue, f.cogs, f.gross_profit
FROM core.fact_sales f
JOIN core.dim_date d    ON d.date_key = f.date_key
JOIN core.dim_branch b  ON b.branch_id = f.branch_id
JOIN core.dim_product p ON p.product_id = f.product_id;

-- Latest status per pipeline stage plus the open data-quality picture.
CREATE OR REPLACE VIEW ops.v_pipeline_health AS
SELECT DISTINCT ON (stage)
       stage, status, started_at, finished_at,
       EXTRACT(EPOCH FROM (finished_at - started_at)) AS duration_seconds,
       rows_in, rows_out, rows_rejected, error
FROM ops.pipeline_runs
ORDER BY stage, started_at DESC;

CREATE OR REPLACE VIEW quarantine.v_failure_summary AS
SELECT rule_code, severity, disposition, COUNT(*) AS failures, MIN(detected_at) AS first_seen, MAX(detected_at) AS last_seen
FROM quarantine.dq_failures
GROUP BY rule_code, severity, disposition;

GRANT SELECT ON ALL TABLES IN SCHEMA analytics, ops, quarantine TO ${READER_ROLE};
