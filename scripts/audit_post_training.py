"""Audit the post-training pipeline and generate a markdown report.

Evaluates:
1. SFT dataset quality (format, length, language, duplicates)
2. DPO dataset quality (pair format, overlap, similarity)
3. Source flywheel data quality (badcase/goodcase counts, category distribution)
4. Pipeline improvement recommendations

Usage:
    python -m scripts.audit_post_training
    python -m scripts.audit_post_training --out report.md
"""
from __future__ import annotations
import argparse, json, logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def build_audit_report() -> str:
    """Build a markdown audit report."""
    from config import settings
    from post_training.quality import (
        evaluate_sft, evaluate_dpo, recommendations, _load_jsonl,
    )
    from data_flywheel.collector import BadCaseCollector

    sections = []
    sections.append("# 后训练流程审查报告\n")
    sections.append(f"**生成时间**: {datetime.now().isoformat()}\n")
    sections.append(f"**SFT 路径**: `{settings.post_train_output_dir / 'sft.jsonl'}`")
    sections.append(f"**DPO 路径**: `{settings.post_train_output_dir / 'dpo.jsonl'}`\n")

    # Load data
    sft_records = _load_jsonl(settings.post_train_output_dir / "sft.jsonl")
    dpo_records = _load_jsonl(settings.post_train_output_dir / "dpo.jsonl")

    sections.append("## 1. 数据概览\n")
    sections.append("| 数据集 | 记录数 | 路径 |")
    sections.append("|---|---|---|")
    sections.append(f"| SFT | {len(sft_records)} | {settings.post_train_output_dir / 'sft.jsonl'} |")
    sections.append(f"| DPO | {len(dpo_records)} | {settings.post_train_output_dir / 'dpo.jsonl'} |")

    # Flywheel source
    try:
        collector = BadCaseCollector()
        bad_count = collector.bad_store.count()
        good_count = collector.good_store.count()
        sections.append(f"| Badcase 源 | {bad_count} | `{settings.badcase_store_path}` |")
        sections.append(f"| Goodcase 源 | {good_count} | `{settings.goodcase_store_path}` |")
        sections.append(f"\n**转化率**: good→SFT = {len(sft_records)}/{good_count} = {len(sft_records)/max(good_count,1)*100:.1f}%")
        sections.append(f"**转化率**: bad→DPO = {len(dpo_records)}/{bad_count} = {len(dpo_records)/max(bad_count,1)*100:.1f}%\n")
    except Exception as exc:
        sections.append(f"\n飞轮数据读取失败: {exc}\n")

    # SFT eval
    sections.append("## 2. SFT 数据质量评估\n")
    sft_stats = evaluate_sft(sft_records)
    if sft_stats.get("total", 0) > 0:
        sections.append("| 指标 | 数值 |")
        sections.append("|---|---|")
        for k, v in sft_stats.items():
            if isinstance(v, dict):
                v = ", ".join(f"{dk}:{dv}" for dk, dv in v.items())
            sections.append(f"| {k} | {v} |")
    else:
        sections.append("SFT 数据集为空。")
    sections.append("")

    # DPO eval
    sections.append("## 3. DPO 数据质量评估\n")
    dpo_stats = evaluate_dpo(dpo_records)
    if dpo_stats.get("total", 0) > 0:
        sections.append("| 指标 | 数值 |")
        sections.append("|---|---|")
        for k, v in dpo_stats.items():
            sections.append(f"| {k} | {v} |")
    else:
        sections.append("DPO 数据集为空。")
    sections.append("")

    # Recommendations
    sections.append("## 4. 改进建议\n")
    recs = recommendations(sft_stats, dpo_stats)
    for r in recs:
        sections.append(f"- {r}")
    sections.append("")

    # Pipeline review
    sections.append("## 5. Pipeline 流程审查\n")
    sections.append("### 5.1 当前流程\n")
    sections.append("```")
    sections.append("goodcases.jsonl → build_sft() → sft.jsonl")
    sections.append("badcases.jsonl + goodcases.jsonl → build_dpo() → dpo.jsonl (Jaccard ≥ 0.3)")
    sections.append("```")
    sections.append("")
    sections.append("### 5.2 已识别问题\n")
    sections.append("- **SFT 无质量过滤**: 直接 1:1 复制 goodcase，未过滤空响应、过短响应、格式异常")
    sections.append("- **DPO 配对粗糙**: Jaccard 0.3 阈值偏低，可能配对 chosen 和 rejected 都是错的样本")
    sections.append("- **无 chosen 质量校验**: 不验证 chosen 是否真的是好答案（只假设 goodcase 就是好的）")
    sections.append("- **无数据增强**: 没有同义改写、回译等增强手段")
    sections.append("- **无 train/eval split**: 全部数据用于训练，无独立评估集")
    sections.append("")
    sections.append("### 5.3 推荐改进\n")
    sections.append("1. **加 SFT 质量过滤**: 在 build_sft 中加 filter，移除空响应/过短/重复")
    sections.append("2. **提升 DPO 配对质量**: 用 embedding 相似度替代 Jaccard，阈值提到 0.5；增加 chosen 质量校验")
    sections.append("3. **加 train/eval split**: 80/20 切分，保留独立 eval 集监控训练效果")
    sections.append("4. **加数据增强**: 用 LLM 对 user_input 同义改写，扩充训练样本")
    sections.append("5. **集成分类信息**: 利用 data_flywheel.classifier 的 category 做分层抽样，保证各类错误都被覆盖")
    sections.append("6. **加频次加权**: 高 occurrence_count 的 badcase 应该在 DPO 中权重更高")
    sections.append("")

    return "\n".join(sections)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args()

    report = build_audit_report()
    out_path = Path(args.out) if args.out else Path("data/eval/post_training_audit.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"report written to {out_path}")
    print("\n" + "="*60 + "\n")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
