"""JSONL parser: extracts LineageManifest + StageMetrics tuples from a pipeline log.

We only care about two event types — `transform_start` (which carries the lineage
manifest) and `stage_complete` (which carries runtime metrics). Everything else is
silently skipped. Malformed lines and events that claim to be `transform_start` but
are missing manifest fields are logged as `parse_warning` and dropped.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from lineage.config import configure_logging
from lineage.models import LineageManifest, ParsedRun, StageMetrics

REQUIRED_MANIFEST_KEYS = {
    "stage",
    "run_id",
    "ts",
    "target_table",
    "source_tables",
    "depends_on_stages",
    "transform_type",
    "derived_columns",
}

REQUIRED_METRICS_KEYS = {"stage", "run_id", "output_row_count", "duration_ms"}


def _extract_run_id_from_filename(path: Path) -> str:
    return path.stem


def parse_run(jsonl_path: str | Path) -> ParsedRun:
    path = Path(jsonl_path)
    if not path.exists():
        raise FileNotFoundError(f"log file not found: {path}")

    fallback_run_id = _extract_run_id_from_filename(path)
    log = configure_logging(fallback_run_id, "parser")

    parsed = ParsedRun(run_id=fallback_run_id)

    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("parse_warning", line_number=lineno, reason=f"json decode error: {exc}")
                continue
            if not isinstance(event, dict):
                log.warning("parse_warning", line_number=lineno, reason="event is not a JSON object")
                continue

            etype = event.get("event")
            if etype == "transform_start":
                manifest = _coerce_manifest(event, lineno, log)
                if manifest is not None:
                    parsed.manifests.append(manifest)
                    if not parsed.run_id or parsed.run_id == fallback_run_id:
                        parsed.run_id = manifest.run_id
            elif etype == "stage_complete":
                metrics = _coerce_metrics(event, lineno, log)
                if metrics is not None:
                    parsed.metrics[metrics.stage] = metrics
            else:
                continue

    _warn_on_orphans(parsed, log)
    return parsed


def _coerce_manifest(event: dict, lineno: int, log) -> Optional[LineageManifest]:
    missing = REQUIRED_MANIFEST_KEYS - event.keys()
    if missing:
        # Stages 01/02/03 in this project emit transform_start with just `sql` —
        # those aren't full lineage manifests, so we skip them with a parse_warning
        # instead of crashing. Only stages that emit the Option-B manifest are ingested.
        log.warning(
            "parse_warning",
            line_number=lineno,
            reason="transform_start missing manifest fields",
            missing_keys=sorted(missing),
            stage=event.get("stage"),
        )
        return None

    if not isinstance(event["source_tables"], list):
        log.warning("parse_warning", line_number=lineno, reason="source_tables not a list")
        return None
    if not isinstance(event["depends_on_stages"], list):
        log.warning("parse_warning", line_number=lineno, reason="depends_on_stages not a list")
        return None
    if not isinstance(event["derived_columns"], dict):
        log.warning("parse_warning", line_number=lineno, reason="derived_columns not a dict")
        return None

    return LineageManifest(
        stage=str(event["stage"]),
        run_id=str(event["run_id"]),
        ts=str(event["ts"]),
        target_table=str(event["target_table"]),
        source_tables=[str(s) for s in event["source_tables"]],
        depends_on_stages=[str(s) for s in event["depends_on_stages"]],
        transform_type=str(event["transform_type"]),
        derived_columns={str(k): str(v) for k, v in event["derived_columns"].items()},
    )


def _coerce_metrics(event: dict, lineno: int, log) -> Optional[StageMetrics]:
    missing = REQUIRED_METRICS_KEYS - event.keys()
    if missing:
        # stage_complete from non-transform stages (00, 99) lack output_row_count/duration_ms
        # in some shapes — silently skip those. We only warn if the event clearly
        # *should* have metrics but doesn't.
        return None
    try:
        return StageMetrics(
            stage=str(event["stage"]),
            run_id=str(event["run_id"]),
            ts=str(event.get("ts", "")),
            output_row_count=int(event["output_row_count"]),
            duration_ms=int(event["duration_ms"]),
        )
    except (TypeError, ValueError) as exc:
        log.warning("parse_warning", line_number=lineno, reason=f"metrics coercion failed: {exc}")
        return None


def _warn_on_orphans(parsed: ParsedRun, log) -> None:
    for m in parsed.manifests:
        if m.stage not in parsed.metrics:
            log.warning(
                "manifest_without_metrics",
                stage=m.stage,
                target_table=m.target_table,
                reason="no matching stage_complete event found in log",
            )
