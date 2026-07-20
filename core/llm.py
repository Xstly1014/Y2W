"""LLM factory.

Single entry point for building a chat model. Supports any OpenAI-compatible
endpoint (OpenAI / DeepSeek / Moonshot / Zhipu etc.). Swap providers here
without touching the rest of the codebase.
"""
from __future__ import annotations

import logging

from langchain_openai import ChatOpenAI

from config import settings

logger = logging.getLogger(__name__)


def build_llm(
    *,
    model: str | None = None,
    temperature: float | None = None,
    streaming: bool = False,
) -> ChatOpenAI:
    """Build a ChatOpenAI instance from project settings.

    Args:
        model: Override the model name from settings.
        temperature: Override the temperature from settings.
        streaming: Whether to stream tokens.

    Raises:
        ValueError: If ``OPENAI_API_KEY`` is not set. We fail fast here
            rather than letting every downstream agent invocation fail
            with an opaque 401 from the provider.
    """
    if not settings.openai_api_key:
        raise ValueError(
            "OPENAI_API_KEY is not set. Configure it in .env "
            "(see .env.example) before building the LLM."
        )
    return ChatOpenAI(
        model=model or settings.llm_model_name,
        temperature=temperature if temperature is not None else settings.llm_temperature,
        api_key=settings.openai_api_key,
        base_url=settings.openai_api_base,
        streaming=streaming,
    )
