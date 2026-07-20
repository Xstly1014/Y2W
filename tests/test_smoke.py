"""Smoke test — import every public module to catch syntax / import errors.

If this test passes, the project's import graph is healthy. It does NOT
exercise behavior; that's what the other test_*.py files are for.
"""
from __future__ import annotations


def test_config_imports():
    from config import settings
    assert settings is not None
    assert settings.llm_model_name  # has a default


def test_core_imports():
    from core.agent import build_agent
    from core.llm import build_llm
    assert callable(build_agent)
    assert callable(build_llm)


def test_tools_imports():
    from tools import get_builtin_tools
    tools = get_builtin_tools()
    assert isinstance(tools, list)
    assert len(tools) >= 3  # calculator + time + search


def test_memory_imports():
    from memory.short_term import ShortTermMemory
    from memory.long_term import LongTermMemory
    assert ShortTermMemory is not None
    assert LongTermMemory is not None


def test_rag_imports():
    from rag import (
        build_embeddings, build_vectorstore, load_vectorstore,
        Indexer, build_retriever, build_rag_tool,
        ingest_file, ingest_paths, chunk_text,
    )
    # All callable / class-like
    for obj in [build_embeddings, build_vectorstore, load_vectorstore,
                build_retriever, build_rag_tool,
                ingest_file, ingest_paths, chunk_text]:
        assert callable(obj)
    assert isinstance(Indexer, type)


def test_skills_imports():
    from skills.base import Skill
    from skills.summarize import SummarizeSkill
    assert isinstance(Skill, type)
    assert isinstance(SummarizeSkill, type)


def test_mcp_imports():
    from mcp_integration.client import MCPClient
    assert isinstance(MCPClient, type)


def test_evaluation_imports():
    from evaluation.metrics import exact_match, contains, llm_judge
    from evaluation.runner import EvalRunner, EvalCase, EvalResult
    assert callable(exact_match)
    assert callable(contains)
    assert callable(llm_judge)
    assert isinstance(EvalRunner, type)
    assert isinstance(EvalCase, type)
    assert isinstance(EvalResult, type)


def test_flywheel_imports():
    from data_flywheel.storage import JsonlStore
    from data_flywheel.collector import BadCaseCollector
    assert isinstance(JsonlStore, type)
    assert isinstance(BadCaseCollector, type)


def test_post_training_imports():
    from post_training.pipeline import PostTrainingPipeline
    assert isinstance(PostTrainingPipeline, type)


def test_observability_imports():
    from observability.cost import extract_usage, estimate_cost, PRICE_TABLE
    from observability.tracing import TraceRecorder, trace_invocation
    assert callable(extract_usage)
    assert callable(estimate_cost)
    assert isinstance(PRICE_TABLE, dict)
    assert isinstance(TraceRecorder, type)
    assert callable(trace_invocation)


def test_main_imports():
    import main
    assert hasattr(main, "main")
    assert hasattr(main, "build_default_agent")
