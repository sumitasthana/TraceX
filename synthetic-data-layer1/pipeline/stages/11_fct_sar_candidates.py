"""Stage 11 — produce fct_regulatory_sar_candidates.

Filters fct_customer_risk_profile down to customers who fire at least one of three
inclusion rules (OR logic), and joins back to stg_transaction_normalized via
src_account to attach all-time international-transaction context. flagging_reasons is
built with the spec-mandated `list_filter([CASE…], x -> x IS NOT NULL)` pattern so the
firing rules survive in lineage as a real list, not a concatenated string.
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
OUTPUT_TABLE = "fct_regulatory_sar_candidates"

LINEAGE_MANIFEST: dict = {
    "target_table": "fct_regulatory_sar_candidates",
    "source_tables": [
        "fct_customer_risk_profile",
        "stg_transaction_normalized",
        "src_account",
        "src_transaction",
    ],
    "depends_on_stages": ["10_fct_risk_profile", "02_stg_transactions"],
    "transform_type": "FILTER_AGGREGATE",
    "derived_columns": {
        "flagging_reasons":
            "list_filter([\n"
            "    CASE WHEN risk_tier='HIGH' THEN 'RULE_HIGH_RISK_TIER' END,\n"
            "    CASE WHEN international_txn_ratio>0.5 AND total_txn_volume_usd_90d>10000 "
            "THEN 'RULE_INTL_VOLUME' END,\n"
            "    CASE WHEN reversal_rate>0.1 AND txn_count_90d>5 "
            "THEN 'RULE_HIGH_REVERSAL' END\n"
            "], x -> x IS NOT NULL)",
        "total_suspicious_amount_usd":
            "COALESCE(SUM(net_amount_usd) FILTER (is_international), 0) per customer "
            "(all-time, not 90d)",
        "suspicious_txn_count":
            "COUNT(*) FILTER (is_international) per customer (all-time)",
        "counterparty_countries":
            "list_distinct(list(LEFT(counterparty_bank_bic, 2)) FILTER ("
            "counterparty_bank_bic IS NOT NULL AND counterparty_bank_bic <> '')) "
            "per customer",
        "dominant_channel":
            "mode(channel) per customer (most-frequent channel)",
        "sar_priority":
            "'CRITICAL' if risk_score > 0.8 else "
            "'HIGH' if risk_score > 0.65 else 'MEDIUM'",
    },
}

# Inclusion rules expressed as a single SQL CASE chain inside list_filter.
# All three predicates are evaluated independently; list_filter strips the NULLs left
# by non-firing CASE branches, leaving only the rule names that actually fired.
TRANSFORM_SQL = f"""
CREATE OR REPLACE TABLE {OUTPUT_TABLE} AS
WITH customer_txns AS (
    SELECT
        a.customer_id,
        t.channel,
        t.is_international,
        t.net_amount_usd,
        s.counterparty_bank_bic
    FROM stg_transaction_normalized t
    JOIN src_account a     ON a.account_id = t.account_id
    JOIN src_transaction s ON s.txn_id = t.txn_id
),
customer_suspicion AS (
    SELECT
        rp.customer_id,
        COALESCE(SUM(ct.net_amount_usd) FILTER (WHERE ct.is_international), 0)
            AS total_suspicious_amount_usd,
        CAST(COUNT(*) FILTER (WHERE ct.is_international) AS BIGINT)
            AS suspicious_txn_count,
        COALESCE(
            list_distinct(
                list(LEFT(ct.counterparty_bank_bic, 2)) FILTER (
                    WHERE ct.counterparty_bank_bic IS NOT NULL
                      AND ct.counterparty_bank_bic <> ''
                )
            ),
            CAST([] AS VARCHAR[])
        ) AS counterparty_countries,
        mode(ct.channel) AS dominant_channel
    FROM fct_customer_risk_profile rp
    LEFT JOIN customer_txns ct USING (customer_id)
    GROUP BY rp.customer_id
),
flagged AS (
    SELECT
        rp.customer_id,
        rp.full_name,
        rp.risk_score,
        rp.risk_tier,
        list_filter(
            [
                CASE WHEN rp.risk_tier = 'HIGH'
                     THEN 'RULE_HIGH_RISK_TIER' END,
                CASE WHEN rp.international_txn_ratio > 0.5
                      AND rp.total_txn_volume_usd_90d > 10000
                     THEN 'RULE_INTL_VOLUME' END,
                CASE WHEN rp.reversal_rate > 0.1
                      AND rp.txn_count_90d > 5
                     THEN 'RULE_HIGH_REVERSAL' END
            ],
            x -> x IS NOT NULL
        ) AS flagging_reasons,
        s.total_suspicious_amount_usd,
        s.suspicious_txn_count,
        s.counterparty_countries,
        s.dominant_channel,
        CASE
            WHEN rp.risk_score > 0.80 THEN 'CRITICAL'
            WHEN rp.risk_score > 0.65 THEN 'HIGH'
            ELSE 'MEDIUM'
        END AS sar_priority,
        rp.kyc_stale_flag,
        rp.branch_region
    FROM fct_customer_risk_profile rp
    LEFT JOIN customer_suspicion s USING (customer_id)
)
SELECT
    customer_id,
    full_name,
    risk_score,
    risk_tier,
    flagging_reasons,
    total_suspicious_amount_usd,
    suspicious_txn_count,
    counterparty_countries,
    dominant_channel,
    sar_priority,
    kyc_stale_flag,
    branch_region,
    CURRENT_TIMESTAMP AS flagged_at
FROM flagged
WHERE len(flagging_reasons) >= 1
"""

PRIORITY_SUMMARY_SQL = f"""
SELECT
    SUM(CASE WHEN sar_priority = 'CRITICAL' THEN 1 ELSE 0 END) AS critical_n,
    SUM(CASE WHEN sar_priority = 'HIGH'     THEN 1 ELSE 0 END) AS high_n,
    SUM(CASE WHEN sar_priority = 'MEDIUM'   THEN 1 ELSE 0 END) AS medium_n,
    COUNT(*)                                                  AS total_n
FROM {OUTPUT_TABLE}
"""

REGION_SUMMARY_SQL = f"""
SELECT branch_region, COUNT(*) AS n
FROM {OUTPUT_TABLE}
GROUP BY branch_region
ORDER BY n DESC
"""


def main() -> int:
    run_id = get_run_id()
    log = configure_logging(run_id, STAGE_NAME)
    started = time.perf_counter()

    try:
        with db_connect() as con:
            rp_rows = table_row_count(con, "fct_customer_risk_profile")
            txn_rows = table_row_count(con, "stg_transaction_normalized")
            log.info(
                "stage_start",
                input_tables=LINEAGE_MANIFEST["source_tables"],
                fct_customer_risk_profile_rows=rp_rows,
                stg_transaction_normalized_rows=txn_rows,
                output_table=OUTPUT_TABLE,
            )

            log.info("transform_start", **LINEAGE_MANIFEST, sql=TRANSFORM_SQL.strip())
            t0 = time.perf_counter()
            con.execute(TRANSFORM_SQL)
            output_rows = table_row_count(con, OUTPUT_TABLE)
            log.info(
                "transform_complete",
                output_row_count=output_rows,
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

            critical_n, high_n, medium_n, total_n = con.execute(PRIORITY_SUMMARY_SQL).fetchone()
            critical_n = int(critical_n or 0)
            high_n = int(high_n or 0)
            medium_n = int(medium_n or 0)
            total_n = int(total_n or 0)
            log.info(
                "sar_summary",
                total_candidates=total_n,
                CRITICAL=critical_n,
                HIGH=high_n,
                MEDIUM=medium_n,
            )

            region_rows = con.execute(REGION_SUMMARY_SQL).fetchall()
            top3 = region_rows[:3]
            region_counts: dict[str, int] = {}
            for region, n in top3:
                region_counts[str(region) if region is not None else "UNKNOWN"] = int(n)
            log.info("sar_by_region", region_counts=region_counts)

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
