"""Tests for the inference acceleration modules:
- core.prompt_cache (PromptCache + cached_invoke)
- core.batch_inference (batch_invoke, abatch_invoke, batch_embed)
- scripts.eval_inference_speed (build_report with --skip-llm)

All tests are offline — they use fakes instead of real LLM / embedding calls.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace


# --------------------------------------------------------------------------- #
# PromptCache
# --------------------------------------------------------------------------- #
def test_prompt_cache_basic_set_get():
    from core.prompt_cache import PromptCache

    c = PromptCache(max_size=4)
    assert c.get("m", 0.0, "sys", "hi") is None  # miss
    c.set("m", 0.0, "sys", "hi", "hello")
    assert c.get("m", 0.0, "sys", "hi") == "hello"  # hit


def test_prompt_cache_key_depends_on_all_inputs():
    """Different model / temperature / system / user → different cache slot."""
    from core.prompt_cache import PromptCache

    c = PromptCache()
    c.set("m", 0.0, "sys", "hi", "hello")
    assert c.get("m2", 0.0, "sys", "hi") is None
    assert c.get("m", 0.5, "sys", "hi") is None
    assert c.get("m", 0.0, "sys2", "hi") is None
    assert c.get("m", 0.0, "sys", "hi2") is None
    # Original still hits.
    assert c.get("m", 0.0, "sys", "hi") == "hello"


def test_prompt_cache_lru_eviction():
    from core.prompt_cache import PromptCache

    c = PromptCache(max_size=2)
    c.set("m", 0.0, "s", "u1", "r1")
    c.set("m", 0.0, "s", "u2", "r2")
    c.set("m", 0.0, "s", "u3", "r3")  # evicts u1 (oldest)
    assert c.get("m", 0.0, "s", "u1") is None
    assert c.get("m", 0.0, "s", "u2") == "r2"
    assert c.get("m", 0.0, "s", "u3") == "r3"


def test_prompt_cache_lru_updates_order_on_get():
    """Accessing an entry should keep it from being evicted."""
    from core.prompt_cache import PromptCache

    c = PromptCache(max_size=2)
    c.set("m", 0.0, "s", "u1", "r1")
    c.set("m", 0.0, "s", "u2", "r2")
    # Touch u1 → u2 is now oldest.
    assert c.get("m", 0.0, "s", "u1") == "r1"
    c.set("m", 0.0, "s", "u3", "r3")  # evicts u2
    assert c.get("m", 0.0, "s", "u2") is None
    assert c.get("m", 0.0, "s", "u1") == "r1"
    assert c.get("m", 0.0, "s", "u3") == "r3"


def test_prompt_cache_overwrite_existing_key():
    from core.prompt_cache import PromptCache

    c = PromptCache()
    c.set("m", 0.0, "s", "u", "old")
    c.set("m", 0.0, "s", "u", "new")
    assert c.get("m", 0.0, "s", "u") == "new"
    assert c.stats()["size"] == 1


def test_prompt_cache_stats_and_clear():
    from core.prompt_cache import PromptCache

    c = PromptCache(max_size=10)
    assert c.stats()["size"] == 0
    assert c.stats()["max_size"] == 10
    assert c.stats()["disk_enabled"] is False
    c.set("m", 0.0, "s", "u", "r")
    assert c.stats()["size"] == 1
    n = c.clear()
    assert n == 1
    assert c.stats()["size"] == 0


def test_prompt_cache_disk_round_trip(tmp_path: Path):
    from core.prompt_cache import PromptCache

    disk = tmp_path / "cache.jsonl"
    c1 = PromptCache(max_size=10, disk_path=disk)
    c1.set("m", 0.0, "s", "u", "hello-disk")
    # Disk file should now have one line.
    lines = [l for l in disk.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["response"] == "hello-disk"
    assert entry["model"] == "m"

    # New instance loads from disk.
    c2 = PromptCache(max_size=10, disk_path=disk)
    assert c2.get("m", 0.0, "s", "u") == "hello-disk"


def test_prompt_cache_disk_clear_wipes_file(tmp_path: Path):
    from core.prompt_cache import PromptCache

    disk = tmp_path / "cache.jsonl"
    c = PromptCache(disk_path=disk)
    c.set("m", 0.0, "s", "u", "r")
    assert disk.read_text(encoding="utf-8").strip() != ""
    c.clear()
    assert disk.read_text(encoding="utf-8").strip() == ""


def test_prompt_cache_disk_skips_corrupt_lines(tmp_path: Path):
    """A corrupt JSON line must not crash load — just skip."""
    from core.prompt_cache import PromptCache

    disk = tmp_path / "cache.jsonl"
    disk.write_text("not json\n{\"key\": \"k1\", \"response\": \"r1\"}\n", encoding="utf-8")
    c = PromptCache(disk_path=disk)
    # The valid entry loaded; corrupt skipped. We can't query it without the
    # full key, but the load itself must not raise.
    assert c.stats()["size"] == 1


def test_prompt_cache_metadata_stored(tmp_path: Path):
    from core.prompt_cache import PromptCache

    c = PromptCache()
    c.set("m", 0.0, "s", "u", "r", metadata={"latency_ms": 120.0})
    # Metadata is internal but should not break get.
    assert c.get("m", 0.0, "s", "u") == "r"


# --------------------------------------------------------------------------- #
# cached_invoke
# --------------------------------------------------------------------------- #
class _FakeLLM:
    """Records every invoke() call and returns a canned response."""

    def __init__(self, *, model: str = "fake-model", temperature: float = 0.0):
        self.model_name = model
        self.temperature = temperature
        self.calls: list = []
        self._response = "fake-response"

    def invoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=self._response)


def test_cached_invoke_skips_cache_when_temperature_positive():
    from core.prompt_cache import PromptCache, cached_invoke

    cache = PromptCache()
    llm = _FakeLLM(temperature=0.7)
    out1 = cached_invoke(llm, "sys", "hi", cache=cache)
    out2 = cached_invoke(llm, "sys", "hi", cache=cache)
    assert out1 == "fake-response"
    assert out2 == "fake-response"
    # Non-deterministic → both calls hit the LLM.
    assert len(llm.calls) == 2
    # And nothing was cached.
    assert cache.stats()["size"] == 0


def test_cached_invoke_hits_cache_on_second_call():
    from core.prompt_cache import PromptCache, cached_invoke

    cache = PromptCache()
    llm = _FakeLLM(temperature=0.0)
    out1 = cached_invoke(llm, "sys", "hi", cache=cache)
    out2 = cached_invoke(llm, "sys", "hi", cache=cache)
    assert out1 == "fake-response"
    assert out2 == "fake-response"
    # Second call must be served from cache, not LLM.
    assert len(llm.calls) == 1
    assert cache.stats()["size"] == 1


def test_cached_invoke_resolves_model_and_temperature_from_llm():
    from core.prompt_cache import PromptCache, cached_invoke

    cache = PromptCache()
    llm = _FakeLLM(model="abc", temperature=0.0)
    cached_invoke(llm, "sys", "hi", cache=cache)
    # The cached entry should be retrievable using the same model/temp.
    assert cache.get("abc", 0.0, "sys", "hi") == "fake-response"


def test_cached_invoke_different_user_input_not_cached():
    from core.prompt_cache import PromptCache, cached_invoke

    cache = PromptCache()
    llm = _FakeLLM(temperature=0.0)
    cached_invoke(llm, "sys", "hi", cache=cache)
    cached_invoke(llm, "sys", "yo", cache=cache)
    assert len(llm.calls) == 2


# --------------------------------------------------------------------------- #
# batch_invoke
# --------------------------------------------------------------------------- #
def test_batch_invoke_preserves_order():
    from core.batch_inference import batch_invoke

    class SlowFakeLLM:
        def __init__(self):
            self.model_name = "fake"

        def invoke(self, messages):
            # Echo last human message content.
            last = messages[-1].content
            return SimpleNamespace(content=f"resp:{last}")

    llm = SlowFakeLLM()
    out = batch_invoke(llm, ["a", "b", "c"], max_workers=3)
    assert out == ["resp:a", "resp:b", "resp:c"]


def test_batch_invoke_empty_prompts():
    from core.batch_inference import batch_invoke

    class L:
        def invoke(self, m):
            raise AssertionError("should not be called")

    assert batch_invoke(L(), [], max_workers=4) == []


def test_batch_invoke_partial_failure_does_not_break_batch():
    from core.batch_inference import batch_invoke

    class FlakeyLLM:
        def __init__(self):
            self.model_name = "fake"

        def invoke(self, messages):
            content = messages[-1].content
            if content == "boom":
                raise RuntimeError("intentional boom")
            return SimpleNamespace(content=f"ok:{content}")

    out = batch_invoke(FlakeyLLM(), ["a", "boom", "c"], max_workers=3)
    assert out[0] == "ok:a"
    assert out[1].startswith("[batch error]")
    assert "intentional boom" in out[1]
    assert out[2] == "ok:c"


def test_batch_invoke_includes_system_message():
    """When system is provided, every call must receive a SystemMessage first."""
    from core.batch_inference import batch_invoke
    from langchain_core.messages import HumanMessage, SystemMessage

    class CaptureLLM:
        def __init__(self):
            self.seen: list = []

        def invoke(self, messages):
            self.seen.append(messages)
            return SimpleNamespace(content="ok")

    llm = CaptureLLM()
    batch_invoke(llm, ["a", "b"], system="be nice", max_workers=2)
    assert len(llm.seen) == 2
    for msgs in llm.seen:
        assert isinstance(msgs[0], SystemMessage)
        assert msgs[0].content == "be nice"
        assert isinstance(msgs[-1], HumanMessage)


# --------------------------------------------------------------------------- #
# abatch_invoke
# --------------------------------------------------------------------------- #
def test_abatch_invoke_preserves_order():
    from core.batch_inference import abatch_invoke

    class AsyncFakeLLM:
        def __init__(self):
            self.model_name = "fake"

        async def ainvoke(self, messages):
            content = messages[-1].content
            return SimpleNamespace(content=f"resp:{content}")

    llm = AsyncFakeLLM()
    out = asyncio.run(abatch_invoke(llm, ["a", "b", "c"], max_concurrency=3))
    assert out == ["resp:a", "resp:b", "resp:c"]


def test_abatch_invoke_falls_back_to_sync_invoke_in_thread():
    """LLM without ainvoke should still work via run_in_executor."""
    from core.batch_inference import abatch_invoke

    class SyncOnlyLLM:
        def __init__(self):
            self.model_name = "fake"

        def invoke(self, messages):
            return SimpleNamespace(content=f"sync:{messages[-1].content}")

    llm = SyncOnlyLLM()
    out = asyncio.run(abatch_invoke(llm, ["x", "y"], max_concurrency=2))
    assert out == ["sync:x", "sync:y"]


def test_abatch_invoke_empty():
    from core.batch_inference import abatch_invoke

    out = asyncio.run(abatch_invoke(_FakeLLM(), [], max_concurrency=2))
    assert out == []


def test_abatch_invoke_partial_failure():
    from core.batch_inference import abatch_invoke

    class FlakeyAsyncLLM:
        def __init__(self):
            self.model_name = "fake"

        async def ainvoke(self, messages):
            content = messages[-1].content
            if content == "fail":
                raise RuntimeError("async boom")
            return SimpleNamespace(content=f"ok:{content}")

    out = asyncio.run(abatch_invoke(FlakeyAsyncLLM(), ["a", "fail", "c"], max_concurrency=3))
    assert out[0] == "ok:a"
    assert out[1].startswith("[batch error]")
    assert out[2] == "ok:c"


# --------------------------------------------------------------------------- #
# batch_embed
# --------------------------------------------------------------------------- #
def test_batch_embed_empty():
    from core.batch_inference import batch_embed

    class E:
        def embed_documents(self, texts):
            raise AssertionError("should not be called")

    assert batch_embed(E(), []) == []


def test_batch_embed_uses_embed_documents_in_chunks():
    from core.batch_inference import batch_embed

    class FakeEmb:
        def __init__(self):
            self.calls: list[list[str]] = []

        def embed_documents(self, texts):
            self.calls.append(list(texts))
            return [[float(hash(t) % 100)] for t in texts]

        def embed_query(self, text):
            return [42.0]

    emb = FakeEmb()
    out = batch_embed(emb, ["a", "b", "c", "d", "e"], batch_size=2)
    # 5 texts with batch_size=2 → 3 chunks (2,2,1).
    assert len(emb.calls) == 3
    assert [len(c) for c in emb.calls] == [2, 2, 1]
    assert len(out) == 5


def test_batch_embed_falls_back_to_embed_query_on_not_implemented():
    """When embed_documents raises NotImplementedError, use embed_query."""
    from core.batch_inference import batch_embed

    class QueryOnlyEmb:
        def __init__(self):
            self.queries: list[str] = []

        def embed_documents(self, texts):
            raise NotImplementedError("no batch support")

        def embed_query(self, text):
            self.queries.append(text)
            return [float(len(text))]

    emb = QueryOnlyEmb()
    out = batch_embed(emb, ["aa", "bbb"], batch_size=64)
    assert out == [[2.0], [3.0]]
    assert emb.queries == ["aa", "bbb"]


# --------------------------------------------------------------------------- #
# scripts.eval_inference_speed (skip-llm mode)
# --------------------------------------------------------------------------- #
def test_eval_report_skip_llm_contains_required_sections():
    from scripts.eval_inference_speed import build_report

    report = build_report(skip_llm=True)
    # Required headers.
    assert "# 推理加速评估报告" in report
    assert "## 1. 当前 LLM 调用模式分析" in report
    assert "## 2. Prompt 缓存理论收益" in report
    assert "## 3. KV Cache 评估" in report
    assert "## 4. 批量推理收益" in report
    assert "## 5. 实测数据" in report
    assert "## 6. 推荐加速方案优先级" in report
    # Skip-llm mode banner.
    assert "--skip-llm 模式跳过实测" in report
    # Mentions the implemented modules.
    assert "core/batch_inference.batch_embed" in report
    assert "core/prompt_cache.PromptCache" in report


def test_eval_report_skip_llm_writes_file_via_main(tmp_path: Path):
    """End-to-end: run main() with --skip-llm and --out, assert file written."""
    import sys

    from scripts.eval_inference_speed import main

    out_file = tmp_path / "report.md"
    # main() reads sys.argv itself, so we patch argv directly.
    old_argv = sys.argv
    sys.argv = ["eval_inference_speed", "--skip-llm", "--out", str(out_file)]
    try:
        rc = main()
    finally:
        sys.argv = old_argv
    assert rc == 0
    assert out_file.exists()
    text = out_file.read_text(encoding="utf-8")
    assert "# 推理加速评估报告" in text


# --------------------------------------------------------------------------- #
# settings: inference acceleration fields exist
# --------------------------------------------------------------------------- #
def test_settings_exposes_inference_acceleration_fields():
    from config import settings

    assert hasattr(settings, "llm_prompt_cache_enabled")
    assert hasattr(settings, "llm_prompt_cache_max_size")
    assert hasattr(settings, "llm_prompt_cache_disk_path")
    assert hasattr(settings, "llm_batch_max_workers")
    # Defaults match the spec.
    assert settings.llm_prompt_cache_enabled is False
    assert settings.llm_prompt_cache_max_size == 256
    assert settings.llm_prompt_cache_disk_path is None
    assert settings.llm_batch_max_workers == 4
