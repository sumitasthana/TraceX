"""SQL Parser agent — resolves ambiguous columns sql_parser.py left for the LLM."""
from langgraph.prebuilt import create_react_agent

from lineage.llm_client import get_fast_llm
from lineage.prompt_loader import get_prompt_loader
from lineage.agents.sql_parser.tools import (
    TOOLS,
    get_table_schema,
    get_cte_definition,
    resolve_column_expression,
)

NAME = "sql_parser"


def build():
    """Build the SQL Parser specialist agent (stateless, fast model)."""
    loader = get_prompt_loader()
    return create_react_agent(
        model=get_fast_llm(),
        tools=TOOLS,
        prompt=loader.get_prompt(NAME),
    )


__all__ = [
    "build",
    "TOOLS",
    "NAME",
    "get_table_schema",
    "get_cte_definition",
    "resolve_column_expression",
]
