# 0719agent 系统架构与模块职责

> 本轮 review 产出的架构总览。每个模块一段：职责 / 关键文件 / 数据接口 / 技术选型 / 已知扩展点。

## 1. 系统架构图（三服务）

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          0719agent 三服务架构                                 │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐    SSE     ┌──────────────┐     HTTP    ┌──────────────┐  │
│  │  浏览器      │◄──────────►│  api 服务     │◄──────────►│  mock_platform│  │
│  │  static/     │  fetch+    │  8000 端口   │  httpx     │  8001 端口   │  │
│  │  index.html  │  EventSrc  │  业务 API 层 │  X-Tenant-Id│  模拟 Shopify│  │
│  │  admin.html  │            │  FastAPI     │            │  订单/物流/退款│ │
│  └──────────────┘            └──────────────┘            └──────────────┘  │
│        ▲                            │                                        │
│        │                            │ /customer-service/chat/stream         │
│        │                            ▼                                        │
│  ┌──────────────┐    HTTP     ┌──────────────┐                              │
│  │  浏览器      │◄──────────►│  ecommerce   │                              │
│  │  /shop/      │            │  8002 端口   │                              │
│  │  Vue 3 SPA   │            │  完整电商平台│                              │
│  │              │            │  PostgreSQL  │                              │
│  └──────────────┘            └──────────────┘                              │
│        ▲                                                                      │
│        │ 浏览器                                                                │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

**端口分配**（来自 `config/settings.py`）：

| 端口 | 服务 | 启动方式 |
|---|---|---|
| 8000 | `api` (FastAPI, ReAct agent + 业务 API) | `python -m scripts.run_all` |
| 8001 | `mock_platform` (模拟 Shopify) | 同上一键启动 |
| 8002 | `ecommerce` (独立电商平台) | 同上一键启动（需 PG 16） |

**为何分三服务而非一个**：
- `api` 8000 跑 agent + 业务逻辑，**LLM 调用密集**（每次 chat 1-3s），单独服务便于水平扩容。
- `mock_platform` 8001 是**测试替身**，不接真实 Shopify 时反复重置数据方便。
- `ecommerce` 8002 是**完整业务平台**（PostgreSQL + 14 张表 + 订单生命周期 worker），耦合度低，可以独立迭代。

## 2. 模块依赖图（agent 主链路）

```
┌─────────────────────────────────────────────────────────────────┐
│                       api/server.py 入口                         │
│  lifespan 预热 default_tenant 的 agent（30-100s）                │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                  api/deps.py 依赖注入层                          │
│  @lru_cache 单例：get_llm / get_indexer / get_collector         │
│  per-tenant 缓存：get_agent_for_tenant(tenant_id) → 多租户      │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                core/multi_agent.py 多 agent 编排                │
│  router 节点（LLM 分类）→ order_ops / knowledge / escalation   │
│  外层 MemorySaver checkpointer 持有 thread_id 隔离               │
└────┬──────────┬──────────┬──────────┬───────────────────────────┘
     │          │          │          │
     ▼          ▼          ▼          ▼
  builtin    commerce   rag_search  memory_tools
  tools      (订单/物流/  (BGE 向量  save_memory
  calculator 退款)      库检索)    recall_memory
  time
  search
```

## 3. 各模块职责速查

### 3.1 `core/` — Agent 装配

| 文件 | 职责 | 关键技术选型 |
|---|---|---|
| `llm.py` | LLM 工厂 | `langchain_openai.ChatOpenAI`，OpenAI 兼容协议（OpenAI / DeepSeek / Moonshot / Zhipu 都通） |
| `agent.py` | 单 agent 装配 | `langgraph.prebuilt.create_react_agent` + `MemorySaver` checkpointer |
| `multi_agent.py` | 多 agent 编排 | `langgraph.StateGraph`，router/order_ops/knowledge/escalation 四节点 |
| `prompt_cache.py` | LLM 响应缓存 | LRU + 可选磁盘持久化，`temperature > 0` 跳过（不缓存非确定性） |
| `batch_inference.py` | 批量推理 | `ThreadPoolExecutor` / `asyncio.Semaphore` 两种并发模式 |

**Router LLM 路由设计**（`multi_agent.py:ROUTER_PROMPT`）：

```python
ROUTE_ORDER_OPS   = "order_ops"    # 订单/物流/退款
ROUTE_KNOWLEDGE   = "knowledge"    # 政策/FAQ/商品
ROUTE_ESCALATION  = "escalation"   # 转人工（$200+退款/投诉/法律威胁/第3次退款）
```

Router 故意**不用 `llm.with_structured_output()`**（某些 provider 不支持 `response_format`），改用纯 prompt + JSON 解析，**更便携也更鲁棒**。

### 3.2 `rag/` — 检索增强

| 文件 | 职责 |
|---|---|
| `embeddings.py` | Embedding 工厂：OpenAI / 本地 BGE (`BAAI/bge-small-zh-v1.5`) 二选一 |
| `vectorstore.py` | 三后端工厂：`faiss`（默认）/ `pg_python`（PG + numpy）/ `pgvector`（需扩展） |
| `indexer.py` | 高层接口：add / search / CRUD / stats，**多 collection 管理** |
| `ingest.py` | 文件切分 + 索引入口（支持 .txt / .md） |
| `retriever.py` | LangChain Retriever 适配器 |
| `rag_tool.py` | `rag_search(query)` 工具：top-k 检索，返回拼接文本 |
| `pg_vectorstore.py` | `PGVectorStore` 实现：bytea 存 embedding + numpy 余弦相似度 |

**RAG 关键设计**：
- **多租户隔离**：`collection = f"{kb_collection_prefix}_{tenant_id}"`（如 `kb_demo-tenant`）
- **init placeholder**：`build_vectorstore` 插入一个 `metadata._init = True` 的哨兵 doc，避免空集合
- **per-doc metadata update**：PG 走 JSONB `||` 浅合并，FAISS 不支持（返回 False）

### 3.3 `memory/` — 记忆

| 文件 | 职责 | 关键算法 |
|---|---|---|
| `short_term.py` | 短期对话缓冲 | `collections.deque(maxlen=N)`，FIFO |
| `long_term.py` | 长期向量记忆 | Ebbinghaus 遗忘曲线 `exp(-days/half_life)`，**half_life = base * (1 + importance)**，高重要性衰减更慢 |
| `fact_extractor.py` | LLM 三元组抽取 | `(subject, predicate, object)` + 重要性评分，防御式 JSON 解析 |
| `memory_tool.py` | 暴露给 agent 的工具 | `save_memory(text, importance, category)` / `recall_memory(query, k)` |

**长期记忆关键设计**：
- **重要性分档**：`IMPORTANCE_HIGH=0.9` / `NORMAL=0.5` / `LOW=0.2`
- **有效评分**：`similarity * (importance + decay) / 2`，把语义相似度 × 时间衰减 × 重要性一起考虑
- **per-user namespace**：`metadata.user_id` 字段做跨用户隔离
- **过载采样**：`recall` 时取 `k * 2` 个候选再重排，让高重要性记忆有出头机会

### 3.4 `skills/` — 技能

| Skill | 工具数 | 用途 | 是否接入主链路 |
|---|---|---|---|
| `CommerceSkills` | 3 | query_order / query_logistics / create_refund | ✅ 已接入 `api/deps.py:order_tools` |
| `SummarizeSkill` | 1 | summarize_text | ✅ 已接入 `knowledge_tools` |
| `TranslatorSkill` | 1 | translate_text | ✅ 已接入 `knowledge_tools` |
| `CodeReviewSkill` | 1 | review_code | ❌ 已注册但未接入 agent |
| `DataAnalysisSkill` | 2 | analyze_csv / analyze_json | ❌ 已注册但未接入 agent |

**Skill 元数据**（`skills/base.py`）：
- `version` / `tags` / `permissions` / `dependencies` / `enabled_by_default`
- **用 tuple 默认值**（不是 list）规避可变类属性共享陷阱（坑 50）

### 3.5 `api/` — FastAPI 业务层

| 路由前缀 | 文件 | 主要端点 |
|---|---|---|
| `/api/chat` | `routes/chat.py` | `POST ""` (非流) / `POST /stream` (SSE) / `GET /conversations/{thread_id}/history` |
| `/api/kb` | `routes/kb.py` | `POST /upload` / `GET /search` / `DELETE /documents/{id}` / `DELETE /collections/{name}` / `GET /documents` / `GET /documents/{id}/versions` / `GET /stats` / `POST /upload-incremental` |
| `/api/feedback` | `routes/ops.py` | `POST ""` (提交) / `GET ""` (列表) |
| `/api/traces` | `routes/ops.py` | `GET ""` / `GET /{trace_id}` |
| `/api/flywheel` | `routes/ops.py` | `GET /stats` / `POST /post-train` |
| `/api/dashboard` | `routes/ops.py` | `GET ""` (聚合统计) |
| `/api/health` | `routes/ops.py` | `GET ""` |

**安全机制**：
- 所有外部 ID（`tenant_id` / `thread_id` / `trace_id`）经 `validate_safe_id` 拦截路径遍历
- header / settings 默认值绕过 Pydantic，**手动 try/except 二次校验**
- `feedback_router.get` 合并 good + bad 两库，新到旧排序
- `get_trace` 强制 tenant 隔离（不匹配返回 403）

### 3.6 `observability/` — 可观测性

| 文件 | 职责 |
|---|---|
| `tracing.py` | 一次 agent 调用的全链路记录：LLM calls / tool calls / final answer / latency / token / cost |
| `cost.py` | Token 用量提取 + 费用估算（手维护 `PRICE_TABLE`） |

**Trace 文件布局**：`data/traces/<thread_id>.jsonl`，每行一次调用的完整 trace。

**已知短板**：
- `record_llm_call` 的 `latency_ms=0`（stream 模式拿不到，**已知扩展点**）
- 没接 LangSmith / LangFuse
- 没真实 latency 计时（应该按 node 维度 `time.perf_counter`）

### 3.7 `data_flywheel/` — 数据飞轮

| 文件 | 职责 |
|---|---|
| `collector.py` | BadCase / GoodCase JSONL 持久化 + 自动分类 + 去重 + 优先级 |
| `classifier.py` | 9 分类（rule-based + LLM-based hybrid） |
| `deduper.py` | exact-dup + near-dup（embedding cosine ≥ 0.92） |
| `prioritizer.py` | 优先级评分：`frequency × impact × severity × recency_decay` |
| `storage.py` | `JsonlStore` append-only JSONL |

**优先级评分公式**：
```
priority = log(1 + occurrence_count) × impact_for_text × severity_for_category × exp(-age_days/30)
```

### 3.8 `post_training/` — 后训练

| 文件 | 职责 |
|---|---|
| `pipeline.py` | SFT / DPO 数据集生成（`build_sft` / `build_sft_filtered` / `build_dpo` / `build_dpo_enhanced`） |
| `quality.py` | 训练数据质量评估（13 SFT 指标 + 11 DPO 指标） |

**已发现的训练数据问题**（来自 `audit_post_training.py`）：
- SFT 重复率 50%（8 条只有 4 条唯一）
- DPO 只有 1 对（转化率 14.3%），chosen/rejected 误配（计算器答退款）
- chosen 长度偏置：16 字符 vs 612 字符

### 3.9 `mcp_integration/` — MCP 集成

| 文件 | 状态 |
|---|---|
| `client.py` | **仍是 stub**：`connect()` no-op，`as_tools()` 返回空 list。**已知扩展点**（接官方 `mcp` SDK） |
| `protocol.py` | 5 个 pydantic 模型（MCPToolSpec / MCPResource / MCPPrompt / MCPToolCallResult / MCPCapability） |
| `registry.py` | MCPServerConfig / MCPServerState / MCPServerRegistry 多 server 管理 |

**避坑关键**：包名**不能叫 `mcp`**（会屏蔽官方 PyPI SDK），用 `mcp_integration`（坑 1）。

### 3.10 `evaluation/` — 评估

| 文件 | 职责 |
|---|---|
| `runner.py` | 全 agent 评估（LLM-based） |
| `retrieval_runner.py` | 检索评估（无 LLM，调 indexer） |
| `metrics.py` | exact_match / contains / llm_judge / fuzzy_match / length_ratio |
| `retrieval_metrics.py` | recall@k / precision@k / mrr / ndcg / hit_rate / average_precision |
| `__main__.py` | CLI：`python -m evaluation retrieval` / `python -m evaluation answers` |
| `fixtures/` | 8 个 answer case + 8 个 retrieval case（中文电商场景） |

### 3.11 `ecommerce/` — 完整电商平台（8002）

| 层 | 文件 | 职责 |
|---|---|---|
| 配置 | `config.py` | Pydantic Settings |
| 数据库 | `db/base.py` + `db/models.py` | SQLAlchemy 2.0 + 14 张表 |
| 种子 | `db/seed.py` | 120 商品 + 8 大类 + 3 优惠券 |
| 业务 | `services/` | cart / order / recommend / product |
| 路由 | `routes/` | catalog / cart / users / orders / recommend / customer_service |
| 前端 | `static/shop/` | Vue 3 SPA（CDN，无构建工具） |

**订单生命周期**（`server._order_lifecycle_worker`，每 15s）：
```
pending_payment → paid → shipped (60s后) → delivered (5min后) → completed
```

## 4. 关键技术选型

| 维度 | 选型 | 原因 |
|---|---|---|
| Agent 框架 | LangChain + langgraph | 生态最成熟，ReAct 开箱即用 |
| LLM 协议 | OpenAI 兼容 | 一份代码跑 OpenAI / DeepSeek / Moonshot / Zhipu |
| Embedding | sentence-transformers (BGE) 默认 | 本地免 token，无代理权限问题（坑 9） |
| 向量库 | FAISS 默认 / PG 兜底 | FAISS 零基础设施；PG 适合企业级 |
| Checkpointer | MemorySaver（内存） | 单进程够用，**多 worker 需换 SQLite/PG** |
| 后端 | FastAPI + uvicorn + sse-starlette | 原生 SSE 支持，async 友好 |
| 前端 | 原生 JS + Vue 3（CDN） | 无构建工具，开箱即跑 |
| ORM | SQLAlchemy 2.0 | 新 API（`Mapped[T]`），不用 1.x 风格（坑 42） |
| 配置 | pydantic-settings | 类型安全，`.env` 自动加载（坑 8：先 `load_dotenv` 再 `import settings`） |
| 多租户 | `contextvars.ContextVar` + HTTP 头 | 不用全局变量（避免并发串号，坑 30 教训） |

## 5. 数据持久化目录

```
data/                          # 全部运行时产物（.gitignore）
├── vectorstore/               # FAISS 索引文件
│   ├── documents/             # collection 1
│   ├── kb_demo-tenant/        # tenant 1 的 KB
│   └── long_term_memory/      # 长期记忆
├── traces/                    # 一次 agent 调用的全链路 trace
│   ├── <thread_id>.jsonl      # 每行一个 trace
│   └── ...
├── flywheel/                  # 数据飞轮
│   ├── badcases.jsonl
│   └── goodcases.jsonl
├── post_training/             # 训练数据
│   ├── sft.jsonl
│   └── dpo.jsonl
├── eval/                      # 评估产物
│   ├── results/               # EvalRunner 报告
│   ├── inference_speed_report.md
│   └── post_training_audit.md
└── (PG 业务表，电商模块专用)
```

## 6. 启动链路

```bash
# 一键启动（推荐）
python -m scripts.run_all
# 等 30-100s 看到 "0719agent Commerce Platform is up"
# 访问 http://127.0.0.1:8000/ 看聊天
# 访问 http://127.0.0.1:8000/admin 看运营后台
# 访问 http://127.0.0.1:8002/shop 看电商前台（需先装 PG16 + 跑 seed）
```

详见 `AGENT-WORKFLOW.md` 第 2 节"启动/验证命令"。
