# 0719agent

一个用 LangChain + langgraph 搭起来的 **跨境电商 AI 客服 SaaS 平台**。把 ReAct agent 挂载到 FastAPI 业务层，对接 mock Shopify，端到端跑通"采集 → 处理 → 分析 → 决策 → 执行 → 反馈"的数据闭环。

底层保留了一个完整 Agent 项目应有的所有核心模块（tools / RAG / memory / skills / MCP / evaluation / flywheel / observability / post-training），每个模块都按"最小但可扩展"的原则实现，方便后续针对单个细节深入迭代。

## 包含的模块

| 模块 | 路径 | 作用 |
| --- | --- | --- |
| **核心 Agent** | `core/` | LLM 工厂 + ReAct Agent 主循环（基于 langgraph） |
| **工具** | `tools/` | 内置 calculator / time / search 三个工具 + 工具注册中心 |
| **记忆** | `memory/` | 短期对话记忆 + 长期向量记忆（复用 RAG 向量库） |
| **RAG** | `rag/` | embeddings + FAISS 向量库 + indexer + retriever + rag_tool + ingest 入口 |
| **MCP** | `mcp_integration/` | Model Context Protocol 客户端骨架（无服务器时自动 no-op） |
| **技能** | `skills/` | 比工具更高层的 capability，示例：summarize / commerce（订单/物流/退款）|
| **评估** | `evaluation/` | YAML 样例集 + 指标（exact_match / contains / llm_judge）+ runner |
| **数据飞轮** | `data_flywheel/` | badcase / goodcase 收集，JSONL 存储 |
| **可观测性** | `observability/` | 每次 agent 调用的 trace（LLM/tool 步骤、token、cost）+ 飞轮联动 |
| **后训练** | `post_training/` | 把飞轮数据导出成 SFT / DPO 训练集 |
| **业务平台** | `api/` + `mock_platform/` + `static/` | FastAPI Web 服务 + 模拟 Shopify + 单页面 UI |
| **脚本** | `scripts/` | 一键启动 + 端到端验证 |
| **样本数据** | `samples/` | 卖家 FAQ / 商品目录 / 退换货政策（demo 用）|

## 快速开始

### 1. 环境要求
- Python 3.10+（项目用了 `str | None` 等新语法）
- 任意 OpenAI 兼容的 LLM API（OpenAI / DeepSeek / Moonshot / Zhipu 等）

### 2. 安装
```powershell
# 建议用 Python 3.11 建独立 venv
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> Embedding 默认走本地 `sentence-transformers`（已在 `requirements.txt` 中，首次启动会自动下载 `BAAI/bge-small-zh-v1.5` 模型约 95MB 到 `C:\Users\<user>\.cache\huggingface\`）。
> 如需改用 OpenAI embedding，在 `.env` 中设置 `EMBEDDING_PROVIDER=openai` 并确保 token 有 embedding 权限。

### 3. 配置
```powershell
Copy-Item .env.example .env
# 然后编辑 .env，填入 OPENAI_API_KEY / OPENAI_API_BASE / LLM_MODEL_NAME
```

### 4. 启动 Web 平台（推荐入口）

```powershell
# 一键启动 mock_platform (8001) + api (8000) 两个服务
.venv\Scripts\python.exe -m scripts.run_all
```

等服务起来后（看到 "0719agent Commerce Platform is up"）：

- **浏览器访问** `http://127.0.0.1:8000/` → Web 控制台
  - 左栏：实时聊天（SSE 流式输出 + 每步推理过程，👍/👎 反馈喂飞轮）
  - 中栏：知识库管理（上传 .txt/.md、一键加载 samples、调试检索）
  - 右栏：可观测看板（good/bad case 计数、avg latency、total cost、最近 trace 列表、最近 badcase、post-training 触发按钮）
- **API 文档** `http://127.0.0.1:8000/docs` （Swagger UI，可手动调任意接口）
- **Mock 平台** `http://127.0.0.1:8001/health` （模拟 Shopify）
- **另开终端** 跑 `python scripts/demo.py` 做 8 步端到端验证

试一试：在 Web 控制台点 "Load samples" → 在聊天框输入：
- `What is your return policy for defective products?`（agent 调 RAG）
- `I want to refund order 1001 because it is defective.`（agent 调 query_order + create_refund）

### 5. CLI 用法（开发调试）

```powershell
# 交互式对话（支持多轮记忆，输入 reset 清空记忆，exit 退出）
# 每次回答后会问 helpful? (y/n/<enter>=skip)，y/n 会带 trace_id 写入飞轮
.venv\Scripts\python.exe main.py chat

# 把文件/目录索引进 RAG 向量库（支持 .txt / .md，目录会递归）
.venv\Scripts\python.exe main.py ingest README.md AGENT-WORKFLOW.md --collection docs

# 跑评估集（每个 case 自动 trace + 喂飞轮，表格里显示 trace_id）
.venv\Scripts\python.exe main.py eval

# 查看最近的 trace（按 latency 排序，用于诊断 badcase）
.venv\Scripts\python.exe main.py traces --limit 20

# 查看飞轮 badcase / goodcase 计数
.venv\Scripts\python.exe main.py flywheel

# 根据飞轮数据生成 SFT / DPO 训练集
.venv\Scripts\python.exe main.py post-train
```

### 6. 测试
```powershell
# 83 个单测，覆盖 smoke import / calculator / flywheel / ingest / observability / evaluation
# 不依赖 LLM，~2.5s 跑完；测试期间 data/ 目录会被重定向到 tmp_path，不会污染真实数据
.venv\Scripts\python.exe -m pytest -q
```

## 项目结构

```
0719agent/
├── main.py                  # CLI 入口：chat / ingest / eval / flywheel / post-train / traces
├── pytest.ini               # pytest 配置
├── requirements.txt
├── .env.example
├── config/                  # 配置中心（pydantic-settings）
├── core/                    # LLM + Agent builder
├── tools/                   # 内置工具 + 注册中心
├── memory/                  # 短期 / 长期记忆
├── rag/                     # embeddings / vectorstore / indexer / retriever / rag_tool / ingest
├── mcp_integration/         # MCP 客户端（命名避开官方 mcp SDK 冲突）
├── skills/                  # 技能基类 + summarize + commerce（订单/物流/退款）
├── evaluation/              # 评估指标 + runner + fixtures
├── data_flywheel/           # badcase / goodcase 收集
├── observability/           # tracing（trace 事件） + cost（token 估算）
├── post_training/           # SFT / DPO 数据生成
├── mock_platform/           # 模拟 Shopify（订单/物流/退款），端口 8001
├── api/                     # FastAPI 业务层，端口 8000（chat SSE / kb / feedback / traces / dashboard）
├── static/                  # 单页面 Web UI（聊天+KB+trace+飞轮看板）
├── samples/                 # demo 用样本（FAQ / 商品 / 退换货政策）
├── scripts/                 # run_all.py（一键启动）+ demo.py（端到端验证）
├── tests/                   # pytest 单测（smoke / calculator / flywheel / ingest / observability / evaluation）
└── data/                    # 运行时产物（向量库、飞轮、trace、训练集，已 gitignore）
```

## 数据流（完整闭环）

```
买家消息 ──> /api/chat ──> Agent (ReAct)
                            │  └─[observability/tracing 旁路记录每一步 LLM/tool 事件 + token + cost]
                            │
                            ├─> builtin tools (calculator / time / search)
                            ├─> RAG tool ──> FAISS  ◀── /api/kb/upload 把 .txt/.md 索引进来
                            ├─> Skill: summarize
                            └─> Skill: commerce ──> mock_platform (订单/物流/退款)
                            │
                            └─> 答案 ──> Web UI 👍/👎 ──> /api/feedback ──> 飞轮(带 trace_id)
                                                                │
                                                                └─> /api/flywheel/post-train
                                                                      │
                                                                      └─> SFT/DPO JSONL
                                                                            │
                                                                            └─> 上传到 DeepSeek/OpenAI 微调
                                                                                  │
                                                                                  └─> fine-tuned model ──> 回到 Agent
```

`trace_id` 是连接 chat → flywheel → trace 文件的唯一钥匙：发现一个 badcase 后，从飞轮里读到 `trace_id`，再去 `data/traces/<thread>.jsonl` 里 grep 这个 id，就能看到那一次调用的每一步 LLM 输出和工具结果。

### 多租户
通过 HTTP 头 `X-Tenant-Id` 传递（Web UI 右上角可设置）。每个租户有独立的：
- mock_platform 订单/物流/退款数据（首次访问时从 seed 深拷贝）
- FAISS 向量库 collection（`kb_{tenant_id}`）
- 编译好的 agent 实例（`api/deps.py` 缓存）

## 后续扩展方向

每个模块都在源文件头部注释里列出了"Future expansion hooks"，主要方向：
- **业务平台**：接真实 Shopify OAuth、多 LLM 路由降成本、转人工工作流、真实微调闭环、扩 Shopee/TikTok Shop、Postgres + RLS、K8s 弹性扩容
- **RAG**：混合检索（BM25 + dense）、reranking、更多 loader（PDF/docx/HTML）、语义切分、元数据过滤
- **MCP**：接入官方 `mcp` SDK，支持多服务器、资源/prompt、OAuth
- **Skills**：插件化发现、版本管理、每技能独立评估
- **Memory**：摘要记忆、实体记忆、跨会话用户画像
- **Evaluation**：LLM-as-judge、工具调用轨迹评估、回归看板
- **Data Flywheel**：自动 badcase 检测、人工标注 UI、去重与质量过滤、DPO 偏好对
- **Observability**：LangSmith/LangFuse 导出、真实 latency 计时、trace replay、实时 dashboard
- **Post Training**：指令增强、偏好对构造、train/eval 切分、对接微调 API

详见 `AGENT-WORKFLOW.md`。
