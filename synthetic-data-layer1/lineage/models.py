"""Dataclasses describing what the parser extracts from a pipeline JSONL log."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


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
