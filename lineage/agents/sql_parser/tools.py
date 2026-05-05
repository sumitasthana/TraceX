"""Tools for the SQL Parser agent: schema introspection + CTE extraction."""
from __future__ import annotations

import json
import re
from typing import List

import duckdb
import sqlglot
from langchain_core.tools import tool
from sqlglot import exp

from lineage.config import get_log_dir  # noqa: F401  (keeps lineage namespace warm)
from pipeline.config import get_db_path

DIALECT = "duckdb"


@tool
def get_table_schema(table_name: str) -> str:
    """Return the column names and data types for a DuckDB table.

    Use this BEFORE asserting that any column exists in a source table.
    Opens a read-only connection. Returns a multi-line "name: type" listing
    or "Error: ..." on failure.
    """
    db_path = get_db_path()
    if not db_path.exists():
        return f"Error: DuckDB not found at {db_path}"
    try:
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            rows = con.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = ?
                ORDER BY ordinal_position
                """,
                [table_name],
            ).fetchall()
        finally:
            con.close()
    except Exception as exc:
        return f"Error: schema lookup failed for {table_name}: {exc}"

    if not rows:
        return f"Error: table '{table_name}' not found in DuckDB"
    return f"Schema for {table_name}:\n" + "\n".join(f"  {name}: {dtype}" for name, dtype in rows)


@tool
def get_cte_definition(cte_name: str, sql: str) -> str:
    """Extract the inner SELECT body of a named CTE from a SQL statement.

    Returns the rendered SQL of the CTE's inner SELECT, or "Error: ..." if
    the CTE is not found. Used to walk multi-hop CTE chains back to their
    physical source tables.
    """
    try:
        tree = sqlglot.parse_one(sql, read=DIALECT)
    except Exception as exc:
        return f"Error: failed to parse SQL: {exc}"

    target = cte_name.lower()
    with_node = tree.args.get("with")
    if not with_node:
        sel = tree.find(sqlglot.exp.Select)
        if sel is not None:
            with_node = sel.args.get("with")
    if not with_node:
        return f"Error: SQL has no WITH clause; '{cte_name}' is not a CTE"

    for cte in with_node.expressions:
        if cte.alias_or_name.lower() == target:
            inner = cte.this
            return f"CTE {cte.alias_or_name}:\n{inner.sql(dialect=DIALECT, pretty=True)}"

    available = ", ".join(c.alias_or_name for c in with_node.expressions)
    return f"Error: CTE '{cte_name}' not found. Available: {available}"


@tool
def resolve_column_expression(expression: str, source_tables: List[str], schemas: dict) -> str:
    """Given an expression and pre-fetched schemas, list the table.column refs it touches.

    `schemas` is a JSON-decodable dict of `{table: [column_name, ...]}` you
    have already collected. Returns one line per matched reference, or
    "Error: ..." on parse failure. This is a deterministic helper — useful
    for double-checking work before emitting the final JSON.
    """
    if isinstance(schemas, str):
        try:
            schemas = json.loads(schemas)
        except json.JSONDecodeError:
            schemas = {}

    try:
        tree = sqlglot.parse_one(expression, read=DIALECT)
    except Exception as exc:
        return f"Error: failed to parse expression: {exc}"

    base_tables = {t.lower() for t in source_tables}
    refs: list[str] = []

    for col in tree.find_all(exp.Column):
        raw_table = (col.table or "").lower()
        col_name = col.name
        if raw_table in base_tables:
            refs.append(f"{raw_table}.{col_name}")
            continue
        # Unqualified — see if exactly one source has this column
        matches = [
            t for t, cols in schemas.items()
            if t.lower() in base_tables and col_name in (cols or [])
        ]
        if len(matches) == 1:
            refs.append(f"{matches[0].lower()}.{col_name} (unqualified)")
        else:
            refs.append(f"?.{col_name} (unresolved; matches={matches or 'none'})")

    if not refs:
        # Fallback regex sweep for cases sqlglot couldn't parse cleanly
        for match in re.finditer(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)", expression):
            t, c = match.group(1).lower(), match.group(2)
            if t in base_tables:
                refs.append(f"{t}.{c}")

    return "Resolved refs:\n" + ("\n".join(f"  {r}" for r in refs) if refs else "  (none)")


TOOLS = [get_table_schema, get_cte_definition, resolve_column_expression]
