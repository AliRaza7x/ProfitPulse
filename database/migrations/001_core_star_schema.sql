-- Core star schema: dimensions + one fact table per business-event family.
-- Money is NUMERIC(16,2); keys are the natural business IDs (short, stable, and
-- they make idempotent upserts trivial). ${READER_ROLE} is substituted by the
-- migration runner.

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS staging;   -- Spark JDBC landing tables; never queried by BI

-- ---------------------------------------------------------------- dimensions
CREATE TABLE core.dim_date (
    date_key     INTEGER      PRIMARY KEY,            -- yyyymmdd
    full_date    DATE         NOT NULL UNIQUE,
    year         SMALLINT     NOT NULL,
    quarter      SMALLINT     NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    month        SMALLINT     NOT NULL CHECK (month BETWEEN 1 AND 12),
    month_name   TEXT         NOT NULL,
    year_month   CHAR(7)      NOT NULL,               -- 2025-03
    iso_week     SMALLINT     NOT NULL,
    day_of_week  SMALLINT     NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),  -- 1 = Monday
    day_name     TEXT         NOT NULL,
    is_weekend   BOOLEAN      NOT NULL
);

CREATE TABLE core.dim_supplier (
    supplier_id  VARCHAR(10)  PRIMARY KEY,
    first_seen   DATE,
    last_seen    DATE,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE core.dim_branch (
    branch_id    VARCHAR(10)  PRIMARY KEY,
    city         TEXT         NOT NULL,
    first_seen   DATE,
    last_seen    DATE,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE core.dim_product (
    product_id     VARCHAR(10)   PRIMARY KEY,
    category       TEXT          NOT NULL,
    supplier_id    VARCHAR(10)   NOT NULL REFERENCES core.dim_supplier(supplier_id),
    list_price     NUMERIC(12,2) NOT NULL CHECK (list_price > 0),
    -- Book cost: median unit_cost on non-purchase events. Purchase-price variance
    -- is measured against this (see docs/METRICS.md).
    standard_cost  NUMERIC(12,2) NOT NULL CHECK (standard_cost > 0),
    first_seen     DATE,
    last_seen      DATE,
    updated_at     TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX ix_dim_product_category ON core.dim_product (category);
CREATE INDEX ix_dim_product_supplier ON core.dim_product (supplier_id);

-- --------------------------------------------------------------------- facts
-- Every fact row keeps its Kafka lineage (topic/partition/offset) and the run
-- that loaded it, so any number can be traced back to a message.

CREATE TABLE core.fact_sales (
    event_id         VARCHAR(20)   PRIMARY KEY,
    event_ts         TIMESTAMP     NOT NULL,
    date_key         INTEGER       NOT NULL REFERENCES core.dim_date(date_key),
    branch_id        VARCHAR(10)   NOT NULL REFERENCES core.dim_branch(branch_id),
    product_id       VARCHAR(10)   NOT NULL REFERENCES core.dim_product(product_id),
    quantity         INTEGER       NOT NULL CHECK (quantity > 0),
    unit_price       NUMERIC(12,2) NOT NULL CHECK (unit_price > 0),
    unit_cost        NUMERIC(12,2) NOT NULL CHECK (unit_cost > 0),
    discount_pct     NUMERIC(7,4)  NOT NULL CHECK (discount_pct >= 0 AND discount_pct < 1),
    gross_sales      NUMERIC(16,2) NOT NULL,   -- quantity * unit_price
    discount_amount  NUMERIC(16,2) NOT NULL,   -- gross_sales - net_revenue
    net_revenue      NUMERIC(16,2) NOT NULL CHECK (net_revenue > 0),
    cogs             NUMERIC(16,2) NOT NULL,
    gross_profit     NUMERIC(16,2) NOT NULL,
    kafka_topic      TEXT,
    kafka_partition  INTEGER,
    kafka_offset     BIGINT,
    load_run_id      UUID,
    loaded_at        TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX ix_fact_sales_date    ON core.fact_sales (date_key);
CREATE INDEX ix_fact_sales_branch  ON core.fact_sales (branch_id, date_key);
CREATE INDEX ix_fact_sales_product ON core.fact_sales (product_id, date_key);

CREATE TABLE core.fact_returns (
    event_id         VARCHAR(20)   PRIMARY KEY,
    event_ts         TIMESTAMP     NOT NULL,
    date_key         INTEGER       NOT NULL REFERENCES core.dim_date(date_key),
    branch_id        VARCHAR(10)   NOT NULL REFERENCES core.dim_branch(branch_id),
    product_id       VARCHAR(10)   NOT NULL REFERENCES core.dim_product(product_id),
    quantity         INTEGER       NOT NULL CHECK (quantity > 0),
    return_reason    TEXT          NOT NULL,
    refund_value     NUMERIC(16,2) NOT NULL CHECK (refund_value > 0),
    cost_value       NUMERIC(16,2) NOT NULL,   -- quantity * unit_cost
    restockable      BOOLEAN       NOT NULL,   -- derived from reason (config/analytics.yaml)
    profit_impact    NUMERIC(16,2) NOT NULL,   -- negative: profit given back by this return
    kafka_topic      TEXT,
    kafka_partition  INTEGER,
    kafka_offset     BIGINT,
    load_run_id      UUID,
    loaded_at        TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX ix_fact_returns_date    ON core.fact_returns (date_key);
CREATE INDEX ix_fact_returns_branch  ON core.fact_returns (branch_id, date_key);
CREATE INDEX ix_fact_returns_product ON core.fact_returns (product_id, date_key);

CREATE TABLE core.fact_purchases (
    event_id               VARCHAR(20)   PRIMARY KEY,
    event_ts               TIMESTAMP     NOT NULL,
    date_key               INTEGER       NOT NULL REFERENCES core.dim_date(date_key),
    branch_id              VARCHAR(10)   NOT NULL REFERENCES core.dim_branch(branch_id),
    product_id             VARCHAR(10)   NOT NULL REFERENCES core.dim_product(product_id),
    supplier_id            VARCHAR(10)   NOT NULL REFERENCES core.dim_supplier(supplier_id),
    quantity               INTEGER       NOT NULL CHECK (quantity > 0),
    unit_cost              NUMERIC(12,2) NOT NULL CHECK (unit_cost > 0),     -- price actually paid
    standard_cost          NUMERIC(12,2) NOT NULL CHECK (standard_cost > 0), -- book cost at load time
    list_price             NUMERIC(12,2) NOT NULL,
    spend                  NUMERIC(16,2) NOT NULL,   -- quantity * unit_cost
    standard_spend         NUMERIC(16,2) NOT NULL,   -- quantity * standard_cost
    price_variance_amount  NUMERIC(16,2) NOT NULL,   -- spend - standard_spend (+ = overpaid)
    price_variance_pct     NUMERIC(10,6) NOT NULL,   -- (unit_cost / standard_cost) - 1
    cost_above_list_price  BOOLEAN       NOT NULL,   -- bought above what we sell it for
    kafka_topic            TEXT,
    kafka_partition        INTEGER,
    kafka_offset           BIGINT,
    load_run_id            UUID,
    loaded_at              TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX ix_fact_purchases_date     ON core.fact_purchases (date_key);
CREATE INDEX ix_fact_purchases_supplier ON core.fact_purchases (supplier_id, date_key);
CREATE INDEX ix_fact_purchases_product  ON core.fact_purchases (product_id, date_key);

-- Stock adjustments and damage. The source gives no sign for adjustments, so
-- `quantity` is an unsigned magnitude and `direction` records that honestly.
CREATE TABLE core.fact_inventory_movements (
    event_id         VARCHAR(20)   PRIMARY KEY,
    event_ts         TIMESTAMP     NOT NULL,
    date_key         INTEGER       NOT NULL REFERENCES core.dim_date(date_key),
    branch_id        VARCHAR(10)   NOT NULL REFERENCES core.dim_branch(branch_id),
    product_id       VARCHAR(10)   NOT NULL REFERENCES core.dim_product(product_id),
    movement_type    TEXT          NOT NULL CHECK (movement_type IN ('STOCK_ADJUSTMENT', 'DAMAGE')),
    direction        TEXT          NOT NULL CHECK (direction IN ('UNSIGNED', 'LOSS')),
    quantity         INTEGER       NOT NULL CHECK (quantity > 0),
    unit_cost        NUMERIC(12,2) NOT NULL CHECK (unit_cost > 0),
    value_at_cost    NUMERIC(16,2) NOT NULL,
    kafka_topic      TEXT,
    kafka_partition  INTEGER,
    kafka_offset     BIGINT,
    load_run_id      UUID,
    loaded_at        TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX ix_fact_inventory_date    ON core.fact_inventory_movements (date_key);
CREATE INDEX ix_fact_inventory_branch  ON core.fact_inventory_movements (branch_id, movement_type);
CREATE INDEX ix_fact_inventory_product ON core.fact_inventory_movements (product_id, movement_type);

-- The source PRICE_CHANGE rows carry the current list price only (no old/new
-- pair), so this table records price observations rather than price deltas.
CREATE TABLE core.fact_price_changes (
    event_id         VARCHAR(20)   PRIMARY KEY,
    event_ts         TIMESTAMP     NOT NULL,
    date_key         INTEGER       NOT NULL REFERENCES core.dim_date(date_key),
    branch_id        VARCHAR(10)   NOT NULL REFERENCES core.dim_branch(branch_id),
    product_id       VARCHAR(10)   NOT NULL REFERENCES core.dim_product(product_id),
    list_price       NUMERIC(12,2) NOT NULL CHECK (list_price > 0),
    standard_cost    NUMERIC(12,2) NOT NULL CHECK (standard_cost > 0),
    list_margin_pct  NUMERIC(10,6) NOT NULL,   -- (list_price - standard_cost) / list_price
    kafka_topic      TEXT,
    kafka_partition  INTEGER,
    kafka_offset     BIGINT,
    load_run_id      UUID,
    loaded_at        TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX ix_fact_price_changes_product ON core.fact_price_changes (product_id, date_key);

GRANT USAGE ON SCHEMA core TO ${READER_ROLE};
GRANT SELECT ON ALL TABLES IN SCHEMA core TO ${READER_ROLE};
ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT SELECT ON TABLES TO ${READER_ROLE};
