"""YAML prompt loader for TraceX lineage agents.

Mirrors the ReconX `chat/prompt_loader.py` interface: each agent owns its own
`prompt.yaml` next to its `__init__.py` (`lineage/agents/<name>/prompt.yaml`),
and the loader auto-discovers them all on instantiation. Same `get_prompt` /
`render` / `list_prompts` / `update_prompt` interface as ReconX.

Usage::

    loader = get_prompt_loader()
    prompt = loader.get_prompt("sql_parser")
    prompt = loader.render("sql_parser", config)
"""
from __future__ import annotations

import glob
import os
from datetime import date
from typing import Any

import structlog
import yaml

log = structlog.get_logger().bind(module="lineage.prompt_loader")

DEFAULT_AGENTS_DIR = os.path.join(os.path.dirname(__file__), "agents")


class PromptLoader:
    """Loads and manages YAML agent prompts under `lineage/agents/<name>/prompt.yaml`."""

    def __init__(self, agents_dir: str = DEFAULT_AGENTS_DIR):
        self.agents_dir = agents_dir
        self.prompts_dir = agents_dir
        self._cache: dict[str, dict[str, Any]] = {}
        self._load_all()

    def _load_all(self) -> None:
        self._cache.clear()

        project_root = os.path.dirname(os.path.dirname(self.agents_dir))
        scan_patterns = [
            os.path.join(self.agents_dir, "*", "prompt.yaml"),
        ]

        for pattern in scan_patterns:
            for path in glob.glob(pattern):
                try:
                    with open(path, encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    if not data or "name" not in data or "system_prompt" not in data:
                        log.warning("prompt_file_invalid", file=path)
                        continue
                    try:
                        data["_file"] = os.path.relpath(path, project_root)
                    except ValueError:
                        data["_file"] = os.path.basename(path)
                    data["_path"] = path
                    self._cache[data["name"]] = data
                    log.debug("prompt_loaded", name=data["name"], version=data.get("version"))
                except Exception as e:
                    log.error("prompt_load_error", file=path, error=str(e))

    def reload(self) -> None:
        self._load_all()

    def get_prompt(self, name: str) -> str:
        entry = self._cache.get(name)
        if not entry:
            raise KeyError(f"Prompt '{name}' not found. Available: {list(self._cache.keys())}")
        return entry["system_prompt"].strip()

    def render(self, name: str, config=None) -> str:
        prompt = self.get_prompt(name)
        entry = self._cache[name]

        context_tpl = entry.get("context_template", "")
        if context_tpl and config:
            try:
                context = context_tpl.format(
                    db_path=getattr(config, "db_path", "data/tracex_layer0.duckdb"),
                    graph_path=getattr(config, "graph_path", "data/tracex_graph"),
                    today=date.today().isoformat(),
                )
                prompt = prompt + "\n" + context.strip()
            except (KeyError, AttributeError) as e:
                log.warning("context_render_error", name=name, error=str(e))

        return prompt

    def get_metadata(self, name: str) -> dict[str, Any]:
        entry = self._cache.get(name)
        if not entry:
            raise KeyError(f"Prompt '{name}' not found.")
        return {
            "name": entry["name"],
            "version": entry.get("version", "unknown"),
            "description": entry.get("description", ""),
            "model_tier": entry.get("model_tier", "specialist"),
            "tags": entry.get("tags", []),
            "system_prompt": entry["system_prompt"].strip(),
            "context_template": entry.get("context_template", ""),
            "file": entry.get("_file", ""),
        }

    def list_prompts(self) -> list[dict[str, Any]]:
        return [self.get_metadata(name) for name in sorted(self._cache.keys())]

    def update_prompt(self, name: str, new_yaml: str) -> dict[str, Any]:
        data = yaml.safe_load(new_yaml)
        if not data or "name" not in data or "system_prompt" not in data:
            raise ValueError("Invalid YAML: must contain 'name' and 'system_prompt'")

        existing = self._cache.get(name, {})
        default_path = os.path.join(self.agents_dir, name, "prompt.yaml")
        path = existing.get("_path", default_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            f.write(new_yaml)

        self._load_all()
        log.info("prompt_updated", name=name, version=data.get("version"))
        return self.get_metadata(data["name"])


_loader: PromptLoader | None = None


def get_prompt_loader() -> PromptLoader:
    global _loader
    if _loader is None:
        _loader = PromptLoader()
    return _loader
