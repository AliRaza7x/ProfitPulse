# Architecture

## System

```
                              docker compose (project "profitpulse", own network and volumes)
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                                                                            │
 │  CSV ──► producer ──► Kafka (KRaft) ──► Spark ingest ──► RAW ──► Spark clean ──► CLEANED   │
 │  (replay,   (6 topics,   offsets tracked   (Parquet,            (26 rules,        (Parquet) │
 │   --rate,    keyed by    in PostgreSQL     exact messages       quarantine)         │       │
 │   faults)    branch)                       + lineage)               │               ▼       │
 │                                                                     ▼         Spark transform│
 │                                                       quarantine.dq_failures   dims + facts │
 │                                                                                    │        │
 │                                                              Spark load ──► PostgreSQL core  │
 │                                                                                    │        │
 │                          Spark features (SQL over core) ◄──────────────────────────┘        │
 │                                  │                                                          │
 │                      pandas: anomalies, scores, leakage, KPIs   (explainable, unit-tested)  │
 │                                  │                                                          │
 │                      Spark publish ──► PostgreSQL analytics ──► web UI (PDF/XLSX) / Power BI │
 │                                                                                            │
 │  Airflow orchestrates every arrow:  ingestion ─► transformation ─► analytics ─► quality     │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Why each technology is here

| Technology | Purpose in this system | Why not something simpler |
|---|---|---|
| **Kafka** (KRaft, single node) | Event transport and replay between source and platform. Per-branch ordering via message keys; durable offsets so a rerun can resume exactly. | Writing the CSV straight to PostgreSQL would skip the part that makes this a platform: decoupled producers, replay, a dead-letter path, and offset-based incremental ingestion. |
| **PySpark** | Reads Kafka directly, runs validation over every raw message, builds the star schema, and aggregates all fact tables per branch/product/supplier/month (window functions for supplier cadence). | At 300k rows pandas would be quicker. The code is written as set-based DataFrame/SQL stages with no driver-side loops and the master URL is configuration, so the same jobs run on a cluster when volume grows (see "Scaling"). |
| **PostgreSQL** | Serving and analytical store: star schema with keys and constraints, quarantine, operations metadata, BI views. | A lake alone would not give constraints, a read-only BI role, or Power BI connectivity. |
| **Airflow 3** | Dependency-aware orchestration: four DAGs chained by data assets, retries, run history, parameters for the replay. | Cron plus shell scripts would lose retries, per-task logs, backfills and the asset-driven chain. |
| **Parquet lake** | Raw → cleaned → transformed → analytics zones, so every layer can be inspected and reloaded. | Skipping the lake would couple validation to loading. |
| **FastAPI** | A thin read-only service for the dashboard and report downloads. | Power BI is supported through the same tables, but not everyone has it. |
| **pandas** (final mile only) | Anomaly detection, scoring and leakage run over small per-entity tables (15 branches, 1,000 products, 80 suppliers), where explainable logic and unit tests matter more than distribution. | Doing this in Spark would add JVM start-up and hide the logic in expressions. |

## Data zones and processing semantics

| Zone | Location | Contents | Rewrite policy |
|---|---|---|---|
| raw | `lake/raw/events/ingest_batch=<id>/` | Exact Kafka message value + topic/partition/offset/timestamp | append-only; a batch is only "real" once registered in `ops.ingest_batches` |
| cleaned | `lake/cleaned/events`, `/failures` | Typed accepted events; one row per (message, violated rule) | recomputed from all registered raw batches |
| transformed | `lake/transformed/<table>` | Dimensions and facts | recomputed |
| analytics | `lake/analytics/<table>` | Feature tables and scored outputs | recomputed |

* **Incremental ingestion, full recomputation downstream.** Ingest reads only offsets not yet committed
  (`ops.kafka_offsets`). Cleaning and everything after recompute over the whole raw history, which makes duplicate detection
  exact (the first *valid* copy of an `event_id` wins) and every rerun deterministic. At very large volumes the recompute step
  becomes partition-incremental; the zone layout already supports it.
* **Exactly-once effect.** Offsets advance only after the batch is written, row counts match, and the registry is updated in
  one transaction. A crash in between leaves an unregistered directory that is deleted on the next run and never read.
* **Idempotent loads.** Spark writes `staging.<table>` over JDBC; a transaction then merges into the real tables: dimensions
  by upsert, facts by *sync* (upsert plus delete of rows no longer in the cleaned data), analytics by atomic replace of all tables at once.
  Schema drift (an extra or missing column) fails loudly rather than being silently ignored.
* **Nothing is silently discarded.** Every raw message ends up accepted or in `quarantine.dq_failures` verbatim, and the
  identity *raw = accepted + rejected = warehouse facts + quarantined* is checked by the clean stage and again by the quality DAG.

## Kafka topics

| Topic | Carries | Partitions | Key |
|---|---|---:|---|
| `pp.sales` | SALE | 3 | `branch_id` |
| `pp.purchases` | PURCHASE | 1 | `branch_id` |
| `pp.returns` | RETURN | 1 | `branch_id` |
| `pp.inventory` | STOCK_ADJUSTMENT, DAMAGE | 1 | `branch_id` |
| `pp.price_changes` | PRICE_CHANGE | 1 | `branch_id` |
| `pp.dead_letter` | unroutable messages (unknown event type, unparseable JSON) | 1 | none |

Messages are flat JSON (17 source fields plus `schema_version`). The producer sorts by event time (the file is shuffled),
supports batch replay (`--rate 0`) and rate simulation (`--rate 50`), and can inject faults (`--corrupt-rate`).

## PostgreSQL

Schemas: `core` (star schema), `quarantine`, `ops`, `analytics`, `staging` (Spark landing, not exposed). Migrations are numbered SQL
files in `database/migrations/`, checksummed, applied by `python -m profitpulse migrate`, and re-runnable.

* **Dimensions:** `dim_date`, `dim_branch`, `dim_product` (category, supplier, list price, standard cost), `dim_supplier`.
* **Facts:** `fact_sales`, `fact_returns`, `fact_purchases`, `fact_inventory_movements`, `fact_price_changes`. Each keeps its
  Kafka lineage (topic/partition/offset) and the load run, so any number traces back to a message.
* **Design choices:** natural business keys as primary keys (short, stable, make upserts trivial); `CHECK` constraints for
  positive quantities and bounded discounts; foreign keys everywhere; money as `NUMERIC(16,2)`. Facts are not partitioned at this
  size; partition by month when a fact table passes roughly 100M rows.
* **Roles:** `pp_app` owns schemas and is used by the pipeline; `pp_reader` is read-only and is what the web UI and BI tools use.
  A test proves the reader cannot write and cannot see `staging`.
* **BI views:** `analytics.v_executive_overview`, `v_leakage_by_type`, `v_anomaly_feed`, `v_sales_detail`, `ops.v_pipeline_health`,
  `quarantine.v_failure_summary`.

## Airflow DAGs

| DAG | Trigger | Tasks |
|---|---|---|
| `ingestion_pipeline` | manual (params: replay on/off, rate, limit, corrupt rate) | `check_infrastructure` → `decide_publish` → (`replay_source_events` or `skip_replay`) → `kafka_to_raw` → `reconcile_ingest` |
| `transformation_pipeline` | asset `raw/events` | `validate_and_clean` → `build_dimensions_and_facts` → `load_warehouse` |
| `analytics_pipeline` | asset `postgres/core` | `build_features` → `detect_and_score` → `publish_analytics` → `render_report_snapshot` |
| `data_quality_pipeline` | assets `core` **and** `analytics` (both) | `warehouse_checks` → `analytics_checks` |

Each stage runs as its own process (`python -m profitpulse <stage>`), so Spark's JVM is isolated and logs are per task. Every stage
records itself in `ops.pipeline_runs` (rows in/out/rejected, duration, error).

## Data-quality architecture

Two independent layers: (1) *row-level rules* inside the clean stage (26 rules, see `docs/METRICS.md`), and (2) *result-level gates*
after loading, which check Kafka vs raw, raw vs cleaned plus quarantined, cleaned vs warehouse, event uniqueness across facts, foreign keys,
the profit identity, a revenue/profit checksum between the Parquet lake and the warehouse, and warehouse vs analytics totals. Gate results
are stored in `ops.dq_results`; a critical failure fails the DAG run.

## Testing strategy

| Level | What | Where |
|---|---|---|
| Unit | detectors, scoring, leakage and KPI assembly, contract, fault injection, variant dataset | `tests/unit` (pure Python/pandas, seconds) |
| Spark | every validation rule and every injectable fault, transform arithmetic against hand-computed values, feature SQL | `tests/spark` (local SparkSession) |
| Integration | migrations, constraints, merge semantics, role permissions | `tests/integration` (needs PostgreSQL) |
| End to end | the warehouse and analytics agree with an independent pandas recomputation from the CSV; the detector agrees with independent statistics, without hard-coded IDs | `tests/e2e` (after a full run) |

Run all of it with `docker compose run --rm tools python -m pytest` (see the README).

## Scaling beyond one machine

The code is already shaped for it: `SPARK_MASTER` points the same jobs at a cluster; Kafka partitions and the `branch_id` key allow more
consumers; analytics are per-entity aggregates (small output however large the input). What would change first: raw ingestion in
larger partitioned micro-batches with `AvailableNow` streaming, partitioned fact tables, incremental (partition-scoped) cleaning,
and the Airflow executor moving from Local to Celery/Kubernetes. None of this is implemented, because 300k events do not need it.

## Decisions worth knowing

* **Spark runs in local mode.** One container, no cluster to babysit. The decision is reversible by configuration.
* **No Kafka UI, no Spark cluster, no Redis/Celery.** Not needed to demonstrate or run the platform.
* **Ports** are published on `127.0.0.1` only and chosen after checking the existing Docker environment (see README).
* **Airflow runs without a login** (`SimpleAuthManager` with all-admin) because it is bound to localhost. Do not expose this stack to a network as is.
