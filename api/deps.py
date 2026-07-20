"""Dependency injection: build once, reuse across requests.

- One Indexer (loads BGE embeddings once at startup)
- One agent factory: each tenant gets its own compiled multi-agent graph,
  because the agent's RAG collection is tenant-specific.
- BadCaseCollector is per-process (JSONL files are tenant-agnostic for now;
  tenant_id is stored in metadata).
"""
from __future__ import annotations

import logging
from functools import lru_cache
from threading import Lock
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from config import settings
from core.llm import build_llm
from core.multi_agent import build_multi_agent
from data_flywheel.collector import BadCaseCollector
from memory import LongTermMemory, build_memory_tools
from rag.embeddings import build_embeddings
from rag.indexer import Indexer
from rag.rag_tool import build_rag_tool
from skills.commerce import CommerceSkills
from skills.code_review import CodeReviewSkill
from skills.data_analysis import DataAnalysisSkill
from skills.summarize import SummarizeSkill
from skills.translator import TranslatorSkill
from skills.web_ops import WebOpsSkill
from tools import get_builtin_tools

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是跨境电商平台的专业智能客服助手，负责高效解决买家关于订单、物流、退款、商品、政策的咨询。\n\n"
    "【回答风格规范】\n"
    "1. 直接给结论，不要寒暄。严禁使用「你好！我是XX助手」「很高兴为你服务」「有什么可以帮你」等 LLM 式开场白，"
    "首个字符就应该是有效信息（如订单号、操作结果、政策要点）。\n"
    "2. 信息结构化呈现：\n"
    "   - 多个数据点（订单详情、退款明细、物流节点）必须用 Markdown 表格\n"
    "   - 步骤类信息用编号列表（1. 2. 3.）\n"
    "   - 政策/条款用引用块（>）\n"
    "   - 关键数据（金额、单号、时效）用加粗或行内代码\n"
    "3. 推理过程自然前置：在执行操作前用一句话说明「正在为你做什么」，如「正在查询订单 1001 状态...」，"
    "不要在结尾集中展示思考过程，不要解释你的内部逻辑。\n"
    "4. 工具调用结果必须转译为用户可读语言，严禁直接输出 JSON、技术参数或工具返回的原始字符串。\n"
    "5. 操作完成用「● 已处理：」开头并紧跟关键信息；操作未通过用「● 未通过：」开头并紧跟原因，再给下一步建议。\n"
    "6. 中文回复（除非买家使用其他语言）。语气专业、克制、不啰嗦，不使用任何 emoji。\n"
    "7. 答案长度控制在必要范围，能用表格说清楚的不用段落，能用列表的不用段落。\n\n"
    "【业务规则】\n"
    "1. 退款前必须调 query_order 验证订单可退款状态，再调 create_refund。\n"
    "2. 政策/FAQ/商品详情通过 rag_search 查询知识库，严禁凭空回答。\n"
    "3. 超出权限（退款 > $200 / 投诉物流公司 / 法律威胁 / 索要额外补偿 / 同一订单第 3 次退款），"
    "明确告知将升级到人工客服，不要硬处理。\n"
    "4. 退款金额、订单号、物流单号等关键信息必须用表格或列表清晰展示。\n\n"
    "【输出格式示例】\n"
    "买家问：「订单 1001 怎么退款？」\n"
    "你的回答应该是：\n"
    "● 已处理：订单 1001 退款申请已创建\n\n"
    "| 项目 | 内容 |\n"
    "| --- | --- |\n"
    "| 订单号 | 1001 |\n"
    "| 退款金额 | $47.48 |\n"
    "| 退款单号 | RF-1001-0001 |\n"
    "| 处理状态 | 已创建 |\n\n"
    "退款将在 3-5 个工作日原路退回。如需其他帮助请告知。"
)


@lru_cache(maxsize=1)
def get_llm() -> BaseChatModel:
    return build_llm()


@lru_cache(maxsize=1)
def get_indexer() -> Indexer:
    """One Indexer per process (embeddings are expensive to load)."""
    return Indexer(build_embeddings())


@lru_cache(maxsize=1)
def get_collector() -> BadCaseCollector:
    return BadCaseCollector()


@lru_cache(maxsize=1)
def get_long_term_memory() -> LongTermMemory:
    """One LongTermMemory per process (shares the Indexer and LLM).

    Used by build_memory_tools to give the agent save_memory / recall_memory
    tools, so it can persist user facts/preferences across sessions.
    """
    return LongTermMemory(get_indexer(), llm=get_llm())


def tenant_collection(tenant_id: str) -> str:
    return f"{settings.kb_collection_prefix}_{tenant_id}"


# --------------------------------------------------------------------------- #
# Per-tenant agent cache
# --------------------------------------------------------------------------- #
_AGENT_LOCK = Lock()
_AGENTS: dict[str, Any] = {}


def get_agent_for_tenant(tenant_id: str) -> Any:
    """Return the compiled multi-agent graph for a tenant. Builds on first access."""
    if tenant_id in _AGENTS:
        return _AGENTS[tenant_id]

    with _AGENT_LOCK:
        # Double-checked locking.
        if tenant_id in _AGENTS:
            return _AGENTS[tenant_id]

        llm = get_llm()
        indexer = get_indexer()
        rag_tool = build_rag_tool(indexer, collection=tenant_collection(tenant_id))

        # Order-ops sub-agent: builtin tools + commerce skills + web_ops
        # (so the agent can, for example, "go to the carrier site and check
        # the tracking timeline visually" or "redeem the coupon on the
        # activity page for me").
        order_tools: list[BaseTool] = [
            *get_builtin_tools(),
            *CommerceSkills().get_tools(),
            *WebOpsSkill().get_tools(),
        ]
        # Knowledge sub-agent: RAG retriever + summarize skill + translator
        # (cross-border commerce serves multilingual buyers) + long-term
        # memory tools (save_memory / recall_memory) so the agent can persist
        # user facts/preferences across sessions + code review / data analysis
        # skills (extended capabilities for power users who paste code or CSV)
        # + web_ops (so knowledge Q&A can "go check the page" instead of
        # only relying on the local RAG corpus).
        memory_tools = build_memory_tools(get_long_term_memory())
        knowledge_tools: list[BaseTool] = [
            rag_tool,
            *SummarizeSkill(llm).get_tools(),
            *TranslatorSkill(llm).get_tools(),
            *CodeReviewSkill(llm).get_tools(),
            *DataAnalysisSkill().get_tools(),
            *WebOpsSkill().get_tools(),
            *memory_tools,
        ]
        agent = build_multi_agent(
            llm,
            order_tools=order_tools,
            knowledge_tools=knowledge_tools,
            system_prompt=SYSTEM_PROMPT,
            thread_id=f"tenant-{tenant_id}",
        )
        _AGENTS[tenant_id] = agent
        logger.info(
            "built multi-agent for tenant %s (order_tools=%d, knowledge_tools=%d, memory_tools=%d)",
            tenant_id, len(order_tools), len(knowledge_tools), len(memory_tools),
        )
        return agent
