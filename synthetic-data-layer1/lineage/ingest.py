"""CLI entry point for the TraceX lineage ingestion layer.

Usage:
    python lineage/ingest.py --run-id <run_id>
    python lineage/ingest.py --log-file logs/<run_id>.jsonl
    python lineage/ingest.py --latest

After ingesting once, the script ingests the same ParsedRun a second time and
asserts node/edge counts didn't change — the idempotency self-test required by
the spec. The file is parsed exactly once.
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lineage.config import (  # noqa: E402
    configure_logging,
    find_latest_log,
    get_graph_path,
    get_log_dir,
)
from lineage.graph_builder import GraphBuilder  # noqa: E402
from lineage.parser import parse_run  # noqa: E402
from lineage.queries import (  # noqa: E402
    count_edges_by_label,
    count_nodes_by_label,
    get_process_chain,
)


def _resolve_log_path(args) -> Path:
    if args.log_file:
        return Path(args.log_file).resolve()
    if args.run_id:
        return (get_log_dir() / f"{args.run_id}.jsonl").resolve()
    if args.latest:
        latest = find_latest_log()
        if latest is None:
            raise SystemExit(f"no .jsonl logs found in {get_log_dir()}")
        return latest.resolve()
    raise SystemExit("must pass one of --run-id / --log-file / --latest")


def _verify_idempotency(conn, first_counts: dict) -> None:
    """Compare the nodes/edges sub-dicts only — first_counts may carry extra
    metadata (e.g. run_id) that the post-second-pass snapshot does not."""
    second_counts = {
        "nodes": count_nodes_by_label(conn),
        "edges": count_edges_by_label(conn),
    }
    log = configure_logging(first_counts.get("run_id", "lineage"), "ingest")
    a = {"nodes": first_counts["nodes"], "edges": first_counts["edges"]}
    if second_counts != a:
        log.error("idempotency_violation", first=a, second=second_counts)
        raise RuntimeError("Idempotency check failed")
    log.info("idempotency_verified", counts=second_counts)


def _print_human_summary(run_id: str, nodes: dict, edges: dict, duration_ms: int) -> None:
    nd = " | ".join(f"{nodes.get(k, 0)} {k}" for k in ("DataSet", "Column", "Process"))
    ed = " | ".join(
        f"{edges.get(k, 0)} {k}" for k in ("INPUT_TO", "PRODUCES", "DERIVES_FROM", "DEPENDS_ON")
    )
    print()
    print("=== TraceX Ingestion Complete ===")
    print(f"Run ID  : {run_id}")
    print(f"Nodes   : {nd}")
    print(f"Edges   : {ed}")
    print(f"Duration: {duration_ms}ms")


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest a TraceX pipeline JSONL log into Kuzu.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-id", help="run_id (resolved against TRACEX_LOG_DIR)")
    group.add_argument("--log-file", help="explicit path to a JSONL log file")
    group.add_argument("--latest", action="store_true", help="ingest the most-recent log file")
    parser.add_argument(
        "--graph-path", default=None, help="override TRACEX_GRAPH_PATH"
    )
    args = parser.parse_args(argv)

    log_path = _resolve_log_path(args)
    if not log_path.exists():
        raise SystemExit(f"log file does not exist: {log_path}")

    parsed = parse_run(log_path)
    run_id = parsed.run_id

    log = configure_logging(run_id, "ingest")
    log.info(
        "ingest_cli_start",
        log_path=str(log_path),
        manifest_count=len(parsed.manifests),
        metrics_count=len(parsed.metrics),
        graph_path=args.graph_path or str(get_graph_path()),
    )

    if not parsed.manifests:
        log.warning(
            "no_manifests_found",
            log_path=str(log_path),
            reason="no transform_start events with full lineage manifests",
        )

    builder: GraphBuilder | None = None
    started = time.perf_counter()
    try:
        builder = GraphBuilder(args.graph_path, run_id)

        # First ingestion — produces the canonical graph state.
        first_summary = builder.ingest_run(parsed)

        # Snapshot counts for the idempotency self-test.
        first_counts = {
            "run_id": run_id,
            "nodes": count_nodes_by_label(builder.conn),
            "edges": count_edges_by_label(builder.conn),
        }

        # Second ingestion — must be a no-op against the existing graph state.
        builder.ingest_run(parsed)
        _verify_idempotency(builder.conn, first_counts)

        # Final verification surfaces.
        process_chain = get_process_chain(builder.conn, run_id)
        duration_ms = int((time.perf_counter() - started) * 1000)

        log.info(
            "ingestion_complete",
            run_id=run_id,
            nodes=first_counts["nodes"],
            edges=first_counts["edges"],
            duration_ms=duration_ms,
            process_chain=process_chain,
            first_pass=first_summary,
        )

        _print_human_summary(run_id, first_counts["nodes"], first_counts["edges"], duration_ms)
        return 0

    except Exception as exc:
        log.error(
            "ingestion_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
        )
        return 1
    finally:
        if builder is not None:
            builder.close()


if __name__ == "__main__":
    sys.exit(main())
