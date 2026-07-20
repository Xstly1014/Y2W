"""LLM response cache — avoid re-invoking the LLM for identical prompts.

Two strategies:
1. In-memory LRU cache (default, fast, process-local)
2. Optional disk cache (persistent across restarts, JSON Lines)

Cache key = sha256(model + temperature + system_hash + user_hash).
Cache value = the LLM's string response.

Usage:
    cache = PromptCache()
    cached = cache.get(model, temperature, system, user)
    if cached is not None:
        return cached
    response = llm.invoke(...)
    cache.set(model, temperature, system, user, response)
    return response
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _cache_key(
    model: str, temperature: float, system: str, user: str,
) -> str:
    """Stable cache key — same inputs always produce the same key."""
    raw = f"{model}|{temperature:.2f}|{_hash(system)}|{_hash(user)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PromptCache:
    """LRU cache for LLM responses.

    Args:
        max_size: max entries in memory (LRU eviction).
        disk_path: optional Path for persistent disk cache. Each line is
                   one JSON object: {key, model, system_hash, user_hash,
                   response, cached_at}.
    """

    def __init__(
        self,
        max_size: int = 256,
        disk_path: Path | None = None,
    ) -> None:
        self._max_size = max_size
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._disk_path = Path(disk_path) if disk_path else None
        self._lock = Lock()
        if self._disk_path:
            self._disk_path.parent.mkdir(parents=True, exist_ok=True)
            self._disk_path.touch(exist_ok=True)
            self._load_disk()

    def get(
        self, model: str, temperature: float, system: str, user: str,
    ) -> str | None:
        """Return cached response, or None on miss. Updates LRU order."""
        key = _cache_key(model, temperature, system, user)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                entry = self._cache[key]
                return entry["response"]
        return None

    def set(
        self, model: str, temperature: float, system: str, user: str,
        response: str, *, metadata: dict | None = None,
    ) -> None:
        """Cache a response. Evicts oldest if over max_size."""
        key = _cache_key(model, temperature, system, user)
        entry = {
            "key": key,
            "model": model,
            "temperature": temperature,
            "system_hash": _hash(system),
            "user_hash": _hash(user),
            "response": response,
            "metadata": metadata or {},
            "cached_at": self._now_iso(),
        }
        with self._lock:
            self._cache[key] = entry
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)
            if self._disk_path:
                self._append_disk(entry)

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        with self._lock:
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "disk_enabled": self._disk_path is not None,
                "disk_path": str(self._disk_path) if self._disk_path else None,
            }

    def clear(self) -> int:
        """Clear all entries. Returns count cleared."""
        with self._lock:
            n = len(self._cache)
            self._cache.clear()
            if self._disk_path and self._disk_path.exists():
                self._disk_path.write_text("", encoding="utf-8")
            return n

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    def _load_disk(self) -> None:
        """Load existing disk cache into memory on startup."""
        if not self._disk_path or not self._disk_path.exists():
            return
        try:
            with self._disk_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        key = entry.get("key")
                        if key and key not in self._cache:
                            self._cache[key] = entry
                    except json.JSONDecodeError:
                        continue
            # Trim to max_size after loading.
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)
            logger.info(
                "loaded %d entries from disk cache %s",
                len(self._cache), self._disk_path,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to load disk cache: %s", exc)

    def _append_disk(self, entry: dict[str, Any]) -> None:
        if not self._disk_path:
            return
        try:
            with self._disk_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to append disk cache: %s", exc)


# Module-level singleton (lazy-initialized).
_SINGLETON: PromptCache | None = None
_SINGLETON_LOCK = Lock()


def get_prompt_cache() -> PromptCache:
    """Return the process-wide PromptCache singleton.

    Defaults: max_size=256, no disk cache.
    Configure via settings.llm_prompt_cache_* if needed (see settings.py).
    """
    global _SINGLETON
    if _SINGLETON is not None:
        return _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            from config import settings

            _SINGLETON = PromptCache(
                max_size=getattr(settings, "llm_prompt_cache_max_size", 256),
                disk_path=getattr(settings, "llm_prompt_cache_disk_path", None),
            )
    return _SINGLETON


def cached_invoke(
    llm: Any,
    system: str,
    user: str,
    *,
    model: str | None = None,
    temperature: float | None = None,
    cache: PromptCache | None = None,
) -> str:
    """Invoke LLM with cache lookup/store.

    Skips cache when temperature > 0 (non-deterministic outputs shouldn't cache).
    """
    cache = cache or get_prompt_cache()
    # Resolve model/temperature from the llm object if not given.
    model = model or getattr(llm, "model_name", "") or getattr(llm, "model", "")
    temperature = (
        temperature
        if temperature is not None
        else float(getattr(llm, "temperature", 0.0) or 0.0)
    )
    # Skip cache for non-deterministic calls.
    if temperature > 0:
        from langchain_core.messages import HumanMessage, SystemMessage

        return llm.invoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        ).content
    cached = cache.get(model, temperature, system, user)
    if cached is not None:
        logger.debug("prompt cache hit: model=%s user_hash=%s", model, _hash(user))
        return cached
    from langchain_core.messages import HumanMessage, SystemMessage

    response = llm.invoke(
        [SystemMessage(content=system), HumanMessage(content=user)]
    ).content
    cache.set(model, temperature, system, user, response)
    return response


async def cached_ainvoke(
    llm: Any,
    system: str,
    user: str,
    *,
    model: str | None = None,
    temperature: float | None = None,
    cache: PromptCache | None = None,
) -> str:
    """Async version of `cached_invoke` — uses `await llm.ainvoke(...)`.

    Cache lookup/store is synchronous (LRU dict + optional disk append),
    so the cache itself doesn't await — only the LLM miss path awaits.

    Used by the router node (`core/multi_agent.py`) so the router's LLM
    calls go through the cache when `LLM_PROMPT_CACHE_ENABLED=true`.
    See `optimization_logs/2026-07-20/issues-and-fixes.md` P1-6.
    """
    cache = cache or get_prompt_cache()
    model = model or getattr(llm, "model_name", "") or getattr(llm, "model", "")
    temperature = (
        temperature
        if temperature is not None
        else float(getattr(llm, "temperature", 0.0) or 0.0)
    )
    # Skip cache for non-deterministic calls.
    if temperature > 0:
        from langchain_core.messages import HumanMessage, SystemMessage

        resp = await llm.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )
        return resp.content
    cached = cache.get(model, temperature, system, user)
    if cached is not None:
        logger.debug("prompt cache hit (async): model=%s user_hash=%s", model, _hash(user))
        return cached
    from langchain_core.messages import HumanMessage, SystemMessage

    resp = await llm.ainvoke(
        [SystemMessage(content=system), HumanMessage(content=user)]
    )
    response = resp.content
    cache.set(model, temperature, system, user, response)
    return response
