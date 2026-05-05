"""Tools for the Enrichment agent: read/write Column nodes in Kuzu."""
from __future__ import annotations

from typing import Optional

from langchain_core.tools import tool

from lineage.agents._kuzu_pool import get_shared_conn

# Backtick-escape — `Column` is reserved in Kuzu's parser.
COL = "`Column`"


def _column_pk(column_name: str, dataset_name: str) -> str:
    return f"{dataset_name}::{column_name}"


def _open_conn():
    return get_shared_conn()


@tool
def read_column_node(table: str, column: str) -> str:
    """Read the current Column node properties from Kuzu.

    Returns a multi-line property dump or "not found" if the node has not
    been ingested yet.
    """
    pk = _column_pk(column, table)
    try:
        conn = _open_conn()
        result = conn.execute(
            f"""
            MATCH (c:{COL} {{pk: $pk}})
            RETURN c.column_name, c.dataset_name, c.derivation,
                   c.expression, c.transform_type, c.confidence,
                   c.data_type, c.sql_hash, c.semantic_description,
                   c.computed_at
            """,
            {"pk": pk},
        )
    except Exception as exc:
        return f"Error: {exc}"

    if not result.has_next():
        return f"not found: {table}.{column}"
    row = result.get_next()
    fields = [
        "column_name", "dataset_name", "derivation",
        "expression", "transform_type", "confidence",
        "data_type", "sql_hash", "semantic_description",
        "computed_at",
    ]
    pairs = [f"  {fields[i]}: {row[i]!r}" for i in range(len(fields))]
    return f"Column {table}.{column}:\n" + "\n".join(pairs)


@tool
def get_upstream_columns(table: str, column: str) -> str:
    """Walk DERIVES_FROM one hop upstream of (table, column).

    Returns a multi-line listing of source columns with their expressions
    and (where present) prior semantic descriptions. Empty result is
    rendered as "(no upstream columns)" — that is normal for source-table
    columns and does not indicate an error.
    """
    pk = _column_pk(column, table)
    try:
        conn = _open_conn()
        result = conn.execute(
            f"""
            MATCH (c:{COL} {{pk: $pk}})-[:DERIVES_FROM]->(src:{COL})
            RETURN src.dataset_name, src.column_name, src.expression,
                   src.transform_type, src.semantic_description
            """,
            {"pk": pk},
        )
    except Exception as exc:
        return f"Error: {exc}"

    rows: list[str] = []
    while result.has_next():
        r = result.get_next()
        rows.append(
            f"  {r[0]}.{r[1]}  [{r[3] or '?'}]\n"
            f"    expression: {r[2] or '(none)'}\n"
            f"    semantic_description: {r[4] or '(none)'}"
        )
    if not rows:
        return f"(no upstream columns for {table}.{column})"
    return f"Upstream of {table}.{column}:\n" + "\n".join(rows)


@tool
def update_column_node(
    table: str,
    column: str,
    semantic_description: str,
    confidence: float,
) -> str:
    """Write `semantic_description` and `confidence` back to a Column node.

    No-ops gracefully if the node does not exist. Returns a short status
    string describing what was written, or "Error: ..." on failure.
    """
    pk = _column_pk(column, table)
    try:
        conn = _open_conn()
        # Confirm the node exists first so we don't silently create a stub.
        check = conn.execute(
            f"MATCH (c:{COL} {{pk: $pk}}) RETURN count(c)",
            {"pk": pk},
        )
        if not check.has_next() or int(check.get_next()[0]) == 0:
            return f"not found: {table}.{column} (no node to update)"

        conn.execute(
            f"""
            MATCH (c:{COL} {{pk: $pk}})
            SET c.semantic_description = $sd,
                c.confidence            = $conf
            """,
            {"pk": pk, "sd": semantic_description, "conf": float(confidence)},
        )
    except Exception as exc:
        return f"Error: {exc}"

    short = (semantic_description[:80] + "…") if len(semantic_description) > 80 else semantic_description
    return f"updated {table}.{column}: confidence={confidence}, sd={short!r}"


TOOLS = [read_column_node, get_upstream_columns, update_column_node]
