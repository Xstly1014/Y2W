# 问题清单 + 优先级 + 修复方案 + 验收标准

> 本轮 review 产出的 actionable backlog。按严重度分级（P0=线上必修 / P1=近期修 / P2=中期待办 / P3=长期规划）。每条都给出**根因、修复路径、可量化验收标准**。

## 1. 严重度分级标准

| 级别 | 定义 | SLA |
|---|---|---|
| **P0** | 线上功能不可用 / 数据泄露 / 安全漏洞 | 立即修，1 周内 |
| **P1** | 性能 / 可观测性 / 可维护性严重短板 | 2 周内 |
| **P2** | 体验问题 / 文档缺失 / 边缘 case | 1 个月内 |
| **P3** | 锦上添花 / 长期规划 | 季度内 |

## 2. P0 问题（必修）

### P0-1: `observability/tracing.py` 的 `latency_ms` 全部为 0

| 字段 | 内容 |
|---|---|
| **现状** | `recorder.record_llm_call(msg, latency_ms=0.0)` 硬编码传 0；`record_tool_call(...)` 同理。所有 trace 的 step 延迟都是 0，dashboard 的 latency 统计完全无意义 |
| **根因** | `chat.py` 第 709 行 `recorder.record_llm_call(msg, latency_ms=0.0)` 是 trace recording 路径，但 `record_llm_call` 的 `latency_ms` 参数本可以从 streaming chunk 的时间戳算出来。SSE 的 messages stream 模式其实有 langgraph 提供的 `metadata` 信息 |
| **影响** | trace 价值减半（不知道哪一步慢）；性能瓶颈定位失效；用户付 LLM 费用但看不到 latency 投入 |
| **修复路径** | 1) 在 `_iter_message_events` 里用 `time.perf_counter()` 记录每个 step 的开始/结束时间；2) `record_llm_call` / `record_tool_call` 接受可选 `latency_ms`，从 `ctx.start_times[step_id]` 取值；3) `chat.py` 调用时传真实 latency；4) 加 unit test 验证 latency > 0 |
| **验收标准** | `data/traces/<thread>.jsonl` 中每个 `llm_call` 和 `tool_call` 的 `latency_ms` 字段 > 0；`summary` 事件的 `total_latency_ms` 与各步相加误差 < 5% |
| **代码定位** | `observability/tracing.py:88`（record_llm_call）, `api/routes/chat.py:709`（调用处） |

### P0-2: `core/multi_agent.py` router 节点 LLM 同步调用阻塞 FastAPI event loop

| 字段 | 内容 |
|---|---|
| **现状** | router 节点是 `def` 同步函数，内部调 `llm.invoke(...)`，每次 chat 都阻塞 1-3s |
| **根因** | langgraph 的 `StateGraph` 节点支持 async（`async def`），但 router 写成了同步。FastAPI 的 SSE handler 在 `event_gen` 内 await，但 `agent.astream` 内部 await 节点时如果节点是同步函数，事件循环被阻塞 |
| **影响** | 同一进程内所有其他 SSE 流同时卡住（多用户并发时 router latency 翻倍）；与 SSE 实时 UX 设计目标冲突 |
| **修复路径** | 1) router 节点改为 `async def router_node(state)`；2) 内部用 `await llm.ainvoke(...)` 替代 `llm.invoke(...)`；3) 子 agent（order_ops / knowledge）节点同步检查并改 async；4) 加并发压测：10 个并发 chat，p99 latency 改善 |
| **验收标准** | 用 `pytest` + `httpx.AsyncClient` 起 10 个并发 `/api/chat/stream` 请求，p50/p95/p99 latency 与单请求差 < 30% |
| **代码定位** | `core/multi_agent.py:router 节点` |

### P0-3: `data_flywheel/storage.py` 的 `JsonlStore` 并发不安全

| 字段 | 内容 |
|---|---|
| **现状** | `JsonlStore` 的 `append` 用了 `threading.Lock`，但 `BadCaseCollector._increment_occurrence` 等"读 → 改 → 写"操作没有锁，多 worker 并发会丢数据 |
| **根因** | dedup / priority ranking 路径需要 read-all → mutate → clear+rewrite，但 `clear` + 多次 `append` 不是原子操作；两 worker 同时跑会相互覆盖 |
| **影响** | 多 worker 部署时 flywheel 计数失真、badcase 重复计数、priority 评分漂移 |
| **修复路径** | 1) 加 `fcntl.flock(file, LOCK_EX)`（Unix）或 `msvcrt.locking`（Windows）做跨进程文件锁；2) 或迁移到 SQLite（`data/flywheel.db` 单表 `cases(id, type, user_input, prediction, trace_id, ...)`）；3) 短期至少加一个 per-instance 的 `read-modify-write` 互斥锁（注意只锁同实例不同线程还不够，跨进程还是危险） |
| **验收标准** | 启动 2 个 api worker 同时跑 eval（自动产生 feedback），badcases.jsonl 计数 = 单 worker 跑两轮的累加（无丢、无重） |
| **代码定位** | `data_flywheel/storage.py`, `data_flywheel/collector.py:_increment_occurrence` |

### P0-4: `core/multi_agent.py` 子 agent 的 LLM 同步调用同样阻塞

| 字段 | 内容 |
|---|---|
| **现状** | order_ops / knowledge 子 agent 是 `create_react_agent(...)` 创建的，内部 ReAct 循环的 LLM 调用是 `invoke()` 同步 |
| **根因** | `create_react_agent` 的 `agent_executor` 节点是 `def` 同步 |
| **影响** | 同 P0-2，但更严重：每次 chat 调 LLM 1-N 次，每次 1-3s，子 agent 阻塞 = 整个 stream 阻塞 |
| **修复路径** | 检查 langgraph prebuilt 是否支持 async 版本；如果是 langchain `create_react_agent`，则需手动用 `Runnable` 包装并把节点改 async |
| **验收标准** | 与 P0-2 相同 |
| **代码定位** | `core/multi_agent.py:280-285` |

## 3. P1 问题（近期修）

### P1-1: `api/routes/ops.py` traces 列表读全文件每次都全量扫

| 字段 | 内容 |
|---|---|
| **现状** | `GET /api/traces` 实现是用 `Path.glob('*.jsonl')` 然后逐个 `open().readlines()`，每次请求扫所有文件 + 解析所有行 |
| **根因** | 没有索引；trace 文件没有按时间分桶（全部堆在 `data/traces/`） |
| **影响** | traces 数量 > 10K 时 dashboard 拉列表 > 2s，前端转圈 |
| **修复路径** | 1) 加内存索引 `data/traces/_index.json`（每条 trace 的 trace_id / thread_id / started_at / tenant_id），trace finalize 时增量更新；2) 列表端点只读 index，不扫文件；3) 加单元测试：10K trace 时列表 < 100ms |
| **验收标准** | 构造 10K 模拟 trace，列表端点 p95 < 200ms |
| **代码定位** | `api/routes/ops.py:list_traces` |

### P1-2: 缺真实 latency 计时 + SSE 实时 tail

| 字段 | 内容 |
|---|---|
| **现状** | 见 P0-1；附带还缺 SSE tail 推送（前端必须手动 refresh 才能看到新 trace） |
| **修复路径** | 1) P0-1 修完后，2) 单独加一个 WebSocket 端点 `/api/traces/tail` 用 `asyncio` 监听 `_index.json` 的修改事件并实时推送给订阅者 |
| **验收标准** | 后端写入新 trace 后 1s 内 dashboard 自动追加，无须刷新 |
| **代码定位** | 新建 `api/routes/trace_ws.py` |

### P1-3: LangSmith / LangFuse 未接入

| 字段 | 内容 |
|---|---|
| **现状** | 所有 trace 只写本地 JSONL，无法跨服务/跨实例聚合 |
| **修复路径** | `observability/tracing.py` 增加 `LANGCHAIN_API_KEY` 检测：有则开 `langchain.callbacks.LangChainTracer` 同步上报，无则本地写 |
| **验收标准** | 配置 `LANGCHAIN_API_KEY` 后，https://smith.langchain.com 能看到实时 trace |
| **代码定位** | `observability/tracing.py:_write` |

### P1-4: `post_training/pipeline.py` DPO 配对质量差

| 字段 | 内容 |
|---|---|
| **现状** | 审计报告：chosen 16 字符（计算器答退款）、rejected 612 字符，Jaccard 误配 |
| **根因** | 用 token Jaccard 匹配 "bad input ≈ good input"，但 token 集合相似的两个 input 主题可能完全不一样（"refund order 1001" vs "calculate 1+1" 都包含数字） |
| **修复路径** | 1) `build_dpo_enhanced` 已在 `min_similarity=0.5` 默认下用 embedding cosine（比 Jaccard 强）；2) 加额外校验：chosen 和 rejected 主题必须同 domain（用关键词权重或第二个 embedding 判别）；3) 配对时让 LLM judge "这个 chosen 是否能解答 rejected 的 prompt" |
| **验收标准** | `python -m scripts.audit_post_training` 输出：identical_pairs=0 / chosen_rejected_overlap > 0.3 / low_similarity_pairs=0 |
| **代码定位** | `post_training/pipeline.py:201-` |

### P1-5: 数据飞轮的 3 类「造好零件但没装上车」功能

| 字段 | 内容 |
|---|---|
| **现状** | `classifier` / `deduper` / `prioritizer` 三个新模块已实现，但 `BadCaseCollector.record_case` 默认走的是老路径，没用新功能 |
| **根因** | 老接口（`record_case`）保留向后兼容，新接口（`record_case_classified`）没人调用 |
| **修复路径** | 1) `api/routes/ops.py:record_feedback` 改用 `record_case_classified`，自动跑 classify + dedup + priority；2) `data_flywheel/collector.py` 加开关 `enable_auto_classify=True`，跑新路径；3) 单元测试验证 feedback 提交后 badcase 立即带 category + priority 字段 |
| **验收标准** | `curl -X POST /api/feedback` 后，badcases.jsonl 立即包含 `category` / `priority` / `is_dup` 字段 |
| **代码定位** | `api/routes/ops.py:feedback_router.post`, `data_flywheel/collector.py` |

### P1-6: `core/prompt_cache.py` 与 `core/batch_inference.py` 未接入主链路

| 字段 | 内容 |
|---|---|
| **现状** | 实现了 `cached_invoke` / `batch_invoke` / `abatch_invoke`，但 `core/multi_agent.py` 的 LLM 调用是直接的 `llm.invoke(...)`，没经过 cache |
| **影响** | 评估报告说"客服场景 ~40% 命中率，命中时延迟 -80%、费用 -50%"——但实际没接，**潜在收益未实现** |
| **修复路径** | 1) `core/llm.py` 加一个 `CachingLLM` wrapper，`invoke` 时先查 `PromptCache`；2) 评估场景（`EvalRunner.invoke`）换成 `batch_invoke` 并发跑 8 个 case；3) 配置开关 `llm_prompt_cache_enabled=True` |
| **验收标准** | 配置开启后跑 `python -m evaluation answers` 两次，第二次有 N% case 是 prompt cache hit（看 trace 的 `cache_hit: true` 字段） |
| **代码定位** | `core/llm.py:build_llm` |

### P1-7: 新增的 3 个 Skill（`code_review` / `data_analysis` / `translator`）未接入 agent

| 字段 | 内容 |
|---|---|
| **现状** | `skills/__init__.py` 导出 + `skills/registry.py` 注册，但 `api/deps.py:get_agent_for_tenant` 没把 `CodeReviewSkill` 和 `DataAnalysisSkill` 注入到 `order_tools` 或 `knowledge_tools` |
| **影响** | 用户问 "review this code" 或 "analyze this CSV" 时 agent 没工具可用，只能说"我没有这个能力" |
| **修复路径** | 1) 把 `CodeReviewSkill(llm).get_tools()` 加到 `knowledge_tools` 末尾；2) `DataAnalysisSkill` 同理；3) 更新 `multi_agent` 的 prompt 让 router 知道这些新工具的存在；4) 加集成测试：code review 问 → agent 调 `review_code` 工具 → 拿到 markdown 报告 |
| **验收标准** | `python scripts/demo.py` 加一步：`curl -X POST /api/chat/stream -d '{"message":"review this: def foo(): return 1"}'`，trace 里能看到 `tool_call: review_code` |
| **代码定位** | `api/deps.py:122-136` |

## 4. P2 问题（中期待办）

### P2-1: `mcp_integration/client.py` 仍是 stub

| 字段 | 内容 |
|---|---|
| **现状** | `connect()` no-op，`as_tools()` 返回空 list，registry 已能管多 server 但实际没 server 可连 |
| **修复路径** | 接官方 `mcp` SDK（已经在 `requirements.txt` 注释中），实现 `connect` + `list_tools` + `call_tool` |
| **验收标准** | 配置一个 `filesystem` server，agent 能用 `read_file` / `list_directory` 工具 |
| **代码定位** | `mcp_integration/client.py` |

### P2-2: trace 文件缺租户分离目录

| 字段 | 内容 |
|---|---|
| **现状** | 所有 tenant 的 trace 都堆在 `data/traces/`，按 `thread_id` 区分（但 `thread_id` 包含 tenant 前缀） |
| **影响** | 排查时 `ls data/traces/` 几千个文件，UI 体验差 |
| **修复路径** | 改成 `data/traces/<tenant_id>/<thread_id>.jsonl`；`api/routes/chat.py:_traces_dir` + `observability/tracing.py:_traces_dir` 同步修改 |
| **验收标准** | `ls data/traces/demo-tenant/` 看到该租户所有 trace |
| **代码定位** | `observability/tracing.py:42-45` |

### P2-3: `evaluation/runner.py` 不支持并发跑 case

| 字段 | 内容 |
|---|---|
| **现状** | `EvalRunner.run` 是顺序 for 循环，8 个 case × 2s/call = 16s |
| **修复路径** | 用 `ThreadPoolExecutor` / `asyncio.gather` 并发 4 个 case |
| **验收标准** | 跑 `python -m evaluation answers` 时间从 16s 降到 5s |
| **代码定位** | `evaluation/runner.py:run` |

### P2-4: 缺 RAG 召回诊断工具

| 字段 | 内容 |
|---|---|
| **现状** | `GET /api/kb/search` 是 debug 端点，但 dashboard 没有可视化界面 |
| **修复路径** | admin.html KB view 加一个 "诊断：搜 'order refund' 看命中文档" 按钮，调用 `/api/kb/search?q=...&k=5` |
| **验收标准** | dashboard 一键查看知识库召回质量（hit content + score） |
| **代码定位** | `static/admin.html` |

### P2-5: 长期记忆的 fact_extractor 默认关闭

| 字段 | 内容 |
|---|---|
| **现状** | `LONG_TERM_MEMORY_EXTRACT_FACTS=False`，LLM 三元组抽取路径没启用 |
| **修复路径** | 默认 True + 让 LLM 在保存时自动抽取 fact 写入 |
| **验收标准** | 多轮对话后 `list_user_memories` 返回 `[(subject, predicate, object), ...]` 三元组 |
| **代码定位** | `memory/long_term.py:remember_with_importance` |

## 5. P3 问题（长期规划）

### P3-1: 多 LLM 路由

简单 FAQ 用 DeepSeek，复杂投诉用 GPT-4o。LLM 成本预计降 80%。详见 `AGENT-WORKFLOW.md:3.9.2`。

### P3-2: 转人工工作流

agent 检测到「refund > $200 / 投诉物流 / 法律威胁 / 第 3 次退款」时返回 `escalate: true`，前端弹转人工按钮，写 `data/escalations/`。

### P3-3: 真实微调闭环

`/api/flywheel/post-train` 现在只生成 JSONL；扩展为调 DeepSeek/OpenAI fine-tune API，轮询任务状态。

### P3-4: 多渠道扩展

Shopee / TikTok Shop / Amazon，每个渠道一个 `skills/{channel}.py`，统一接口。

### P3-5: Postgres + 多租户 RLS

迁到 Postgres，每张表加 `tenant_id` 列 + Row-Level Security。

## 6. 验收节奏建议

| 时间 | 应完成 |
|---|---|
| 第 1 周 | P0-1 ~ P0-4（修完后单测 + 端到端 demo.py 必须 PASS） |
| 第 2-3 周 | P1-1 ~ P1-7（含功能接入 + 性能基准测试） |
| 第 4 周 | 端到端联调 + 性能基线（p95 < 3s）+ audit 报告 |
| 第 2 个月 | P2-1 ~ P2-5（基础设施建设） |
| 第 3 个月+ | P3（业务扩张） |
