"""Pure merge logic for the catalog/sqlglot/agent precedence rules.

`merge_lineage` takes the three sources of evidence and a sql_hash for the
current run and returns:
  - the resolved list of `ColumnLineageMap` (one per output column, with
    `source` and `review_state` populated per the precedence table),
  - a list of `DivergenceEvent` for catalog rows that disagree with sqlglot.

Pulled into its own module so the unit tests can import without dragging in
DuckDB or Bedrock.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

from lineage.catalog.client import (
    CatalogEdge,
    DivergenceEvent,
    SOURCE_AGENT_INFERRED,
    SOURCE_CATALOG,
    SOURCE_SQLGLOT,
    SOURCE_UNRESOLVED,
    REVIEW_PENDING,
    REVIEW_RATIFIED,
)
from lineage.models import ColumnLineageEdge, ColumnLineageMap, TransformType


def _key(t: str, c: str) -> Tuple[str, str]:
    return (t.lower(), c.lower())


def _src_set(edges: Iterable) -> set[Tuple[str, str]]:
    out: set[Tuple[str, str]] = set()
    for e in edges:
        out.add(_key(getattr(e, "source_table", ""), getattr(e, "source_column", "")))
    return out


def _coerce_transform(s: str) -> TransformType:
    if isinstance(s, TransformType):
        return s
    s = (s or "").upper()
    return TransformType[s] if s in TransformType.__members__ else TransformType.TRANSFORM


def _catalog_edges_to_map(
    target_table: str,
    target_column: str,
    edges: List[CatalogEdge],
    sqlglot_map: ColumnLineageMap | None,
) -> ColumnLineageMap:
    """Render N catalog rows for one target column as a single ColumnLineageMap."""
    sources = [
        ColumnLineageEdge(
            source_table=e.source_table,
            source_column=e.source_column,
            expression=e.expression or (sqlglot_map.full_expression if sqlglot_map else ""),
            transform_type=_coerce_transform(e.transform_type),
        )
        for e in edges if e.source_table or e.source_column
    ]
    full_expr = ""
    sql_hash = ""
    data_type = ""
    if sqlglot_map is not None:
        full_expr = sqlglot_map.full_expression
        sql_hash = sqlglot_map.sql_hash
        data_type = sqlglot_map.data_type
    if not full_expr and edges:
        full_expr = edges[0].expression or ""
    if not sql_hash and edges:
        sql_hash = edges[0].sql_hash or ""
    return ColumnLineageMap(
        target_table=target_table,
        target_column=target_column,
        sources=sources,
        full_expression=full_expr,
        ambiguous=False,
        confidence=1.0,
        data_type=data_type,
        sql_hash=sql_hash,
        source=SOURCE_CATALOG,
        review_state=REVIEW_RATIFIED,
    )


def merge_lineage(
    catalog_edges: List[CatalogEdge],
    sqlglot_maps: List[ColumnLineageMap],
    agent_maps: Dict[Tuple[str, str], ColumnLineageMap],
    sql_hash: str,
) -> Tuple[List[ColumnLineageMap], List[DivergenceEvent]]:
    """Apply the precedence rules and emit divergence events.

    Args:
        catalog_edges: every catalog row for the target_table being processed.
                       Caller should pass only ratified rows; non-ratified
                       rows are ignored here.
        sqlglot_maps:  sqlglot's parser output (one per output column).
        agent_maps:    agent-resolved replacements for ambiguous sqlglot maps,
                       keyed by (target_table, target_column).
        sql_hash:      SHA-256 hash of the current normalised stage SQL.
    """
    # Group ratified catalog rows by target column.
    catalog_by_col: Dict[Tuple[str, str], List[CatalogEdge]] = {}
    for ce in catalog_edges:
        if ce.review_state != REVIEW_RATIFIED:
            continue
        catalog_by_col.setdefault(
            _key(ce.target_table, ce.target_column), []
        ).append(ce)

    sqlglot_by_col: Dict[Tuple[str, str], ColumnLineageMap] = {
        _key(m.target_table, m.target_column): m for m in sqlglot_maps
    }

    resolved: List[ColumnLineageMap] = []
    divergences: List[DivergenceEvent] = []

    # Walk the union of keys so we don't drop catalog-only entries.
    keys = set(sqlglot_by_col.keys()) | set(catalog_by_col.keys())

    # Preserve sqlglot ordering, then append any catalog-only stragglers.
    ordered_keys: List[Tuple[str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for m in sqlglot_maps:
        k = _key(m.target_table, m.target_column)
        if k not in seen:
            ordered_keys.append(k)
            seen.add(k)
    for k in keys:
        if k not in seen:
            ordered_keys.append(k)
            seen.add(k)

    for k in ordered_keys:
        sqlglot_map = sqlglot_by_col.get(k)
        agent_map = agent_maps.get(k)
        catalog_rows = catalog_by_col.get(k, [])

        # ── Branch 1: catalog has a ratified entry ────────────────────────
        if catalog_rows:
            cat_map = _catalog_edges_to_map(
                target_table=catalog_rows[0].target_table,
                target_column=catalog_rows[0].target_column,
                edges=catalog_rows,
                sqlglot_map=sqlglot_map,
            )

            if sqlglot_map is not None and not sqlglot_map.ambiguous:
                cat_set = _src_set(cat_map.sources)
                sg_set = _src_set(sqlglot_map.sources)

                if cat_set and sg_set and cat_set != sg_set:
                    # Divergence — sqlglot wins this run, catalog flagged.
                    divergences.append(DivergenceEvent(
                        target_table=cat_map.target_table,
                        target_column=cat_map.target_column,
                        catalog_sources=sorted(cat_set),
                        sqlglot_sources=sorted(sg_set),
                        catalog_sql_hash=catalog_rows[0].sql_hash or "",
                        current_sql_hash=sql_hash,
                        reason="sqlglot disagrees with ratified catalog edges",
                    ))
                    sqlglot_map.source = SOURCE_SQLGLOT
                    sqlglot_map.review_state = REVIEW_RATIFIED
                    resolved.append(sqlglot_map)
                    continue

                # Aligned. Catalog wins. (sql_hash drift is informational only.)
                resolved.append(cat_map)
                continue

            # sqlglot ambiguous or absent → catalog wins outright.
            resolved.append(cat_map)
            continue

        # ── Branch 2: no catalog. sqlglot decides ─────────────────────────
        if sqlglot_map is not None:
            if not sqlglot_map.ambiguous and sqlglot_map.confidence >= 0.999:
                sqlglot_map.source = SOURCE_SQLGLOT
                sqlglot_map.review_state = REVIEW_RATIFIED
                resolved.append(sqlglot_map)
                continue
            if agent_map is not None:
                # Agent took the ambiguous map and resolved it.
                agent_map.source = SOURCE_AGENT_INFERRED
                agent_map.review_state = REVIEW_PENDING
                resolved.append(agent_map)
                continue
            # No agent help available — leave ambiguous map but tag pending.
            sqlglot_map.source = (
                SOURCE_UNRESOLVED if not sqlglot_map.sources else SOURCE_SQLGLOT
            )
            sqlglot_map.review_state = REVIEW_PENDING
            resolved.append(sqlglot_map)
            continue

        # ── Branch 3: nothing at all (shouldn't happen for known columns) ─
        if agent_map is not None:
            agent_map.source = SOURCE_AGENT_INFERRED
            agent_map.review_state = REVIEW_PENDING
            resolved.append(agent_map)
            continue

    return resolved, divergences


__all__ = ["merge_lineage"]
