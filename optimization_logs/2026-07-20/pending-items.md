# 待实施项核查（README / AGENT-WORKFLOW 提到但未实现 / 未充分实现）

> 本文档对照 README.md + AGENT-WORKFLOW.md + 9.x 节"5 大模块优化记录"，核查每一项扩展点是否已落地、是否还有未完成工作。
> 状态：`✅ 已完成` / `🟡 部分完成 / 部分接入` / `❌ 未实现` / `⚪ 不在当前范围`

## 1. 核心模块（README.md + AGENT-WORKFLOW 第 1 节）

| # | 模块 | 状态 | 备注 |
|---|---|---|---|
| 1.1 | `config/settings.py` 集中配置 | ✅ | `.env` 加载、pydantic-settings 兜底，38 个字段 |
| 1.2 | `core/llm.py` LLM 工厂 | ✅ | OpenAI 兼容，4 个 provider 通 |
| 1.3 | `core/agent.py` 单 agent 装配 | ✅ | `create_react_agent` + `MemorySaver` |
| 1.4 | `core/multi_agent.py` 多 agent 编排 | 🟡 | router + order_ops + knowledge + escalation 已就位；但**router 是同步调用，阻塞 event loop**（P0-2） |
| 1.5 | `tools/builtin/` 原子工具 | ✅ | calculator / time / search |
| 1.6 | `memory/short_term.py` 短期记忆 | ✅ | deque(maxlen) |
| 1.7 | `memory/long_term.py` 长期记忆 | 🟡 | 13 方法 + Ebbinghaus；**但 fact_extractor 默认关闭**（P2-5） |
| 1.8 | `rag/embeddings.py` Embedding 工厂 | ✅ | OpenAI / 本地 BGE |
| 1.9 | `rag/vectorstore.py` 三后端工厂 | ✅ | faiss / pg_python / pgvector |
| 1.10 | `rag/indexer.py` Indexer | ✅ | 多 collection + CRUD + stats |
| 1.11 | `rag/ingest.py` 文件切分 | ✅ | .txt / .md；缺 PDF / docx（AGENT-WORKFLOW 3.1 扩展点） |
| 1.12 | `skills/commerce.py` 电商 Skill | ✅ | 3 个工具，已接入 agent |
| 1.13 | `skills/summarize.py` 摘要 Skill | ✅ | 1 个工具，已接入 |
| 1.14 | `skills/translator.py` 翻译 Skill | ✅ | 1 个工具，已接入 |
| 1.15 | `skills/code_review.py` 代码审查 | ❌ | 已实现 + 注册但**未接入 agent**（P1-7） |
| 1.16 | `skills/data_analysis.py` 数据分析 | ❌ | 已实现 + 注册但**未接入 agent**（P1-7） |
| 1.17 | `mcp_integration/client.py` MCP 客户端 | ❌ | **仍是 stub**（P2-1） |
| 1.18 | `mcp_integration/registry.py` MCP 注册 | ✅ | 多 server 管理 + YAML 配置加载 |
| 1.19 | `evaluation/runner.py` 全 agent 评估 | ✅ | 8 个 case fixtures |
| 1.20 | `evaluation/retrieval_runner.py` 检索评估 | ✅ | 8 个 case + 6 个 IR 指标 |
| 1.21 | `data_flywheel/collector.py` 飞轮 | 🟡 | 已支持分类/去重/优先级，但**主路径未启用新功能**（P1-5） |
| 1.22 | `observability/tracing.py` 全链路 trace | 🟡 | 完整记录但**latency 全部为 0**（P0-1） |
| 1.23 | `observability/cost.py` 费用估算 | ✅ | PRICE_TABLE 手维护 |
| 1.24 | `post_training/pipeline.py` SFT/DPO | 🟡 | 已有 filtered/enhanced 版本，**默认走老路径**且质量差（P1-4） |
| 1.25 | `post_training/quality.py` 训练数据质量 | ✅ | 13 SFT 指标 + 11 DPO 指标 |
| 1.26 | `mock_platform/` 模拟 Shopify | ✅ | 8001 端口 + 订单/物流/退款 |
| 1.27 | `api/` FastAPI 业务层 | ✅ | 7 个路由分组（chat / kb / feedback / traces / flywheel / dashboard / health） |
| 1.28 | `ecommerce/` 完整电商平台 | ✅ | 8002 端口 + 14 张表 + Vue 3 SPA |

**统计**：28 项核心模块，✅ 完成 20 项（71%），🟡 部分 5 项（18%），❌ 未实现 3 项（11%）。

## 2. 9.x 节"5 大模块优化"扩展点

### 9.1 向量数据库

| # | 扩展点 | 状态 | 备注 |
|---|---|---|---|
| 2.1 | pgvector 扩展 | ❌ | 用户环境无 Windows SDK，**放弃**用纯 Python 兜底 |
| 2.2 | FAISS → PG 迁移脚本 | ✅ | `scripts/migrate_faiss_to_pg.py` 支持 dry-run |
| 2.3 | 48 文档已迁到 PG | ✅ | docs / kb_demo / kb_demo-tenant 3 collection |

### 9.2 Skill 系统

| # | 扩展点 | 状态 | 备注 |
|---|---|---|---|
| 2.4 | `DataAnalysisSkill` | 🟡 | 已实现 + 注册，**未接入 agent**（P1-7） |
| 2.5 | `TranslatorSkill` | ✅ | 已实现 + 已接入 `knowledge_tools` |
| 2.6 | `CodeReviewSkill` | 🟡 | 已实现 + 注册，**未接入 agent**（P1-7） |
| 2.7 | Skill 元数据（version / tags / permissions） | ✅ | tuple 默认值规避可变类属性共享（坑 50） |
| 2.8 | SkillRegistry 6 新方法 | ✅ | unregister / filter_by_tag / enabled_tools 等 |

### 9.3 UI 优化

| # | 扩展点 | 状态 | 备注 |
|---|---|---|---|
| 2.9 | `static/index.html` 聊天页 | ✅ | teal 渐变设计 + Kiki 风格，第 10.3 节重写 |
| 2.10 | 4 view 看板（KB / Trace / 飞轮） | ❌ | 第 10.3 节已删除（用户要求 `/` 只做聊天） |
| 2.11 | `static/admin.html` 运营后台 | ✅ | 4 view（dashboard / KB / trace / flywheel）+ 分页 + 5MB 校验 |
| 2.12 | `static/admin.html` 上传 5MB 客户端校验 | ✅ | 坑 40 |
| 2.13 | `static/admin.html` 客户端 tenant 校验 | ✅ | 坑 37 |
| 2.14 | 前端 tenant 默认值对齐 | ✅ | 坑 32 修复后 `'demo-tenant'` |
| 2.15 | admin 分页 scoped 绑定 | ✅ | 坑 33 修复 |
| 2.16 | admin tableKeyFor fail-fast | ✅ | 坑 34 修复 |
| 2.17 | feedback 真实 user_input / prediction | ✅ | 坑 35 修复 |
| 2.18 | 客服面板 Kiki 风格 | ✅ | 第 9.10 节修复（白底 + mode chip） |

### 9.4 MCP 框架

| # | 扩展点 | 状态 | 备注 |
|---|---|---|---|
| 2.19 | `mcp_integration/protocol.py` 5 模型 | ✅ | MCPToolSpec / MCPResource / MCPPrompt 等 |
| 2.20 | `mcp_integration/registry.py` 多 server | ✅ | MCPServerConfig / MCPServerState / MCPServerRegistry |
| 2.21 | `MCPClient` 5 新方法 | ✅ | list_resources / read_resource / list_prompts / get_prompt / server_info |
| 2.22 | MCP YAML 多 server 配置 | ✅ | `mcp_servers_config_path` |
| 2.23 | 真实 `mcp` SDK 接入 | ❌ | stub（P2-1） |

### 9.5 KB 模块 CRUD

| # | 扩展点 | 状态 | 备注 |
|---|---|---|---|
| 2.24 | `DELETE /api/kb/documents/{id}` | ✅ | PG 走 SQL，FAISS 返回 501 |
| 2.25 | `DELETE /api/kb/collections/{name}` | ✅ | 校验 + 租户匹配防越权 |
| 2.26 | `GET /api/kb/documents` 增强版 | ✅ | offset / limit / source / order |
| 2.27 | `GET /api/kb/documents/{id}/versions` | ✅ | 按 source 查所有 chunk |
| 2.28 | `GET /api/kb/stats` | ✅ | total / by_source / backend / avg_chunk_size |
| 2.29 | `POST /api/kb/upload-incremental` | ✅ | 按 source basename 判重 |

### 9.6 长期记忆增强

| # | 扩展点 | 状态 | 备注 |
|---|---|---|---|
| 2.30 | 13 新方法（`remember_with_importance` / `boost_importance` 等） | ✅ | |
| 2.31 | Ebbinghaus 遗忘曲线 | ✅ | `exp(-days/half_life)`，half_life = base * (1+importance) |
| 2.32 | `fact_extractor.py` LLM 三元组 | ✅ | 防御式 JSON 解析 |
| 2.33 | `memory_tool.py` 2 工具 | ✅ | save_memory / recall_memory，已接入 agent |
| 2.34 | FAISS no-op 兜底 | ✅ | mark_accessed / boost_importance / forget_expired 在 FAISS 上 no-op |
| 2.35 | fact_extractor 默认开启 | ❌ | 默认 False（P2-5） |

### 9.7 召回评估

| # | 扩展点 | 状态 | 备注 |
|---|---|---|---|
| 2.36 | 6 IR 指标（recall@k / mrr / ndcg 等） | ✅ | `evaluation/retrieval_metrics.py` |
| 2.37 | `RetrievalEvalRunner` | ✅ | 8 个 case fixtures |
| 2.38 | CLI `python -m evaluation retrieval` | ✅ | `--dataset --out --collection --k` |
| 2.39 | `evaluation/metrics.py` fuzzy_match + length_ratio | ✅ | |

### 9.8 badcase 飞轮

| # | 扩展点 | 状态 | 备注 |
|---|---|---|---|
| 2.40 | `classifier.py` 9 分类 | ✅ | rule-based + LLM-based hybrid |
| 2.41 | `deduper.py` exact + near-dup | ✅ | cosine ≥ 0.92 |
| 2.42 | `prioritizer.py` 评分公式 | ✅ | frequency × impact × severity × recency_decay |
| 2.43 | `record_case_classified` 等 6 新方法 | ✅ | |
| 2.44 | 主链路（feedback 提交）走新路径 | ❌ | 默认仍走老 `record_case`（P1-5） |

### 9.9 推理加速

| # | 扩展点 | 状态 | 备注 |
|---|---|---|---|
| 2.45 | `PromptCache` LRU + 磁盘 | ✅ | |
| 2.46 | `cached_invoke` helper | ✅ | temperature > 0 跳过 |
| 2.47 | `batch_invoke` ThreadPoolExecutor | ✅ | |
| 2.48 | `abatch_invoke` asyncio.Semaphore | ✅ | |
| 2.49 | `batch_embed` 分块 | ✅ | |
| 2.50 | 28 个推理加速单测 | ✅ | |
| 2.51 | `eval_inference_speed.py` 报告 | ✅ | 4 项关键结论 |
| 2.52 | LLM `invoke` 走 PromptCache | ❌ | `core/llm.py` 未改（P1-6） |
| 2.53 | EvalRunner 并发跑 case | ❌ | 仍顺序（P2-3） |

### 9.10 后训练审查

| # | 扩展点 | 状态 | 备注 |
|---|---|---|---|
| 2.54 | `evaluate_sft` 13 指标 | ✅ | |
| 2.55 | `evaluate_dpo` 11 指标 | ✅ | |
| 2.56 | `recommendations` 生成建议 | ✅ | |
| 2.57 | `build_sft_filtered` 过滤 | ✅ | min/max length + dedup |
| 2.58 | `build_dpo_enhanced` embedding 匹配 | ✅ | 阈值 0.5 |
| 2.59 | `audit_post_training.py` CLI | ✅ | 报告 `data/eval/post_training_audit.md` |
| 2.60 | 训练数据问题修复 | 🟡 | 报告已指出，配对质量仍需优化（P1-4） |
| 2.61 | 实际跑微调（DeepSeek/OpenAI） | ❌ | AGENT-WORKFLOW 3.7 扩展点（P3-3） |

## 3. 总体进度

| 类别 | 总数 | ✅ | 🟡 | ❌ | 完成率 |
|---|---|---|---|---|---|
| 核心模块（1.1-1.28） | 28 | 20 | 5 | 3 | 71% |
| 9.x 扩展点（2.1-2.61） | 61 | 41 | 5 | 15 | 67% |
| **合计** | **89** | **61** | **10** | **18** | **68%** |

## 4. 关键未完成项优先级

按"业务价值 × 实现成本"排序：

| 排序 | 项 | 价值 | 成本 | 建议时机 |
|---|---|---|---|---|
| 1 | P1-7 新 Skill 接入 agent | 高 | 低 | 本周 |
| 2 | P1-5 飞轮主路径走新功能 | 中 | 低 | 本周 |
| 3 | P1-6 PromptCache 接入 LLM | 高 | 低 | 第 2 周 |
| 4 | P0-1 latency 计时真实化 | 高 | 中 | 第 1 周 |
| 5 | P0-2 / P0-4 router/agent async 化 | 高 | 中 | 第 1 周 |
| 6 | P1-4 DPO 配对质量 | 中 | 中 | 第 3 周 |
| 7 | P1-1 trace 索引 | 中 | 中 | 第 2 周 |
| 8 | P2-1 MCP 真实 SDK | 中 | 中 | 第 4 周 |
| 9 | P2-5 fact_extractor 默认开 | 中 | 低 | 第 4 周 |
| 10 | P2-3 EvalRunner 并发 | 低 | 低 | 第 4 周 |
