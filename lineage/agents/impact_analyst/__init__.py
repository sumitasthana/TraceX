"""Impact Analyst agent — answers 'what breaks if column X changes?'."""
from langgraph.prebuilt import create_react_agent

from lineage.llm_client import get_fast_llm
from lineage.prompt_loader import get_prompt_loader
from lineage.agents.impact_analyst.tools import (
    TOOLS,
    get_direct_downstream,
    get_full_downstream_chain,
    get_processes_reading_table,
    get_column_expression,
)

NAME = "impact_analyst"


def build():
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
    "get_direct_downstream",
    "get_full_downstream_chain",
    "get_processes_reading_table",
    "get_column_expression",
]
