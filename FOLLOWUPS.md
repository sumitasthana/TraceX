# Tier-1 follow-ups (out of scope for the landing/idempotency/registry bundle)

Items spotted during the Tier-1 work that the spec explicitly told me NOT to
implement now. Carry these into the next sprint.

## Pipeline contracts

- **Late-arriving data**: source rows whose `txn_date` precedes the current
  `as_of_date` should be ingested into the partition that owns their event
  date, not today's. Needs a `received_date` column on `src_transaction` and
  a re-ingest path that re-emits a prior `as_of_date` partition.
- **Source-of-record contracts as YAML**: replace the hardcoded `SOR_MAP` in
  `_1_ingest_landing.py` and `scripts/bootstrap_landing.py` with one YAML file
  per (sor, entity) under `landing/_contracts/` that defines schema, expected
  business-date column, allowed null columns, and PII flags. The ingest stage
  should validate against the contract.
- **Reconciliation table**: a fact comparing landing row counts to L1
  derivations, so silent drops between ingestion and staging surface as DQ
  failures rather than as quiet differences.

## Schema design

- **SCD2 on `src_customer`**: today the customer dimension is rebuilt every
  run; field changes (new branch, KYC status flip) overwrite the old row.
  An effective-from / effective-to pair on `stg_customer_enriched` would let
  fact tables join historically.
- **Boolean `is_fx_backfilled`**: stage 02 currently encodes the backfill
  signal as a `_BACKFILL` suffix on `fx_rate_source`. A separate boolean
  column would let downstream consumers filter cleanly without string magic.

## Operations

- **Notification/SLA**: every `pipeline_runs` row with `status='failed'` or
  `duration_ms > threshold` should fan out to a Slack/email destination. The
  catalog/run-registry is now queryable; this is just glue.
- **Audit-log signing**: `catalog_review_log` rows are append-only by
  convention but not by enforcement. A hash chain (`prev_hash` column +
  trigger) would make tampering detectable.
- **Landing retention**: keep N most-recent partitions per entity, garbage
  collect the rest. Today the tree grows unbounded.

## UX

- **`pipeline_runs` browser in the UI**: the registry tables are populated
  but unsurfaced. A "Runs" view filterable by `status` / `as_of_date` would
  replace the current "Pipeline Runs" page (which reads JSONL files).
