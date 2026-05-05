"""DuckDB-backed `LocalCatalog` — the only concrete `CatalogClient` impl
shipped with TraceX. The schema is created idempotently on first use; every
operation is wrapped so a corrupt or missing catalog never breaks the
pipeline (graph ingest still happens via sqlglot/agent).

Tables (live in `data/tracex_layer0.duckdb` alongside the rest of the platform):

  catalog_lineage          — one row per source edge, the working catalog
  catalog_certification    — one row per certified table
  catalog_review_log       — append-only audit log for ratify/reject/etc.
  catalog_descriptions     — semantic_description ratification lifecycle
                             (separate from edges so descriptions can be
                             ratified independently of lineage)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

import duckdb
import structlog

from lineage.catalog.client import (
    ALL_REVIEW_STATES,
    CatalogClient,
    CatalogEdge,
    REVIEW_ABSENT,
    REVIEW_PENDING,
    REVIEW_RATIFIED,
    SOURCE_AGENT_INFERRED,
    SOURCE_CATALOG,
    SOURCE_SQLGLOT,
    SOURCE_UNRESOLVED,
)

log = structlog.get_logger().bind(component="catalog")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


DDL = [
    """
    CREATE TABLE IF NOT EXISTS catalog_lineage (
        target_table   VARCHAR NOT NULL,
        target_column  VARCHAR NOT NULL,
        source_table   VARCHAR NOT NULL,
        source_column  VARCHAR NOT NULL,
        expression     VARCHAR,
        transform_type VARCHAR,
        confidence     DOUBLE,
        source         VARCHAR NOT NULL,
        review_state   VARCHAR NOT NULL,
        ratified_by    VARCHAR,
        ratified_at    VARCHAR,
        sql_hash       VARCHAR,
        computed_at    VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS catalog_certification (
        table_name    VARCHAR NOT NULL,
        profile       VARCHAR NOT NULL,
        certified_by  VARCHAR,
        certified_at  VARCHAR,
        notes         VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS catalog_review_log (
        ts           VARCHAR NOT NULL,
        action       VARCHAR NOT NULL,
        table_name   VARCHAR,
        column_name  VARCHAR,
        actor        VARCHAR,
        from_state   VARCHAR,
        to_state     VARCHAR,
        reason       VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS catalog_descriptions (
        table_name   VARCHAR NOT NULL,
        column_name  VARCHAR NOT NULL,
        description  VARCHAR,
        source       VARCHAR NOT NULL,
        review_state VARCHAR NOT NULL,
        ratified_by  VARCHAR,
        ratified_at  VARCHAR,
        computed_at  VARCHAR
    )
    """,
]


class LocalCatalog(CatalogClient):
    """DuckDB-backed catalog. Connections are opened per call so multiple
    pipeline subprocesses + the UI can read without a persistent lock."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            from pipeline.config import get_db_path  # late import to avoid cycle
            db_path = str(get_db_path())
        self.db_path = db_path
        self._available = self._bootstrap_schema()

    # ── Internal helpers ──

    def _bootstrap_schema(self) -> bool:
        try:
            con = duckdb.connect(self.db_path)
            try:
                for stmt in DDL:
                    con.execute(stmt)
            finally:
                con.close()
            return True
        except Exception as exc:
            log.warning("catalog_unavailable", phase="bootstrap", error=str(exc))
            return False

    def _connect(self):
        return duckdb.connect(self.db_path)

    # ── Reads ──

    def get_column_lineage(self, table: str) -> List[CatalogEdge]:
        try:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT target_table, target_column, source_table, source_column,
                           expression, transform_type, confidence, source, review_state,
                           ratified_by, ratified_at, sql_hash, computed_at
                    FROM catalog_lineage
                    WHERE target_table = ?
                    """,
                    [table],
                ).fetchall()
            finally:
                con.close()
        except Exception as exc:
            log.warning("catalog_unavailable", phase="get_column_lineage", error=str(exc))
            return []
        return [_row_to_edge(r) for r in rows]

    def get_certification(self, table: str) -> Optional[str]:
        try:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT profile FROM catalog_certification WHERE table_name = ?",
                    [table],
                ).fetchone()
            finally:
                con.close()
        except Exception as exc:
            log.warning("catalog_unavailable", phase="get_certification", error=str(exc))
            return None
        return str(row[0]) if row else None

    def get_review_state(self, table: str, column: str) -> str:
        try:
            con = self._connect()
            try:
                row = con.execute(
                    """
                    SELECT MIN(review_state) FROM catalog_lineage
                    WHERE target_table = ? AND target_column = ?
                    """,
                    [table, column],
                ).fetchone()
            finally:
                con.close()
        except Exception as exc:
            log.warning("catalog_unavailable", phase="get_review_state", error=str(exc))
            return REVIEW_ABSENT
        if not row or not row[0]:
            return REVIEW_ABSENT
        return str(row[0])

    def list_pending_reviews(self) -> List[CatalogEdge]:
        try:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT target_table, target_column, source_table, source_column,
                           expression, transform_type, confidence, source, review_state,
                           ratified_by, ratified_at, sql_hash, computed_at
                    FROM catalog_lineage
                    WHERE review_state = ?
                    ORDER BY target_table, target_column, source_table, source_column
                    """,
                    [REVIEW_PENDING],
                ).fetchall()
            finally:
                con.close()
        except Exception as exc:
            log.warning("catalog_unavailable", phase="list_pending_reviews", error=str(exc))
            return []
        return [_row_to_edge(r) for r in rows]

    def list_certifications(self) -> List[dict]:
        try:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT table_name, profile, certified_by, certified_at, notes
                    FROM catalog_certification
                    ORDER BY profile, table_name
                    """,
                ).fetchall()
            finally:
                con.close()
        except Exception as exc:
            log.warning("catalog_unavailable", phase="list_certifications", error=str(exc))
            return []
        return [
            {
                "table_name": r[0],
                "profile": r[1],
                "certified_by": r[2] or "",
                "certified_at": r[3] or "",
                "notes": r[4] or "",
            }
            for r in rows
        ]

    def list_activity(self, limit: int = 20) -> List[dict]:
        try:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT ts, action, table_name, column_name, actor,
                           from_state, to_state, reason
                    FROM catalog_review_log
                    ORDER BY ts DESC
                    LIMIT ?
                    """,
                    [int(limit)],
                ).fetchall()
            finally:
                con.close()
        except Exception as exc:
            log.warning("catalog_unavailable", phase="list_activity", error=str(exc))
            return []
        return [
            {
                "ts": r[0], "action": r[1],
                "table_name": r[2] or "", "column_name": r[3] or "",
                "actor": r[4] or "", "from_state": r[5] or "",
                "to_state": r[6] or "", "reason": r[7] or "",
            }
            for r in rows
        ]

    # ── Writes ──

    def emit_lineage(self, manifest, source: str = SOURCE_SQLGLOT,
                     auto_ratify: bool = False) -> None:
        """Write each column_map as one or more catalog_lineage rows.

        Per-map `source` and `review_state` from the merge step take priority
        over the function-level `source` / `auto_ratify` arguments — those
        parameters apply only when the map has no explicit value.
        """
        ts = _utc_now()
        try:
            con = self._connect()
            try:
                for cm in getattr(manifest, "column_maps", []) or []:
                    target_table = cm.target_table
                    target_column = cm.target_column
                    sql_hash = cm.sql_hash or manifest.sql_hash
                    expression = cm.full_expression
                    confidence = float(cm.confidence)

                    cm_source = (getattr(cm, "source", "") or source).strip()
                    cm_state = (getattr(cm, "review_state", "") or "").strip()
                    if not cm_state:
                        cm_state = (
                            REVIEW_RATIFIED
                            if auto_ratify and cm_source == SOURCE_SQLGLOT and confidence >= 0.999
                            else REVIEW_PENDING
                        )

                    if not cm.sources:
                        self._upsert_lineage_row(
                            con, target_table, target_column,
                            "", "",
                            expression, "",
                            confidence, cm_source, cm_state,
                            sql_hash, ts,
                        )
                        continue

                    for edge in cm.sources:
                        tt = (edge.transform_type.value
                              if hasattr(edge.transform_type, "value")
                              else str(edge.transform_type))
                        self._upsert_lineage_row(
                            con, target_table, target_column,
                            edge.source_table, edge.source_column,
                            expression, tt,
                            confidence, cm_source, cm_state,
                            sql_hash, ts,
                        )
            finally:
                con.close()
        except Exception as exc:
            log.warning("catalog_unavailable", phase="emit_lineage", error=str(exc))

    def _upsert_lineage_row(
        self,
        con,
        target_table: str, target_column: str,
        source_table: str, source_column: str,
        expression: str, transform_type: str,
        confidence: float, source: str, desired_state: str,
        sql_hash: str, ts: str,
    ) -> None:
        """Idempotent upsert keyed on (target, target_column, source, source_column).

        Preserve existing ratification: if a row is already ratified, keep its
        review_state / ratified_by / ratified_at. Update sqlglot-emitted fields
        like expression / transform_type / sql_hash / confidence freely.
        """
        existing = con.execute(
            """
            SELECT review_state, ratified_by, ratified_at
            FROM catalog_lineage
            WHERE target_table = ? AND target_column = ?
              AND source_table = ? AND source_column = ?
            """,
            [target_table, target_column, source_table, source_column],
        ).fetchone()

        if existing:
            prior_state, prior_ratified_by, prior_ratified_at = existing
            keep_state = prior_state == REVIEW_RATIFIED
            new_state = prior_state if keep_state else desired_state
            con.execute(
                """
                UPDATE catalog_lineage
                SET expression     = ?,
                    transform_type = ?,
                    confidence     = ?,
                    source         = CASE WHEN source = ? THEN source ELSE ? END,
                    review_state   = ?,
                    ratified_by    = ?,
                    ratified_at    = ?,
                    sql_hash       = ?,
                    computed_at    = ?
                WHERE target_table = ? AND target_column = ?
                  AND source_table = ? AND source_column = ?
                """,
                [
                    expression, transform_type, float(confidence),
                    SOURCE_CATALOG, source,
                    new_state,
                    prior_ratified_by or "", prior_ratified_at or "",
                    sql_hash, ts,
                    target_table, target_column, source_table, source_column,
                ],
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
                    target_table, target_column, source_table, source_column,
                    expression, transform_type, float(confidence),
                    source, desired_state,
                    "", "",
                    sql_hash, ts,
                ],
            )

    def emit_description(self, table: str, column: str, description: str,
                         source: str = SOURCE_AGENT_INFERRED) -> None:
        if not description:
            return
        ts = _utc_now()
        try:
            con = self._connect()
            try:
                existing = con.execute(
                    """
                    SELECT review_state FROM catalog_descriptions
                    WHERE table_name = ? AND column_name = ?
                    """,
                    [table, column],
                ).fetchone()
                if existing:
                    keep_state = existing[0] == REVIEW_RATIFIED
                    desired = REVIEW_RATIFIED if keep_state else REVIEW_PENDING
                    con.execute(
                        """
                        UPDATE catalog_descriptions
                        SET description = ?, source = ?, review_state = ?, computed_at = ?
                        WHERE table_name = ? AND column_name = ?
                        """,
                        [description, source, desired, ts, table, column],
                    )
                else:
                    con.execute(
                        """
                        INSERT INTO catalog_descriptions
                        (table_name, column_name, description, source, review_state,
                         ratified_by, ratified_at, computed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [table, column, description, source, REVIEW_PENDING, "", "", ts],
                    )
            finally:
                con.close()
        except Exception as exc:
            log.warning("catalog_unavailable", phase="emit_description", error=str(exc))

    def ratify(self, table: str, column: str, actor: str, reason: str = "") -> None:
        self._set_state(table, column, REVIEW_RATIFIED, actor, reason, action="ratify")

    def reject(self, table: str, column: str, actor: str, reason: str = "") -> None:
        # Reject removes the row entirely; we keep an audit-log entry.
        ts = _utc_now()
        prior_state = self.get_review_state(table, column)
        try:
            con = self._connect()
            try:
                con.execute(
                    "DELETE FROM catalog_lineage WHERE target_table = ? AND target_column = ?",
                    [table, column],
                )
                con.execute(
                    """
                    INSERT INTO catalog_review_log
                    (ts, action, table_name, column_name, actor, from_state, to_state, reason)
                    VALUES (?, 'reject', ?, ?, ?, ?, 'absent', ?)
                    """,
                    [ts, table, column, actor, prior_state, reason],
                )
            finally:
                con.close()
        except Exception as exc:
            log.warning("catalog_unavailable", phase="reject", error=str(exc))

    def downgrade_to_pending(self, target_table: str, target_column: str,
                             reason: str = "") -> None:
        self._set_state(target_table, target_column, REVIEW_PENDING, actor="system",
                        reason=reason, action="downgrade")

    def _set_state(
        self, table: str, column: str, to_state: str,
        actor: str, reason: str, action: str,
    ) -> None:
        if to_state not in ALL_REVIEW_STATES:
            return
        ts = _utc_now()
        try:
            con = self._connect()
            try:
                row = con.execute(
                    """
                    SELECT MIN(review_state) FROM catalog_lineage
                    WHERE target_table = ? AND target_column = ?
                    """,
                    [table, column],
                ).fetchone()
                from_state = (row and row[0]) or REVIEW_ABSENT
                if from_state == REVIEW_ABSENT:
                    # No lineage yet — record the audit-log entry but skip the update.
                    con.execute(
                        """
                        INSERT INTO catalog_review_log
                        (ts, action, table_name, column_name, actor, from_state, to_state, reason)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [ts, action, table, column, actor, from_state, to_state,
                         reason or "no lineage row to update"],
                    )
                    return

                ratified_by = actor if to_state == REVIEW_RATIFIED else ""
                ratified_at = ts if to_state == REVIEW_RATIFIED else ""
                con.execute(
                    """
                    UPDATE catalog_lineage
                    SET review_state = ?,
                        ratified_by  = ?,
                        ratified_at  = ?
                    WHERE target_table = ? AND target_column = ?
                    """,
                    [to_state, ratified_by, ratified_at, table, column],
                )
                con.execute(
                    """
                    INSERT INTO catalog_review_log
                    (ts, action, table_name, column_name, actor, from_state, to_state, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [ts, action, table, column, actor, from_state, to_state, reason],
                )
            finally:
                con.close()
        except Exception as exc:
            log.warning("catalog_unavailable", phase="_set_state",
                        action=action, error=str(exc))

    def health(self) -> dict:
        try:
            con = self._connect()
            try:
                lineage_count = int(con.execute(
                    "SELECT COUNT(*) FROM catalog_lineage").fetchone()[0])
                cert_count = int(con.execute(
                    "SELECT COUNT(*) FROM catalog_certification").fetchone()[0])
            finally:
                con.close()
        except Exception as exc:
            log.warning("catalog_unavailable", phase="health", error=str(exc))
            return {"ok": False, "lineage_count": 0, "certification_count": 0}
        return {"ok": True, "lineage_count": lineage_count,
                "certification_count": cert_count}

    # ── Convenience lookup used by Phase A ──

    def get_ratified_for_target(self, target_table: str, target_column: str) -> List[CatalogEdge]:
        try:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT target_table, target_column, source_table, source_column,
                           expression, transform_type, confidence, source, review_state,
                           ratified_by, ratified_at, sql_hash, computed_at
                    FROM catalog_lineage
                    WHERE target_table = ?
                      AND target_column = ?
                      AND review_state = ?
                    """,
                    [target_table, target_column, REVIEW_RATIFIED],
                ).fetchall()
            finally:
                con.close()
        except Exception as exc:
            log.warning("catalog_unavailable", phase="get_ratified_for_target",
                        error=str(exc))
            return []
        return [_row_to_edge(r) for r in rows]


def _row_to_edge(r) -> CatalogEdge:
    return CatalogEdge(
        target_table=r[0] or "",
        target_column=r[1] or "",
        source_table=r[2] or "",
        source_column=r[3] or "",
        expression=r[4] or "",
        transform_type=r[5] or "",
        confidence=float(r[6] or 0.0),
        source=r[7] or SOURCE_UNRESOLVED,
        review_state=r[8] or REVIEW_PENDING,
        ratified_by=r[9] or "",
        ratified_at=r[10] or "",
        sql_hash=r[11] or "",
        computed_at=r[12] or "",
    )


# ── Module-level singleton + kill switch ──

_catalog: Optional["LocalCatalog"] = None


def _catalog_disabled_via_env() -> bool:
    val = os.environ.get("TRACEX_CATALOG", "on").strip().lower()
    return val in {"0", "false", "no", "off", "disabled"}


def get_catalog() -> Optional[LocalCatalog]:
    """Return the process-local LocalCatalog, or None if TRACEX_CATALOG=off."""
    global _catalog
    if _catalog_disabled_via_env():
        return None
    if _catalog is None:
        _catalog = LocalCatalog()
    return _catalog


__all__ = ["LocalCatalog", "get_catalog"]
