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
        _kuzu_db = kuzu.Database(str(KUZU_PATH))
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

app.mount("/static", StaticFiles(directory=str(UI_ROOT / "static")), name="static")


@app.get("/")
def index():
    return FileResponse(str(UI_ROOT / "static" / "index.html"))


@app.get("/healthz")
def healthz():
    return {"ok": True}
