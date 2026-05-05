"""TraceX CLI — single entrypoint for the whole platform.

Usage:
    python cli.py up                # full bootstrap, then serve UI + API
    python cli.py serve             # just launch the FastAPI server (UI + API)
    python cli.py generate          # generate Layer 0 CSVs
    python cli.py load              # load CSVs into DuckDB
    python cli.py pipeline          # run staging + facts pipeline
    python cli.py ingest [--run-id ID | --log-file PATH | --latest]
    python cli.py healthcheck       # JanusGraph wait + schema bootstrap + smoke test
    python cli.py status            # report which artefacts exist on disk

The FastAPI process serves the SPA (frontend) and the JSON endpoints (backend) on
the same port, so `serve` is a single process. Defaults to 127.0.0.1:8765 — override
with TRACEX_UI_HOST / TRACEX_UI_PORT or --host / --port.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable

DUCKDB_PATH = REPO_ROOT / "data" / "tracex_layer0.duckdb"
CSV_DIR = REPO_ROOT / "data" / "layer0"
GRAPH_PATH = REPO_ROOT / "data" / "tracex_graph"
LOG_DIR = REPO_ROOT / "logs"


# ── helpers ───────────────────────────────────────────────────────────────

def _banner(label: str) -> None:
    print(f"\n== {label} ==", flush=True)


def _run(args: list[str], **kwargs) -> int:
    """Run a subprocess inheriting stdio; return its exit code."""
    proc = subprocess.run(args, cwd=str(REPO_ROOT), **kwargs)
    return proc.returncode


def _run_or_exit(args: list[str], step: str) -> None:
    rc = _run(args)
    if rc != 0:
        print(f"\n[tracex] step '{step}' failed (exit {rc})", file=sys.stderr)
        sys.exit(rc)


# ── subcommands ───────────────────────────────────────────────────────────

def cmd_generate(_args: argparse.Namespace) -> int:
    _banner("Layer 0 — generate synthetic CSVs")
    return _run([PYTHON, str(REPO_ROOT / "layer0" / "generate.py")])


def cmd_load(_args: argparse.Namespace) -> int:
    _banner("Layer 0 — load DuckDB")
    return _run([PYTHON, str(REPO_ROOT / "layer0" / "load_duckdb.py")])


def cmd_pipeline(_args: argparse.Namespace) -> int:
    _banner("Pipeline — run all stages")
    return _run([PYTHON, str(REPO_ROOT / "pipeline" / "run_pipeline.py")])


def cmd_ingest(args: argparse.Namespace) -> int:
    _banner("Lineage — ingest run into graph")
    cmd = [PYTHON, str(REPO_ROOT / "lineage" / "ingest.py")]
    if args.run_id:
        cmd += ["--run-id", args.run_id]
    elif args.log_file:
        cmd += ["--log-file", args.log_file]
    else:
        cmd += ["--latest"]
    return _run(cmd)


def cmd_healthcheck(_args: argparse.Namespace) -> int:
    _banner("JanusGraph — health check + schema bootstrap")
    return _run([PYTHON, str(REPO_ROOT / "graph" / "healthcheck.py")])


def cmd_serve(args: argparse.Namespace) -> int:
    _banner(f"UI — serving frontend + API on http://{args.host}:{args.port}")
    env = os.environ.copy()
    env["TRACEX_UI_HOST"] = args.host
    env["TRACEX_UI_PORT"] = str(args.port)
    return subprocess.run(
        [PYTHON, str(REPO_ROOT / "ui" / "serve.py")],
        cwd=str(REPO_ROOT),
        env=env,
    ).returncode


def cmd_up(args: argparse.Namespace) -> int:
    """Bootstrap everything, then serve. Skips steps whose outputs already exist
    unless --force is passed."""
    if args.force or not any(CSV_DIR.glob("*.csv")):
        _run_or_exit([PYTHON, str(REPO_ROOT / "layer0" / "generate.py")], "generate")
    else:
        print(f"[tracex] skipping generate — CSVs already present in {CSV_DIR}")

    if args.force or not DUCKDB_PATH.exists():
        _run_or_exit([PYTHON, str(REPO_ROOT / "layer0" / "load_duckdb.py")], "load")
    else:
        print(f"[tracex] skipping load — {DUCKDB_PATH.name} already exists")

    _run_or_exit([PYTHON, str(REPO_ROOT / "pipeline" / "run_pipeline.py")], "pipeline")
    _run_or_exit(
        [PYTHON, str(REPO_ROOT / "lineage" / "ingest.py"), "--latest"],
        "ingest",
    )
    return cmd_serve(args)


def cmd_status(_args: argparse.Namespace) -> int:
    def mark(p: Path) -> str:
        return "OK " if p.exists() else "-- "

    csvs = list(CSV_DIR.glob("*.csv")) if CSV_DIR.exists() else []
    runs = list(LOG_DIR.glob("*.jsonl")) if LOG_DIR.exists() else []

    print()
    print(f"  {mark(CSV_DIR)}  CSVs            ({len(csvs)} files in {CSV_DIR})")
    print(f"  {mark(DUCKDB_PATH)}  DuckDB          {DUCKDB_PATH}")
    print(f"  {mark(GRAPH_PATH)}  Lineage graph   {GRAPH_PATH}")
    print(f"  {mark(LOG_DIR)}  Pipeline runs   ({len(runs)} runs in {LOG_DIR})")
    print()
    return 0


# ── arg parsing ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tracex", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("generate", help="generate Layer 0 synthetic CSVs").set_defaults(func=cmd_generate)
    sub.add_parser("load",     help="load CSVs into DuckDB").set_defaults(func=cmd_load)
    sub.add_parser("pipeline", help="run staging + facts pipeline").set_defaults(func=cmd_pipeline)

    ingest_p = sub.add_parser("ingest", help="ingest pipeline JSONL into the lineage graph")
    grp = ingest_p.add_mutually_exclusive_group()
    grp.add_argument("--run-id", help="ingest logs/<run_id>.jsonl")
    grp.add_argument("--log-file", help="ingest a specific JSONL file")
    grp.add_argument("--latest", action="store_true", help="ingest the most recent run (default)")
    ingest_p.set_defaults(func=cmd_ingest)

    sub.add_parser("healthcheck", help="JanusGraph wait + schema bootstrap + smoke test").set_defaults(func=cmd_healthcheck)
    sub.add_parser("status",      help="report which artefacts exist on disk").set_defaults(func=cmd_status)

    serve_p = sub.add_parser("serve", help="launch FastAPI (frontend SPA + JSON API)")
    serve_p.add_argument("--host", default=os.environ.get("TRACEX_UI_HOST", "127.0.0.1"))
    serve_p.add_argument("--port", type=int, default=int(os.environ.get("TRACEX_UI_PORT", "8765")))
    serve_p.set_defaults(func=cmd_serve)

    up_p = sub.add_parser("up", help="full bootstrap then serve (skips steps whose outputs exist)")
    up_p.add_argument("--force", action="store_true", help="re-run generate + load even if outputs exist")
    up_p.add_argument("--host", default=os.environ.get("TRACEX_UI_HOST", "127.0.0.1"))
    up_p.add_argument("--port", type=int, default=int(os.environ.get("TRACEX_UI_PORT", "8765")))
    up_p.set_defaults(func=cmd_up)

    return p


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
