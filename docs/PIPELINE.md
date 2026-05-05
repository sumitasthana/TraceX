# TraceX — Pipeline Reference

Layer 1 (staging) and Layer 2 (facts) read from the Layer 0 raw-source DuckDB at
`data/tracex_layer0.duckdb`. Layer 1 produces `stg_transaction_normalized` and
`stg_customer_enriched` plus a supporting `stg_fx_resolved` lookup. Every stage is an
independent runnable process (Airflow / cron friendly) and emits structured JSON logs.

## Layout

```
pipeline/
  config.py                   # paths, run_id, structlog setup, db helpers
  run_pipeline.py             # orchestrator (subprocesses each stage)
  stages/
    00_validate_sources.py    # precondition: Layer 0 tables exist + non-empty
    01_stg_fx_normalize.py    # build stg_fx_resolved
    02_stg_transactions.py    # build stg_transaction_normalized
    03_stg_customers.py       # build stg_customer_enriched
    10_fct_risk_profile.py    # build fct_customer_risk_profile
    99_validate_outputs.py    # DQ gate over Layer 1 outputs (exits 1 on any fail)
logs/                         # one {run_id}.jsonl per pipeline run (auto-created)
```

## Setup

From the repo root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

The Layer 0 DuckDB file must already exist. If you haven't built it yet:

```powershell
python layer0\generate.py     # writes CSVs into data/layer0/
python layer0\load_duckdb.py  # writes data/tracex_layer0.duckdb
```

## Environment variables

| Variable          | Default                                                         | Meaning                                                              |
|-------------------|-----------------------------------------------------------------|----------------------------------------------------------------------|
| `TRACEX_DB_PATH`  | `data\tracex_layer0.duckdb`                                     | DuckDB file the pipeline reads/writes.                               |
| `TRACEX_LOG_DIR`  | `.\logs`                                                        | Where `{run_id}.jsonl` files are written.                            |
| `TRACEX_RUN_ID`   | (unset → fresh UUID per process)                                | Set by the orchestrator so all stages in a run share a run_id.       |

A `.env` file in this directory is auto-loaded via `python-dotenv`.

## Running

### Full pipeline

```powershell
python pipeline\run_pipeline.py
```

The orchestrator generates one `run_id`, exports it as `TRACEX_RUN_ID`, then invokes each
stage as a subprocess in order. It exits `1` on the first non-zero stage and writes the
failure event to `logs\{run_id}.jsonl` before exiting.

### A single stage in isolation

Each stage is a self-contained script. Standalone runs mint their own `run_id`:

```powershell
python pipeline\stages\02_stg_transactions.py
python pipeline\stages\99_validate_outputs.py
```

To attach a standalone stage to an existing run, export the run_id first:

```powershell
$env:TRACEX_RUN_ID = "9816f247-ea1f-46fa-9f8e-357fb3743d34"
python pipeline\stages\02_stg_transactions.py
```

## Output tables

`stg_fx_resolved` — clean per-currency rate history filtered to `to_currency = 'USD'`.
Consumers ASOF-join on `(from_currency, rate_date)`.

`stg_transaction_normalized` — per-row USD-normalized transactions:

| Column               | Notes                                                                                  |
|----------------------|----------------------------------------------------------------------------------------|
| `amount_usd`         | `amount * fx_rate_used`. USD txns use rate 1.0.                                        |
| `is_reversal`        | `reversal_flag = 'Y'`.                                                                 |
| `is_international`   | `counterparty_bank_bic IS NOT NULL AND NOT LIKE 'US%'`.                                |
| `net_amount_usd`     | `amount_usd` zeroed out for reversals.                                                 |
| `fx_rate_used`       | The rate applied (1.0 for USD).                                                        |
| `fx_rate_source`     | `ECB`, `FED`, `MANUAL`, or `<source>_BACKFILL` if the txn predated the earliest rate. NULL for USD. |

**FX backfill behaviour.** The synthetic Layer 0 `src_fx_rate` history starts in 2022;
some transactions predate it. Stage 02 ASOF-joins to the most recent rate ≤ `txn_date`, and
when none exists falls back to the **earliest** known rate per currency, tagging
`fx_rate_source` with a `_BACKFILL` suffix. Every backfilled `(currency, date)` is also
emitted as a `fx_rate_lookup_miss` warning in the log so the volume is auditable.

`stg_customer_enriched` — derived customer attributes:

| Column                  | Notes                                                  |
|-------------------------|--------------------------------------------------------|
| `full_name`             | `first_name || ' ' || last_name`.                      |
| `age`                   | Whole-year age from `dob` to `CURRENT_DATE`.           |
| `is_us_person`          | `citizenship = 'US' OR country_of_birth = 'US'`.       |
| `kyc_days_since_review` | Days since `kyc_reviewed_at`, NULL if never reviewed.  |
| `kyc_stale_flag`        | `> 365` days since review OR `kyc_status = 'EXPIRED'`. |
| `branch_region`         | Joined from `src_branch.region`.                       |

## Data-quality gate (stage 99)

Every check emits a `data_quality_check` event. The stage exits `1` if any fail.

| `check_name`                          | Rule                                                                |
|---------------------------------------|---------------------------------------------------------------------|
| `stg_txn_amount_usd_not_null`         | No NULLs in `amount_usd`.                                           |
| `stg_txn_net_amount_usd_not_null`     | No NULLs in `net_amount_usd`.                                       |
| `stg_txn_amount_usd_nonneg`           | `amount_usd >= 0` for every row.                                    |
| `stg_txn_reversal_net_zero`           | `net_amount_usd = 0` whenever `is_reversal = TRUE`.                 |
| `stg_txn_no_silent_drops`             | Output row count ≥ `src_transaction` row count.                     |
| `stg_cust_full_name_not_null`         | No NULLs in `full_name`.                                            |
| `stg_cust_age_not_null`               | No NULLs in `age`.                                                  |
| `stg_cust_branch_region_not_null`     | No NULLs in `branch_region`.                                        |
| `stg_cust_age_18_120`                 | `age` between 18 and 120 inclusive.                                 |
| `stg_cust_one_to_one_with_src`        | Output row count = `src_customer` row count exactly.                |

## Reading the JSON logs

Every event is one line of JSON in `logs/{run_id}.jsonl`. Mandatory fields on every event:
`ts` (ISO-8601 UTC), `level`, `event`, `stage`, `run_id`. Stage-specific fields ride along.

Useful one-liners (PowerShell):

```powershell
# All events from one run
Get-Content logs\9816f247-...jsonl | ConvertFrom-Json | Format-Table ts,level,stage,event

# Just the warnings and errors
Get-Content logs\<run_id>.jsonl | ConvertFrom-Json | Where-Object level -in 'warning','error'

# Every DQ check result
Get-Content logs\<run_id>.jsonl | ConvertFrom-Json |
    Where-Object event -eq 'data_quality_check' |
    Format-Table check_name, passed, rows_checked, rows_failed
```

Or with `jq` (Bash):

```bash
jq 'select(.event=="data_quality_check") | {check_name, passed, rows_failed}' logs/<run_id>.jsonl
jq 'select(.level=="warning" or .level=="error")' logs/<run_id>.jsonl
```

## Event taxonomy

| Event                          | Level    | Where               | Purpose                                             |
|--------------------------------|----------|---------------------|-----------------------------------------------------|
| `pipeline_start`               | info     | orchestrator        | run_id, db_path, log_file, stage list.              |
| `orchestrator_stage_dispatch`  | info     | orchestrator        | About to invoke a stage subprocess.                 |
| `orchestrator_stage_ok`        | info     | orchestrator        | Stage exited 0.                                     |
| `orchestrator_stage_failed`    | error    | orchestrator        | Stage exited non-zero — pipeline halts.             |
| `pipeline_complete`            | info/err | orchestrator        | Final status + total `duration_ms`.                 |
| `stage_start`                  | info     | every stage         | Input table(s), input row count.                    |
| `transform_start`              | info     | transform stages    | Full SQL string under the `sql` key.                |
| `transform_complete`           | info     | transform stages    | Output row count + transform `duration_ms`.         |
| `stage_complete`               | info/err | every stage         | Total stage `duration_ms`, output table + count.    |
| `stage_exception`              | error    | every stage         | `error`, `error_type`, `traceback` — then re-raise. |
| `data_quality_check`           | info/err | sources, customers, gate | `check_name`, `expected`, `actual`, `rows_checked`, `rows_failed`, `passed`. |
| `fx_currency_coverage`         | info     | 01                  | Rate-row count + date range per currency.           |
| `fx_rate_lookup_miss`          | warning  | 02                  | One per (currency, date) backfilled row.            |
| `fx_backfill_summary`          | info     | 02                  | Total backfilled rows + percentage.                 |
| `reversal_summary`             | info     | 02                  | Reversal count and percentage of total.             |
| `international_ratio_by_currency` | info  | 02                  | Per currency: total + international counts + pct.   |
| `kyc_stale_summary`            | info/warn| 03                  | Warning if stale ratio > 20% (configurable).        |
| `kyc_status_breakdown`         | info     | 03                  | Counts by `kyc_status`.                             |

## Idempotency

Every transform uses `CREATE OR REPLACE TABLE`, so the pipeline is safe to re-run.
The `logs/` directory accumulates one file per run (delete old runs at your discretion).
