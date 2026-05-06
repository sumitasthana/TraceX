# TraceX Pipeline

Date-anchored staging + facts pipeline over the synthetic banking dataset.
Every run is keyed on a **business date** (`as_of_date`) so the same input on the
same date produces byte-identical output.

## Invocation

```powershell
python pipeline/run_pipeline.py --as-of-date 2024-09-30
```

`--as-of-date` is **required**. Running without it exits non-zero with a clear
error. The orchestrator sets `TRACEX_AS_OF_DATE` in the environment it passes to
each stage subprocess, so every SQL expression that needs a date reads the same
value.

For the higher-level wrapper:

```powershell
python cli.py pipeline --as-of-date 2024-09-30
python cli.py up       --as-of-date 2024-09-30   # bootstrap + pipeline + ingest + serve
```

## Stages, in order

| Stage | What it does |
|---|---|
| `_1_ingest_landing` | Validates `landing/<sor>/<entity>/business_date=<date>/{data.parquet,_manifest.json}` and promotes each parquet into a `raw_*` and `src_*` table. Failure here aborts the run BEFORE any `src_*` table is mutated. |
| `00_validate_sources` | Asserts every `EXPECTED_SOURCES` table exists and is non-empty after ingestion. |
| `01_stg_fx_normalize` | Builds `stg_fx_resolved` (filtered to USD targets). |
| `02_stg_transactions` | Builds `stg_transaction_normalized` via ASOF-join on FX. |
| `03_stg_customers` | Builds `stg_customer_enriched` (age, KYC freshness, branch region). Date-anchored to `as_of_date`. |
| `10_fct_risk_profile` | Partition-overwrite into `fct_customer_risk_profile` for the current `as_of_date`. Other partitions are untouched. |
| `99_validate_outputs` | 15 DQ checks (10 L1 + 5 L2). All L2 checks scope to the current partition. |

## Idempotency

- **Source**: `src_*` tables are output of stage `_1_ingest_landing` keyed on the manifest in the landing partition. Same parquet bytes + same manifest → same `src_*`.
- **Stage**: `stg_*` tables are derived 1:1 from sources, rebuilt on every run (`CREATE OR REPLACE`). They aren't historised — the catalog is.
- **Fact**: `fct_customer_risk_profile` is historised by `(customer_id, as_of_date)`. Each run does `DELETE FROM fct... WHERE as_of_date = ?` then `INSERT`. Re-running for the same date is a no-op semantically; running for a new date appends.

`computed_at` on the fact stays as wall-clock `CURRENT_TIMESTAMP` — it is metadata about *when* the row was produced, not a join key, and it isn't checked by any DQ rule. Every other `CURRENT_DATE` / `CURRENT_TIMESTAMP` has been removed in favour of `as_of_date`.

## Run registry

The orchestrator records every invocation:

```sql
SELECT run_id, as_of_date, status, failed_stage, duration_ms FROM pipeline_runs;
SELECT * FROM pipeline_run_stages WHERE run_id = '<some_uuid>';
```

`git_sha` is best-effort (`git rev-parse HEAD`); nullable when git isn't on PATH.

The orchestrator never holds a DuckDB connection while a stage subprocess runs (DuckDB is single-writer per file). Every registry write opens a short-lived connection and closes it before the next stage dispatch.

## Landing zone

```
landing/
  core_banking/
    branch/business_date=2024-09-30/{data.parquet, _manifest.json}
    customer/business_date=2024-09-30/{data.parquet, _manifest.json}
    account/business_date=2024-09-30/{data.parquet, _manifest.json}
    transaction/business_date=2024-09-30/{data.parquet, _manifest.json}
  fx_vendor/
    fx_rate/business_date=2024-09-30/{data.parquet, _manifest.json}
```

`_manifest.json` schema:

```json
{
  "sor": "core_banking",
  "entity": "transaction",
  "business_date": "2024-09-30",
  "row_count": 12345,
  "sha256": "<hex digest of data.parquet>",
  "produced_at": "2024-10-01T02:13:44Z",
  "schema_version": "1.0"
}
```

The ingestion stage validates every required key, recomputes the sha256, and asserts the row count matches the parquet's actual count. **Any failure aborts the run before mutating `src_*` tables.**

To migrate an existing `data/tracex_layer0.duckdb` into this layout (one-shot):

```powershell
python scripts/bootstrap_landing.py
# or, equivalently:
python cli.py bootstrap-landing
```
