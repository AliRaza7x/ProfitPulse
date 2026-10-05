-- Analytical outputs. These tables are fully rebuilt by the analytics pipeline
-- (derived data), then exposed to the web UI and BI tools. Column meanings are
-- documented in docs/METRICS.md. Amounts: NUMERIC(18,2); ratios/scores: DOUBLE PRECISION.

CREATE SCHEMA IF NOT EXISTS analytics;

-- ------------------------------------------------------------- time series
CREATE TABLE analytics.company_monthly (
    year_month           CHAR(7)          PRIMARY KEY,
    month_start          DATE             NOT NULL,
    sale_events          BIGINT           NOT NULL,
    units_sold           BIGINT           NOT NULL,
    gross_sales          NUMERIC(18,2)    NOT NULL,
    net_revenue          NUMERIC(18,2)    NOT NULL,
    cogs                 NUMERIC(18,2)    NOT NULL,
    gross_profit         NUMERIC(18,2)    NOT NULL,
    discount_amount      NUMERIC(18,2)    NOT NULL,
    return_units         BIGINT           NOT NULL,
    return_value         NUMERIC(18,2)    NOT NULL,
    return_profit_impact NUMERIC(18,2)    NOT NULL,
    damage_value         NUMERIC(18,2)    NOT NULL,
    adjustment_value     NUMERIC(18,2)    NOT NULL,
    purchase_spend       NUMERIC(18,2)    NOT NULL,
    price_variance_amount NUMERIC(18,2)   NOT NULL,
    adjusted_gross_profit NUMERIC(18,2)   NOT NULL,
    gross_margin         DOUBLE PRECISION,
    discount_rate        DOUBLE PRECISION,
    return_rate_units    DOUBLE PRECISION
);

CREATE TABLE analytics.branch_monthly (
    branch_id            VARCHAR(10)      NOT NULL,
    year_month           CHAR(7)          NOT NULL,
    month_start          DATE             NOT NULL,
    sale_events          BIGINT           NOT NULL,
    units_sold           BIGINT           NOT NULL,
    gross_sales          NUMERIC(18,2)    NOT NULL,
    net_revenue          NUMERIC(18,2)    NOT NULL,
    cogs                 NUMERIC(18,2)    NOT NULL,
    gross_profit         NUMERIC(18,2)    NOT NULL,
    discount_amount      NUMERIC(18,2)    NOT NULL,
    return_units         BIGINT           NOT NULL,
    return_value         NUMERIC(18,2)    NOT NULL,
    damage_units         BIGINT           NOT NULL,
    adjustment_units     BIGINT           NOT NULL,
    purchase_spend       NUMERIC(18,2)    NOT NULL,
    gross_margin         DOUBLE PRECISION,
    discount_rate        DOUBLE PRECISION,
    return_rate_units    DOUBLE PRECISION,
    PRIMARY KEY (branch_id, year_month)
);

CREATE TABLE analytics.supplier_monthly (
    supplier_id          VARCHAR(10)      NOT NULL,
    year_month           CHAR(7)          NOT NULL,
    month_start          DATE             NOT NULL,
    purchase_events      BIGINT           NOT NULL,
    purchase_units       BIGINT           NOT NULL,
    purchase_spend       NUMERIC(18,2)    NOT NULL,
    purchase_standard_spend NUMERIC(18,2) NOT NULL,
    ppv_amount           NUMERIC(18,2)    NOT NULL,
    ppv_pct              DOUBLE PRECISION,
    PRIMARY KEY (supplier_id, year_month)
);

CREATE TABLE analytics.inventory_movement_monthly (
    year_month           CHAR(7)          PRIMARY KEY,
    month_start          DATE             NOT NULL,
    units_purchased      BIGINT           NOT NULL,
    units_sold           BIGINT           NOT NULL,
    units_returned       BIGINT           NOT NULL,
    units_damaged        BIGINT           NOT NULL,
    units_adjusted       BIGINT           NOT NULL,   -- unsigned magnitude (source gives no direction)
    replenishment_ratio  DOUBLE PRECISION             -- purchased / sold units
);

-- ------------------------------------------------------------- scorecards
CREATE TABLE analytics.branch_scorecard (
    branch_id            VARCHAR(10)      PRIMARY KEY,
    city                 TEXT             NOT NULL,
    period_start         DATE             NOT NULL,
    period_end           DATE             NOT NULL,
    sale_events          BIGINT           NOT NULL,
    units_sold           BIGINT           NOT NULL,
    gross_sales          NUMERIC(18,2)    NOT NULL,
    net_revenue          NUMERIC(18,2)    NOT NULL,
    cogs                 NUMERIC(18,2)    NOT NULL,
    gross_profit         NUMERIC(18,2)    NOT NULL,
    discount_amount      NUMERIC(18,2)    NOT NULL,
    return_events        BIGINT           NOT NULL,
    return_units         BIGINT           NOT NULL,
    return_value         NUMERIC(18,2)    NOT NULL,
    return_profit_impact NUMERIC(18,2)    NOT NULL,
    damage_units         BIGINT           NOT NULL,
    damage_value         NUMERIC(18,2)    NOT NULL,
    adjustment_events    BIGINT           NOT NULL,
    adjustment_units     BIGINT           NOT NULL,
    adjustment_value     NUMERIC(18,2)    NOT NULL,
    adjustment_event_cost NUMERIC(18,2)   NOT NULL,   -- events x average unit cost (exposure base)
    purchase_units       BIGINT           NOT NULL,
    purchase_spend       NUMERIC(18,2)    NOT NULL,
    adjusted_gross_profit NUMERIC(18,2)   NOT NULL,
    gross_margin         DOUBLE PRECISION,
    discount_rate        DOUBLE PRECISION,
    return_rate_units    DOUBLE PRECISION,
    return_rate_value    DOUBLE PRECISION,
    damage_rate          DOUBLE PRECISION,
    adjustment_avg_qty   DOUBLE PRECISION,
    shrink_rate          DOUBLE PRECISION,           -- (damage + adjustment units) / units sold
    growth_rate          DOUBLE PRECISION,           -- recent N months vs the N before
    score_gross_margin   DOUBLE PRECISION,           -- component percentile ranks, 0..1, 1 = best
    score_discount_rate  DOUBLE PRECISION,
    score_return_rate    DOUBLE PRECISION,
    score_shrink_rate    DOUBLE PRECISION,
    score_growth_rate    DOUBLE PRECISION,
    performance_score    DOUBLE PRECISION,           -- 0..100
    performance_band     TEXT,                       -- strong | watch | weak
    performance_rank     INTEGER,
    anomaly_count        INTEGER          NOT NULL DEFAULT 0,
    max_anomaly_severity TEXT,
    operational_risk     TEXT                        -- low | medium | high (from anomaly severity)
);

CREATE TABLE analytics.product_scorecard (
    product_id           VARCHAR(10)      PRIMARY KEY,
    category             TEXT             NOT NULL,
    supplier_id          VARCHAR(10)      NOT NULL,
    list_price           NUMERIC(12,2)    NOT NULL,
    standard_cost        NUMERIC(12,2)    NOT NULL,
    sale_events          BIGINT           NOT NULL,
    units_sold           BIGINT           NOT NULL,
    gross_sales          NUMERIC(18,2)    NOT NULL,
    net_revenue          NUMERIC(18,2)    NOT NULL,
    cogs                 NUMERIC(18,2)    NOT NULL,
    gross_profit         NUMERIC(18,2)    NOT NULL,
    discount_amount      NUMERIC(18,2)    NOT NULL,
    return_events        BIGINT           NOT NULL,
    return_units         BIGINT           NOT NULL,
    return_value         NUMERIC(18,2)    NOT NULL,
    damage_units         BIGINT           NOT NULL,
    adjustment_units     BIGINT           NOT NULL,
    gross_margin         DOUBLE PRECISION,
    discount_rate        DOUBLE PRECISION,
    return_rate_units    DOUBLE PRECISION,
    damage_rate          DOUBLE PRECISION,
    revenue_recent       NUMERIC(18,2),
    revenue_prior        NUMERIC(18,2),
    growth_rate          DOUBLE PRECISION,
    margin_percentile    DOUBLE PRECISION,           -- within category, 0..1
    is_high_margin       BOOLEAN          NOT NULL DEFAULT FALSE,
    is_low_margin        BOOLEAN          NOT NULL DEFAULT FALSE,
    is_high_return       BOOLEAN          NOT NULL DEFAULT FALSE,
    is_high_discount     BOOLEAN          NOT NULL DEFAULT FALSE,
    is_declining         BOOLEAN          NOT NULL DEFAULT FALSE,
    anomaly_count        INTEGER          NOT NULL DEFAULT 0,
    is_problematic       BOOLEAN          NOT NULL DEFAULT FALSE,
    problem_reasons      TEXT                        -- plain-English list of why it is flagged
);
CREATE INDEX ix_product_scorecard_category ON analytics.product_scorecard (category);

CREATE TABLE analytics.supplier_scorecard (
    supplier_id          VARCHAR(10)      PRIMARY KEY,
    products_supplied    INTEGER          NOT NULL,
    purchase_events      BIGINT           NOT NULL,
    purchase_units       BIGINT           NOT NULL,
    purchase_spend       NUMERIC(18,2)    NOT NULL,
    purchase_standard_spend NUMERIC(18,2) NOT NULL,
    ppv_amount           NUMERIC(18,2)    NOT NULL,   -- spend above book cost (+ = overpaid)
    ppv_pct              DOUBLE PRECISION,
    share_above_standard DOUBLE PRECISION,           -- share of purchases priced above book cost
    purchases_above_list INTEGER          NOT NULL,   -- bought above our own selling price
    cadence_cv           DOUBLE PRECISION,           -- variability of days between purchases (reliability proxy)
    product_return_rate  DOUBLE PRECISION,           -- return units / sold units of supplied products
    product_damage_rate  DOUBLE PRECISION,           -- damage units / sold units of supplied products
    first_purchase       DATE,
    last_purchase        DATE,
    anomaly_count        INTEGER          NOT NULL DEFAULT 0,
    risk_level           TEXT
);

-- --------------------------------------------------------------- inventory
-- Movement-based proxies: the source has no stock-on-hand (docs/METRICS.md).
CREATE TABLE analytics.inventory_risk (
    product_id               VARCHAR(10)   PRIMARY KEY,
    category                 TEXT          NOT NULL,
    supplier_id              VARCHAR(10)   NOT NULL,
    as_of_date               DATE          NOT NULL,
    units_sold_recent        BIGINT        NOT NULL,
    units_sold_prior         BIGINT        NOT NULL,
    velocity_per_day         DOUBLE PRECISION,
    velocity_change          DOUBLE PRECISION,
    units_sold_window        BIGINT        NOT NULL,   -- replenishment window
    units_purchased_window   BIGINT        NOT NULL,
    replenishment_ratio      DOUBLE PRECISION,         -- purchased / sold units in window
    days_since_last_sale     INTEGER,
    days_since_last_purchase INTEGER,
    damage_units_recent      BIGINT        NOT NULL,
    pct_velocity             DOUBLE PRECISION,         -- proxy components, 0..1, 1 = riskiest
    pct_replenishment_gap    DOUBLE PRECISION,
    pct_purchase_recency     DOUBLE PRECISION,
    stockout_risk_score      DOUBLE PRECISION,         -- 0..100
    stockout_risk_level      TEXT,                     -- high | elevated | normal
    is_slow_moving           BOOLEAN       NOT NULL,
    is_dead_stock            BOOLEAN       NOT NULL,
    reorder_flag             BOOLEAN       NOT NULL,
    explanation              TEXT
);

CREATE TABLE analytics.inventory_branch_category (
    branch_id                VARCHAR(10)   NOT NULL,
    category                 TEXT          NOT NULL,
    as_of_date               DATE          NOT NULL,
    units_sold               BIGINT        NOT NULL,
    units_purchased          BIGINT        NOT NULL,
    replenishment_ratio      DOUBLE PRECISION,
    damage_units             BIGINT        NOT NULL,
    adjustment_units         BIGINT        NOT NULL,
    adjustment_value         NUMERIC(18,2) NOT NULL,
    shrink_rate              DOUBLE PRECISION,
    days_since_last_purchase INTEGER,
    PRIMARY KEY (branch_id, category)
);

-- --------------------------------------------------- anomalies and leakage
CREATE TABLE analytics.anomalies (
    anomaly_id           TEXT             PRIMARY KEY,   -- <check_id>:<entity_id>
    as_of_date           DATE             NOT NULL,
    detection_method     TEXT             NOT NULL CHECK (detection_method IN ('PEER', 'TEMPORAL')),
    check_id             TEXT             NOT NULL,
    entity_type          TEXT             NOT NULL,
    entity_id            TEXT             NOT NULL,
    entity_label         TEXT,
    metric               TEXT             NOT NULL,
    direction            TEXT             NOT NULL,
    observed_value       DOUBLE PRECISION NOT NULL,
    baseline_value       DOUBLE PRECISION NOT NULL,
    baseline_spread      DOUBLE PRECISION,
    robust_z             DOUBLE PRECISION NOT NULL,
    severity             TEXT             NOT NULL CHECK (severity IN ('medium', 'high', 'critical')),
    sample_size          BIGINT,
    estimated_exposure   NUMERIC(18,2),
    annualised_exposure  NUMERIC(18,2),
    leakage_type         TEXT,
    period_start         DATE,
    period_end           DATE,
    explanation          TEXT             NOT NULL,
    evidence             JSONB            NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX ix_anomalies_entity   ON analytics.anomalies (entity_type, entity_id);
CREATE INDEX ix_anomalies_severity ON analytics.anomalies (severity);

CREATE TABLE analytics.revenue_leakage (
    leakage_type         TEXT             NOT NULL,   -- EXCESS_DISCOUNT | EXCESS_RETURNS | INVENTORY_DISCREPANCY | PROCUREMENT_OVERPAYMENT | LOW_MARGIN_SHORTFALL
    entity_type          TEXT             NOT NULL,
    entity_id            TEXT             NOT NULL,
    entity_label         TEXT,
    period_start         DATE,
    period_end           DATE,
    exposure_amount      NUMERIC(18,2)    NOT NULL,
    annualised_exposure  NUMERIC(18,2),
    basis                TEXT             NOT NULL,   -- how the number was calculated
    source_anomaly_id    TEXT,
    PRIMARY KEY (leakage_type, entity_type, entity_id)
);

-- ---------------------------------------------------------------- headline
CREATE TABLE analytics.kpi_summary (
    kpi_key      TEXT             PRIMARY KEY,
    section      TEXT             NOT NULL,
    label        TEXT             NOT NULL,
    value_numeric DOUBLE PRECISION,
    value_text   TEXT,
    unit         TEXT,                                -- currency | percent | count | days
    sort_order   INTEGER          NOT NULL DEFAULT 0
);

GRANT USAGE ON SCHEMA analytics TO ${READER_ROLE};
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO ${READER_ROLE};
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO ${READER_ROLE};
