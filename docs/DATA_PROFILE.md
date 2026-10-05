# Source dataset profile

`data/source/profitpulse_synthetic_dataset.csv`, profiled before any pipeline code was written. The
dataset is synthetic, internally consistent in its formulas, and clean at the field level. Its traps are
semantic, and the design follows from them.

## Shape

| | |
|---|---|
| Rows / columns | 300,000 / 17 |
| Date range | 2024-01-01 to 2026-09-30 (1,004 days, 33 months, about 9,100 events a month, no seasonality) |
| Time grain | Hourly timestamps, evenly spread over 24 hours (no business-hours pattern). `event_id` order is unrelated to time, so a replay must sort by timestamp. |
| Events | SALE 204,490, PURCHASE 35,943, RETURN 20,805, STOCK_ADJUSTMENT 17,848, DAMAGE 11,874, PRICE_CHANGE 9,040 |
| Entities | 15 branches in 8 cities, 1,000 products in 8 categories, 80 suppliers |
| Currency | Not stated. Cities suggest PKR; treated as PKR and configurable (`PP_CURRENCY`) |

## Field-level quality

No nulls (except `return_reason`, blank on every non-RETURN row as it should be), no duplicate `event_id`s,
no duplicate rows, all ID formats valid, no non-positive quantities, prices or costs, no unparseable or
future timestamps. The validation layer passes all 300,000 rows with 229 warnings (see below), which is
why a fault-injection mode exists to prove it also catches bad data.

## Formulas that hold (verified on every row)

* `purchase_cost = quantity x unit_cost`, within a cent-rounding allowance of 0.005 x quantity. Fifty-three
  PURCHASE rows from one supplier drift by up to 3 cents because `unit_cost` is stored to 2 dp.
* `profit = revenue - purchase_cost`.
* On SALE rows, `revenue = quantity x unit_price x (1 - discount_pct)` within 0.014%. (`discount_pct` is stored
  rounded to 4 dp, so an absolute tolerance would wrongly reject about 80% of valid sales.)
* `event_value` equals `revenue` for SALE, RETURN, DAMAGE and STOCK_ADJUSTMENT; `purchase_cost` for PURCHASE;
  and `unit_price` for PRICE_CHANGE.

## Semantic traps (and what the platform does about them)

1. **`revenue` and `profit` are filled on every event type.** Summing the columns gives 2,069.2M revenue and
   506.1M profit. Real sales are 1,385.8M and 335.4M, so the naive total is 49% too high. Only SALE rows count
   as revenue; other event types are re-purposed (refund value, write-off value, spend).
2. **`discount_pct` is populated on non-sale rows** (about 6% on purchases, returns, etc.) where it has no
   meaning. It is used only for SALE.
3. **There is no stock-on-hand, no opening balance, and stock adjustments have no sign.** Purchases cover only
   about 17% of units sold, and 99.8% of branch-product running balances would go negative. Stock levels cannot be
   reconstructed, so inventory intelligence uses movement-based proxies and says so everywhere.
4. **`unit_cost` is a constant standard cost on every non-purchase row**, and only varies on PURCHASE rows (and
   only for one supplier). Supplier overpayment is therefore measured against the product's standard cost.
5. **PRICE_CHANGE rows carry no old/new price**, just the current list price. They are kept as price
   observations, not deltas. Prices never change in this file.
6. **Returns have no reference to the original sale**, so they can only be reconciled in aggregate.
7. **Each product has exactly one category, supplier and price; each branch exactly one city** (Karachi, Lahore,
   Islamabad, Rawalpindi, Faisalabad, Multan, Peshawar have two branches each, Quetta one). This makes
   master-data conflicts easy to detect.

## Records that are accepted but warned about (229)

* 221 PURCHASE rows where `unit_price < unit_cost`: goods bought above our own list price (all from one supplier).
  A business signal rather than an error.
* 8 rows that repeat the same branch, product, timestamp, event type and quantity as another event, which with hourly timestamps
  is probably coincidence.

## Injected business problems: what is actually in the file

The brief lists four injected patterns. Detection was run blind (no IDs in the logic) and each was checked
against an independent pandas calculation:

| Brief | In the data? | Evidence |
|---|---|---|
| One branch with unusually high discounting | **Yes** | 22.1% average discount vs 6.1% for peers; 63% of its sales discounted by more than 20% |
| One branch with abnormal inventory adjustments | **Yes** | average adjustment 18.8 units vs 4.0; every adjustment above 7 units belongs to it |
| One product with abnormal return behaviour | **No** | The named product's return rate is 10.2% against a 10.0% median, ranked 459th of 1,000. The highest product (z 3.8 of 1,000 products) is within chance. |
| One supplier with procurement cost inflation | **Yes** | purchases +25% above standard cost on every order since the first month; PKR 513,085 overpaid |

All patterns that exist are constant across the 33 months (none starts at a point in time), so a purely
historical per-entity baseline would never see them. Detection therefore compares each entity with its
peers, and separately watches each entity for change against its own history (which finds nothing here, correctly).

A derived copy of the dataset with a genuine product-return pattern injected can be produced with
`scripts/make_variant_dataset.py`; it exists to prove the return detector works when the pattern is real.
