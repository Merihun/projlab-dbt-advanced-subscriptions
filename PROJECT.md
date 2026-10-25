# Subscription Revenue Platform: Advanced dbt at Scale

Fernhill Outfitters launched **Fernhill Box**, a monthly or annual subscription box, in July 2025. Finance
needs MRR it can trust, with its movements (new, expansion, contraction, churn, reactivation), cohort
retention, and recognised revenue in GBP from invoices in three currencies. You build that as one dbt project
that runs on two warehouses: the lab's PostgreSQL (`dev` and `prod`) and a DuckDB file (`ci`).

This project writes its own macros. There is no `packages.yml` and no `dbt deps`: no dbt_utils, no
dbt_expectations. The date spine, the surrogate key, the cross-database helpers and the generic tests are
yours. That is the point of the project, and it is also why the checks can run offline.

## What is provided

| Path | What it is |
|------|------------|
| `compose.yaml`, `.devcontainer/` | The lab: a workspace (Python 3.12, dbt-core 1.12.5, dbt-postgres 1.11.0, dbt-duckdb 1.11.0, DuckDB 1.5.6, SQLFluff) and PostgreSQL 16 |
| `fernhill_subs/` | The practice data: `python -m fernhill_subs generate` and `python -m fernhill_subs load` |
| `profiles.yml` | The `dev` target, ready. You add `prod` and `ci` (step 1) |
| `dbt_project.yml` | Name, profile and paths. You add the rest |
| `models/`, `macros/`, `tests/generic/`, `snapshots/`, `analyses/` | Empty folders, laid out as the steps use them |
| `answers/finance.txt`, `answers/materialisations.txt` | Two answer sheets (steps 6 and 9) |
| `Makefile` | The commands below (`make` lists them) |
| `.sqlfluff` | SQLFluff with the dbt templater on the `ci` target |
| `tests/test_environment.py` | Proves the lab works |

## Start

```sh
docker compose up -d --build --wait
docker compose exec workspace bash      # this folder is /workspace
make test                               # 5 passed
make data                               # data/batch_1/, data/batch_2/, seeds/*.csv
make load BATCH=1 TARGET=dev            # batch 1 into PostgreSQL (schema raw)
```

## Commands

| Command | What it does |
|---------|--------------|
| `make data` | Writes the two batches and the two seed CSVs. Always the same bytes |
| `make load BATCH=1 TARGET=dev` | Loads a batch into `raw`. `TARGET=dev` or `prod`: PostgreSQL; `TARGET=ci`: DuckDB (`fernhill.duckdb`). Batch 1 starts `raw` from nothing; batch 2 needs batch 1 |
| `make build TARGET=dev` | `dbt build --target dev` |
| `make build-ci` | The `ci` target from nothing: batch 1, `dbt build`, batch 2, `dbt build` (what the checks do) |
| `make freshness`, `make counts`, `make parse` | `dbt source freshness`, `dbt run-operation source_row_counts`, `dbt parse` |
| `make docs` | The docs site on http://localhost:8081 |
| `make ci-state`, `make slim` | Step 10: production's state in `state/`, then a slim build of what changed |
| `make lint` | SQLFluff on `models/` (needs batch 1 in DuckDB) and ruff on the Python |
| `make check` | The project's checks on your machine (download the check kit on the Review page) |

dbt itself runs as `dbt ...` in the workspace; `profiles.yml` and `dbt_project.yml` are in this folder
(`DBT_PROFILES_DIR` and `DBT_PROJECT_DIR` are `/workspace`).

## The targets

| Target | Warehouse | Schemas your models land in |
|--------|-----------|-----------------------------|
| `dev` | PostgreSQL, schema `dbt_dev` | `dbt_dev_staging`, `dbt_dev_intermediate`, `dbt_dev_finance`, `dbt_dev_product`, `dbt_dev_metrics`, `dbt_dev_reference`, `dbt_dev_snapshots`, `dbt_dev_audit` |
| `prod` | PostgreSQL, schema `analytics` | `staging`, `intermediate`, `finance`, `product`, `metrics`, `reference`, `snapshots`, `audit` |
| `ci` | DuckDB file `DBT_DUCKDB_PATH` (default `fernhill.duckdb`), schema `DBT_CI_SCHEMA` (default `ci`) | `ci_staging`, `ci_finance`, ... and, for a pull request built with `DBT_CI_SCHEMA=pr_42` (`dbt-ci.yml` uses the pull request's number, `make slim` uses `pr_local`), `pr_42_finance`, ... |

`dev` and `prod` read the same `raw` schema. Credentials come from the environment (`POSTGRES_HOST`,
`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`); `compose.yaml` sets them in the workspace.

## The data

`make data` writes two batches. Batch 1 is everything received before 1 April 2026; batch 2 everything
received from 1 April to 30 June 2026. The loader adds `_loaded_at` (when it loaded the row, UTC) to every
row.

| Table in `raw` | Source | Loaded as | Rows after batch 1 / 2 |
|----------------|--------|-----------|------------------------|
| `customers` | `billing` | full export, replaced | 1,030 / 1,531 |
| `plan_prices` | `billing` | full export, replaced | 15 / 18 |
| `subscriptions` | `billing` | full export, replaced | 1,047 / 1,572 |
| `subscription_events` | `billing` | appended | 1,462 / 2,780 |
| `invoice_lines` | `billing` | appended | 4,166 / 8,436 |
| `app_events` | `app` | appended | 13,541 / 25,914 |

Seeds (written by `make data` into `seeds/`): `currencies.csv` (GBP, EUR, USD with the **budget rate**,
GBP per unit, that finance uses for MRR) and `fx_rates.csv` (monthly rates, GBP per unit, valid from
`valid_from` up to but not including `valid_to`).

Plans: Mini (monthly), Classic and Explorer (monthly or annual), each priced in GBP, EUR and USD. A price never
changes its amount: a new amount is a new `price_id`. A customer is billed in one currency.

### What is wrong with the data (on purpose)

- **Webhooks arrive twice.** `subscription_events` has rows with the same `event_id` (the billing system
  retries): 46 duplicated events in batch 1, 83 after batch 2, three of them delivered once in March and
  again in April. Keep the **first delivery** (earliest `received_at`).
- **Events arrive late and out of order.** The last event of March arrived in April. Always order a
  subscription's events by `occurred_at`, never by `received_at`.
- **App events are repeated and late.** The app retries (one event, several rows) and phones that were
  offline send events up to three days late: a few events of the last days of March arrive in batch 2.
- **A column is renamed.** From batch 2 the billing export calls `invoice_lines.amount_cents`
  `amount_minor`. The loader adds the new column, so old rows have `amount_cents` and new rows
  `amount_minor`, each NULL where the other is set.
- **A column is new.** `app_events.app_version` exists from batch 2 only.
- **Currency codes in lower case.** A few invoice lines say `eur` or `usd`: 16 in batch 1 and 34 after
  batch 2.
- **Emails** are sometimes in capitals or with spaces around them.
- **The plan catalogue changes in batch 2.** Explorer is renamed **Explorer Plus** (updated 15 April 2026);
  Classic monthly gets new prices (new `price_id`s ending `_2026`, created 20 April 2026) and the old ones are
  archived (`active` false, updated 1 May 2026). Classic monthly subscribers are moved to the new prices on 1
  to 3 May 2026 by `subscription_updated` events.
- **Billing is real billing:** mid-period plan and quantity changes produce two `proration` lines (a credit
  for the old price and a charge for the new one, for the rest of the period), pauses stop billing,
  cancellations sometimes get a `refund` line, and some lines get a `credit_note` of 5.00.

## The definitions the checks use

**Subscription events.** Event types `subscription_created`, `subscription_updated`, `subscription_paused`,
`subscription_resumed`, `subscription_cancelled`. Each row carries the state after the event: `price_id`,
`quantity`, `status` (`active`, `paused`, `cancelled`).

**Subscription periods** (`int_subscription_periods`). One row per (deduplicated) event: the state is valid
from the event's `occurred_at` (`valid_from`) until the subscription's next event's `occurred_at`
(`valid_to`), NULL for the last event. `mrr_local` is the state's monthly amount: `unit_amount x quantity`,
divided by 12 for an annual price, 0 unless `status` is `active`. Annual prices divide by 12 exactly.

**MRR** (`int_customer_mrr_months`, `fct_mrr`). One row per customer and month, from the month of the
customer's first subscription event to the **last month in the data** (the month of the latest
`occurred_at`: March 2026 after batch 1, June 2026 after batch 2), months without MRR included as 0. A
customer's MRR for a month is the sum of `mrr_local` of the periods valid **at the end of the month**: the
periods with `valid_from` before the first instant of the next month and `valid_to` NULL or not before it.
`mrr_gbp` = `round(mrr_local x budget_gbp_rate, 2)` (the customer's currency in `currencies`).

**Movements** (`fct_mrr_movements`). One row per customer and month in which `mrr_gbp` differs from the
previous month's (0 before the customer's first month): **new** (from 0, never above 0 before),
**reactivation** (from 0, above 0 in some earlier month), **expansion** (up), **contraction** (down, not to
0), **churn** (to 0). `mrr_change_gbp` = this month's `mrr_gbp` minus last month's. A pause brings MRR to 0
(churn) and a resume back (reactivation).

**Cohort retention** (`rpt_cohort_retention`). A customer's cohort is the first month their `mrr_gbp` was
above 0. For each cohort and each month from the cohort's month to the last month (`months_since_start`
0, 1, ...): `cohort_customers`, `active_customers` (members with `mrr_gbp` above 0 that month),
`retention_rate` = `round(active / cohort, 4)` and `retained_mrr_gbp` (their `mrr_gbp`).

**Recognised revenue** (`int_invoice_line_allocations`, `fct_revenue_gbp`). Every invoice line's amount is
spread over the days of its service period `[period_start, period_end)`: a month gets `amount x (days of the
period in that month) / (days of the period)`. In GBP at the `fx_rates` rate valid on the day the line was
**issued**. `fct_revenue_gbp` has one row per month and currency; only these totals are rounded (to pence).

**Daily engagement** (`fct_daily_engagement`). Per day an app event happened (`occurred_at`): the number of
distinct customers and of (deduplicated) events.

**Snapshots.** `plan_prices_snapshot`: timestamp strategy on `updated_at`, so a version is valid from the
row's `updated_at`; after a snapshot following each batch it holds 27 versions of 18 prices.
`subscriptions_snapshot`: check strategy on `status`, `price_id`, `quantity`. Both set
`dbt_valid_to_current` to `9999-12-31`.

## How your work is checked

The checks copy your dbt project into a temporary folder, generate the data afresh and run dbt on the **ci
target only** (DuckDB, offline, no `dbt deps`): batch 1 and `dbt build`, batch 2 and `dbt build`, then
`dbt run --select fct_daily_engagement --event-time-start 2026-03-01 --event-time-end 2026-07-01`. Before
batch 2 they drop `app_version` from `fct_app_events`, as if the table had been built before the column
existed. They read `manifest.json`, `run_results.json` and `semantic_manifest.json`, query the DuckDB file,
and compare with the numbers `fernhill_subs` works out from the same rows. They read your `profiles.yml`,
`answers/`, `selectors.yml` and `.github/workflows/` but use a `profiles.yml` of their own (targets `ci`,
`dev` and `prod`, all DuckDB) to run dbt. Nothing on PostgreSQL is checked: the lab is where you see it work.
