"""Post-stage hook that builds and ingests a deep-lineage manifest live.

Called by every transform stage immediately after its SQL executes:

    from lineage.manifest_builder import build_and_ingest

    build_and_ingest(
        stage=STAGE_NAME,
        run_id=run_id,
        sql=TRANSFORM_SQL,
        target_table=OUTPUT_TABLE,
        source_tables=["..."],
        depends_on_stages=["..."],
        transform_type="TRANSFORM_JOIN",
        output_row_count=output_rows,
        duration_ms=transform_duration_ms,
        con=con,
    )

Procedure (in order):

  1. sql_parser.parse(sql) → list[ColumnLineageMap]
  2. For each ambiguous map, call sql_parser_agent (try/except — never crash)
  3. Build StageLineageManifest with sql_hash
  4. graph_builder.ingest_stage_manifest(manifest)
  5. For each map with confidence>=0.5, call enrichment_agent (try/except)
  6. Emit `lineage_manifest_complete` structured log event
  7. Return manifest

Critical: this function MUST NEVER RAISE. Any unhandled exception is caught,
logged as `lineage_manifest_exception`, and the function returns a partial
manifest so the calling pipeline stage still completes successfully.
"""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timezone
from typing import List, Optional

import duckdb

from lineage.config import configure_logging
from lineage.graph_builder import GraphBuilder
from lineage.models import (
    ColumnLineageEdge,
    ColumnLineageMap,
    StageLineageManifest,
    TransformType,
)
from lineage.sql_parser import hash_sql, parse as sql_parse


# Lazy module-level agent singletons. Building a Bedrock-backed
# create_react_agent has a non-trivial cost; reuse across stages within
# the same Python process. Stages run as subprocesses so each subprocess
# pays the cost once.

_sql_parser_agent = None
_enrichment_agent = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_sql_parser_agent():
    global _sql_parser_agent
    if _sql_parser_agent is None:
        from lineage.agents.sql_parser import build as build_sql_parser
        _sql_parser_agent = build_sql_parser()
    return _sql_parser_agent


def _get_enrichment_agent():
    global _enrichment_agent
    if _enrichment_agent is None:
        from lineage.agents.enrichment import build as build_enrichment
        _enrichment_agent = build_enrichment()
    return _enrichment_agent


def _agent_response_to_text(result) -> str:
    """Pull the last AI message text out of a LangGraph create_react_agent invoke result."""
    try:
        messages = result.get("messages", []) if isinstance(result, dict) else []
        for msg in reversed(messages):
            content = getattr(msg, "content", None)
            if content is None and isinstance(msg, dict):
                content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        text = blk.get("text", "")
                        if text.strip():
                            return text
    except Exception:
        pass
    return str(result)


def _try_parse_map_json(text: str) -> Optional[dict]:
    """Best-effort JSON extraction from a model response."""
    s = (text or "").strip()
    if s.startswith("OUT_OF_SCOPE:"):
        return None
    if s.startswith("```"):
        # Strip ```json ... ``` fences if the model added them despite instructions
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
        if s.endswith("```"):
            s = s[:-3].strip()
    # Find first '{' and last '}' to tolerate any leading prose
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return None


def _map_from_dict(d: dict, fallback: ColumnLineageMap) -> ColumnLineageMap:
    """Reconstitute a ColumnLineageMap from agent JSON, falling back per field."""
    sources_raw = d.get("sources") or []
    sources: List[ColumnLineageEdge] = []
    for src in sources_raw:
        if not isinstance(src, dict):
            continue
        try:
            tt_raw = (src.get("transform_type") or "TRANSFORM").upper()
            tt = TransformType(tt_raw) if tt_raw in TransformType.__members__ else TransformType.TRANSFORM
        except Exception:
            tt = TransformType.TRANSFORM
        sources.append(
            ColumnLineageEdge(
                source_table=str(src.get("source_table", "")),
                source_column=str(src.get("source_column", "")),
                expression=str(src.get("expression", fallback.full_expression)),
                transform_type=tt,
            )
        )

    return ColumnLineageMap(
        target_table=str(d.get("target_table", fallback.target_table)),
        target_column=str(d.get("target_column", fallback.target_column)),
        sources=sources or fallback.sources,
        full_expression=str(d.get("full_expression", fallback.full_expression)),
        ambiguous=bool(d.get("ambiguous", False)),
        semantic_description=str(d.get("semantic_description", "")),
        confidence=float(d.get("confidence", fallback.confidence)),
        data_type=str(d.get("data_type", fallback.data_type)),
        sql_hash=str(d.get("sql_hash", fallback.sql_hash)),
    )


def _resolve_ambiguous(
    column_map: ColumnLineageMap,
    sql: str,
    target_table: str,
    log,
) -> ColumnLineageMap:
    """Invoke the sql_parser agent on a single ambiguous map. On any error, fall back to the original map at confidence=0.5."""
    try:
        agent = _get_sql_parser_agent()
        user_payload = json.dumps(
            {
                "column_map": {
                    "target_table": column_map.target_table,
                    "target_column": column_map.target_column,
                    "sources": [
                        {
                            "source_table": s.source_table,
                            "source_column": s.source_column,
                            "expression": s.expression,
                            "transform_type": s.transform_type.value,
                        }
                        for s in column_map.sources
                    ],
                    "full_expression": column_map.full_expression,
                    "ambiguous": column_map.ambiguous,
                    "confidence": column_map.confidence,
                    "sql_hash": column_map.sql_hash,
                },
                "full_sql": sql,
                "target_table": target_table,
            },
            ensure_ascii=False,
        )
        result = agent.invoke({"messages": [{"role": "user", "content": user_payload}]})
        text = _agent_response_to_text(result)
        parsed = _try_parse_map_json(text)
        if parsed is None:
            log.warning(
                "lineage_agent_unparseable",
                target_column=column_map.target_column,
                response_preview=text[:200],
            )
            column_map.confidence = 0.5
            return column_map
        return _map_from_dict(parsed, fallback=column_map)
    except Exception as exc:
        log.warning(
            "lineage_agent_failed",
            target_column=column_map.target_column,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        column_map.confidence = 0.5
        return column_map


def _enrich_column(column_map: ColumnLineageMap, log) -> None:
    """Invoke the enrichment agent on one resolved column map. Failures are logged and swallowed."""
    try:
        agent = _get_enrichment_agent()
        user_payload = json.dumps(
            {
                "target_table": column_map.target_table,
                "target_column": column_map.target_column,
                "full_expression": column_map.full_expression,
                "transform_type": (
                    column_map.sources[0].transform_type.value
                    if column_map.sources else TransformType.AMBIGUOUS.value
                ),
                "confidence": column_map.confidence,
                "sources": [
                    {"source_table": s.source_table, "source_column": s.source_column}
                    for s in column_map.sources
                ],
            },
            ensure_ascii=False,
        )
        agent.invoke({"messages": [{"role": "user", "content": user_payload}]})
    except Exception as exc:
        log.warning(
            "lineage_enrichment_failed",
            target_column=column_map.target_column,
            error=str(exc),
            error_type=type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_and_ingest(
    stage: str,
    run_id: str,
    sql: str,
    target_table: str,
    source_tables: List[str],
    depends_on_stages: List[str],
    transform_type: str,
    output_row_count: int,
    duration_ms: int,
    con: Optional[duckdb.DuckDBPyConnection] = None,
    skip_enrichment: bool = False,
) -> StageLineageManifest:
    """Run sql_parser → ambiguous-resolver → graph ingest → enrichment.

    Never raises. On any failure, logs `lineage_manifest_exception` and
    returns a partial manifest with whatever was salvaged.
    """
    log = configure_logging(run_id, "manifest_builder")
    started = time.perf_counter()
    ts = _utc_now_iso()

    column_maps: List[ColumnLineageMap] = []
    ambiguous_count = 0
    agent_calls_made = 0
    enrichment_calls_made = 0
    sql_hash_value = hash_sql(sql)
    skip_enrichment = bool(skip_enrichment) or _enrichment_disabled_via_env()

    # 1. Deterministic parse
    try:
        column_maps = sql_parse(sql, target_table=target_table, source_tables=source_tables)
    except Exception as exc:
        log.error(
            "lineage_sql_parse_failed",
            stage=stage,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        column_maps = []

    # 2. Resolve ambiguous maps via the sql_parser agent. Each invocation is
    #    isolated — one failure does not block the rest.
    skip_resolution = _resolution_disabled_via_env()
    resolved: List[ColumnLineageMap] = []
    for cm in column_maps:
        if cm.ambiguous:
            ambiguous_count += 1
            if not skip_resolution:
                try:
                    cm = _resolve_ambiguous(cm, sql=sql, target_table=target_table, log=log)
                    agent_calls_made += 1
                except Exception as exc:
                    log.warning(
                        "lineage_agent_failed",
                        target_column=cm.target_column,
                        error=str(exc),
                    )
                    cm.confidence = 0.5
        # Always make sure the per-map sql_hash mirrors the stage SQL hash.
        if not cm.sql_hash:
            cm.sql_hash = sql_hash_value
        resolved.append(cm)

    manifest = StageLineageManifest(
        stage=stage,
        run_id=run_id,
        ts=ts,
        target_table=target_table,
        source_tables=list(source_tables),
        depends_on_stages=list(depends_on_stages),
        transform_type=transform_type,
        column_maps=resolved,
        sql_hash=sql_hash_value,
        output_row_count=int(output_row_count),
        duration_ms=int(duration_ms),
    )

    # 3. Graph ingest — wrapped so failure here doesn't crash the stage.
    builder = None
    try:
        builder = GraphBuilder(run_id=run_id)
        try:
            builder.ingest_stage_manifest(manifest)
        finally:
            builder.close()
    except Exception as exc:
        log.error(
            "lineage_manifest_exception",
            stage=stage,
            phase="graph_ingest",
            error=str(exc),
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
        )
        # Fall through — emit the summary log so the run still has a paper trail.

    # Drop our reference and force a GC pass so the Kuzu Database file lock is
    # released BEFORE the enrichment loop starts opening its own connections.
    builder = None
    import gc as _gc
    _gc.collect()

    # 4. Enrichment — only on resolved maps with confidence >= 0.5.
    if not skip_enrichment:
        for cm in resolved:
            if cm.confidence < 0.5:
                continue
            try:
                _enrich_column(cm, log)
                enrichment_calls_made += 1
            except Exception as exc:
                log.warning(
                    "lineage_enrichment_failed",
                    target_column=cm.target_column,
                    error=str(exc),
                )

    log.info(
        "lineage_manifest_complete",
        stage=stage,
        target_table=target_table,
        total_columns=len(resolved),
        ambiguous_count=ambiguous_count,
        agent_calls_made=agent_calls_made,
        enrichment_calls_made=enrichment_calls_made,
        skipped_enrichment=skip_enrichment,
        sql_hash_prefix=sql_hash_value[:12],
        total_duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return manifest


def _enrichment_disabled_via_env() -> bool:
    import os
    # Master kill-switch for all Bedrock-backed agents. Default ON now that
    # .env supplies AWS creds. Set TRACEX_LINEAGE_AGENTS=off to disable.
    master = os.environ.get("TRACEX_LINEAGE_AGENTS", "on").strip().lower()
    if master in {"0", "false", "no", "off", "disabled"}:
        return True
    val = os.environ.get("TRACEX_LINEAGE_ENRICHMENT", "").strip().lower()
    return val in {"0", "false", "no", "off", "disabled"}


def _resolution_disabled_via_env() -> bool:
    import os
    master = os.environ.get("TRACEX_LINEAGE_AGENTS", "on").strip().lower()
    if master in {"0", "false", "no", "off", "disabled"}:
        return True
    val = os.environ.get("TRACEX_LINEAGE_RESOLVE", "").strip().lower()
    return val in {"0", "false", "no", "off", "disabled"}


__all__ = ["build_and_ingest"]
