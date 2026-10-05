# Metric definitions and methodology

Every number in the dashboard, the PDF and the Excel report is defined here. Parameters live in
`config/analytics.yaml` and `config/rules.yaml`; nothing below is hard-coded to a particular branch, product
or supplier.

## What counts as what

| Measure | Definition |
|---|---|
| Net sales (net revenue) | Sum of `revenue` on SALE rows only |
| Gross sales | `quantity x unit_price` on SALE rows (value at list price) |
| Discount amount | Gross sales minus net sales |
| Discount rate | Discount amount / gross sales (value-weighted, not an average of percentages) |
| COGS | `purchase_cost` (`quantity x unit_cost`) on SALE rows |
| Gross profit / margin | Net sales minus COGS; divided by net sales |
| Refund value | `revenue` on RETURN rows |
| Return rate | Returned units / units sold (also shown by value) |
| Return profit impact | Restockable return (reason not Damaged or Quality Issue): minus the original profit. Non-restockable: minus the full refund. |
| Damage value | `quantity x unit_cost` on DAMAGE rows |
| Adjustment value | `quantity x unit_cost` on STOCK_ADJUSTMENT rows. **Unsigned**: the source has no direction. |
| Profit after returns and damage | Gross profit + return profit impact - damage value |
| Shrink rate | (damaged units + adjusted units) / units sold |
| Standard cost | Median `unit_cost` over a product's non-PURCHASE rows (book cost) |
| Purchase-price variance (PPV) | Purchase spend minus quantity x standard cost; percentage is PPV / spend at standard |
| Growth | Net sales in the last 6 months vs the 6 months before (windows anchored on the as-of date) |

The as-of date is the latest event date (2026-09-30 here); set `as_of_date` for live use.

## Anomaly detection (explainable, no black box)

**Peer checks.** For a metric such as the branch discount rate, take every branch's value and compute a
*robust z-score*:

    z = (value - median of peers) / max(1.4826 x MAD of peers, min_scale)

MAD (median absolute deviation) is used instead of standard deviation because the anomaly itself would inflate a
mean/std baseline and mask its own signal (a branch at 22% would drag the average up and shrink its own z-score).
`min_scale` stops a near-constant peer group from producing infinite scores. A finding is raised only if **all** hold:

1. z is above the severity threshold (medium 3.5, high 6, critical 10; product-level checks use 4.5 / 7 / 10 because
   there are about 1,000 products and chance findings must stay near zero),
2. the gap to the peer median exceeds a practical minimum (`min_abs_effect`, e.g. 2 percentage points), and
3. the entity has enough observations (`min_n`).

**Temporal checks.** For monthly series (branch discount, return, margin; supplier cost), the mean of the last 3 months is
compared with the mean and spread of the previous 12 months. This catches a behaviour *change*, which peer checks
cannot, and is silent when a problem has always existed. It is quiet on this dataset, correctly.

**Exposure.** The gap to the peer median applied to the entity's own base (e.g. discount gap x gross sales). It is shown
over the whole period and per year. Findings can overlap (a discounting branch also has lower margin), so the margin check
is reported as an anomaly but never counted as leakage.

**Explanations.** Every anomaly carries a sentence generated from its evidence: what is unusual, against what, how strong,
how many observations, and what it costs. Example:

> BR07 (Peshawar): discount rate is 22.1% against a peer median of 6.1% across 15 branches (15.9 percentage points higher;
> robust z = 31.9, critical severity). Based on 13,626 sales. Estimated exposure: PKR 15,921,300 over 2.7 years (about PKR 5,793,893 a year).

## Revenue leakage types

| Type | Source | Notes |
|---|---|---|
| Excess discounting | peer check on discount rate (branch, product) | gap x gross sales |
| Excess returns | peer check on return rate | gap x net sales |
| Inventory discrepancy | peer checks on adjustment size and damage rate | **upper bound**: adjustments carry no direction, so gains would reduce it |
| Procurement overpayment | peer check on PPV% | equals total spend above standard cost |
| Low-margin pricing opportunity | bottom-decile products within a category | Reported **separately**: every category has a bottom decile by construction, so it is an opportunity, not detected leakage |

## Branch performance score

For each of five measures (gross margin 25%, discount rate 25%, return rate 15%, shrink rate 15%, growth 20%) compute the same
robust z-score against the other branches, oriented so positive is better, and map to 0..1:

    component = 0.5 + 0.5 x clip(z / 3, -1, 1)          score = 100 x sum(weight x component)

0.5 is typical, 0 is at least three robust SDs worse than the peer median, 1 at least three better. Components are stored beside the
score, so any branch can be taken apart. Bands: strong 45 and above, watch 30 and above, otherwise weak. A first version used
percentile ranks, which gave noise-level differences the same weight as a catastrophic gap (the branch giving away 22% in discounts
ranked above one that was marginally worse on three measures). A regression test pins the corrected behaviour.

## Inventory intelligence and its limits

**The source contains no stock on hand**, so nothing here is a count of units on the shelf:

| Indicator | Definition |
|---|---|
| Sales velocity | Units sold per day over the last 90 days; change vs the 90 days before |
| Replenishment ratio | Units purchased / units sold over the last 180 days |
| Days since last purchase / sale | Recency in days |
| Stockout-risk proxy | 100 x (0.35 x rank of velocity + 0.40 x rank of low replenishment + 0.25 x rank of purchase recency); high >= 75, elevated >= 55 |
| Slow-moving | Velocity in the bottom 20% of its category |
| Dead-stock candidate | No sale in 60 days (product level; branch-product pairs are too sparse, at about one sale per 74 days) |
| Reorder review | No purchase in 45+ days and velocity above the median |
| Inventory age | Not derivable (no receipts/lots); days since last purchase is the closest proxy |

## Data-quality rules

Rules, severities and parameters are in `config/rules.yaml`; implementation in `src/profitpulse/validation/rules.py`.
REJECT rows are quarantined with their raw payload; WARN rows are accepted and logged.

| Rule | Severity | Catches |
|---|---|---|
| MALFORMED_PAYLOAD | REJECT | message is not a JSON object |
| MISSING_EVENT_ID, INVALID_EVENT_ID_FORMAT, DUPLICATE_EVENT_ID | REJECT | missing, malformed or repeated IDs (the first valid copy wins) |
| INVALID_EVENT_TYPE | REJECT | unknown type (these are routed to the dead-letter topic) |
| MISSING_REQUIRED_FIELD | REJECT | any required column empty |
| INVALID_TIMESTAMP, TIMESTAMP_OUT_OF_RANGE | REJECT | unparseable, before 2020 or in the future |
| INVALID_BRANCH_ID / PRODUCT_ID / SUPPLIER_ID | REJECT | pattern mismatch |
| INVALID_QUANTITY / UNIT_COST / UNIT_PRICE / DISCOUNT | REJECT | non-numeric, non-positive, fractional quantity, discount outside 0..95% |
| INVALID_RETURN_REASON | REJECT | RETURN without a known reason |
| PURCHASE_COST_MISMATCH, REVENUE_MISMATCH, PROFIT_MISMATCH, EVENT_VALUE_MISMATCH | REJECT | reported totals disagree with the arithmetic (with rounding tolerance) |
| PRODUCT_ATTRIBUTE_CONFLICT, BRANCH_CITY_CONFLICT | REJECT | category/supplier/city disagree with the entity's established values |
| TOPIC_ROUTE_MISMATCH, UNEXPECTED_RETURN_REASON | WARN | event on the wrong topic; reason on a non-RETURN |
| NEGATIVE_LIST_MARGIN | WARN | `unit_price < unit_cost` |
| POSSIBLE_DUPLICATE_BUSINESS_KEY | WARN | same branch/product/timestamp/type/quantity as an earlier event |

A deliberate separation: **validity** (is this row well-formed?) is not **anomaly** (is this valid row unusual?). The
stock adjustments that are 4x larger than normal are valid data and are the finding; a naive "quantity <= 7" rule would have
quarantined the evidence.
