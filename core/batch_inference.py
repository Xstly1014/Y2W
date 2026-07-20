"""Batch inference utilities — parallel LLM/embedding calls.

Three modes:
1. ThreadPoolExecutor for I/O-bound LLM calls (default, max_workers=4)
2. asyncio.gather for async LLM calls (when llm supports ainvoke)
3. OpenAI batch API wrapper (for offline batch jobs, optional)

Usage:
    from core.batch_inference import batch_invoke
    results = batch_invoke(llm, prompts, system="...", max_workers=4)
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)


def batch_invoke(
    llm: Any,
    prompts: list[str],
    *,
    system: str = "",
    max_workers: int = 4,
    timeout: float | None = None,
) -> list[str]:
    """Invoke LLM on a batch of prompts in parallel (sync).

    Returns results in the SAME ORDER as prompts. On individual failure,
    the corresponding slot is filled with f"[batch error] {exc}".
    """
    if not prompts:
        return []
    results: list[str | None] = [None] * len(prompts)

    def _invoke(idx: int, prompt: str) -> tuple[int, str]:
        messages: list[Any] = []
        if system:
            messages.append(SystemMessage(content=system))
        messages.append(HumanMessage(content=prompt))
        out = llm.invoke(messages).content
        return idx, out

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_invoke, i, p): i for i, p in enumerate(prompts)}
        for fut in as_completed(futures, timeout=timeout):
            i = futures[fut]
            try:
                idx, out = fut.result()
                results[idx] = out
            except Exception as exc:  # noqa: BLE001
                logger.warning("batch_invoke[%d] failed: %s", i, exc)
                results[i] = f"[batch error] {exc}"
    # Fill any remaining None (timeout) with error.
    for i, r in enumerate(results):
        if r is None:
            results[i] = "[batch error] timeout"
    return [r or "" for r in results]


async def abatch_invoke(
    llm: Any,
    prompts: list[str],
    *,
    system: str = "",
    max_concurrency: int = 4,
) -> list[str]:
    """Async batch invoke using asyncio.Semaphore.

    Requires the llm to support `ainvoke`. Falls back to sync `invoke`
    in a thread if not.
    """
    if not prompts:
        return []
    semaphore = asyncio.Semaphore(max_concurrency)
    results: list[str | None] = [None] * len(prompts)

    async def _invoke(idx: int, prompt: str) -> None:
        async with semaphore:
            messages: list[Any] = []
            if system:
                messages.append(SystemMessage(content=system))
            messages.append(HumanMessage(content=prompt))
            try:
                if hasattr(llm, "ainvoke"):
                    out = await llm.ainvoke(messages)
                    results[idx] = out.content
                else:
                    # Fallback: run sync invoke in thread.
                    loop = asyncio.get_running_loop()
                    out = await loop.run_in_executor(
                        None, lambda: llm.invoke(messages)
                    )
                    results[idx] = out.content
            except Exception as exc:  # noqa: BLE001
                logger.warning("abatch_invoke[%d] failed: %s", idx, exc)
                results[idx] = f"[batch error] {exc}"

    await asyncio.gather(*[_invoke(i, p) for i, p in enumerate(prompts)])
    return [r or "" for r in results]


def batch_embed(
    embeddings: Any,
    texts: list[str],
    *,
    batch_size: int = 64,
) -> list[list[float]]:
    """Batch embed texts, calling embed_documents in chunks.

    Most embedding providers (OpenAI, BGE) accept batches but cap at ~100.
    We chunk to batch_size and concat results.
    """
    if not texts:
        return []
    all_vecs: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        try:
            vecs = embeddings.embed_documents(chunk)
            all_vecs.extend(vecs)
        except NotImplementedError:
            # Fallback: embed one by one.
            for t in chunk:
                all_vecs.append(embeddings.embed_query(t))
    return all_vecs
