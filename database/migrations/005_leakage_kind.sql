-- Separate anomaly-driven leakage from structural pricing opportunity in the BI view.
CREATE OR REPLACE VIEW analytics.v_leakage_by_type AS
SELECT leakage_type,
       COUNT(*)                         AS findings,
       SUM(exposure_amount)             AS exposure_amount,
       SUM(annualised_exposure)         AS annualised_exposure,
       CASE WHEN leakage_type = 'LOW_MARGIN_SHORTFALL' THEN 'opportunity' ELSE 'detected' END AS kind
FROM analytics.revenue_leakage
GROUP BY leakage_type;

GRANT SELECT ON analytics.v_leakage_by_type TO ${READER_ROLE};
