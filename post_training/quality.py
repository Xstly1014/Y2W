"""SFT/DPO data quality evaluation.

Metrics:
- SFT: length distribution, language detection, duplicate rate, format compliance
- DPO: chosen/rejected length ratio, overlap rate, pair quality, similarity distribution
"""
from __future__ import annotations
import json, logging, re
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _is_chinese(text: str) -> bool:
    """Heuristic: >30% CJK chars = Chinese."""
    if not text:
        return False
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return cjk / max(len(text), 1) > 0.3


def _is_english(text: str) -> bool:
    """Heuristic: >70% ASCII letters/spaces = English."""
    if not text:
        return False
    ascii_count = sum(1 for c in text if c.isascii() and (c.isalpha() or c.isspace()))
    return ascii_count / max(len(text), 1) > 0.7


def _tokenize(text: str) -> list[str]:
    return text.lower().split()


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


# ---------- SFT evaluation ----------
def evaluate_sft(records: list[dict]) -> dict[str, Any]:
    """Evaluate SFT dataset quality.

    Expected record format: {"messages": [{"role": "user", "content": ...}, {"role": "assistant", "content": ...}]}

    Returns:
        {
            "total": N,
            "valid_format": N,  # has 2 messages with user+assistant
            "invalid_format": N,
            "avg_user_length": float,
            "avg_assistant_length": float,
            "max_user_length": int,
            "max_assistant_length": int,
            "language_distribution": {"zh": N, "en": N, "other": N},
            "duplicate_rate": float,  # duplicates / total
            "unique_inputs": N,
            "empty_responses": N,  # assistant content empty
            "too_short_responses": N,  # assistant content < 10 chars
            "too_long_responses": N,  # assistant content > 4000 chars
        }
    """
    if not records:
        return {"total": 0}

    total = len(records)
    valid = 0
    user_lens, asst_lens = [], []
    languages = Counter()
    user_inputs_seen = []
    empty = 0
    too_short = 0
    too_long = 0

    for rec in records:
        msgs = rec.get("messages", [])
        if len(msgs) != 2:
            continue
        if msgs[0].get("role") != "user" or msgs[1].get("role") != "assistant":
            continue
        valid += 1
        user_content = msgs[0].get("content", "")
        asst_content = msgs[1].get("content", "")
        user_lens.append(len(user_content))
        asst_lens.append(len(asst_content))
        user_inputs_seen.append(user_content)

        # Language detection (based on assistant content)
        if _is_chinese(asst_content):
            languages["zh"] += 1
        elif _is_english(asst_content):
            languages["en"] += 1
        else:
            languages["other"] += 1

        if not asst_content.strip():
            empty += 1
        elif len(asst_content) < 10:
            too_short += 1
        elif len(asst_content) > 4000:
            too_long += 1

    # Duplicate detection (normalized user input)
    norm_inputs = [re.sub(r"\s+", " ", u.lower().strip()) for u in user_inputs_seen]
    unique = len(set(norm_inputs))
    dup_rate = (len(norm_inputs) - unique) / max(len(norm_inputs), 1)

    return {
        "total": total,
        "valid_format": valid,
        "invalid_format": total - valid,
        "avg_user_length": sum(user_lens) / max(len(user_lens), 1),
        "avg_assistant_length": sum(asst_lens) / max(len(asst_lens), 1),
        "max_user_length": max(user_lens) if user_lens else 0,
        "max_assistant_length": max(asst_lens) if asst_lens else 0,
        "language_distribution": dict(languages),
        "duplicate_rate": round(dup_rate, 3),
        "unique_inputs": unique,
        "empty_responses": empty,
        "too_short_responses": too_short,
        "too_long_responses": too_long,
    }


# ---------- DPO evaluation ----------
def evaluate_dpo(records: list[dict]) -> dict[str, Any]:
    """Evaluate DPO preference pair quality.

    Expected record format: {"prompt": ..., "chosen": ..., "rejected": ..., "similarity": float}

    Returns:
        {
            "total": N,
            "valid_format": N,
            "avg_prompt_length": float,
            "avg_chosen_length": float,
            "avg_rejected_length": float,
            "chosen_rejected_overlap": float,  # avg Jaccard between chosen and rejected
            "avg_similarity": float,  # similarity field avg
            "chosen_shorter_than_rejected": N,  # chosen is shorter (usually bad)
            "identical_pairs": N,  # chosen == rejected
            "low_similarity_pairs": N,  # similarity < 0.3
            "high_similarity_pairs": N,  # similarity > 0.7 (might be too similar)
        }
    """
    if not records:
        return {"total": 0}

    total = len(records)
    valid = 0
    prompt_lens, chosen_lens, rejected_lens = [], [], []
    overlaps = []
    sims = []
    chosen_shorter = 0
    identical = 0
    low_sim = 0
    high_sim = 0

    for rec in records:
        if not all(k in rec for k in ("prompt", "chosen", "rejected")):
            continue
        valid += 1
        prompt = rec["prompt"]
        chosen = rec["chosen"]
        rejected = rec["rejected"]
        sim = rec.get("similarity", 0.0)

        prompt_lens.append(len(prompt))
        chosen_lens.append(len(chosen))
        rejected_lens.append(len(rejected))
        sims.append(sim)

        # Jaccard overlap between chosen and rejected (token-level)
        ch_toks = set(_tokenize(chosen))
        rj_toks = set(_tokenize(rejected))
        overlap = _jaccard(ch_toks, rj_toks)
        overlaps.append(overlap)

        if len(chosen) < len(rejected):
            chosen_shorter += 1
        if chosen.strip() == rejected.strip():
            identical += 1
        if sim < 0.3:
            low_sim += 1
        elif sim > 0.7:
            high_sim += 1

    return {
        "total": total,
        "valid_format": valid,
        "invalid_format": total - valid,
        "avg_prompt_length": sum(prompt_lens) / max(len(prompt_lens), 1),
        "avg_chosen_length": sum(chosen_lens) / max(len(chosen_lens), 1),
        "avg_rejected_length": sum(rejected_lens) / max(len(rejected_lens), 1),
        "chosen_rejected_overlap": round(sum(overlaps) / max(len(overlaps), 1), 3),
        "avg_similarity": round(sum(sims) / max(len(sims), 1), 3),
        "chosen_shorter_than_rejected": chosen_shorter,
        "identical_pairs": identical,
        "low_similarity_pairs": low_sim,
        "high_similarity_pairs": high_sim,
    }


# ---------- Recommendations ----------
def recommendations(sft_stats: dict, dpo_stats: dict) -> list[str]:
    """Generate improvement recommendations based on stats."""
    recs = []

    if sft_stats.get("total", 0) == 0:
        recs.append("**SFT 数据集为空** — 需要先运行 PostTrainingPipeline.build_sft() 生成数据。")
    else:
        if sft_stats.get("duplicate_rate", 0) > 0.1:
            recs.append(f"SFT 重复率 {sft_stats['duplicate_rate']*100:.1f}% 偏高 — 建议加去重逻辑。")
        if sft_stats.get("too_short_responses", 0) > sft_stats.get("total", 1) * 0.1:
            recs.append(f"SFT {sft_stats['too_short_responses']} 条响应过短（<10 字符）— 建议过滤。")
        if sft_stats.get("empty_responses", 0) > 0:
            recs.append(f"SFT {sft_stats['empty_responses']} 条空响应 — 必须过滤。")
        if sft_stats.get("invalid_format", 0) > 0:
            recs.append(f"SFT {sft_stats['invalid_format']} 条格式无效 — 检查 pipeline.build_sft。")

    if dpo_stats.get("total", 0) == 0:
        recs.append("**DPO 数据集为空** — 需要先运行 PostTrainingPipeline.build_dpo() 生成数据。")
    else:
        if dpo_stats.get("identical_pairs", 0) > 0:
            recs.append(f"DPO {dpo_stats['identical_pairs']} 对 chosen==rejected — 必须剔除。")
        if dpo_stats.get("low_similarity_pairs", 0) > dpo_stats.get("total", 1) * 0.3:
            recs.append(f"DPO {dpo_stats['low_similarity_pairs']} 对相似度过低（<0.3）— 配对质量差。")
        if dpo_stats.get("chosen_rejected_overlap", 0) > 0.7:
            recs.append(f"DPO chosen/rejected 重叠率 {dpo_stats['chosen_rejected_overlap']*100:.1f}% 过高 — 难以学到区分。")
        if dpo_stats.get("chosen_shorter_than_rejected", 0) > dpo_stats.get("total", 1) * 0.5:
            recs.append(f"DPO {dpo_stats['chosen_shorter_than_rejected']} 对 chosen 比 rejected 短 — 可能存在长度偏置。")

    if not recs:
        recs.append("✓ 数据质量良好，未发现明显问题。")
    return recs
