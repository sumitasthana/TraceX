"""FastAPI backend for the TraceX UI.

Reads directly from the artefacts the rest of the platform produces:
    - DuckDB     data/tracex_layer0.duckdb        (Layer 0/1/2 tables)
    - Kuzu       data/tracex_graph                (lineage graph)
    - JSONL      logs/{run_id}.jsonl              (pipeline runs)

Endpoints:
    GET  /api/dashboard                  — top-level metrics for the briefing page
    GET  /api/runs                       — list of pipeline runs (compact)
    GET  /api/runs/{run_id}              — single run with all stages + DQ checks
    GET  /api/lineage/graph              — full graph for vis-network
    GET  /api/lineage/dataset/{name}     — upstream/downstream of a dataset
    GET  /api/dq/{run_id}                — DQ check results from a run
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

import duckdb
import kuzu
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

UI_ROOT = Path(__file__).resolve().parent
REPO_ROOT = UI_ROOT.parent

DUCKDB_PATH = REPO_ROOT / "data" / "tracex_layer0.duckdb"
KUZU_PATH = REPO_ROOT / "data" / "tracex_graph"
LOGS_DIR = REPO_ROOT / "logs"

# Make `from lineage.queries import ...` resolvable for shared graph helpers.
sys.path.insert(0, str(REPO_ROOT))
from lineage.queries import (  # noqa: E402
    count_edges_by_label,
    count_nodes_by_label,
    get_dataset_downstream,
    get_dataset_upstream,
    get_process_chain,
)

app = FastAPI(title="TraceX UI", version="0.1.0")


# ----------------------------------------------------------------------
# Resource handles. Open lazily and cache; both stores are read-mostly.
# ----------------------------------------------------------------------

_duckdb_conn: Optional[duckdb.DuckDBPyConnection] = None
_kuzu_db: Optional[kuzu.Database] = None


def duck() -> duckdb.DuckDBPyConnection:
    global _duckdb_conn
    if _duckdb_conn is None:
        if not DUCKDB_PATH.exists():
            raise HTTPException(503, f"DuckDB not found at {DUCKDB_PATH}")
        _duckdb_conn = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    return _duckdb_conn


def kuzu_conn() -> kuzu.Connection:
    """Open a fresh Kuzu connection per request — Kuzu connections are not
    thread-safe and FastAPI may serve concurrent requests on different workers.
    The Database object itself is fine to share."""
    global _kuzu_db
    if _kuzu_db is None:
        if not KUZU_PATH.exists():
            raise HTTPException(503, f"Kuzu graph not found at {KUZU_PATH}")
        _kuzu_db = kuzu.Database(str(KUZU_PATH), read_only=True)
    return kuzu.Connection(_kuzu_db)


# ----------------------------------------------------------------------
# JSONL log parsing — runs and DQ checks live here, not in DuckDB.
# ----------------------------------------------------------------------

def _list_run_files() -> list[Path]:
    if not LOGS_DIR.exists():
        return []
    return sorted(LOGS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)


def _read_events(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _summarize_run(path: Path) -> dict:
    """Collapse the JSONL of a single run into a compact dashboard-friendly dict."""
    run_id = path.stem
    events = _read_events(path)

    stages: dict[str, dict] = {}
    pipeline_status: Optional[str] = None
    pipeline_started_at: Optional[str] = None
    pipeline_duration_ms: Optional[int] = None
    dq_total = 0
    dq_passed = 0

    for e in events:
        et = e.get("event")
        stage = e.get("stage")

        if et == "pipeline_start":
            pipeline_started_at = e.get("ts")
        elif et == "pipeline_complete":
            pipeline_status = e.get("status")
            pipeline_duration_ms = e.get("duration_ms")
        elif et == "stage_complete" and stage:
            stages[stage] = {
                "stage": stage,
                "status": e.get("status", "ok"),
                "duration_ms": e.get("duration_ms"),
                "output_row_count": e.get("output_row_count"),
                "output_table": e.get("output_table"),
                "ts": e.get("ts"),
            }
        elif et == "data_quality_check":
            dq_total += 1
            if e.get("passed") is True:
                dq_passed += 1

    return {
        "run_id": run_id,
        "started_at": pipeline_started_at,
        "duration_ms": pipeline_duration_ms,
        "status": pipeline_status or ("ok" if all(s.get("status") == "ok" for s in stages.values()) else "unknown"),
        "stage_count": len(stages),
        "stages": list(stages.values()),
        "dq_total": dq_total,
        "dq_passed": dq_passed,
        "log_path": str(path),
    }


# ----------------------------------------------------------------------
# /api/dashboard
# ----------------------------------------------------------------------

@app.get("/api/dashboard")
def dashboard():
    runs = [_summarize_run(p) for p in _list_run_files()]
    latest = runs[0] if runs else None

    # Lineage counts — gracefully degrade if Kuzu hasn't been ingested yet.
    try:
        c = kuzu_conn()
        node_counts = count_nodes_by_label(c)
        edge_counts = count_edges_by_label(c)
    except Exception:
        node_counts = {}
        edge_counts = {}

    return {
        "pipeline_runs_total": len(runs),
        "datasets_total": int(node_counts.get("DataSet", 0)),
        "columns_total": int(node_counts.get("Column", 0)),
        "processes_total": int(node_counts.get("Process", 0)),
        "latest_run": latest,
        "graph_node_counts": node_counts,
        "graph_edge_counts": edge_counts,
        "dq_pass_rate": (
            f"{latest['dq_passed']}/{latest['dq_total']}" if latest and latest["dq_total"] else "—"
        ),
    }


# ----------------------------------------------------------------------
# /api/runs
# ----------------------------------------------------------------------

@app.get("/api/runs")
def list_runs():
    return [_summarize_run(p) for p in _list_run_files()]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    path = LOGS_DIR / f"{run_id}.jsonl"
    if not path.exists():
        raise HTTPException(404, f"run not found: {run_id}")
    summary = _summarize_run(path)

    # Attach the full DQ check list for the detail view.
    events = _read_events(path)
    dq_checks = [
        {
            "check_name": e.get("check_name"),
            "passed": e.get("passed"),
            "expected": e.get("expected"),
            "actual": e.get("actual"),
            "rows_checked": e.get("rows_checked"),
            "rows_failed": e.get("rows_failed"),
            "stage": e.get("stage"),
        }
        for e in events
        if e.get("event") == "data_quality_check"
    ]
    summary["dq_checks"] = dq_checks
    return summary


# ----------------------------------------------------------------------
# /api/lineage/*
# ----------------------------------------------------------------------

LAYER_COLOR = {
    "layer_0": "#0f766e",   # teal — raw sources
    "layer_1": "#1d4ed8",   # blue — staging
    "layer_2": "#6d28d9",   # purple — facts
    "unknown": "#6b7280",
}


@app.get("/api/lineage/graph")
def lineage_graph():
    c = kuzu_conn()

    nodes: list[dict] = []
    edges: list[dict] = []

    # DataSets
    r = c.execute("MATCH (d:DataSet) RETURN d.name, d.layer, d.row_count")
    while r.has_next():
        name, layer, row_count = r.get_next()
        nodes.append({
            "id": f"ds::{name}",
            "label": name,
            "group": "DataSet",
            "layer": layer,
            "row_count": int(row_count or 0),
            "color": LAYER_COLOR.get(layer, "#6b7280"),
            "shape": "box",
        })

    # Processes
    r = c.execute("MATCH (p:Process) RETURN p.stage, p.run_id, p.transform_type, p.duration_ms")
    while r.has_next():
        stage, run_id, transform_type, duration_ms = r.get_next()
        nodes.append({
            "id": f"proc::{stage}::{run_id}",
            "label": stage,
            "group": "Process",
            "transform_type": transform_type,
            "duration_ms": int(duration_ms or 0),
            "color": "#0c1f3d",
            "shape": "ellipse",
        })

    # Edges — only the structural ones (INPUT_TO, PRODUCES, DEPENDS_ON).
    # Column-level DERIVES_FROM is hidden here to keep the diagram readable.
    r = c.execute(
        "MATCH (d:DataSet)-[:INPUT_TO]->(p:Process) "
        "RETURN d.name, p.stage, p.run_id"
    )
    while r.has_next():
        ds, stage, run_id = r.get_next()
        edges.append({
            "from": f"ds::{ds}",
            "to": f"proc::{stage}::{run_id}",
            "label": "INPUT_TO",
            "color": "#9ca3af",
        })

    r = c.execute(
        "MATCH (p:Process)-[:PRODUCES]->(d:DataSet) "
        "RETURN p.stage, p.run_id, d.name"
    )
    while r.has_next():
        stage, run_id, ds = r.get_next()
        edges.append({
            "from": f"proc::{stage}::{run_id}",
            "to": f"ds::{ds}",
            "label": "PRODUCES",
            "color": "#1d4ed8",
        })

    r = c.execute(
        "MATCH (a:Process)-[:DEPENDS_ON]->(b:Process) "
        "RETURN a.stage, a.run_id, b.stage, b.run_id"
    )
    while r.has_next():
        a_stage, a_run, b_stage, b_run = r.get_next()
        edges.append({
            "from": f"proc::{a_stage}::{a_run}",
            "to": f"proc::{b_stage}::{b_run}",
            "label": "DEPENDS_ON",
            "color": "#b45309",
            "dashes": True,
        })

    return {"nodes": nodes, "edges": edges}


@app.get("/api/lineage/dataset/{name}")
def dataset_lineage(name: str):
    c = kuzu_conn()
    return {
        "table_name": name,
        "upstream": get_dataset_upstream(c, name),
        "downstream": get_dataset_downstream(c, name),
    }


# ----------------------------------------------------------------------
# /api/dq/{run_id}
# ----------------------------------------------------------------------

@app.get("/api/dq/{run_id}")
def dq_for_run(run_id: str):
    path = LOGS_DIR / f"{run_id}.jsonl"
    if not path.exists():
        raise HTTPException(404, f"run not found: {run_id}")
    events = _read_events(path)
    by_stage: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        if e.get("event") != "data_quality_check":
            continue
        by_stage[e.get("stage", "?")].append({
            "check_name": e.get("check_name"),
            "passed": e.get("passed"),
            "expected": e.get("expected"),
            "actual": e.get("actual"),
            "rows_checked": e.get("rows_checked"),
            "rows_failed": e.get("rows_failed"),
        })
    return {"run_id": run_id, "checks_by_stage": by_stage}


# ----------------------------------------------------------------------
# /api/datasets — backing the Datasets browse view
# ----------------------------------------------------------------------

@app.get("/api/datasets")
def list_datasets():
    c = kuzu_conn()
    r = c.execute(
        "MATCH (d:DataSet) RETURN d.name, d.layer, d.row_count, d.computed_at "
        "ORDER BY d.layer ASC, d.name ASC"
    )
    out = []
    while r.has_next():
        name, layer, row_count, computed_at = r.get_next()
        out.append({
            "name": name,
            "layer": layer,
            "row_count": int(row_count or 0),
            "computed_at": computed_at,
        })
    return out


# ----------------------------------------------------------------------
# Static file serving — index.html at /, assets at /static/*.
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# Deep column-lineage endpoints
# ----------------------------------------------------------------------

COL_LABEL = "`Column`"


def _column_pk(column_name: str, dataset_name: str) -> str:
    return f"{dataset_name}::{column_name}"


@app.get("/api/lineage/dataset/{name}/columns")
def lineage_dataset_columns(name: str):
    """Every Column node for a dataset, with the per-column source count.

    Powers the click-to-expand drill-down in the Lineage Explorer + Datasets view.
    """
    c = kuzu_conn()
    q = c.execute(
        f"""
        MATCH (col:{COL_LABEL} {{dataset_name: $name}})
        OPTIONAL MATCH (col)-[d:DERIVES_FROM]->(:{COL_LABEL})
        RETURN col.column_name, col.transform_type, col.confidence,
               col.data_type, col.expression, col.semantic_description,
               col.derivation, col.sql_hash, col.computed_at,
               count(d) AS source_count
        ORDER BY col.column_name
        """,
        {"name": name},
    )
    out: list[dict] = []
    while q.has_next():
        r = q.get_next()
        out.append({
            "column": r[0],
            "transform_type": r[1] or "",
            "confidence": float(r[2]) if r[2] is not None else None,
            "data_type": r[3] or "",
            "expression": r[4] or "",
            "semantic_description": r[5] or "",
            "derivation": r[6] or "",
            "sql_hash": r[7] or "",
            "computed_at": r[8] or "",
            "source_count": int(r[9]) if r[9] is not None else 0,
        })
    return {"table": name, "columns": out}


@app.get("/api/lineage/column/{table}/{column}")
def lineage_column(table: str, column: str):
    """Full upstream chain for a single column across all hops."""
    c = kuzu_conn()
    pk = _column_pk(column, table)
    head = c.execute(
        f"""
        MATCH (n:{COL_LABEL} {{pk: $pk}})
        RETURN n.column_name, n.dataset_name, n.expression,
               n.transform_type, n.confidence, n.data_type,
               n.semantic_description, n.sql_hash
        """,
        {"pk": pk},
    )
    if not head.has_next():
        raise HTTPException(404, f"column not found: {table}.{column}")
    row = head.get_next()
    head_payload = {
        "table": row[1],
        "column": row[0],
        "expression": row[2] or "",
        "transform_type": row[3] or "",
        "confidence": float(row[4]) if row[4] is not None else None,
        "data_type": row[5] or "",
        "semantic_description": row[6] or "",
        "sql_hash": row[7] or "",
    }

    chain_q = c.execute(
        f"""
        MATCH path = (start:{COL_LABEL} {{pk: $pk}})
                     -[:DERIVES_FROM*1..10]->(src:{COL_LABEL})
        RETURN src.dataset_name, src.column_name, src.expression,
               src.transform_type, src.confidence,
               src.semantic_description, length(path) AS hops
        ORDER BY hops ASC
        """,
        {"pk": pk},
    )
    seen: set[tuple[str, str, int]] = set()
    chain: list[dict] = []
    while chain_q.has_next():
        r = chain_q.get_next()
        key = (str(r[0]), str(r[1]), int(r[6]))
        if key in seen:
            continue
        seen.add(key)
        chain.append({
            "hop": int(r[6]),
            "source_table": r[0],
            "source_column": r[1],
            "expression": r[2] or "",
            "transform_type": r[3] or "",
            "confidence": float(r[4]) if r[4] is not None else None,
            "semantic_description": r[5] or "",
        })

    return {**head_payload, "upstream_chain": chain}


@app.get("/api/lineage/impact/{table}/{column}")
def lineage_impact(table: str, column: str, change_type: str = "RENAME"):
    """Invoke the impact_analyst agent. Falls back to a direct Kuzu query on agent failure.

    `change_type` accepts RENAME, TYPE_CHANGE, or DROP. Defaults to RENAME.
    Returns the agent's JSON impact report verbatim, or
    `{error, fallback}` when the agent is disabled / fails.
    """
    change_type = (change_type or "RENAME").upper()
    if change_type not in {"RENAME", "TYPE_CHANGE", "DROP"}:
        raise HTTPException(400, f"unsupported change_type: {change_type}")

    # Direct downstream chain — used both as the agent's input context AND
    # as the fallback payload when the agent isn't available.
    c = kuzu_conn()
    pk = _column_pk(column, table)
    fallback_q = c.execute(
        f"""
        MATCH path = (child:{COL_LABEL})-[:DERIVES_FROM*1..10]->(c:{COL_LABEL} {{pk: $pk}})
        RETURN child.dataset_name, child.column_name,
               child.transform_type, child.expression, length(path) AS hops
        ORDER BY hops ASC
        """,
        {"pk": pk},
    )
    fallback: list[dict] = []
    seen: set[tuple[str, str]] = set()
    while fallback_q.has_next():
        r = fallback_q.get_next()
        key = (str(r[0]), str(r[1]))
        if key in seen:
            continue
        seen.add(key)
        fallback.append({
            "affected_table": r[0],
            "affected_column": r[1],
            "transform_type": r[2] or "",
            "expression": r[3] or "",
            "hops": int(r[4]),
        })

    # Try the agent. Any error → fallback path.
    try:
        from lineage.agents.impact_analyst import build as build_agent
        agent = build_agent()
        import json as _json
        payload = _json.dumps({
            "changed_table": table,
            "changed_column": column,
            "change_type": change_type,
        })
        result = agent.invoke({"messages": [{"role": "user", "content": payload}]})
        # Pull the last AI message text.
        text = ""
        for msg in reversed(result.get("messages", []) or []):
            content = getattr(msg, "content", None)
            if isinstance(content, str) and content.strip():
                text = content
                break
            if isinstance(content, list):
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        text = blk.get("text", "")
                        if text.strip():
                            break
                if text:
                    break
        # Best-effort JSON extraction.
        s = (text or "").strip()
        if s.startswith("```"):
            s = s.strip("`")
            if s.lower().startswith("json"):
                s = s[4:]
            s = s.strip()
            if s.endswith("```"):
                s = s[:-3].strip()
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end > start:
            return _json.loads(s[start : end + 1])
        return {"error": "agent returned non-JSON response", "fallback": fallback, "raw": text}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "fallback": fallback}


@app.get("/api/lineage/process/{stage}/{run_id}")
def lineage_process(stage: str, run_id: str):
    """Process node detail: stage metadata, inputs, output, and per-column summary."""
    c = kuzu_conn()
    pk = f"{stage}::{run_id}"
    head = c.execute(
        """
        MATCH (p:Process {pk: $pk})
        RETURN p.stage, p.run_id, p.transform_type, p.target_table,
               p.duration_ms, p.output_row_count, p.sql_hash, p.computed_at
        """,
        {"pk": pk},
    )
    if not head.has_next():
        raise HTTPException(404, f"process not found: {stage}/{run_id}")
    r = head.get_next()
    head_payload = {
        "stage": r[0], "run_id": r[1], "transform_type": r[2],
        "target_table": r[3], "duration_ms": int(r[4]) if r[4] is not None else 0,
        "output_row_count": int(r[5]) if r[5] is not None else 0,
        "sql_hash": r[6] or "", "computed_at": r[7] or "",
    }

    inputs_q = c.execute(
        """
        MATCH (d:DataSet)-[:INPUT_TO]->(p:Process {pk: $pk})
        RETURN d.name
        """,
        {"pk": pk},
    )
    input_tables = []
    while inputs_q.has_next():
        input_tables.append(str(inputs_q.get_next()[0]))

    out_q = c.execute(
        """
        MATCH (p:Process {pk: $pk})-[:PRODUCES]->(d:DataSet)
        RETURN d.name
        """,
        {"pk": pk},
    )
    output_table = head_payload["target_table"]
    if out_q.has_next():
        output_table = str(out_q.get_next()[0])

    cols_q = c.execute(
        f"""
        MATCH (col:{COL_LABEL} {{dataset_name: $tbl}})
        OPTIONAL MATCH (col)-[d:DERIVES_FROM]->(:{COL_LABEL})
        RETURN col.column_name, col.transform_type, col.confidence,
               col.semantic_description, count(d) AS source_count
        ORDER BY col.column_name
        """,
        {"tbl": output_table},
    )
    out_cols: list[dict] = []
    while cols_q.has_next():
        r = cols_q.get_next()
        out_cols.append({
            "column": r[0],
            "transform_type": r[1] or "",
            "confidence": float(r[2]) if r[2] is not None else None,
            "semantic_description": r[3] or "",
            "source_count": int(r[4]) if r[4] is not None else 0,
        })

    return {
        **head_payload,
        "input_tables": input_tables,
        "output_table": output_table,
        "output_columns": out_cols,
    }


# ----------------------------------------------------------------------
# Chat endpoint — routes through chat_supervisor → lineage_search / impact_analyst
# ----------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


_chat_agent = None
_chat_histories: dict[str, list] = {}
MAX_HISTORY_TURNS = 10


def _get_chat_agent():
    """Lazily build the chat supervisor on first request and reuse across calls."""
    global _chat_agent
    if _chat_agent is None:
        from lineage.agents.chat_supervisor import build as build_supervisor  # noqa: WPS433
        _chat_agent = build_supervisor()
    return _chat_agent


def _friendly_tool_event(name: str, input_data: dict) -> str | None:
    """Map a LangGraph on_tool_start event to a user-facing thinking line.

    Returns None for tool events the user doesn't need to see.
    """
    n = (name or "").lower()
    args = input_data or {}

    # Supervisor delegation
    if n == "ask_lineage_search":
        return "Routing to Lineage Search specialist…"
    if n == "ask_impact_analyst":
        return "Routing to Impact Analyst specialist…"

    # Lineage search tools
    if n == "search_columns_by_text":
        q = (args.get("query") or "").strip()[:60]
        return f"Searching column descriptions for “{q}”…" if q else "Searching column descriptions…"
    if n == "search_datasets_by_name":
        q = (args.get("query") or "").strip()[:60]
        return f"Searching table names for “{q}”…" if q else "Searching table names…"
    if n == "get_columns_for_dataset":
        t = (args.get("table_name") or "").strip()
        return f"Reading columns of {t}…" if t else "Reading columns of dataset…"
    if n == "get_column_detail":
        t, c = (args.get("table") or "").strip(), (args.get("column") or "").strip()
        return f"Reading details for {t}.{c}…" if t and c else "Reading column details…"

    # Impact analyst tools
    if n == "get_full_downstream_chain":
        t, c = (args.get("table") or "").strip(), (args.get("column") or "").strip()
        return f"Walking downstream chain of {t}.{c}…" if t and c else "Walking downstream chain…"
    if n == "get_direct_downstream":
        t, c = (args.get("table") or "").strip(), (args.get("column") or "").strip()
        return f"Reading direct downstream of {t}.{c}…" if t and c else "Reading direct downstream…"
    if n == "get_processes_reading_table":
        t = (args.get("table") or "").strip()
        return f"Finding processes that read {t}…" if t else "Finding upstream processes…"
    if n == "get_column_expression":
        t, c = (args.get("table") or "").strip(), (args.get("column") or "").strip()
        return f"Reading expression of {t}.{c}…" if t and c else "Reading column expression…"

    return None


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    """Stream a chat response as SSE — mirrors the ReconX shape exactly.

    Events emitted (SSE format `event: <name>\\ndata: <JSON>\\n\\n`):

      tool_start  — {"tool": "<name>", "label": "<friendly thinking line>"}
      tool_result — {"tool": "ask_*",  "output": "<truncated specialist output>"}
      token       — {"token": "<incremental supervisor text>"}
      final       — {"response": "<full text>", "conversation_id": "...",
                     "specialist_used": "...", "duration_ms": N}
      done        — {}
      error       — {"message": "..."}
    """
    import time
    from fastapi.responses import StreamingResponse  # noqa: WPS433

    started = time.perf_counter()
    conv_id = req.conversation_id or "default"

    async def gen():
        import json as _json

        try:
            agent = _get_chat_agent()
            history = _chat_histories.get(conv_id, [])
            if len(history) > MAX_HISTORY_TURNS * 2:
                history = history[-(MAX_HISTORY_TURNS * 2):]

            from langchain_core.messages import AIMessage, HumanMessage  # noqa: WPS433

            user_msg = HumanMessage(content=req.message)
            messages = history + [user_msg]

            # Track active ask_* delegations by run_id; while any is in flight,
            # suppress supervisor token events (they are usually empty padding
            # while the specialist runs anyway).
            active_delegations: set[str] = set()
            specialist_used = "unknown"
            final_text = ""

            def sse(event_name: str, payload: dict) -> str:
                return f"event: {event_name}\ndata: {_json.dumps(payload, ensure_ascii=False)}\n\n"

            async for event in agent.astream_events({"messages": messages}, version="v2"):
                kind = event.get("event")
                name = event.get("name") or ""

                if kind == "on_tool_start":
                    data = event.get("data") or {}
                    label = _friendly_tool_event(name, data.get("input") or {})
                    if name.startswith("ask_"):
                        run_id = event.get("run_id", "")
                        active_delegations.add(run_id)
                        if name == "ask_lineage_search":
                            specialist_used = (
                                "both" if specialist_used == "impact_analyst" else "lineage_search"
                            )
                        elif name == "ask_impact_analyst":
                            specialist_used = (
                                "both" if specialist_used == "lineage_search" else "impact_analyst"
                            )
                    if label:
                        yield sse("tool_start", {"tool": name, "label": label})

                elif kind == "on_tool_end" and name.startswith("ask_"):
                    run_id = event.get("run_id", "")
                    active_delegations.discard(run_id)
                    raw = (event.get("data") or {}).get("output", "")
                    if hasattr(raw, "content"):
                        out = raw.content
                    else:
                        out = raw
                    if isinstance(out, list):
                        out = "".join(
                            b.get("text", "") for b in out
                            if isinstance(b, dict) and b.get("type") == "text"
                        ) or str(out)
                    elif not isinstance(out, str):
                        out = str(out)
                    if len(out) > 2000:
                        out = out[:2000] + "\n... (truncated)"
                    yield sse("tool_result", {"tool": name, "output": out})

                elif kind == "on_chat_model_stream" and not active_delegations:
                    chunk = (event.get("data") or {}).get("chunk")
                    text = ""
                    if chunk is not None:
                        c = getattr(chunk, "content", None)
                        if isinstance(c, str):
                            text = c
                        elif isinstance(c, list):
                            for blk in c:
                                if isinstance(blk, dict) and blk.get("type") == "text":
                                    text += blk.get("text", "")
                    if text:
                        final_text += text
                        yield sse("token", {"token": text})

            # Persist conversation state — only the human turn + final AI msg.
            _chat_histories[conv_id] = messages + [AIMessage(content=final_text)]

            yield sse("final", {
                "response": final_text or "(no response)",
                "conversation_id": conv_id,
                "specialist_used": specialist_used,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            })
            yield sse("done", {})

        except Exception as exc:
            yield (
                f"event: error\ndata: "
                f"{_json.dumps({'message': f'The lineage agent encountered an error: {exc}'}, ensure_ascii=False)}\n\n"
            )
            yield "event: done\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/chat")
async def chat(req: ChatRequest):
    import time

    started = time.perf_counter()
    conv_id = req.conversation_id or "default"

    try:
        agent = _get_chat_agent()
        history = _chat_histories.get(conv_id, [])

        # Trim to MAX_HISTORY_TURNS pairs (user + assistant = 1 turn).
        if len(history) > MAX_HISTORY_TURNS * 2:
            history = history[-(MAX_HISTORY_TURNS * 2):]

        from langchain_core.messages import AIMessage, HumanMessage  # noqa: WPS433

        messages = history + [HumanMessage(content=req.message)]
        result = await agent.ainvoke({"messages": messages})

        # Pull the last AI message text — first reverse hit with no tool_calls.
        response_text = ""
        for msg in reversed(result.get("messages", [])):
            content = getattr(msg, "content", None)
            if isinstance(content, str) and content.strip() and not getattr(msg, "tool_calls", None):
                response_text = content
                break
            if isinstance(content, list) and not getattr(msg, "tool_calls", None):
                buf = []
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        buf.append(blk.get("text", ""))
                joined = "".join(buf).strip()
                if joined:
                    response_text = joined
                    break

        # Sniff which specialist(s) were dispatched.
        specialist_used = "unknown"
        for msg in result.get("messages", []):
            calls = getattr(msg, "tool_calls", None) or []
            for call in calls:
                name = call.get("name", "") if isinstance(call, dict) else getattr(call, "name", "")
                if "lineage_search" in name:
                    specialist_used = "both" if specialist_used == "impact_analyst" else "lineage_search"
                elif "impact_analyst" in name:
                    specialist_used = "both" if specialist_used == "lineage_search" else "impact_analyst"

        # Persist trimmed history (human turn + final AI turn).
        _chat_histories[conv_id] = messages + [AIMessage(content=response_text)]

        return {
            "response": response_text or "(no response)",
            "conversation_id": conv_id,
            "specialist_used": specialist_used,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

    except Exception as exc:  # never raise out of the chat endpoint
        return {
            "response": f"The lineage agent encountered an error: {exc}",
            "conversation_id": conv_id,
            "specialist_used": "error",
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "error": True,
        }


# ----------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(UI_ROOT / "static")), name="static")


@app.get("/")
def index():
    return FileResponse(str(UI_ROOT / "static" / "index.html"))


@app.get("/healthz")
def healthz():
    return {"ok": True}
