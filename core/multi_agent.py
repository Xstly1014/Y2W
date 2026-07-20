"""Multi-agent orchestration graph.

Architecture:
    router ──► order_ops   (ReAct sub-agent: commerce + builtin tools)
           ├──► knowledge  (ReAct sub-agent: rag_search + summarize)
           └──► escalation (template reply + ticket id, no tools)

The router classifies the user's intent via LLM structured output and
stores `route` / `route_reason` / `subagent_name` in graph state so the
API layer can surface "transferring you to the order specialist" UX.

Per-thread conversation memory is preserved by a MemorySaver checkpointer
on the outer graph; sub-agents are stateless (the outer graph owns memory).

Public surface is intentionally identical to `core.agent.build_agent`:
    agent.stream({"messages": [...]}, config=..., stream_mode="updates")
    agent.get_state(config=...)
    agent.invoke(...)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Annotated, Any, Literal
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Routing taxonomy
# --------------------------------------------------------------------------- #
ROUTE_ORDER_OPS = "order_ops"
ROUTE_KNOWLEDGE = "knowledge"
ROUTE_ESCALATION = "escalation"

# Display names surfaced to the front-end as the "current specialist".
SUBAGENT_DISPLAY_NAMES: dict[str, str] = {
    ROUTE_ORDER_OPS: "订单专员",
    ROUTE_KNOWLEDGE: "知识库专员",
    ROUTE_ESCALATION: "人工客服",
}


class RouteDecision(BaseModel):
    """Structured LLM output: which sub-agent should handle this turn."""

    route: Literal["order_ops", "knowledge", "escalation"] = Field(
        ...,
        description=(
            "Intended sub-agent. order_ops = order/logistics/refund actions; "
            "knowledge = policy/FAQ/product info from RAG; "
            "escalation = handover to human agent."
        ),
    )
    route_reason: str = Field(
        ...,
        description="One short Chinese sentence explaining why this route was chosen.",
    )


ROUTER_PROMPT = (
    "你是跨境电商客服系统的请求路由器，负责将买家请求分流到合适的专员。\n\n"
    "分类规则：\n"
    "- order_ops（订单操作）：买家想查询订单、查询物流轨迹、申请退款、查询退款状态等"
    "需要操作订单系统的请求。\n"
    "- knowledge（知识咨询）：买家询问退货政策、运费规则、清关时效、商品详情、FAQ 等，"
    "需要从知识库检索答案的请求。\n"
    "- escalation（升级人工）：满足任一条件即升级：\n"
    "    * 退款金额超过 $200\n"
    "    * 投诉物流公司（要求赔偿、投诉服务质量）\n"
    "    * 法律威胁（起诉、举报、律师函等）\n"
    "    * 索要额外补偿（差价、运费券、礼品卡等超出常规退款的诉求）\n"
    "    * 同一订单第 3 次及以上退款\n"
    "    * 强烈不满情绪（粗口、侮辱、要求立刻找主管等）\n\n"
    "请只输出一个 JSON 对象，不要有任何额外文字、不要 markdown 代码块标记。格式如下：\n"
    '{"route": "order_ops|knowledge|escalation", "route_reason": "一句简短中文说明路由理由"}\n'
    "route 必须是 order_ops / knowledge / escalation 三者之一。"
)


def _parse_router_json(text: str) -> tuple[str, str]:
    """Parse the router LLM's JSON response. Falls back to knowledge route."""
    if not text:
        return ROUTE_KNOWLEDGE, "路由器未返回内容，默认走知识库专员"
    # Strip markdown code fences if present.
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    # Try to find the first {...} block.
    brace_match = re.search(r"\{[^{}]*\}", cleaned, re.DOTALL)
    if brace_match and not cleaned.startswith("{"):
        cleaned = brace_match.group(0)
    try:
        obj = json.loads(cleaned)
        route = str(obj.get("route", "")).strip().lower()
        reason = str(obj.get("route_reason", "")).strip()
        if route not in {ROUTE_ORDER_OPS, ROUTE_KNOWLEDGE, ROUTE_ESCALATION}:
            return ROUTE_KNOWLEDGE, reason or f"路由器返回未知 route={route}，默认走知识库专员"
        return route, reason or "路由器未给出理由"
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("router JSON parse failed: %s; raw=%r", exc, text[:200])
        return ROUTE_KNOWLEDGE, f"路由器输出解析失败，默认走知识库专员"


# --------------------------------------------------------------------------- #
# Sub-agent system prompts (appended to the shared top-level system prompt)
# --------------------------------------------------------------------------- #
ORDER_OPS_PROMPT = (
    "你是跨境电商平台的订单专员，专门处理订单查询、物流追踪、退款创建等订单类操作。\n\n"
    "工作准则：\n"
    "1. 直接给结论，不要寒暄。首个字符就应该是有效信息（订单号、操作结果、政策要点）。\n"
    "2. 退款前必须先调 query_order 验证订单可退款状态，再调 create_refund。\n"
    "3. 工具返回结果必须转译为用户可读的中文，严禁直接输出 JSON 或技术参数。\n"
    "4. 多个数据点（订单详情、退款明细、物流节点）用 Markdown 表格呈现。\n"
    "5. 关键数据（金额、单号、时效）用加粗或行内代码。\n"
    "6. 操作完成用「● 已处理：」开头并紧跟关键信息；操作未通过用「● 未通过：」开头并紧跟原因。\n"
    "7. 超出权限（退款 > $200、投诉物流、法律威胁、索要额外补偿、同一订单第 3 次退款）时，"
    "不要硬处理，直接告知用户将升级到人工客服，不要尝试调用任何工具。\n"
    "8. 语气专业、克制、不啰嗦，不使用任何 emoji。\n"
)

KNOWLEDGE_PROMPT = (
    "你是跨境电商平台的知识库专员，负责回答退货政策、运费规则、清关时效、商品详情、FAQ 等咨询。\n\n"
    "工作准则：\n"
    "1. 必须通过 rag_search 检索知识库后再回答，严禁凭空生成政策或商品信息。\n"
    "2. 政策 / 条款用引用块（>）呈现，关键条款用加粗。\n"
    "3. 直接给结论，不要寒暄。\n"
    "4. 工具返回的原始片段必须转译为用户可读的中文，禁止直接输出检索编号或原始 chunk 文本。\n"
    "5. 知识库无匹配时，明确告知用户该问题需要转交人工，不要编造答案。\n"
    "6. 语气专业、克制、不啰嗦，不使用任何 emoji。\n"
)


# --------------------------------------------------------------------------- #
# Graph state
# --------------------------------------------------------------------------- #
class MultiAgentState(TypedDict, total=False):
    """State shared across nodes.

    `messages` carries the conversation (with the langgraph add_messages
    reducer so sub-agent outputs merge cleanly). `route` / `route_reason`
    / `subagent_name` are set by the router and read by the API layer.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    route: str
    route_reason: str
    subagent_name: str


# --------------------------------------------------------------------------- #
# Node: router (LLM-based intent classification)
# --------------------------------------------------------------------------- #
def _build_router(llm: BaseChatModel):
    """Return a router node that classifies intent via prompt + JSON parse.

    We intentionally avoid `llm.with_structured_output()` because some
    providers (e.g. certain ZhipuAI / MiniMax models) reject the
    `response_format` parameter it sets. Plain prompt + JSON parse is
    portable and degrades gracefully on malformed output.
    """

    def router_node(state: MultiAgentState) -> dict[str, Any]:
        messages = state.get("messages", [])
        # Pass full history so the LLM can detect "same order 3rd refund"
        # patterns, but route based on the latest user turn.
        try:
            ai_msg = llm.invoke([SystemMessage(content=ROUTER_PROMPT), *messages])
            text = getattr(ai_msg, "content", "") or ""
            route, route_reason = _parse_router_json(text)
        except Exception as exc:  # noqa: BLE001
            # Network / provider error — fall back to the knowledge route so
            # the user still gets *some* answer instead of a 500.
            logger.warning(
                "router LLM call failed (%s); defaulting to knowledge route", exc
            )
            route, route_reason = (
                ROUTE_KNOWLEDGE,
                f"路由器调用失败，默认走知识库专员：{exc}",
            )
        subagent_name = SUBAGENT_DISPLAY_NAMES.get(route, route)
        logger.info(
            "router decision: route=%s subagent=%s reason=%s",
            route, subagent_name, route_reason,
        )
        return {
            "route": route,
            "route_reason": route_reason,
            "subagent_name": subagent_name,
        }

    return router_node


# --------------------------------------------------------------------------- #
# Node: escalation (no tools, template reply + ticket id)
# --------------------------------------------------------------------------- #
ESCALATION_TEMPLATE = (
    "● 已升级到人工客服\n\n"
    "| 项目 | 内容 |\n"
    "| --- | --- |\n"
    "| 工单号 | `{ticket_id}` |\n"
    "| 升级原因 | {reason} |\n"
    "| 当前处理 | {specialist} |\n\n"
    "你的请求已转交人工客服，专员将在工作时间内尽快联系你。\n"
    "如需补充信息请直接回复本对话，并请保留工单号以便查询处理进度。"
)


def _escalation_node(state: MultiAgentState) -> dict[str, Any]:
    """Generate the standard escalation reply with a unique ticket id."""
    reason = state.get("route_reason") or "触发升级条件"
    specialist = SUBAGENT_DISPLAY_NAMES[ROUTE_ESCALATION]
    # Ticket id is locally generated (uuid4); the API-layer trace_id is
    # separate but recorded alongside in the trace file for correlation.
    ticket_id = f"ESC-{uuid4().hex[:12].upper()}"
    content = ESCALATION_TEMPLATE.format(
        ticket_id=ticket_id, reason=reason, specialist=specialist
    )
    return {"messages": [AIMessage(content=content)]}


# --------------------------------------------------------------------------- #
# Graph builder
# --------------------------------------------------------------------------- #
def build_multi_agent(
    llm: BaseChatModel,
    *,
    order_tools: list[BaseTool],
    knowledge_tools: list[BaseTool],
    system_prompt: str | None = None,
    thread_id: str = "default",
) -> Any:
    """Build the multi-agent orchestration graph.

    Args:
        llm: Chat model driving the router and both ReAct sub-agents.
        order_tools: Tools for the order-ops sub-agent
            (typically builtin tools + CommerceSkills).
        knowledge_tools: Tools for the knowledge sub-agent
            (typically rag_search + SummarizeSkill).
        system_prompt: Top-level system prompt; merged into each sub-agent
            prompt so global style rules still apply.
        thread_id: Default thread id. The langgraph checkpointer scopes by
            the `configurable.thread_id` passed at call time, so this is
            only a hint for callers that don't pass a config.

    Returns:
        A compiled langgraph graph. Use:
            agent.stream({"messages": [...]}, config=..., stream_mode="updates")
            agent.get_state(config=...)
            agent.invoke({"messages": [...]}, config=...)
    """
    base_prompt = (system_prompt or "").strip()
    order_prompt = (
        base_prompt + "\n\n" + ORDER_OPS_PROMPT if base_prompt else ORDER_OPS_PROMPT
    )
    knowledge_prompt = (
        base_prompt + "\n\n" + KNOWLEDGE_PROMPT if base_prompt else KNOWLEDGE_PROMPT
    )

    # Sub-agents are compiled ReAct graphs. We add them as nodes inside the
    # orchestration graph; they share the parent's `messages` key.
    # No per-subgraph checkpointer — the outer MemorySaver owns persistence.
    order_ops_agent = create_react_agent(
        model=llm,
        tools=order_tools,
        prompt=order_prompt,
    )
    knowledge_agent = create_react_agent(
        model=llm,
        tools=knowledge_tools,
        prompt=knowledge_prompt,
    )

    router_node = _build_router(llm)

    graph = StateGraph(MultiAgentState)
    graph.add_node("router", router_node)
    graph.add_node(ROUTE_ORDER_OPS, order_ops_agent)
    graph.add_node(ROUTE_KNOWLEDGE, knowledge_agent)
    graph.add_node(ROUTE_ESCALATION, _escalation_node)

    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        lambda state: state.get("route", ROUTE_KNOWLEDGE),
        {
            ROUTE_ORDER_OPS: ROUTE_ORDER_OPS,
            ROUTE_KNOWLEDGE: ROUTE_KNOWLEDGE,
            ROUTE_ESCALATION: ROUTE_ESCALATION,
        },
    )
    graph.add_edge(ROUTE_ORDER_OPS, END)
    graph.add_edge(ROUTE_KNOWLEDGE, END)
    graph.add_edge(ROUTE_ESCALATION, END)

    checkpointer = MemorySaver()
    # Note: `thread_id` is purely informational here. langgraph picks up the
    # runtime thread_id from `config["configurable"]["thread_id"]` at call
    # time, so the checkpointer scopes per-thread correctly without us
    # having to bake it into the compiled graph.
    return graph.compile(checkpointer=checkpointer)
