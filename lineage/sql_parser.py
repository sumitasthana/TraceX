"""Deterministic column-lineage walker over DuckDB SQL.

Pure Python, zero LLM dependency. Uses `sqlglot` with `dialect='duckdb'` to
parse a single `CREATE OR REPLACE TABLE ... AS SELECT ...` statement and
emit one `ColumnLineageMap` per output column.

Resolution strategy:

  1. Parse with sqlglot, pull the outermost SELECT.
  2. Build a CTE table: name -> (subquery_node, alias_to_real_table_map).
  3. For each output projection, walk its expression and collect every
     `Column` reference. Each reference is resolved to a (real_table,
     real_column) pair by following CTE chains until we hit a base table.
  4. Classify the projection's `transform_type` from its expression shape.
  5. Detect AMBIGUOUS shapes (CASE/COALESCE across multiple base tables)
     and flag them for the agent.

Deliberately conservative: any expression we cannot resolve cleanly is
flagged AMBIGUOUS with `confidence=0.5` and an empty source list — the
agent gets the unfinished work.
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, List, Optional

import sqlglot
from sqlglot import exp

from lineage.models import (
    ColumnLineageEdge,
    ColumnLineageMap,
    TransformType,
)

DIALECT = "duckdb"

_AGG_FUNCS = {"sum", "count", "avg", "min", "max", "mode", "string_agg", "list", "median", "stddev", "variance", "first", "last", "any_value"}
_WINDOW_KEYWORDS = ("OVER (", "OVER(",)
_TYPE_SENSITIVE_PATTERNS = (
    "||", "concat", "date_diff", "age", "extract", "interval",
    "round", "cast", " + ", " - ", " * ", " / ",
)


def parse(sql: str, target_table: str, source_tables: List[str]) -> List[ColumnLineageMap]:
    """Walk `sql` and emit one ColumnLineageMap per output column.

    `target_table` is the table this SELECT writes (CREATE OR REPLACE TABLE T AS ...).
    `source_tables` is the whitelist of physical tables this stage reads from;
    references to tables outside this set are dropped from the source list.
    """
    sql_hash = hashlib.sha256(sql.strip().lower().encode("utf-8")).hexdigest()
    try:
        tree = sqlglot.parse_one(sql, read=DIALECT)
    except Exception:
        return []

    select = _outermost_select(tree)
    if select is None:
        return []

    # Map every CTE name to its inner SELECT for chain resolution.
    cte_index: dict[str, exp.Select] = {}
    with_node = (
        tree.args.get("with_") or tree.args.get("with")
        or select.args.get("with_") or select.args.get("with")
    )
    if not with_node:
        # Fall back to walking the tree — different sqlglot versions hang the
        # WITH at different levels of nesting.
        for n in tree.walk():
            if isinstance(n, exp.With):
                with_node = n
                break
    if with_node:
        for cte in with_node.expressions:
            inner = cte.this if hasattr(cte, "this") else None
            if isinstance(inner, exp.Select):
                cte_index[cte.alias_or_name.lower()] = inner

    base_tables = {t.lower() for t in source_tables} | {target_table.lower()}

    out: List[ColumnLineageMap] = []
    for projection in select.expressions or []:
        full_expr = projection.sql(dialect=DIALECT)
        target_col = _projection_alias(projection)
        if not target_col:
            continue

        edges, ambiguous, transform_type = _classify_projection(
            projection, select, cte_index, base_tables
        )

        confidence = 1.0 if not ambiguous else 0.5
        out.append(
            ColumnLineageMap(
                target_table=target_table,
                target_column=target_col,
                sources=edges,
                full_expression=full_expr,
                ambiguous=ambiguous,
                semantic_description="",
                confidence=confidence,
                data_type="",
                sql_hash=sql_hash,
            )
        )

    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _outermost_select(tree: exp.Expression) -> Optional[exp.Select]:
    if isinstance(tree, exp.Select):
        return tree
    if isinstance(tree, exp.Create):
        inner = tree.expression
        if isinstance(inner, exp.Select):
            return inner
        if isinstance(inner, exp.Subquery):
            sub = inner.this
            if isinstance(sub, exp.Select):
                return sub
    if isinstance(tree, exp.Subquery) and isinstance(tree.this, exp.Select):
        return tree.this
    found = tree.find(exp.Select)
    return found


def _projection_alias(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Alias):
        return node.alias
    if isinstance(node, exp.Column):
        return node.name
    if hasattr(node, "alias") and node.alias:
        return node.alias
    if isinstance(node, exp.Star):
        return None
    return None


def _classify_projection(
    projection: exp.Expression,
    select: exp.Select,
    cte_index: dict[str, exp.Select],
    base_tables: set[str],
) -> tuple[List[ColumnLineageEdge], bool, TransformType]:
    """Return (sources, ambiguous, transform_type) for one projection."""
    inner = projection.this if isinstance(projection, exp.Alias) else projection
    expr_sql_lower = projection.sql(dialect=DIALECT).lower()

    # Constants and CURRENT_*
    if _is_constant(inner):
        return [], False, TransformType.CONSTANT

    # Column refs across the whole projection
    column_refs = list(inner.find_all(exp.Column))
    if not column_refs:
        return [], False, TransformType.CONSTANT

    # Resolve each column reference to (real_table, real_column)
    resolved: List[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    select_alias_map = _alias_to_table(select)
    for col in column_refs:
        for real_table, real_col in _resolve_column(
            col, select_alias_map, cte_index, base_tables
        ):
            if (real_table, real_col) in seen:
                continue
            seen.add((real_table, real_col))
            resolved.append((real_table, real_col, projection.sql(dialect=DIALECT)))

    # Ambiguity detection: CASE WHEN or COALESCE referencing >=2 distinct base tables
    distinct_tables = {t for t, _, _ in resolved if t in base_tables}
    has_case = inner.find(exp.Case) is not None
    has_coalesce = any(
        f.this.lower() == "coalesce"
        for f in inner.find_all(exp.Anonymous)
        if hasattr(f, "this") and isinstance(f.this, str)
    ) or inner.find(exp.Coalesce) is not None
    is_ambiguous = (has_case or has_coalesce) and len(distinct_tables) >= 2

    # transform_type classification (first match wins)
    transform_type = _shape_transform_type(inner, expr_sql_lower, projection)

    if is_ambiguous:
        transform_type = TransformType.AMBIGUOUS

    edges = [
        ColumnLineageEdge(
            source_table=t,
            source_column=c,
            expression=e,
            transform_type=transform_type,
        )
        for (t, c, e) in resolved
        if t in base_tables
    ]

    # If we filtered everything out (every ref pointed to a CTE we couldn't resolve)
    if not edges and column_refs:
        return [], True, TransformType.AMBIGUOUS

    return edges, is_ambiguous, transform_type


def _is_constant(node: exp.Expression) -> bool:
    if isinstance(node, (exp.Literal, exp.Boolean, exp.Null)):
        return True
    if isinstance(node, exp.Interval):
        return True
    if isinstance(node, exp.CurrentDate) or isinstance(node, exp.CurrentTimestamp):
        return True
    # Anonymous CURRENT_* aren't always typed — sniff the SQL
    s = node.sql(dialect=DIALECT).strip().upper()
    if s in {"CURRENT_DATE", "CURRENT_TIMESTAMP", "CURRENT_TIME", "NOW()"}:
        return True
    return False


def _shape_transform_type(
    inner: exp.Expression,
    expr_sql_lower: str,
    projection: exp.Expression,
) -> TransformType:
    # Aggregate
    for fn in inner.find_all(exp.AggFunc):
        return TransformType.AGGREGATE
    for fn in inner.find_all(exp.Anonymous):
        if isinstance(fn.this, str) and fn.this.lower() in _AGG_FUNCS:
            return TransformType.AGGREGATE

    # Window
    if any(k.lower() in expr_sql_lower for k in _WINDOW_KEYWORDS):
        return TransformType.WINDOW
    if inner.find(exp.Window) is not None:
        return TransformType.WINDOW

    # PASSTHROUGH: bare column with same name as alias (or no alias)
    if isinstance(projection, exp.Column):
        return TransformType.PASSTHROUGH
    if isinstance(projection, exp.Alias) and isinstance(projection.this, exp.Column):
        col = projection.this
        if col.name == projection.alias:
            return TransformType.PASSTHROUGH
        return TransformType.RENAME

    return TransformType.TRANSFORM


def _alias_to_table(select: exp.Select) -> dict[str, str]:
    """Map every FROM/JOIN alias in this SELECT to its underlying table or CTE name.

    sqlglot uses `from_` in newer versions to avoid the Python keyword
    collision; older versions used `from`. Try both. From.this holds the
    primary table; multi-table FROMs (rare in DuckDB) put extras in
    From.expressions.
    """
    out: dict[str, str] = {}
    from_clause = select.args.get("from_") or select.args.get("from")
    if from_clause is not None:
        primary = getattr(from_clause, "this", None)
        if primary is not None:
            _record_alias(primary, out)
        for source in getattr(from_clause, "expressions", None) or []:
            _record_alias(source, out)
    for join in select.args.get("joins") or []:
        _record_alias(join.this, out)
    return out


def _star_qualifier(proj: exp.Expression) -> Optional[str]:
    """Return the qualifying alias of a star projection.

    `t.*`     → "t"
    bare `*`  → "" (empty string, distinct from None)
    not a star → None
    """
    if isinstance(proj, exp.Star):
        return ""
    if isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star):
        tbl = proj.table or ""
        return str(tbl)
    return None


def _record_alias(node: exp.Expression, out: dict[str, str]) -> None:
    if isinstance(node, exp.Table):
        alias = node.alias_or_name
        out[alias.lower()] = node.name.lower()
        out[node.name.lower()] = node.name.lower()
    elif isinstance(node, exp.Subquery):
        alias = node.alias_or_name
        if alias:
            out[alias.lower()] = alias.lower()
    elif isinstance(node, exp.Alias):
        inner = node.this
        if isinstance(inner, exp.Table):
            out[node.alias.lower()] = inner.name.lower()
            out[inner.name.lower()] = inner.name.lower()


def _resolve_column(
    col: exp.Column,
    alias_to_table: dict[str, str],
    cte_index: dict[str, exp.Select],
    base_tables: set[str],
    visited: Optional[set[str]] = None,
) -> Iterable[tuple[str, str]]:
    """Yield every (real_table, real_column) that this column reference points at.

    Walks CTE chains: if the resolved table is itself a CTE, dive into the CTE's
    SELECT, find the matching projection by alias, recursively resolve its column
    references, and propagate them up. Unqualified column references are
    resolved to the single FROM/JOIN source in scope (which may itself be a CTE).
    """
    visited = visited or set()
    raw_table = (col.table or "").lower()
    col_name = col.name

    # Build the candidate-target list:
    #  - qualified column → exactly one candidate (the resolved alias)
    #  - unqualified column → every distinct value in alias_to_table
    if raw_table:
        candidates = [alias_to_table.get(raw_table, raw_table)]
    else:
        candidates = list({v for v in alias_to_table.values() if v})
        if not candidates:
            return

    for real in candidates:
        if not real:
            continue
        if real in base_tables:
            yield (real, col_name)
            continue

        if real in cte_index and real not in visited:
            new_visited = visited | {real}
            inner_select = cte_index[real]
            inner_alias_map = _alias_to_table(inner_select)
            # Find the matching projection in the CTE
            matched = False
            for proj in inner_select.expressions or []:
                alias = _projection_alias(proj)
                if alias != col_name:
                    continue
                matched = True
                inner_node = proj.this if isinstance(proj, exp.Alias) else proj
                # Constants — no further sources to attribute
                if _is_constant(inner_node):
                    break
                for inner_col in inner_node.find_all(exp.Column):
                    yield from _resolve_column(
                        inner_col, inner_alias_map, cte_index, base_tables, new_visited
                    )
                break
            if matched:
                continue
            # Star projection in the CTE — find the qualifier (if any) and
            # propagate this column name through only that side.
            for proj in inner_select.expressions or []:
                qualifier = _star_qualifier(proj)
                if qualifier is None:
                    continue
                if qualifier:  # e.g. t.* — limit to that single alias
                    targets = [inner_alias_map.get(qualifier.lower(), qualifier.lower())]
                else:  # bare * — fan out to all aliases in scope
                    targets = list({v for v in inner_alias_map.values() if v})
                for inner_real in targets:
                    if not inner_real:
                        continue
                    synth = exp.Column(this=exp.Identifier(this=col_name))
                    synth.set("table", exp.Identifier(this=inner_real))
                    yield from _resolve_column(
                        synth, inner_alias_map, cte_index, base_tables, new_visited
                    )
                break
            continue

        # Unknown alias — keep as-is so the caller can mark it ambiguous
        yield (real, col_name)


# ---------------------------------------------------------------------------
# Public diagnostic helpers
# ---------------------------------------------------------------------------

def normalize_sql(sql: str) -> str:
    return sql.strip().lower()


def hash_sql(sql: str) -> str:
    return hashlib.sha256(normalize_sql(sql).encode("utf-8")).hexdigest()


def is_type_sensitive_expression(expression: str) -> bool:
    """Used by impact_analyst tooling; cheap substring check on a stored expression."""
    s = (expression or "").lower()
    return any(p in s for p in _TYPE_SENSITIVE_PATTERNS)


# Keep import surface tidy for callers
__all__ = ["parse", "normalize_sql", "hash_sql", "is_type_sensitive_expression"]
