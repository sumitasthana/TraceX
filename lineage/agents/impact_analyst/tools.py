"""Tools for the Impact Analyst agent: graph traversals over the lineage Kuzu DB."""
from __future__ import annotations

from typing import Optional

from langchain_core.tools import tool

from lineage.config import get_db, get_conn

COL = "`Column`"


def _column_pk(column_name: str, dataset_name: str) -> str:
    return f"{dataset_name}::{column_name}"


def _open_conn():
    return get_conn(get_db())


@tool
def get_direct_downstream(table: str, column: str) -> str:
    """Return one-hop DERIVES_FROM children of (table, column).

    DERIVES_FROM points target → source, so the *children* in the impact
    sense are nodes whose DERIVES_FROM edge lands at this column. Returns a
    multi-line listing or "(no direct downstream)" if there are none.
    """
    pk = _column_pk(column, table)
    try:
        conn = _open_conn()
        result = conn.execute(
            f"""
            MATCH (child:{COL})-[:DERIVES_FROM]->(c:{COL} {{pk: $pk}})
            RETURN child.dataset_name, child.column_name,
                   child.transform_type, child.expression
            """,
            {"pk": pk},
        )
    except Exception as exc:
        return f"Error: {exc}"

    rows: list[str] = []
    while result.has_next():
        r = result.get_next()
        rows.append(f"  {r[0]}.{r[1]}  [{r[2] or '?'}]\n    expression: {r[3] or '(none)'}")
    if not rows:
        return f"(no direct downstream of {table}.{column})"
    return f"Direct downstream of {table}.{column}:\n" + "\n".join(rows)


@tool
def get_full_downstream_chain(table: str, column: str, max_hops: int = 10) -> str:
    """Recursive DERIVES_FROM walk, returning every descendant column.

    Each line: `<dataset>.<column>  [transform_type]  hops=<n>`. Stops a
    branch when no further DERIVES_FROM edge points at the current node.
    Caps at `max_hops` (default 10) to bound traversal cost.
    """
    pk = _column_pk(column, table)
    upper = max(1, min(int(max_hops), 25))
    try:
        conn = _open_conn()
        result = conn.execute(
            f"""
            MATCH path = (child:{COL})-[:DERIVES_FROM*1..{upper}]->(c:{COL} {{pk: $pk}})
            RETURN child.dataset_name, child.column_name,
                   child.transform_type, length(path) AS hops
            ORDER BY hops ASC
            """,
            {"pk": pk},
        )
    except Exception as exc:
        return f"Error: {exc}"

    seen: set[tuple[str, str]] = set()
    rows: list[str] = []
    while result.has_next():
        r = result.get_next()
        key = (str(r[0]), str(r[1]))
        if key in seen:
            continue
        seen.add(key)
        rows.append(f"  {r[0]}.{r[1]}  [{r[2] or '?'}]  hops={int(r[3])}")
    if not rows:
        return f"(no downstream chain for {table}.{column})"
    return f"Downstream chain of {table}.{column} (max_hops={upper}):\n" + "\n".join(rows)


@tool
def get_processes_reading_table(table: str) -> str:
    """List Process nodes with INPUT_TO this DataSet."""
    try:
        conn = _open_conn()
        result = conn.execute(
            """
            MATCH (d:DataSet {name: $name})-[:INPUT_TO]->(p:Process)
            RETURN p.stage, p.run_id, p.transform_type, p.target_table
            """,
            {"name": table},
        )
    except Exception as exc:
        return f"Error: {exc}"

    rows: list[str] = []
    while result.has_next():
        r = result.get_next()
        rows.append(
            f"  stage={r[0]}  run_id={r[1]}  transform_type={r[2]}  target_table={r[3]}"
        )
    if not rows:
        return f"(no Process nodes read {table})"
    return f"Processes reading {table}:\n" + "\n".join(rows)


@tool
def get_column_expression(table: str, column: str) -> str:
    """Return stored `expression` and `transform_type` for a Column node."""
    pk = _column_pk(column, table)
    try:
        conn = _open_conn()
        result = conn.execute(
            f"""
            MATCH (c:{COL} {{pk: $pk}})
            RETURN c.expression, c.transform_type, c.confidence, c.data_type
            """,
            {"pk": pk},
        )
    except Exception as exc:
        return f"Error: {exc}"

    if not result.has_next():
        return f"not found: {table}.{column}"
    r = result.get_next()
    return (
        f"{table}.{column}:\n"
        f"  expression: {r[0] or '(none)'}\n"
        f"  transform_type: {r[1] or '?'}\n"
        f"  confidence: {r[2]}\n"
        f"  data_type: {r[3] or '?'}"
    )


TOOLS = [
    get_direct_downstream,
    get_full_downstream_chain,
    get_processes_reading_table,
    get_column_expression,
]
