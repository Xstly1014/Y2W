"""LLM factory.

Single entry point for building a chat model. Supports any OpenAI-compatible
endpoint (OpenAI / DeepSeek / Moonshot / Zhipu etc.). Swap providers here
without touching the rest of the codebase.
"""
from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from config import settings

logger = logging.getLogger(__name__)


def build_llm(
    *,
    model: str | None = None,
    temperature: float | None = None,
    streaming: bool = False,
) -> BaseChatModel:
    """Build a chat model from project settings.

    When `LLM_MOCK=1` is set in .env (or `settings.llm_mock = True`),
    returns a `MockChatModel` that drives the ReAct sub-agents with
    keyword-based tool-call decisions. Used for offline demos when the
    upstream LLM API token is unavailable / quota-exceeded (e.g. 401).
    The mock still drives the real ReAct tool loop, so end-to-end UX
    is identical to a live LLM.

    Args:
        model: Override the model name from settings.
        temperature: Override the temperature from settings.
        streaming: Whether to stream tokens.

    Raises:
        ValueError: If `OPENAI_API_KEY` is not set AND `LLM_MOCK` is
            not enabled. We fail fast here rather than letting every
            downstream agent invocation fail with an opaque 401 from
            the provider.
    """
    # --- Mock LLM path ------------------------------------------------
    if getattr(settings, "llm_mock", False):
        from core.mock_llm import MockChatModel

        logger.warning(
            "LLM_MOCK=1 -> using MockChatModel (no upstream API calls). "
            "Set LLM_MOCK=0 in .env to use the real LLM."
        )
        return MockChatModel()

    # --- Real LLM path ------------------------------------------------
    if not settings.openai_api_key:
        raise ValueError(
            "OPENAI_API_KEY is not set. Configure it in .env "
            "(see .env.example) before building the LLM — or set "
            "LLM_MOCK=1 to use the offline MockChatModel for demos."
        )
    return ChatOpenAI(
        model=model or settings.llm_model_name,
        temperature=temperature if temperature is not None else settings.llm_temperature,
        api_key=settings.openai_api_key,
        base_url=settings.openai_api_base,
        streaming=streaming,
    )
