"""Stage 02 — produce stg_transaction_normalized.

USD-equivalent amounts are computed by ASOF-joining stg_fx_resolved on the most recent
rate at-or-before txn_date. USD transactions short-circuit to rate=1.0 with a NULL source.
When a transaction predates the earliest known rate for its currency we fall back to the
earliest available rate and tag fx_rate_source with a '_BACKFILL' suffix — banks need a
USD-equivalent for every transaction, but the suffix preserves the lineage signal that
this row was reconciled with a non-asof rate. Every miss is also logged as a warning.

After the build we surface three pipeline-health signals: any FX lookup misses, the
reversal share of the population, and the international ratio per original currency.
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.config import (  # noqa: E402
    configure_logging,
    db_connect,
    get_run_id,
    table_row_count,
)

STAGE_NAME = Path(__file__).stem
INPUT_TABLE = "src_transaction"
FX_TABLE = "stg_fx_resolved"
OUTPUT_TABLE = "stg_transaction_normalized"

TRANSFORM_SQL = f"""
CREATE OR REPLACE TABLE {OUTPUT_TABLE} AS
WITH fallback AS (
    SELECT
        from_currency,
        FIRST(rate         ORDER BY rate_date) AS first_rate,
        FIRST(rate_source  ORDER BY rate_date) AS first_source,
        MIN(rate_date)                         AS first_date
    FROM {FX_TABLE}
    GROUP BY from_currency
),
asof_join AS (
    SELECT
        t.*,
        f.rate        AS asof_rate,
        f.rate_source AS asof_source
    FROM {INPUT_TABLE} t
    ASOF LEFT JOIN {FX_TABLE} f
      ON t.currency = f.from_currency
     AND t.txn_date >= f.rate_date
)
SELECT
    aj.txn_id,
    aj.account_id,
    aj.txn_date,
    aj.txn_timestamp,
    aj.txn_type,
    aj.channel,
    aj.status,
    ROUND(
        aj.amount *
        CASE
            WHEN aj.currency = 'USD'      THEN 1.0
            WHEN aj.asof_rate IS NOT NULL THEN aj.asof_rate
            ELSE fb.first_rate
        END
    , 2) AS amount_usd,
    (aj.reversal_flag = 'Y') AS is_reversal,
    (aj.counterparty_bank_bic IS NOT NULL
        AND aj.counterparty_bank_bic NOT LIKE 'US%') AS is_international,
    ROUND(
        aj.amount *
        CASE
            WHEN aj.currency = 'USD'      THEN 1.0
            WHEN aj.asof_rate IS NOT NULL THEN aj.asof_rate
            ELSE fb.first_rate
        END
        * CASE WHEN aj.reversal_flag = 'Y' THEN 0 ELSE 1 END
    , 2) AS net_amount_usd,
    aj.currency AS original_currency,
    CASE
        WHEN aj.currency = 'USD'      THEN 1.0
        WHEN aj.asof_rate IS NOT NULL THEN aj.asof_rate
        ELSE fb.first_rate
    END AS fx_rate_used,
    CASE
        WHEN aj.currency = 'USD'      THEN NULL
        WHEN aj.asof_rate IS NOT NULL THEN aj.asof_source
        ELSE fb.first_source || '_BACKFILL'
    END AS fx_rate_source
FROM asof_join aj
LEFT JOIN fallback fb ON aj.currency = fb.from_currency
"""

# A miss is a non-USD txn whose date precedes the earliest known rate for that currency,
# triggering the _BACKFILL fallback. We surface every (currency, date) so backfill volume
# is auditable in the JSONL log.
FX_MISS_SQL = f"""
SELECT original_currency, txn_date, COUNT(*) AS miss_count
FROM {OUTPUT_TABLE}
WHERE fx_rate_source LIKE '%_BACKFILL'
  AND original_currency <> 'USD'
GROUP BY original_currency, txn_date
ORDER BY miss_count DESC
"""

REVERSAL_SQL = f"""
SELECT
    SUM(CASE WHEN is_reversal THEN 1 ELSE 0 END) AS reversal_count,
    COUNT(*)                                     AS total_count
FROM {OUTPUT_TABLE}
"""

INTERNATIONAL_SQL = f"""
SELECT
    original_currency,
    COUNT(*)                                                 AS total_txns,
    SUM(CASE WHEN is_international THEN 1 ELSE 0 END)        AS international_txns
FROM {OUTPUT_TABLE}
GROUP BY original_currency
ORDER BY total_txns DESC
"""


def main() -> int:
    run_id = get_run_id()
    log = configure_logging(run_id, STAGE_NAME)
    started = time.perf_counter()

    try:
        with db_connect() as con:
            input_rows = table_row_count(con, INPUT_TABLE)
            fx_rows = table_row_count(con, FX_TABLE)
            log.info(
                "stage_start",
                input_table=INPUT_TABLE,
                input_row_count=input_rows,
                fx_table=FX_TABLE,
                fx_row_count=fx_rows,
                output_table=OUTPUT_TABLE,
            )

            log.info("transform_start", sql=TRANSFORM_SQL.strip())
            t0 = time.perf_counter()
            con.execute(TRANSFORM_SQL)
            output_rows = table_row_count(con, OUTPUT_TABLE)
            log.info(
                "transform_complete",
                output_row_count=output_rows,
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

            # FX misses — non-USD transactions that fell back to the earliest known rate
            miss_rows = con.execute(FX_MISS_SQL).fetchall()
            total_misses = sum(int(r[2]) for r in miss_rows)
            for currency, txn_date, miss_count in miss_rows:
                log.warning(
                    "fx_rate_lookup_miss",
                    currency=currency,
                    txn_date=str(txn_date),
                    miss_count=int(miss_count),
                    resolution="backfilled_with_earliest_rate",
                )
            log.info(
                "fx_backfill_summary",
                rows_checked=output_rows,
                rows_backfilled=total_misses,
                backfill_pct=round((total_misses / output_rows * 100) if output_rows else 0.0, 4),
            )

            # Reversal stats
            reversal_count, total_count = con.execute(REVERSAL_SQL).fetchone()
            reversal_count = int(reversal_count or 0)
            total_count = int(total_count or 0)
            ratio = (reversal_count / total_count) if total_count else 0.0
            log.info(
                "reversal_summary",
                reversal_count=reversal_count,
                total_count=total_count,
                reversal_pct=round(ratio * 100, 4),
            )

            # International ratio per currency
            for currency, total, intl in con.execute(INTERNATIONAL_SQL).fetchall():
                total = int(total)
                intl = int(intl or 0)
                pct = (intl / total * 100) if total else 0.0
                log.info(
                    "international_ratio_by_currency",
                    currency=currency,
                    total_txns=total,
                    international_txns=intl,
                    international_pct=round(pct, 4),
                )

        log.info(
            "stage_complete",
            output_table=OUTPUT_TABLE,
            output_row_count=output_rows,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return 0
    except Exception as exc:
        log.error(
            "stage_exception",
            error=str(exc),
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
        )
        raise


if __name__ == "__main__":
    sys.exit(main())
