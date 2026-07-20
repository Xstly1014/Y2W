# 0719agent Agent 链路全面 Review 报告

> **日期**：2026-07-20  
> **范围**：核心 agent 链路（前端 → API → agent → 工具 → 回流）  
> **背景**：本轮在修复客服面板 Kiki 样式 + FormData Blob 报错后，对整个项目做全面系统性 review。  
> **目标**：梳理已实现模块 / 找出待实施项 / 列出问题清单 / 制定修复方案。

## 1. TL;DR

| 维度 | 评分 | 关键结论 |
|---|---|---|
| **模块完整度** | ⭐⭐⭐⭐ | 11 个核心模块全部到位（agent / RAG / memory / skills / MCP / eval / flywheel / observability / post-training / api / ecommerce），但 **部分新增强功能未接入主链路**（badcase 分类/去重/优先级、prompt cache、batch inference、新 Skills） |
| **代码质量** | ⭐⭐⭐⭐ | 类型注解齐全、防御式错误处理成熟、安全（路径遍历、prompt injection、AST DoS 都已修）。**但有几处函数定义重复**和**`observability/tracing.py` 的 latency 全部为 0**（不准确） |
| **稳定性** | ⭐⭐⭐⭐ | 121 单测全过（~2.8s），冷启动预热机制成熟（lifespan warmup）。**但 `JsonlStore` 并发下不安全**（增量去重要 read-all → rewrite） |
| **性能** | ⭐⭐⭐ | LLM 调用 ~1-3s（正常），RAG 检索 ~50ms。**主要瓶颈在 router LLM 同步调用**（会阻塞 FastAPI event loop）和 **traces 列表读全文件每次都全量扫** |
| **可观测性** | ⭐⭐⭐⭐ | trace 完整、cost 估算、分类占位、优先级评分、token 用量都有。**缺真实 latency 计时**（全部 0）、**缺 SSE 实时 tail**、**缺 LangSmith/LangFuse 导出** |
| **UX** | ⭐⭐⭐⭐⭐ | Kiki 风格客服面板、teal 渐变设计、推理卡片、拖拽、暗色模式、上传 / 反馈 / 跟进 chips 一应俱全 |
| **业务闭环** | ⭐⭐⭐⭐ | chat → trace → flywheel → post-train 已打通。**SFT 重复率 50%、DPO 配对质量差**（审计报告已发现） |

## 2. Review 范围

本轮 review 覆盖：
- ✅ `core/` agent 装配 + LLM 工厂 + 批量推理 + prompt cache
- ✅ `api/` 全部路由（chat / kb / feedback / traces / flywheel / dashboard / health）
- ✅ `skills/` 全部 skill（commerce / summarize / translator / code_review / data_analysis）
- ✅ `rag/` embeddings / indexer / vectorstore / ingest / retriever / rag_tool
- ✅ `memory/` 短期 / 长期 / fact_extractor / memory_tool
- ✅ `observability/` tracing / cost
- ✅ `data_flywheel/` collector / classifier / deduper / prioritizer
- ✅ `post_training/` pipeline / quality
- ✅ `evaluation/` runner / retrieval_runner / metrics
- ✅ `mcp_integration/` client / protocol / registry
- ✅ `ecommerce/` 路由 + 前端 Vue 3 SPA + components.js
- ✅ `static/index.html`（agent web 控制台）
- ✅ `tools/` 全部内置工具
- ✅ `mock_platform/` 模拟 Shopify

**未深入 review**（不在本轮范围）：
- 测试代码（`tests/`）
- 工具细节（`tools/builtin/calculator.py` 等只过了一眼）
- 详细的 mock_platform 数据模型
- 详细的电商 service 层（cart_service / order_service / recommend_service）

## 3. 文档清单

| 文件 | 内容 | 读者 |
|---|---|---|
| [architecture.md](./architecture.md) | 模块职责 / 依赖图 / 架构图 / 技术选型 | 后端开发者 / 后续接手的 agent |
| [data-flow.md](./data-flow.md) | 一次 `/api/chat` 请求的完整追踪（前端 → API → agent → 工具 → 回流） | 后端开发者 / 排障人员 |
| [issues-and-fixes.md](./issues-and-fixes.md) | 问题清单 / 优先级 / 修复方案 / 验收标准 | 项目 owner / 排期 |
| [pending-items.md](./pending-items.md) | 待实施项核查（README / AGENT-WORKFLOW 提到但未实现） | 项目 owner / 路线图规划 |
| [resume-highlight.md](./resume-highlight.md) | 本轮 review 发现的性能 / 架构亮点（可用于简历） | 个人简历 |

## 4. 一句话总结

**项目已具备一个企业级跨境电商 AI 客服 SaaS 平台的全部核心模块**，但部分新增强功能（badcase 分类/去重/优先级、prompt cache、batch inference、新 Skills）**模块齐全但未接入主链路**——属于"造好零件但没装上车"。下一阶段重点是把这些零件装上去，并修缮 observability 的 latency 计时、router 同步阻塞、JsonlStore 并发安全等几个具体的工程化问题。
