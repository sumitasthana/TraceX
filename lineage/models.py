"""Dataclasses describing what the parser extracts from a pipeline JSONL log.

Two parallel manifest formats live here:

  * `LineageManifest` / `ParsedRun` — the original Option-B JSONL-replay format.
    Stages emit a `transform_start` event with derived_columns; `parser.parse_run`
    produces `ParsedRun`; `graph_builder.ingest_run` reads it.
  * `StageLineageManifest` — the deep column-lineage format produced live by
    `manifest_builder.build_and_ingest` after a stage's SQL executes. Carries
    one `ColumnLineageMap` per output column with sqlglot-resolved sources,
    transform classification, agent-resolved descriptions, and a confidence
    score. `graph_builder.ingest_stage_manifest` reads it.

The original models stay intact so existing JSONL replay continues to work
unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List


# ---------------------------------------------------------------------------
# Original Option-B replay models — DO NOT REMOVE.
# ---------------------------------------------------------------------------

@dataclass
class LineageManifest:
    stage: str
    run_id: str
    ts: str
    target_table: str
    source_tables: List[str]
    depends_on_stages: List[str]
    transform_type: str
    derived_columns: Dict[str, str]


@dataclass
class StageMetrics:
    stage: str
    run_id: str
    ts: str
    output_row_count: int
    duration_ms: int


@dataclass
class ParsedRun:
    run_id: str
    manifests: List[LineageManifest] = field(default_factory=list)
    metrics: Dict[str, StageMetrics] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Deep column-lineage models — produced by the new manifest_builder.
# ---------------------------------------------------------------------------

class TransformType(str, Enum):
    PASSTHROUGH = "PASSTHROUGH"
    RENAME = "RENAME"
    TRANSFORM = "TRANSFORM"
    AGGREGATE = "AGGREGATE"
    WINDOW = "WINDOW"
    CONSTANT = "CONSTANT"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass
class ColumnLineageEdge:
    """One source-side reference contributing to an output column."""
    source_table: str
    source_column: str
    expression: str
    transform_type: TransformType


@dataclass
class ColumnLineageMap:
    """Full lineage for one output column of one stage.

    `source` and `review_state` carry provenance through the pipeline:
      source ∈ {catalog, sqlglot, agent_inferred, unresolved}
      review_state ∈ {ratified, pending_review}
    Both default to the safest (unknown / pending) values; merge_lineage
    promotes them per the precedence table.
    """
    target_table: str
    target_column: str
    sources: List[ColumnLineageEdge]
    full_expression: str
    ambiguous: bool
    semantic_description: str = ""
    confidence: float = 1.0
    data_type: str = ""
    sql_hash: str = ""
    source: str = "unresolved"
    review_state: str = "pending_review"


@dataclass
class StageLineageManifest:
    """Replaces the hand-written Option-B dict for the deep-lineage path."""
    stage: str
    run_id: str
    ts: str
    target_table: str
    source_tables: List[str]
    depends_on_stages: List[str]
    transform_type: str
    column_maps: List[ColumnLineageMap]
    sql_hash: str
    output_row_count: int
    duration_ms: int
