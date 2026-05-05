"""Tools for the Impact Analyst agent: graph traversals over the lineage Kuzu DB."""
from __future__ import annotations

from typing import Optional

from langchain_core.tools import tool

from lineage.agents._kuzu_pool import get_shared_conn

COL = "`Column`"


def _column_pk(column_name: str, dataset_name: str) -> str:
    return f"{dataset_name}::{column_name}"


def _open_conn():
    return get_shared_conn()


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
def get_full_downstream_chain(
    table: str,
    column: str,
    max_hops: int = 10,
    include_unratified: bool = False,
) -> str:
    """Recursive DERIVES_FROM walk, returning every descendant column.

    By default applies **profile gating**: pending-review edges whose target
    column lives in a P1-certified table (read from the catalog; falls back
    to the `fct_*` prefix when the catalog is off) are excluded. The agent
    can ask for everything by passing `include_unratified=True` — the
    supervisor surfaces this when the user explicitly says so.

    Each line: `<dataset>.<column>  [transform_type]  source=<S> review=<R> hops=<n>`.
    """
    pk = _column_pk(column, table)
    upper = max(1, min(int(max_hops), 25))
    try:
        conn = _open_conn()
        result = conn.execute(
            f"""
            MATCH path = (child:{COL})-[:DERIVES_FROM*1..{upper}]->(c:{COL} {{pk: $pk}})
            RETURN child.dataset_name, child.column_name,
                   child.transform_type, child.review_state, child.source,
                   length(path) AS hops
            ORDER BY hops ASC
            """,
            {"pk": pk},
        )
    except Exception as exc:
        return f"Error: {exc}"

    p1_tables = _p1_table_set()

    seen: set[tuple[str, str]] = set()
    rows: list[str] = []
    filtered = 0
    while result.has_next():
        r = result.get_next()
        ds, col_name = str(r[0]), str(r[1])
        key = (ds, col_name)
        if key in seen:
            continue
        seen.add(key)

        review_state = str(r[3] or "")
        source = str(r[4] or "")
        is_p1 = (ds in p1_tables) if p1_tables else ds.startswith("fct_")

        if (
            not include_unratified
            and review_state == "pending_review"
            and is_p1
        ):
            filtered += 1
            continue

        rows.append(
            f"  {ds}.{col_name}  [{r[2] or '?'}]  "
            f"source={source or '?'} review={review_state or '?'} hops={int(r[5])}"
        )

    head = f"Downstream chain of {table}.{column} (max_hops={upper}):"
    body = "\n".join(rows) if rows else "  (none)"
    note = ""
    if filtered:
        note = (
            f"\nNote: {filtered} pending-review edge(s) excluded from impact "
            f"(P1 gating). Pass include_unratified=True to see them."
        )
        try:
            import structlog
            structlog.get_logger().bind(component="impact_analyst").info(
                "impact_filtered_unratified",
                table=table, column=column, filtered=filtered,
            )
        except Exception:
            pass
    return f"{head}\n{body}{note}"


def _p1_table_set() -> set[str]:
    """Return the set of P1-certified tables. Empty when catalog is disabled."""
    try:
        from lineage.catalog.local import get_catalog
        cat = get_catalog()
        if cat is None:
            return set()
        return {
            row["table_name"] for row in cat.list_certifications()
            if row.get("profile", "").upper() == "P1"
        }
    except Exception:
        return set()


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
