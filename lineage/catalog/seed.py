"""One-shot catalog seeder — `python cli.py catalog seed`.

Idempotent. Re-running:
  - Updates `certified_at` on the existing certification rows; no duplicates.
  - Reapplies the single ratified lineage entry for `stg_fx_resolved.rate`
    using the live SQL hash so future stage 01 runs hit the catalog.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import structlog

from lineage.catalog.client import (
    REVIEW_RATIFIED,
    SOURCE_CATALOG,
)
from lineage.catalog.local import LocalCatalog
from lineage.sql_parser import hash_sql

log = structlog.get_logger().bind(component="catalog_seed")


CERTIFICATIONS = [
    ("fct_customer_risk_profile",   "P1", "Regulatory anchor — risk_score / risk_tier"),
    ("stg_transaction_normalized",  "P2", "High-risk customer-facing — feeds risk model"),
    ("stg_customer_enriched",       "P2", "High-risk customer-facing — feeds risk model"),
    ("stg_fx_resolved",             "P3", "Production reference data"),
    ("src_customer",                "P3", "Source-of-record raw"),
    ("src_account",                 "P3", "Source-of-record raw"),
    ("src_transaction",             "P3", "Source-of-record raw"),
    ("src_branch",                  "P3", "Source-of-record raw"),
    ("src_fx_rate",                 "P3", "Source-of-record raw"),
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_stage01_sql() -> str:
    """Read the live `TRANSFORM_SQL` constant from stage 01 so the seeded
    catalog row's `sql_hash` matches what stage 01 emits at runtime."""
    here = Path(__file__).resolve()
    stage_path = here.parents[2] / "pipeline" / "stages" / "01_stg_fx_normalize.py"
    src = stage_path.read_text(encoding="utf-8")
    m = re.search(r'TRANSFORM_SQL\s*=\s*f?"""(.+?)"""', src, re.DOTALL)
    if not m:
        return ""
    raw = m.group(1)
    # Resolve the f-string placeholders we care about.
    raw = raw.replace("{OUTPUT_TABLE}", "stg_fx_resolved")
    raw = raw.replace("{INPUT_TABLE}", "src_fx_rate")
    return raw


def seed(catalog: LocalCatalog | None = None, actor: str = "seed") -> dict:
    """Apply the demo seed. Returns a small summary dict."""
    catalog = catalog or LocalCatalog()
    ts = _utc_now()
    summary = {
        "certifications_upserted": 0,
        "lineage_seeded": 0,
        "ts": ts,
    }

    # ── 1. Certifications ──
    try:
        con = catalog._connect()
        try:
            for table_name, profile, notes in CERTIFICATIONS:
                existing = con.execute(
                    "SELECT 1 FROM catalog_certification WHERE table_name = ?",
                    [table_name],
                ).fetchone()
                if existing:
                    con.execute(
                        """
                        UPDATE catalog_certification
                        SET profile = ?, certified_by = ?, certified_at = ?, notes = ?
                        WHERE table_name = ?
                        """,
                        [profile, actor, ts, notes, table_name],
                    )
                else:
                    con.execute(
                        """
                        INSERT INTO catalog_certification
                        (table_name, profile, certified_by, certified_at, notes)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        [table_name, profile, actor, ts, notes],
                    )
                summary["certifications_upserted"] += 1
            con.execute(
                """
                INSERT INTO catalog_review_log
                (ts, action, table_name, column_name, actor, from_state, to_state, reason)
                VALUES (?, 'certify_seed', '*', '*', ?, 'absent', 'certified',
                        'one-shot seed of demo certifications')
                """,
                [ts, actor],
            )
        finally:
            con.close()
    except Exception as exc:
        log.error("seed_certifications_failed", error=str(exc))

    # ── 2. One ratified lineage entry to demonstrate the catalog-hit path ──
    sql = _read_stage01_sql()
    sql_hash = hash_sql(sql) if sql else ""

    try:
        con = catalog._connect()
        try:
            existing = con.execute(
                """
                SELECT 1 FROM catalog_lineage
                WHERE target_table = 'stg_fx_resolved' AND target_column = 'rate'
                  AND source_table = 'src_fx_rate' AND source_column = 'rate'
                """,
            ).fetchone()
            if existing:
                con.execute(
                    """
                    UPDATE catalog_lineage
                    SET expression = ?, transform_type = 'PASSTHROUGH', confidence = 1.0,
                        source = ?, review_state = ?,
                        ratified_by = ?, ratified_at = ?,
                        sql_hash = ?, computed_at = ?
                    WHERE target_table = 'stg_fx_resolved' AND target_column = 'rate'
                      AND source_table = 'src_fx_rate' AND source_column = 'rate'
                    """,
                    ["rate", SOURCE_CATALOG, REVIEW_RATIFIED, actor, ts, sql_hash, ts],
                )
            else:
                con.execute(
                    """
                    INSERT INTO catalog_lineage (
                        target_table, target_column, source_table, source_column,
                        expression, transform_type, confidence, source, review_state,
                        ratified_by, ratified_at, sql_hash, computed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        "stg_fx_resolved", "rate",
                        "src_fx_rate", "rate",
                        "rate", "PASSTHROUGH", 1.0,
                        SOURCE_CATALOG, REVIEW_RATIFIED,
                        actor, ts,
                        sql_hash, ts,
                    ],
                )
            con.execute(
                """
                INSERT INTO catalog_review_log
                (ts, action, table_name, column_name, actor, from_state, to_state, reason)
                VALUES (?, 'ratify_seed', 'stg_fx_resolved', 'rate', ?,
                        'absent', 'ratified',
                        'one-shot seed: demonstrates catalog-hit path on stage 01')
                """,
                [ts, actor],
            )
            summary["lineage_seeded"] = 1
        finally:
            con.close()
    except Exception as exc:
        log.error("seed_lineage_failed", error=str(exc))

    log.info("catalog_seed_complete", **summary, sql_hash_prefix=sql_hash[:12])
    return summary


__all__ = ["seed", "CERTIFICATIONS"]
