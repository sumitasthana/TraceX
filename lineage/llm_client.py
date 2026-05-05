"""LLM client factory for TraceX lineage agents.

Mirrors the shape of ReconX `llm/client.py`: AWS Bedrock via `ChatBedrock`,
temperature=0, `max_retries=3`. TraceX has no cheaper tier yet — `get_llm`
and `get_fast_llm` both return the same model.

Configuration:

    TRACEX_BEDROCK_REGION  — AWS region for Bedrock runtime (default: us-east-1)
    TRACEX_BEDROCK_MODEL   — model id (default: us.anthropic.claude-sonnet-4-6)
    TRACEX_BEDROCK_MAX_TOK — max output tokens (default: 4096)

Credentials come from the AWS SDK chain (env vars, profile, instance role).
"""
from __future__ import annotations

import os
from functools import lru_cache

import boto3
import structlog
from langchain_aws import ChatBedrock

log = structlog.get_logger().bind(module="lineage.llm_client")

DEFAULT_REGION = "us-east-1"
DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096


def _bedrock_region() -> str:
    return (
        os.environ.get("TRACEX_BEDROCK_REGION")
        or os.environ.get("AWS_BEDROCK_REGION")
        or os.environ.get("AWS_REGION")
        or DEFAULT_REGION
    )


def _bedrock_model() -> str:
    return (
        os.environ.get("TRACEX_BEDROCK_MODEL")
        or os.environ.get("AWS_BEDROCK_MODEL")
        or DEFAULT_MODEL
    )


def _max_tokens() -> int:
    try:
        return int(os.environ.get("TRACEX_BEDROCK_MAX_TOK", DEFAULT_MAX_TOKENS))
    except ValueError:
        return DEFAULT_MAX_TOKENS


@lru_cache(maxsize=1)
def _bedrock_client():
    region = _bedrock_region()
    log.info("bedrock_client_init", region=region)
    return boto3.client("bedrock-runtime", region_name=region)


def _build_chat() -> ChatBedrock:
    model_id = _bedrock_model()
    return ChatBedrock(
        model_id=model_id,
        client=_bedrock_client(),
        model_kwargs={"temperature": 0, "max_tokens": _max_tokens()},
        max_retries=3,
    )


def get_llm() -> ChatBedrock:
    """Return the primary LLM. TraceX uses one tier, so identical to get_fast_llm."""
    return _build_chat()


def get_fast_llm() -> ChatBedrock:
    """Return the specialist-tier LLM. Same model as get_llm in TraceX (no cheaper tier yet)."""
    return _build_chat()
