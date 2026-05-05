"""Tools for the Lineage Search agent: substring-based discovery over the Kuzu graph.

Kuzu does not yet support `LIKE` / `CONTAINS` predicates in production-friendly
form, so we fetch the candidate node set into Python and filter there. We cap
the fetch at 2000 nodes to bound memory.
"""
from __future__ import annotations

import re
from typing import Iterable

from langchain_core.tools import tool

from lineage.agents._kuzu_pool import get_shared_conn

COL = "`Column`"

MAX_COLUMN_FETCH = 2000


def _tokenize(query: str) -> list[str]:
    """Split on non-alphanum so 'where is the 90-day transaction volume' →
    ['where','is','the','90','day','transaction','volume']. Drops one-letter
    tokens to limit noise."""
    raw = [t.lower() for t in re.split(r"[^A-Za-z0-9]+", query or "") if t]
    return [t for t in raw if len(t) > 1]


def _score(query_tokens: list[str], sd: str, col: str, ds: str) -> tuple[int, str]:
    """Return (score, reason) per the lineage_search SKILL rubric."""
    sd_l = (sd or "").lower()
    col_l = (col or "").lower()
    ds_l = (ds or "").lower()

    if not query_tokens:
        return 0, "no terms"

    sd_hits = [t for t in query_tokens if t in sd_l]
    if sd_l and len(sd_hits) == len(query_tokens):
        return 3, f"semantic_description contains all terms: {sd_hits!r}"
    if sd_l and sd_hits:
        return 2, f"semantic_description contains {sd_hits!r}"

    col_hits = [t for t in query_tokens if t in col_l]
    # exact substring of a term, or term substring of column
    if any(t == col_l or col_l in t or t in col_l for t in query_tokens):
        return 2, f"column_name matches term: {col_l}"
    if col_hits:
        return 2, f"column_name contains {col_hits!r}"

    ds_hits = [t for t in query_tokens if t in ds_l]
    if ds_hits:
        return 1, f"dataset_name contains {ds_hits!r}"

    # Last-chance: partial match of any column-name fragment
    if col_l:
        for t in query_tokens:
            if len(t) >= 4 and t[:4] in col_l:
                return 1, f"column_name has partial fragment '{t[:4]}'"
    return 0, "no match"


def _layer_for_dataset_query(table_name: str) -> str:
    if table_name.startswith("src_"):
        return "layer_0"
    if table_name.startswith("stg_"):
        return "layer_1"
    if table_name.startswith("fct_") or table_name.startswith("dim_"):
        return "layer_2"
    return "unknown"


@tool
def search_columns_by_text(query: str, layer_filter: str = "", limit: int = 20) -> str:
    """Search Column nodes whose semantic_description, column_name, or
    dataset's name contains any of the query terms.

    layer_filter: optional 'layer_0' / 'layer_1' / 'layer_2' to restrict
    results. Empty string returns all layers.

    Returns a multi-line listing, one match per line, ranked by score then by
    confidence descending. Line format:
        score=N table.column [transform_type] conf=X.XX layer=layer_K — semantic_description
    """
    tokens = _tokenize(query)
    if not tokens:
        return "Error: empty query"

    layer_filter = (layer_filter or "").strip().lower() or ""
    if layer_filter and layer_filter not in {"layer_0", "layer_1", "layer_2"}:
        return f"Error: invalid layer_filter {layer_filter!r}"

    try:
        conn = get_shared_conn()
        result = conn.execute(
            f"""
            MATCH (col:{COL})
            RETURN col.column_name, col.dataset_name, col.transform_type,
                   col.confidence, col.semantic_description, col.expression,
                   col.data_type
            LIMIT $cap
            """,
            {"cap": MAX_COLUMN_FETCH},
        )
    except Exception as exc:
        return f"Error: column fetch failed: {exc}"

    scored: list[tuple[int, float, str, str, str, float, str, str, str, str]] = []
    while result.has_next():
        r = result.get_next()
        col_name, ds_name, tt, conf, sd, expr, dt = (
            str(r[0] or ""), str(r[1] or ""), str(r[2] or ""),
            float(r[3]) if r[3] is not None else 0.0,
            str(r[4] or ""), str(r[5] or ""), str(r[6] or ""),
        )
        layer = _layer_for_dataset_query(ds_name)
        if layer_filter and layer != layer_filter:
            continue

        score, reason = _score(tokens, sd, col_name, ds_name)
        if score <= 0:
            continue
        scored.append((score, conf, ds_name, col_name, tt, conf, sd, expr, dt, reason))

    scored.sort(key=lambda x: (-x[0], -x[1], x[2], x[3]))
    if not scored:
        return f'NO_MATCHES: tokens={tokens!r}'

    lines: list[str] = []
    for s, _conf, ds_name, col_name, tt, conf, sd, expr, dt, reason in scored[: max(1, int(limit))]:
        layer = _layer_for_dataset_query(ds_name)
        sd_short = (sd[:140] + "…") if len(sd) > 140 else sd
        expr_short = (expr[:120] + "…") if len(expr) > 120 else expr
        lines.append(
            f"score={s} {ds_name}.{col_name} [{tt or '?'}] conf={conf:.2f} layer={layer} "
            f"data_type={dt or '—'} | sd={sd_short!r} | expr={expr_short!r} | why={reason}"
        )
    return "\n".join(lines)


@tool
def search_datasets_by_name(query: str) -> str:
    """Search DataSet node names for substring matches against the query.

    Returns up to 10 results, one per line: `name [layer] row_count=N`.
    """
    tokens = _tokenize(query)
    if not tokens:
        return "Error: empty query"
    try:
        conn = get_shared_conn()
        result = conn.execute("MATCH (d:DataSet) RETURN d.name, d.layer, d.row_count")
    except Exception as exc:
        return f"Error: dataset fetch failed: {exc}"

    rows: list[tuple[int, str, str, int]] = []
    while result.has_next():
        r = result.get_next()
        name, layer, row_count = str(r[0] or ""), str(r[1] or ""), int(r[2] or 0)
        nl = name.lower()
        hits = sum(1 for t in tokens if t in nl)
        if hits == 0:
            continue
        rows.append((hits, name, layer, row_count))

    rows.sort(key=lambda r: (-r[0], r[1]))
    if not rows:
        return f"NO_MATCHES: tokens={tokens!r}"
    return "\n".join(
        f"{name} [{layer or '?'}] row_count={row_count}"
        for _hits, name, layer, row_count in rows[:10]
    )


@tool
def get_columns_for_dataset(table_name: str) -> str:
    """List every Column node for the named DataSet with its key properties.

    Format per line: `<column> [transform_type] conf=X.XX data_type=… — sd`
    """
    try:
        conn = get_shared_conn()
        result = conn.execute(
            f"""
            MATCH (col:{COL} {{dataset_name: $name}})
            RETURN col.column_name, col.transform_type, col.confidence,
                   col.data_type, col.semantic_description, col.expression
            ORDER BY col.column_name
            """,
            {"name": table_name},
        )
    except Exception as exc:
        return f"Error: {exc}"

    lines: list[str] = []
    while result.has_next():
        r = result.get_next()
        col, tt, conf, dt, sd, expr = (
            str(r[0] or ""), str(r[1] or ""),
            float(r[2]) if r[2] is not None else 0.0,
            str(r[3] or ""), str(r[4] or ""), str(r[5] or ""),
        )
        sd_short = (sd[:140] + "…") if len(sd) > 140 else sd
        expr_short = (expr[:100] + "…") if len(expr) > 100 else expr
        lines.append(
            f"  {col} [{tt or '?'}] conf={conf:.2f} data_type={dt or '—'} | sd={sd_short!r} | expr={expr_short!r}"
        )
    if not lines:
        return f"(no Column nodes found for {table_name})"
    return f"Columns of {table_name}:\n" + "\n".join(lines)


@tool
def get_column_detail(table: str, column: str) -> str:
    """Return full Column properties + a 3-hop upstream chain summary.

    Used by the agent to enrich top results before composing its answer.
    """
    pk = f"{table}::{column}"
    try:
        conn = get_shared_conn()
        head = conn.execute(
            f"""
            MATCH (c:{COL} {{pk: $pk}})
            RETURN c.column_name, c.dataset_name, c.transform_type,
                   c.confidence, c.data_type, c.semantic_description,
                   c.expression, c.sql_hash
            """,
            {"pk": pk},
        )
        if not head.has_next():
            return f"not found: {table}.{column}"
        r = head.get_next()
        head_str = (
            f"{r[1]}.{r[0]}\n"
            f"  transform_type:       {r[2] or '?'}\n"
            f"  confidence:           {r[3]}\n"
            f"  data_type:            {r[4] or '—'}\n"
            f"  semantic_description: {r[5] or '(empty)'}\n"
            f"  expression:           {r[6] or '(empty)'}"
        )

        chain_q = conn.execute(
            f"""
            MATCH path = (c:{COL} {{pk: $pk}})-[:DERIVES_FROM*1..3]->(src:{COL})
            RETURN src.dataset_name, src.column_name, src.transform_type,
                   length(path) AS hops
            ORDER BY hops ASC
            """,
            {"pk": pk},
        )
        seen: set[tuple[str, str]] = set()
        chain_rows: list[str] = []
        while chain_q.has_next():
            cr = chain_q.get_next()
            key = (str(cr[0]), str(cr[1]))
            if key in seen:
                continue
            seen.add(key)
            chain_rows.append(f"    hop={int(cr[3])} {cr[0]}.{cr[1]} [{cr[2] or '?'}]")
    except Exception as exc:
        return f"Error: {exc}"

    if chain_rows:
        return head_str + "\n  upstream_chain (≤3 hops):\n" + "\n".join(chain_rows)
    return head_str + "\n  upstream_chain: (none — leaf column)"


TOOLS = [
    search_columns_by_text,
    search_datasets_by_name,
    get_columns_for_dataset,
    get_column_detail,
]
