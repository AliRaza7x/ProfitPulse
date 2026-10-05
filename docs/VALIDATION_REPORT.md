# ProfitPulse: end-to-end validation report

Generated 2026-10-05T11:53:27+00:00 from a live run. Data as of 2026-09-30. Amounts in PKR.

## 1. Record accounting

| Stage | Records |
|---|---:|
| Rows in the source CSV | 300,000 |
| Published to Kafka | 300,997 |
| Ingested from Kafka into the raw zone | 300,997 |
| Processed by validation | 300,997 |
| Accepted | 300,000 |
| Rejected (quarantined, kept verbatim) | 997 |
| Stored in the PostgreSQL warehouse | 300,000 |

Accounting identity (processed = accepted + rejected, stored = accepted): **holds**.


Ingestion was incremental. Each batch read only the Kafka offsets not yet committed:

| Raw batch | Messages | Validation run: raw in | Accepted | Rejected |
|---|---:|---:|---:|---:|
| 20261005T113924859513Z | 300,000 | 300,000 (cumulative) | 300,000 | 0 |
| 20261005T114711558930Z | 997 | 300,997 (cumulative) | 300,000 | 997 |

After the later batches the warehouse still holds exactly the accepted events: bad data that arrives later is quarantined, not loaded and not lost.

| Fact table | Stored | Source rows of that type | Match |
|---|---:|---:|:--:|
| fact_sales | 204,490 | 204,490 | yes |
| fact_returns | 20,805 | 20,805 | yes |
| fact_purchases | 35,943 | 35,943 | yes |
| fact_inventory_movements | 29,722 | 29,722 | yes |
| fact_price_changes | 9,040 | 9,040 | yes |

## 2. Financial totals

Only SALE rows are revenue. The CSV also fills `revenue` and `profit` on other event types, which is why summing those columns naively overstates sales.

| Measure | Warehouse | Recomputed from CSV | Difference |
|---|---:|---:|---:|
| Net sales revenue | 1,385,801,741.13 | 1,385,801,741.13 | 0.00 |
| Gross profit (sales) | 335,447,394.30 | 335,447,394.30 | 0.00 |
| Units sold | 819,537 | 819,537 | 0 |
| Refund value (returns) | 142,920,562.76 | 142,920,562.76 | 0.00 |

For comparison, summing the CSV's `revenue` column over all rows gives 2,069,189,811.19 (+49% vs real sales) and `profit` 506,113,402.79.

- Gross margin: 24.21%
- Cost of goods sold: 1,050,354,346.83
- Gross profit after returns and damaged stock: 196,472,841.93 (profit given back by returns -78,394,322.44)
- Purchase spend: 184,224,518.16; paid above standard cost: 513,084.85; purchases above our own list price: 221

## 3. Returns

- Return events: 20,805; units returned: 83,273 (10.16% of units sold)
- Refund value: 142,920,562.76 (10.31% of net sales)
- Not resaleable (Damaged or Quality Issue): 8,285 returns; profit given back by returns: -78,394,322.44

## 4. Discounts (SALE rows)

- Discount given: 107,414,936.89 on 1,493,216,678.02 of list-price sales = **7.19%**
- Average discount per sale: 7.20%; maximum 43.9%
- Sales discounted by more than 20%: 4.20%; sales with no discount: 6.05%
- Loss-making sales (negative gross profit): 5,066

## 5. Inventory risk (movement-based proxies; the source has no stock on hand)

| Stockout-risk level | Products |
|---|---:|
| normal | 581 |
| elevated | 314 |
| high | 105 |

Reorder-review flags: 111; slow-moving: 215; dead-stock candidates: 0.
- Stock Adjustment: 88,434 units, 114,415,432.76 at cost
- Damage: 47,252 units, 60,580,229.93 at cost

## 6. Detected anomalies

4 anomalies. Each carries an explanation; none is an unexplained score.

- **BR12 (Rawalpindi)**, `branch_adjustment_qty`, critical, robust z 59.0, exposure 22,607,245.48 (8,226,963.71 a year)  
  BR12 (Rawalpindi): average stock-adjustment size is 18.8 units against a peer median of 4.0 units across 15 branches (14.8 units higher; robust z = 59.0, critical severity). Based on 1,158 stock adjustments. Estimated exposure: PKR 22,607,245 over 2.7 years (about PKR 8,226,964 a year).
- **BR07 (Peshawar)**, `branch_discount_rate`, critical, robust z 31.9, exposure 15,921,299.93 (5,793,892.80 a year)  
  BR07 (Peshawar): discount rate is 22.1% against a peer median of 6.1% across 15 branches (15.9 percentage points higher; robust z = 31.9, critical severity). Based on 13,626 sales. Estimated exposure: PKR 15,921,300 over 2.7 years (about PKR 5,793,893 a year).
- **BR07 (Peshawar)**, `branch_margin`, critical, robust z 30.2, exposure 11,768,208.74 (4,282,548.54 a year)  
  BR07 (Peshawar): gross margin is 9.9% against a peer median of 25.0% across 15 branches (15.1 percentage points lower; robust z = 30.2, critical severity). Based on 13,626 sales. Estimated exposure: PKR 11,768,209 over 2.7 years (about PKR 4,282,549 a year).
- **SUP018**, `supplier_price_variance`, critical, robust z 50.1, exposure 513,084.85 (186,715.82 a year)  
  SUP018: purchase cost versus standard cost is +25.0% against a peer median of +0.0% across 80 suppliers (25.0 percentage points higher; robust z = 50.1, critical severity). Based on 435 purchases. Estimated exposure: PKR 513,085 over 2.7 years (about PKR 186,716 a year).

| Leakage type | Findings | Exposure | Per year | Kind |
|---|---:|---:|---:|---|
| INVENTORY_DISCREPANCY | 1 | 22,607,245.48 | 8,226,963.71 | detected |
| LOW_MARGIN_SHORTFALL | 104 | 17,570,753.94 | 6,394,142.76 | opportunity |
| EXCESS_DISCOUNT | 1 | 15,921,299.93 | 5,793,892.80 | detected |
| PROCUREMENT_OVERPAYMENT | 1 | 513,084.85 | 186,715.82 | detected |

## 7. Data-quality gates

22 of 22 gates passed.

| Layer | Check | Status |
|---|---|:--:|
| analytics | BRANCH_SCORECARD_EQUALS_WAREHOUSE | PASS |
| analytics | BRANCH_SCORES_COMPLETE | PASS |
| analytics | EVERY_ANOMALY_HAS_AN_EXPLANATION | PASS |
| analytics | KPI_PROFIT_EQUALS_WAREHOUSE | PASS |
| analytics | KPI_REVENUE_EQUALS_WAREHOUSE | PASS |
| analytics | LEAKAGE_EXPOSURE_RECORDED | PASS |
| analytics | MONTHLY_SERIES_EQUALS_WAREHOUSE | PASS |
| warehouse | EVENT_IN_ONE_FACT_ONLY | PASS |
| warehouse | FRESH_CLEAN_RUN | PASS |
| warehouse | FRESH_INGEST_RUN | PASS |
| warehouse | FRESH_LOAD_RUN | PASS |
| warehouse | FRESH_TRANSFORM_RUN | PASS |
| warehouse | KAFKA_OFFSETS_EQUAL_RAW_MESSAGES | PASS |
| warehouse | NO_ORPHAN_FACTS | PASS |
| warehouse | QUARANTINE_HOLDS_EVERY_REJECT | PASS |
| warehouse | RAW_EQUALS_CLEAN_PLUS_REJECTED | PASS |
| warehouse | RAW_MESSAGES_EQUAL_KAFKA | PASS |
| warehouse | SALES_ARE_POSITIVE | PASS |
| warehouse | SALES_PROFIT_CHECKSUM_LAKE_VS_WAREHOUSE | PASS |
| warehouse | SALES_PROFIT_IDENTITY | PASS |
| warehouse | SALES_REVENUE_CHECKSUM_LAKE_VS_WAREHOUSE | PASS |
| warehouse | WAREHOUSE_FACTS_EQUAL_ACCEPTED_EVENTS | PASS |

Quarantine / warnings by rule:

| Rule | Disposition | Messages |
|---|---|---:|
| NEGATIVE_LIST_MARGIN | ACCEPTED_WITH_WARNING | 264 |
| PURCHASE_COST_MISMATCH | REJECTED | 200 |
| REVENUE_MISMATCH | REJECTED | 160 |
| INVALID_QUANTITY | REJECTED | 137 |
| PROFIT_MISMATCH | REJECTED | 129 |
| PRODUCT_ATTRIBUTE_CONFLICT | REJECTED | 75 |
| INVALID_UNIT_COST | REJECTED | 60 |
| MALFORMED_PAYLOAD | REJECTED | 52 |
| INVALID_EVENT_TYPE | REJECTED | 50 |
| INVALID_DISCOUNT | REJECTED | 49 |
| INVALID_PRODUCT_ID | REJECTED | 49 |
| MISSING_REQUIRED_FIELD | REJECTED | 48 |
| TIMESTAMP_OUT_OF_RANGE | REJECTED | 48 |
| INVALID_EVENT_ID_FORMAT | REJECTED | 47 |
| DUPLICATE_EVENT_ID | REJECTED | 47 |
| EVENT_VALUE_MISMATCH | REJECTED | 46 |
| INVALID_BRANCH_ID | REJECTED | 43 |
| MISSING_EVENT_ID | REJECTED | 43 |
| INVALID_UNIT_PRICE | REJECTED | 40 |
| INVALID_SUPPLIER_ID | REJECTED | 40 |
| BRANCH_CITY_CONFLICT | REJECTED | 39 |
| INVALID_TIMESTAMP | REJECTED | 35 |
| POSSIBLE_DUPLICATE_BUSINESS_KEY | ACCEPTED_WITH_WARNING | 8 |
| INVALID_RETURN_REASON | REJECTED | 6 |

## 8. Processing duration

### Full-dataset run (all source events, orchestrated by Airflow)

| Stage | Seconds | Rows in | Rows out | Rejected |
|---|---:|---:|---:|---:|
| publish | 11.6 | 300,000 | 300,000 | 0 |
| ingest | 17.7 | 300,000 | 300,000 | 0 |
| clean | 69.5 | 300,000 | 300,000 | 0 |
| transform | 33.7 | 300,000 | 300,000 | 0 |
| load | 36.6 | 300,000 | 300,000 | 0 |
| features | 40.8 | 0 | 5,416 | 0 |
| analytics | 0.5 | 0 | 2,239 | 0 |
| publish_analytics | 23.9 | 5,560 | 5,560 | 0 |

Sum of stage durations: **234 s**; wall-clock from first stage start to the last quality gate: **262 s** (includes Airflow scheduling gaps).

### Later incremental batch (faulty messages only)

| Stage | Seconds | Rows in | Rows out | Rejected |
|---|---:|---:|---:|---:|
| publish | 6.0 | 20,000 | 997 | 0 |
| ingest | 22.4 | 997 | 997 | 0 |
| clean | 77.1 | 300,997 | 300,000 | 997 |
| transform | 38.6 | 300,000 | 300,000 | 0 |
| load | 35.3 | 300,000 | 300,000 | 0 |
| features | 40.2 | 0 | 5,416 | 0 |
| analytics | 0.5 | 0 | 2,239 | 0 |
| publish_analytics | 22.0 | 5,560 | 5,560 | 0 |
