# 一次 `/api/chat/stream` 请求的完整数据流追踪

> 本文把一次完整的客服对话从「用户敲字到最终 SSE 事件」全链路画清楚。  
> 目的是给排障人员一个"端到端地图"——任何一段出问题都能快速定位到代码行。  
> 参考实现：`api/routes/chat.py`（入口）+ `api/deps.py`（依赖注入）+ `core/multi_agent.py`（agent 编排）+ `observability/tracing.py`（旁路追踪）。

## 1. 整体时序图

```
买家浏览器                FastAPI(api:8000)            agent 内部                mock_shopify(8001)         data/ 文件系统
    │                          │                          │                            │                          │
    │ 1. POST /api/chat/stream │                          │                            │                          │
    │ {message,thread_id?,X-Tenant-Id}                    │                            │                          │
    │ ────────────────────────►│                          │                            │                          │
    │                          │                          │                            │                          │
    │                          │ 2. _resolve_tenant()     │                            │                          │
    │                          │    (validate_safe_id)    │                            │                          │
    │                          │                          │                            │                          │
    │                          │ 3. _new_thread_id()      │                            │                          │
    │                          │    = tenant-{id}-{uuid4[:12]}                        │                          │
    │                          │                          │                            │                          │
    │                          │ 4. current_tenant_id.set(tenant)                     │                          │
    │                          │    (ContextVar for tools)│                           │                          │
    │                          │                          │                            │                          │
    │                          │ 5. TraceRecorder(...)    │                            │                          │
    │                          │    → trace_id = uuid4    │                            │                          │
    │                          │                          │                            │                          │
    │                          │ 6. yield meta {thread_id, tenant_id}                 │                          │
    │ ◄────────────────────────│                          │                            │                          │
    │                          │                          │                            │                          │
    │                          │ 7. get_agent_for_tenant()│                            │                          │
    │                          │    → first call: 30-100s build (BGE+langgraph+8 tools) │                         │
    │                          │    → cached: <1ms        │                            │                          │
    │                          │                          │                            │                          │
    │                          │ 8. agent.astream({messages:[user]})                  │                          │
    │                          │  ───────────────────────►│                            │                          │
    │                          │  stream_mode=["messages","updates"]                   │                          │
    │                          │  subgraphs=True          │                            │                          │
    │                          │                          │                            │                          │
    │                          │                          │ 9. router 节点执行          │                          │
    │                          │                          │   ├─ 一次 LLM 分类          │                          │
    │                          │                          │   └─ JSON 解析: route      │                          │
    │                          │                          │      {order_ops|knowledge|escalation}                   │
    │                          │                          │                            │                          │
    │                          │                          │ 10. 选中的子 agent 节点执行 │                          │
    │                          │                          │     (create_react_agent)   │                          │
    │                          │                          │     ReAct 循环:            │                          │
    │                          │                          │       a) LLM 决定调工具     │                          │
    │                          │                          │       b) 工具执行           │                          │
    │                          │                          │       c) 工具结果回 LLM     │                          │
    │                          │                          │       d) LLM 决定结束       │                          │
    │                          │                          │                            │                          │
    │                          │                          │ 11. commerce 工具调用:       │                          │
    │                          │                          │     httpx 调 mock_shopify   │                          │
    │                          │                          │ ──────────────────────────►│                          │
    │                          │                          │     X-Tenant-Id 透传        │                          │
    │                          │                          │ ◄──────────────────────────│                          │
    │                          │                          │     JSON 响应              │                          │
    │                          │                          │                            │                          │
    │                          │                          │ 12. agent.aget_state()      │                          │
    │                          │                          │     → 取最后一条 AIMessage  │                          │
    │                          │                          │                            │                          │
    │                          │ 13. yield final {answer, trace_id, num_steps, ok}    │                          │
    │ ◄────────────────────────│                          │                            │                          │
    │                          │ 14. yield summary {total_latency_ms, num_tools,...} │                          │
    │ ◄────────────────────────│                          │                            │                          │
    │                          │                          │                            │                          │
    │                          │ 15. recorder.finalize()  │                            │                          │
    │                          │ ──────────────────────────────────────────────────────────────────────────────────►│
    │                          │                          │                            │      data/traces/<thread>.jsonl
    │                          │                          │                            │                          │
    │                          │ 16. 用户点击 👎 反馈       │                            │                          │
    │                          │    POST /api/feedback    │                            │                          │
    │                          │ ────────────────────────►│                            │                          │
    │                          │ 17. POST /api/feedback → BadCaseCollector.record_case                                │
    │                          │ ──────────────────────────────────────────────────────────────────────────────────►│
    │                          │                          │                            │      data/flywheel/badcases.jsonl
    │                          │                          │                            │                          │
    │                          │ 18. 离线: post-train      │                            │                          │
    │                          │    python -m scripts.audit_post_training                │                          │
    │                          │    python -m main post-train│                          │                          │
    │                          │ ──────────────────────────────────────────────────────────────────────────────────►│
    │                          │                          │                            │      data/post_training/sft.jsonl
    │                          │                          │                            │      data/post_training/dpo.jsonl
    │                          │                          │                            │                          │
    │                          │ 19. 离线: 召回评估          │                            │                          │
    │                          │    python -m evaluation retrieval                     │                          │
    │                          │                          │                            │      data/eval/results/*.json
    │                          │                          │                            │                          │
    │                          │                          │                            │                          │
    │ ◄────────────────────────│                          │                            │                          │
```

## 2. 关键步骤代码定位

| 步骤 | 文件:行 | 关键变量 / 函数 |
|---|---|---|
| 1. SSE 连接 | `api/routes/chat.py:138` | `chat_stream` |
| 2. tenant 解析 | `api/routes/chat.py:42-53` | `_resolve_tenant` |
| 3. thread_id 生成 | `api/routes/chat.py:56-65` | `_new_thread_id` |
| 4. ContextVar | `skills/commerce.py:current_tenant_id` | `set` / `reset` |
| 5. TraceRecorder | `observability/tracing.py:64` | `trace_id` |
| 6. meta 事件 | `api/routes/chat.py:166-172` | 浏览器立即获得 thread_id |
| 7. agent 装配 | `api/deps.py:107-149` | `_AGENTS` per-tenant 缓存 |
| 8. astream | `api/routes/chat.py:200-205` | `subgraphs=True` 关键 |
| 9. router 节点 | `core/multi_agent.py:ROUTER_*` | LLM 分类 → JSON |
| 10. 子 agent | `core/multi_agent.py:239-` | `create_react_agent` |
| 11. 工具调用 | `skills/commerce.py` | httpx → mock_shopify |
| 12. 取最终答案 | `api/routes/chat.py:260-272` | 反向遍历找最后一条 AIMessage |
| 13. final 事件 | `api/routes/chat.py:274-285` | `{answer, trace_id, num_steps, ok}` |
| 14. summary 事件 | `api/routes/chat.py:286-297` | `{total_latency_ms, num_tools, ...}` |
| 15. trace 落盘 | `observability/tracing.py:finalize` | `data/traces/<thread>.jsonl` |
| 16. 用户反馈 | `static/index.html:handleFeedback` | DOM 提取 user_input/prediction |
| 17. 飞轮收集 | `data_flywheel/collector.py` | `bad_store.append` |
| 18. 后训练 | `post_training/pipeline.py` | `build_sft` / `build_dpo` |
| 19. 评估 | `evaluation/retrieval_runner.py` | `recall@k` / `mrr` / ... |

## 3. 数据格式详解

### 3.1 客户端 → 服务端

```json
POST /api/chat/stream
Headers: X-Tenant-Id: demo-tenant
Body:
{
  "message": "I want to refund order 1001 because it is defective",
  "thread_id": "tenant-demo-tenant-abc123def456",   // 可选，省略则服务端生成
  "context": {"page": "order_detail", "order_id": 1001}  // 可选
}
```

### 3.2 服务端 → 客户端（SSE 事件流）

```sse
event: meta
data: {"thread_id": "tenant-demo-tenant-abc123def456", "tenant_id": "demo-tenant"}

event: step_start
data: {"step_id": "step-1", "step_type": "agent_think", "friendly_message": "正在判断你的请求...", "node": "router"}

event: step_end
data: {"step_id": "step-1", "preview": "", "latency_ms": 1240.5, "node": "router"}

event: route
data: {"route": "order_ops", "route_reason": "User asks for refund", "subagent_name": "order_ops", "node": "router"}

event: step_start
data: {"step_id": "step-2", "step_type": "tool_call", "friendly_message": "正在查询订单 1001 状态...", "tool_name": "query_order", "tool_args": {"order_id": 1001}, "node": "order_ops"}

event: step_end
data: {"step_id": "step-2", "preview": "{\"order_id\":1001,\"status\":\"delivered\"}", "latency_ms": 85.2, "node": "order_ops"}

event: step_start
data: {"step_id": "step-3", "step_type": "llm_call", "friendly_message": "正在分析并生成回复...", "node": "order_ops"}

event: step_end
data: {"step_id": "step-3", "preview": "● 已处理：...", "latency_ms": 1820.3, "node": "order_ops"}

event: final
data: {"answer": "● 已处理：订单 1001 退款申请已创建\n\n| 项目 | 内容 |\n| --- | --- |\n| 订单号 | 1001 |\n...", "trace_id": "uuid-xxx", "num_steps": 3, "ok": true}

event: summary
data: {"total_latency_ms": 3145.0, "num_tools_called": 1, "num_llm_calls": 2, "num_steps": 3}
```

### 3.3 服务端落盘数据

**trace 文件**：`data/traces/<thread_id>.jsonl`，每行一个 JSON：

```json
{
  "trace_id": "uuid-xxx",
  "thread_id": "tenant-demo-tenant-abc123def456",
  "tenant_id": "demo-tenant",
  "model_name": "gpt-4o-mini",
  "user_input": "I want to refund order 1001 because it is defective",
  "final_answer": "● 已处理：...",
  "steps": [
    {"type": "llm_call", "latency_ms": 1240.5, "tokens": {"input": 850, "output": 12}, "cost_usd": 0.000213},
    {"type": "tool_call", "name": "query_order", "args": {"order_id": 1001}, "result": "{...}", "latency_ms": 85.2},
    {"type": "llm_call", "latency_ms": 1820.3, "tokens": {"input": 920, "output": 230}, "cost_usd": 0.000689}
  ],
  "total_cost_usd": 0.000902,
  "error": null,
  "started_at": 1721345678.123,
  "finalized_at": 1721345681.268
}
```

**badcase 文件**：`data/flywheel/badcases.jsonl`（用户点 👎）：

```json
{
  "user_input": "I want to refund order 1001 because it is defective",
  "prediction": "● 已处理：...",
  "trace_id": "uuid-xxx",
  "thread_id": "tenant-demo-tenant-abc123def456",
  "tenant_id": "demo-tenant",
  "category": "tool_failed",
  "priority": 1.42,
  "timestamp": "2026-07-20T08:00:00Z"
}
```

## 4. 关键设计点

### 4.1 为什么用 SSE 而非 WebSocket

- **单向推送足够**：前端只读后端的 agent 事件，不需要反向 channel。
- **HTTP/1.1 兼容**：过 CDN / 反向代理无需特殊配置。
- **断线重连简单**：浏览器原生 `EventSource` 自动重连。

### 4.2 为什么用 `subgraphs=True`

不传时：父 `astream()` 只看到子图**完成**时的事件 —— 全部 tool-call / tool-result / final-LLM 一次性 burst 到浏览器，破坏"思考卡片逐条出现"的实时 UX。

传时：chunk 元组变为 `(namespace, mode, data)`，可以监听子图**内部**每个 message 事件（langgraph 0.2+ API）。

### 4.3 为什么用 `astream` 而非 `stream`

`stream`（同步）会**阻塞 event loop**：SSE 事件被 buffer 直到 agent 全部完成才发送，UX 同上崩溃。`astream`（async）是真正的非阻塞，事件能实时流到浏览器。

### 4.4 为什么反向遍历找最后一条 AIMessage

agent 异常终止时 `messages[-1]` 可能是 `ToolMessage`（工具调用结果），原版直接取 `msgs[-1].content` 会把工具返回的 JSON 当答案给用户。`for msg in reversed(msgs)` 找到 `AIMessage` 才 break（坑 #19 / #29）。

### 4.5 为什么 ContextVar 而非全局变量

`skills/commerce.py` 用 `current_tenant_id: ContextVar` 把 tenant_id 透传到工具函数。原因：FastAPI 异步处理多个并发请求时，全局变量会**串号**——A 用户的工具调用可能拿到 B 用户的 tenant_id，导致数据隔离被绕过（坑 #30 教训）。

### 4.6 为什么 lifespan 预热 agent

`api/server.py` 的 `lifespan` 钩子在启动时同步构造 default tenant 的 agent（加载 BGE 95MB + 编译 langgraph + 注册 8 工具），耗 30-100s。如果懒加载，浏览器首请求会超时（坑 #27）。

## 5. 排障速查

| 现象 | 怀疑点 | 排查命令 |
|---|---|---|
| 浏览器 30s 转圈后 ERR_ABORTED | 预热没跑 / 前端 tenant 默认值与后端不匹配 | `curl http://127.0.0.1:8000/api/health` 看 ready 状态；检查 `state.tenantId` 默认值 |
| SSE 一直收到 step_start 没有 step_end | 工具调用卡住 | 看 `data/traces/<thread>.jsonl` 对应 trace 的 tool_call.latency_ms |
| final 事件 answer 是空字符串 | ToolMessage-as-final bug 复发 | `grep -rn "msgs\[-1\]" api/` 全局搜索 |
| 用户反馈落不进飞轮 | `state.tenantId` 与 trace tenant_id 不一致 | 看 badcases.jsonl 是否带 tenant_id 字段 |
| 工具返回 JSON 原始字符串 | agent 没用 LLM 二次转译 | 看 system prompt 的 "工具调用结果必须转译为用户可读语言" 是否还在 |
| history 端点返回空 | trace 没 tenant_id 字段 | 检查 `TraceRecorder.__init__` 是否传 `tenant_id`（坑 #28） |
| 不同用户看到彼此消息 | 默认 thread_id 撞车 | 检查 `_new_thread_id` 是否生成 uuid 后缀（坑 #30） |
