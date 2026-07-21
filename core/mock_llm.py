"""Mock chat model for local demos without an external LLM API.

Why this exists
---------------
The project's ChatOpenAI hits an upstream OpenAI-compatible proxy
(`OPENAI_API_BASE` in .env). When the proxy returns 401 (expired /
revoked token) or the project is offline, every router / sub-agent
call fails and the user sees a single error bubble. To keep the
front-end end-to-end demo-able, we ship a keyword-driven mock that
mimics the LLM just enough to drive the ReAct sub-agents through
their normal tool calls (query_order, query_logistics, rag_search,
web_open_url, ...).

It is intentionally tiny: it does NOT try to look smart, it just
matches common Chinese e-commerce intents and returns the same
`AIMessage` / `AIMessageChunk` shapes langgraph expects, so the rest
of the pipeline (tracing, SSE, UI) keeps working unchanged.

Activation
----------
Set `LLM_MOCK=1` in `.env` (or `settings.llm_mock = True` in code).
`build_llm()` will return a `MockChatModel` instead of `ChatOpenAI`.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, AsyncIterator, Iterator, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult


# --------------------------------------------------------------------------- #
# Intent heuristics
# --------------------------------------------------------------------------- #
_ORDER_RE = re.compile(r"(?:订单\s*号?\s*[:：]?\s*)?(\d{3,6})")
_TRACKING_RE = re.compile(r"(?:快递|物流|运单|单号)\s*[:：]?\s*([A-Z0-9]{6,20})", re.I)
_REFUND_KW = ("退款", "退钱", "退货款", "退订")
_LOGISTICS_KW = ("物流", "快递", "运单", "到哪了", "派送", "签收", "轨迹", "到哪里")
_QUERY_ORDER_KW = ("订单状态", "订单详情", "我的订单", "查订单", "查询订单", "查一下订单", "订单 1001", "订单1001")
_WEB_KW = ("打开", "网页", "网站", "看看", "看看页面", "访问", "访问页面", "现场", "操作网页", "看看活动", "活动页")
_POLICY_KW = ("政策", "运费", "退货", "退换", "售后", "售后政策", "能不能退", "可以退吗", "时效", "清关", "多久到", "几天到")
_PRODUCT_KW = ("推荐", "热销", "新品", "商品", "买什么")
_ESCALATION_KW = ("投诉", "起诉", "举报", "律师", "主管", "经理", "第三次退款", "200", "粗口", "骂人")

# Translate the platform's English status tokens to Chinese labels the
# customer can read at a glance. Anything not in the map is shown
# verbatim — the platform may add new statuses we haven't seen yet.
_STATUS_CN: dict[str, str] = {
    "pending": "待处理",
    "processing": "处理中",
    "shipped": "已发货",
    "in_transit": "运输中",
    "out_for_delivery": "派送中",
    "delivered": "已签收",
    "cancelled": "已取消",
    "refunded": "已退款",
    "returned": "已退货",
}


# --------------------------------------------------------------------------- #
# Mock model
# --------------------------------------------------------------------------- #
class MockChatModel(BaseChatModel):
    """A drop-in `BaseChatModel` that decides what to do by keyword.

    The model's only job is to (a) route the user's first turn and
    (b) walk the ReAct loop one step at a time (decide which tool
    to call, then — once the tool result is back — produce the final
    markdown answer). It's not trying to be clever, it's trying to
    make the demo work offline.
    """

    # ---- langchain BaseChatModel surface ---------------------------------
    @property
    def _llm_type(self) -> str:
        return "mock-llm"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model": "mock-llm", "temperature": 0.0}

    # Core sync path: BaseChatModel._generate is abstract; we just
    # route through the same decision function used by ainvoke.
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        msg = self._decide(messages)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    # The two methods langgraph + chat.py actually call.
    async def ainvoke(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> AIMessage:
        return self._decide(messages)

    async def astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[AIMessageChunk]:
        result = self._decide(messages)
        # Yield one chunk so callers iterating get the same shape as
        # the real ChatOpenAI stream path.
        yield AIMessageChunk(
            content=result.content,
            tool_calls=result.tool_calls,
            tool_call_chunks=[
                {
                    "id": tc.get("id", ""),
                    "name": tc.get("name", ""),
                    "args": tc.get("args", {}),
                    "index": i,
                }
                for i, tc in enumerate(result.tool_calls or [])
            ],
        )

    # langgraph's create_react_agent calls bind_tools() before invoking.
    # The mock ignores the binding (it decides from the message
    # history) but must return a model-like object.
    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "MockChatModel":
        # Stash tool names for nicer error messages / debugging.
        self._bound_tool_names = [getattr(t, "name", str(t)) for t in tools]
        return self

    # Sync fallback (some langgraph internals may still call invoke/stream).
    def invoke(self, messages: list[BaseMessage], **kwargs: Any) -> AIMessage:
        return self._decide(messages)

    def stream(self, messages: list[BaseMessage], **kwargs: Any) -> Iterator[AIMessageChunk]:
        result = self._decide(messages)
        yield AIMessageChunk(
            content=result.content,
            tool_calls=result.tool_calls,
        )

    # ---- internal decision logic -----------------------------------------
    def _decide(self, messages: list[BaseMessage]) -> AIMessage:
        system_text = self._system_text(messages)
        last_user = self._last_user_text(messages)
        last_tool = self._last_tool_message(messages)

        # (1) Router prompt — return a route JSON.
        if "请求路由器" in system_text or "分类规则" in system_text:
            route, reason = self._route_for(last_user)
            return AIMessage(
                content=json.dumps(
                    {"route": route, "route_reason": reason}, ensure_ascii=False
                )
            )

        # (2) Order-ops sub-agent. Walk ReAct: query_order first,
        # then query_logistics OR create_refund based on user intent.
        if "订单专员" in system_text:
            return self._order_ops_step(messages, last_user, last_tool)

        # (3) Knowledge sub-agent. Walk ReAct: rag_search first,
        # then web_open_url when the user asked for a page visit.
        if "知识库" in system_text or "知识咨询" in system_text:
            return self._knowledge_step(messages, last_user, last_tool)

        # (4) Generic fallback — just answer in markdown.
        return AIMessage(
            content=f"● 已处理：已收到你的问题「{last_user}」。\n\n这是 mock 模式下的回显，"
            "用于本地演示链路。如需完整 LLM 回答，请关闭 `LLM_MOCK` 并配置有效 `OPENAI_API_KEY`。"
        )

    # ---- router ---------------------------------------------------------
    def _route_for(self, text: str) -> tuple[str, str]:
        if any(kw in text for kw in _ESCALATION_KW):
            return "escalation", f"触发升级条件（mock 路由）: {text[:24]}"
        if any(kw in text for kw in (*_LOGISTICS_KW, *_REFUND_KW, "订单")):
            return "order_ops", f"订单/物流/退款类请求（mock 路由）: {text[:24]}"
        if any(kw in text for kw in (*_WEB_KW, *_POLICY_KW, *_PRODUCT_KW)):
            return "knowledge", f"知识/网页/政策咨询（mock 路由）: {text[:24]}"
        return "knowledge", f"默认走知识库专员（mock 路由）: {text[:24]}"

    # ---- order-ops ReAct ------------------------------------------------
    def _order_ops_step(
        self, messages: list[BaseMessage], last_user: str, last_tool: ToolMessage | None
    ) -> AIMessage:
        # No tool has been called yet → start with query_order so the
        # next call can reference the order (refundability, tracking_no,
        # etc.). This is the "validate before acting" rule.
        if last_tool is None:
            oid = self._extract_order_id(last_user) or "1001"
            return self._tool_call(
                name="query_order",
                args={"order_id": oid},
                narration=f"正在查询订单 {oid} 状态...",
            )

        # Already called query_order → decide what's next based on
        # which action the user explicitly asked for.
        if last_tool.name == "query_order":
            # Pull tracking_no out of the order record so we can pass it
            # to query_logistics exactly as the live agent would. (Mock
            # LLM doesn't actually call the real tool — the data dict
            # is what the mock would have received.)
            tracking_no = "TRACK-1001-US"
            try:
                parsed = json.loads(last_tool.content) if isinstance(last_tool.content, str) else last_tool.content
                if isinstance(parsed, dict) and parsed.get("tracking_no"):
                    tracking_no = parsed["tracking_no"]
            except (json.JSONDecodeError, TypeError):
                pass
            # Did the user ask for a refund?
            if any(kw in last_user for kw in _REFUND_KW):
                return self._tool_call(
                    name="create_refund",
                    args={"order_id": self._extract_order_id(last_user) or "1001",
                          "reason": "not_received",
                          "amount_usd": 47.48},
                    narration="正在为该订单创建退款申请...",
                )
            # Did the user ask for logistics?
            if any(kw in last_user for kw in _LOGISTICS_KW):
                return self._tool_call(
                    name="query_logistics",
                    args={"tracking_no": tracking_no},
                    narration="正在查询物流轨迹...",
                )
            # Otherwise: produce a final markdown answer from the query result.
            return self._order_final_answer(last_tool, last_user)

        # query_logistics or create_refund already happened → wrap up.
        return self._order_final_answer(last_tool, last_user)

    def _order_final_answer(
        self, last_tool: ToolMessage, last_user: str
    ) -> AIMessage:
        """Render the final markdown answer for whichever tool finished.

        Each branch matches the message style the real LLM would emit
        per ORDER_OPS_PROMPT: a `● 已处理` / `● 未通过` prefix, key
        facts in a Markdown table, and (when useful) an [ACTION] block
        for the next obvious step. Returns an `AIMessage` carrying
        ONLY content (no tool_calls) so the agent loop ends.
        """
        tool_payload = last_tool.content
        # Tools return either JSON strings or pre-formatted plain text
        # (e.g. query_logistics emits "tracking=... latest_events: ...",
        # create_refund emits "refund processed: refund_id=..."). Try
        # JSON first; fall back to a lightweight parser for the
        # plain-text shape so the mock can still surface useful data.
        data: dict[str, Any] = {}
        timeline: list[dict[str, str]] = []
        if isinstance(tool_payload, str):
            try:
                data = json.loads(tool_payload)
                if not isinstance(data, dict):
                    data = {}
            except (json.JSONDecodeError, TypeError):
                if last_tool.name == "query_logistics":
                    data, timeline = self._parse_logistics_text(tool_payload)
                elif last_tool.name == "create_refund":
                    data = self._parse_refund_text(tool_payload)
                elif last_tool.name == "query_order":
                    # query_order emits a compact plain-text summary like:
                    #   "order#1001 status=refunded total=$47.48 items=[...] tracking=TRACK-1001-US refundable=False"
                    # The old fallback was to show a hard-coded "已发货"
                    # regardless of the real status, which gave a wrong
                    # answer (e.g. when the order is already refunded).
                    # Parse the key-value tokens so the final answer
                    # reflects what's actually in the data.
                    data = self._parse_order_text(tool_payload)
        elif isinstance(tool_payload, dict):
            data = tool_payload
        if not isinstance(data, dict):
            data = {}

        # ---- query_logistics result ---------------------------------
        if last_tool.name == "query_logistics":
            tracking = data.get("tracking_no") or self._extract_tracking(tool_payload if isinstance(tool_payload, str) else "") or "TRACK-1001-US"
            if not timeline:
                # Try the JSON path's `timeline` field too.
                timeline = data.get("timeline") or []
            if timeline and isinstance(timeline, list):
                rows = "\n".join(
                    f"| `{e.get('ts','')}` | **{e.get('status','')}** | {e.get('location','')} |"
                    for e in timeline
                    if isinstance(e, dict)
                ) or "| - | - | - |"
            else:
                rows = "| - | - | - |"
            return AIMessage(
                content=(
                    f"● 已处理：物流轨迹（{tracking}）\n\n"
                    "| 时间 | 状态 | 位置 |\n| --- | --- | --- |\n"
                    f"{rows}\n\n"
                    "如需进一步操作（联系承运商、申请理赔等），请告知。"
                )
            )

        # ---- create_refund result -----------------------------------
        if last_tool.name == "create_refund":
            raw_status = str(data.get("status", "已创建")).lower()
            if raw_status == "rejected":
                # The refund was rejected by the platform (e.g. order
                # already refunded, refundable=False, etc.). Surface
                # the reason so the user knows why.
                reason = data.get("reason_message") or "该订单当前不可退款"
                return AIMessage(
                    content=(
                        f"● 未通过：退款申请被拒绝\n\n"
                        f"> {reason}\n\n"
                        "如需进一步处理，请联系人工客服。"
                    )
                )
            refund_id = data.get("refund_id", "RF-1001-0001")
            amount = data.get("amount_usd", data.get("amount", "47.48"))
            # Map the English status tokens the mock platform returns
            # back to the human-readable Chinese label.
            status_map = {
                "processed": "已处理",
                "created": "已创建",
                "pending": "处理中",
            }
            status = status_map.get(raw_status, data.get("status", "已创建"))
            return AIMessage(
                content=(
                    f"● 已处理：订单退款已提交\n\n"
                    "| 项目 | 内容 |\n| --- | --- |\n"
                    f"| 退款单号 | `{refund_id}` |\n"
                    f"| 退款金额 | **${amount}** |\n"
                    f"| 处理状态 | **{status}** |\n\n"
                    "退款将在 3-5 个工作日内原路退回。"
                    "[ACTION]\n"
                    '{"id": "view-orders", "label": "查看我的订单", "prompt": "打开我的订单列表"}\n'
                    "[/ACTION]\n"
                )
            )

        # ---- query_order result (default) ---------------------------
        # Use whatever the parser pulled out of the tool text — fall
        # back to a 1001/shipped placeholder only when the data is
        # truly empty. The status string is translated through
        # `_STATUS_CN` so e.g. "refunded" → "已退款" (not the old
        # always-shipped "已发货" default).
        order_id = str(data.get("order_id") or "1001")
        raw_status = str(data.get("status") or "shipped")
        status = _STATUS_CN.get(raw_status, raw_status)
        # Amount: total_usd (raw text) → amount (display)
        amount = data.get("amount_usd") or data.get("total_amount") or data.get("amount") or "47.48"
        # Tracking: only show the value when the platform actually
        # gave us one. Otherwise the cell is a clean "—" so the user
        # doesn't see a misleading "N/A" or "TRACK-1001-US" pulled
        # from the demo default.
        tracking = data.get("tracking_no")
        if not tracking:
            tracking = "—"
        carrier = data.get("carrier", "顺丰速运")
        refundable = data.get("refundable")
        # If the order is already refunded, the refund button is
        # misleading. Suppress it so the user isn't given a button
        # that the backend will reject.
        can_refund = True
        if isinstance(refundable, bool):
            can_refund = refundable
        elif isinstance(refundable, str):
            can_refund = refundable.strip().lower() in {"true", "1", "yes"}
        elif isinstance(refundable, (int, float)):
            can_refund = bool(refundable)
        # Tailor the followup blurb to the actual state.
        if raw_status == "refunded":
            follow_blurb = (
                f"订单已完成退款，无需再次申请退款。如需查看物流轨迹或联系客服，请告知。"
            )
        elif raw_status in {"pending", "processing"}:
            follow_blurb = (
                f"订单正在处理中，尚未发货。暂无法查询物流，待发货后可查看轨迹。"
            )
        else:
            follow_blurb = "订单已发货，如需查看物流轨迹或申请退款，请告知。"
        # Build the [ACTION] cards list. Always show 查看物流; only
        # show 申请退款 when the order is still refundable.
        action_cards_md = (
            "[ACTION]\n"
            '{"id": "view-logistics", "label": "查看物流", '
            '"prompt": "帮我查一下订单 ' + order_id + ' 的物流轨迹"}\n'
            "[/ACTION]\n"
        )
        if can_refund:
            action_cards_md += (
                "[ACTION]\n"
                '{"id": "create-refund", "label": "申请退款", '
                '"prompt": "帮我申请订单 ' + order_id + ' 的退款"}\n'
                "[/ACTION]\n"
            )
        return AIMessage(
            content=(
                f"● 已处理：订单 {order_id} 查询结果如下\n\n"
                "| 项目 | 内容 |\n| --- | --- |\n"
                f"| 订单号 | `{order_id}` |\n"
                f"| 状态 | **{status}** |\n"
                f"| 金额 | **${amount}** |\n"
                f"| 物流单号 | `{tracking}` ({carrier}) |\n\n"
                f"{follow_blurb}\n\n"
                f"{action_cards_md}"
            )
        )

    # ---- knowledge ReAct ------------------------------------------------
    def _knowledge_step(
        self, messages: list[BaseMessage], last_user: str, last_tool: ToolMessage | None
    ) -> AIMessage:
        if last_tool is None:
            # Did the user ask to open a page? Start with web_open_url.
            if any(kw in last_user for kw in _WEB_KW) and "url" in last_user.lower() or "http" in last_user:
                url = self._extract_url(last_user) or "https://example.com"
                return self._tool_call(
                    name="web_open_url",
                    args={"url": url},
                    narration=f"正在打开网页 {url}...",
                )
            # Did the user name a product/policy question? Start with rag_search.
            if any(kw in last_user for kw in (*_POLICY_KW, *_PRODUCT_KW)):
                query = self._rag_query(last_user)
                return self._tool_call(
                    name="rag_search",
                    args={"query": query},
                    narration="正在检索知识库...",
                )
            # Otherwise: open a sensible default page (e.g. terms of service).
            return self._tool_call(
                name="web_open_url",
                args={"url": "https://example.com"},
                narration="正在打开网页...",
            )
        # After a tool result, return a final answer.
        if last_tool.name == "web_open_url":
            return AIMessage(
                content=(
                    "● 已处理：已打开目标页面\n\n"
                    f"已访问 `{last_tool.content[:120]}`，页面加载完成。"
                    "（mock LLM 模式：未真正渲染，仅展示端到端链路。）\n\n"
                    "如需对该页面执行点击/提取/截图操作，请告知。"
                )
            )
        # rag_search / web_extract_text / etc.
        return AIMessage(
            content=(
                "● 已处理：根据知识库检索结果整理\n\n"
                "> 这是 mock LLM 模式下的回显，用于本地演示完整链路。\n\n"
                "如需真实知识库答案，请关闭 `LLM_MOCK` 并配置有效的 `OPENAI_API_KEY`。"
            )
        )

    # ---- tool-call helper ------------------------------------------------
    def _tool_call(self, *, name: str, args: dict[str, Any], narration: str) -> AIMessage:
        """Emit an `AIMessage` with a single tool_call and a small visible
        text chunk so langgraph's intermediate `content` field can
        drive the Kiki-mode interim_answer bubble.
        """
        tool_call_id = f"call_{uuid.uuid4().hex[:10]}"
        return AIMessage(
            content=narration,  # visible text — flushed as interim_answer
            tool_calls=[
                {
                    "id": tool_call_id,
                    "name": name,
                    "args": args,
                }
            ],
        )

    # ---- text helpers ----------------------------------------------------
    def _system_text(self, messages: list[BaseMessage]) -> str:
        out: list[str] = []
        for m in messages:
            if isinstance(m, SystemMessage):
                out.append(m.content if isinstance(m.content, str) else str(m.content))
        return "\n".join(out)

    def _last_user_text(self, messages: list[BaseMessage]) -> str:
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                return m.content if isinstance(m.content, str) else str(m.content)
        # Fallback: any non-system, non-tool, non-AI message.
        for m in reversed(messages):
            t = getattr(m, "type", "")
            if t in {"user", "human", "humanmessage"}:
                return m.content if isinstance(m.content, str) else str(m.content)
        return ""

    def _last_tool_message(self, messages: list[BaseMessage]) -> ToolMessage | None:
        for m in reversed(messages):
            if isinstance(m, ToolMessage):
                return m
        return None

    def _extract_order_id(self, text: str) -> str | None:
        m = _ORDER_RE.search(text or "")
        return m.group(1) if m else None

    def _extract_url(self, text: str) -> str | None:
        m = re.search(r"https?://\S+", text or "")
        return m.group(0).rstrip("。，,;；") if m else None

    def _extract_tracking(self, text: str) -> str | None:
        """Pick the `tracking=...` value out of a query_logistics payload."""
        m = re.search(r"tracking\s*=\s*([A-Za-z0-9_\-]+)", text or "")
        return m.group(1) if m else None

    def _parse_order_text(self, text: str) -> dict[str, Any]:
        """Parse the plain-text shape `query_order` returns.

        Example input:
            order#1001 status=refunded total=$47.48 items=[2x Organic
            Cotton T-Shirt, 1x Bamboo Fiber Socks] tracking=TRACK-1001-US
            refundable=False

        Returns a dict so the markdown renderer can surface the real
        status / amount / tracking / refundable flag (instead of the
        old "hard-code shipped" fallback). Anything that doesn't match
        is silently skipped — the user just sees less detail, not a
        crash.
        """
        out: dict[str, Any] = {}
        if not text:
            return out
        # `order#1001` → order_id
        m = re.search(r"order#\s*([A-Za-z0-9_\-]+)", text)
        if m:
            out["order_id"] = m.group(1)
        # `status=refunded` etc.
        m = re.search(r"\bstatus\s*=\s*([A-Za-z_]+)", text)
        if m:
            out["status"] = m.group(1)
        # `total=$47.48` — strip the $ and keep just the number.
        m = re.search(r"\btotal\s*=\s*\$?\s*([\d.]+)", text)
        if m:
            try:
                out["total_usd"] = float(m.group(1))
            except ValueError:
                pass
        # `tracking=TRACK-1001-US` — but the platform emits "N/A" when
        # the order has no tracking number yet, so the captured value
        # must accept "/" too. When the value is "N/A" we drop the
        # key entirely so the answer shows the placeholder "—"
        # instead of a misleading "N".
        m = re.search(r"\btracking\s*=\s*([A-Za-z0-9_\-/]+)", text)
        if m:
            v = m.group(1)
            if v and v.upper() != "N/A":
                out["tracking_no"] = v
        # `refundable=True|False` — the value is a Python repr, so
        # anything in {True, False} is fine.
        m = re.search(r"\brefundable\s*=\s*(True|False|true|false|1|0)", text)
        if m:
            out["refundable"] = m.group(1).lower() in {"true", "1"}
        # `items=[2x Organic Cotton T-Shirt, 1x Bamboo Fiber Socks]`
        # — keep it as a human-readable string for the answer.
        m = re.search(r"items\s*=\s*\[([^\]]*)\]", text)
        if m:
            out["items"] = m.group(1).strip()
        return out

    def _parse_logistics_text(
        self, text: str
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        """Parse the plain-text shape `query_logistics` returns.

        Example input:
            tracking=TRACK-1001-US latest_events: 2026-07-13T03:00:00Z in_transit @ Anchorage, US | 2026-07-15T09:00:00Z customs_clearance @ Los Angeles, US | ...

        Returns ({tracking_no}, timeline) so the markdown renderer can
        emit a proper table even when the tool returns a flat string
        (which is what the live `query_logistics` tool does today).
        """
        out: dict[str, Any] = {}
        timeline: list[dict[str, str]] = []
        if not text:
            return out, timeline
        tracking = self._extract_tracking(text)
        if tracking:
            out["tracking_no"] = tracking
        # Match "YYYY-MM-DDTHH:MM:SSZ status @ location" segments.
        pattern = re.compile(
            r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+"
            r"([A-Za-z_]+)\s+@\s+([^|]+?)\s*(?=\||$)"
        )
        for m in pattern.finditer(text):
            timeline.append(
                {"ts": m.group(1), "status": m.group(2), "location": m.group(3).strip()}
            )
        return out, timeline

    def _parse_refund_text(self, text: str) -> dict[str, Any]:
        """Parse the plain-text shape `create_refund` returns.

        Two shapes are supported:
          1. Success: "refund processed: refund_id=... amount=$... reason=... status=processed"
          2. Rejection: "refund rejected: <human-readable reason>"

        Returns a dict so the markdown renderer can use the real
        refund_id instead of a placeholder. Falls back to {} on miss.
        """
        out: dict[str, Any] = {}
        if not text:
            return out
        if "rejected" in text:
            out["status"] = "rejected"
            # Keep the rejection reason around for the user-facing
            # explanation (everything after the colon).
            idx = text.find(":")
            if idx >= 0:
                out["reason_message"] = text[idx + 1:].strip()
            return out
        m = re.search(r"refund_id\s*=\s*([A-Za-z0-9_\-]+)", text)
        if m:
            out["refund_id"] = m.group(1)
        m = re.search(r"amount\s*=\s*\$?\s*([\d.]+)", text)
        if m:
            try:
                out["amount_usd"] = float(m.group(1))
            except ValueError:
                pass
        m = re.search(r"reason\s*=\s*(\w+)", text)
        if m:
            out["reason"] = m.group(1)
        m = re.search(r"status\s*=\s*(\w+)", text)
        if m:
            out["status"] = m.group(1)
        return out

    def _rag_query(self, text: str) -> str:
        # Strip the order id and noise words; return the rest as the
        # RAG search query. This is intentionally crude — mock LLM.
        cleaned = re.sub(r"订单\s*\d+", "", text or "")
        return cleaned.strip() or text
