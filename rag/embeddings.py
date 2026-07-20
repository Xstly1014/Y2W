"""Embedding model factory.

Two providers are supported out of the box:
  * `openai` — OpenAI-compatible embeddings API.
  * `local`  — sentence-transformers model loaded in-process (good for
               offline runs and Chinese text via `bge-small-zh-v1.5`).
"""
from __future__ import annotations

from typing import Literal

from langchain_core.embeddings import Embeddings

from config import settings


def build_embeddings(
    provider: Literal["openai", "local"] | None = None,
    model_name: str | None = None,
) -> Embeddings:
    provider = provider or settings.embedding_provider
    model_name = model_name or settings.embedding_model_name

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=model_name,
            api_key=settings.openai_api_key,
            base_url=settings.openai_api_base,
        )

    if provider == "local":
        from langchain_community.embeddings import HuggingFaceEmbeddings

        # Allow shorthand "local:bge-small-zh-v1.5" in env var.
        if model_name.startswith("local:"):
            model_name = model_name.split(":", 1)[1]
        return HuggingFaceEmbeddings(model_name=model_name)

    raise ValueError(f"Unknown embedding provider: {provider}")
