"""Auto-classify badcases by error type.

Rule-based + LLM-based hybrid:
1. Rule-based: pattern matching on user_input / prediction for known error signatures
2. LLM-based: when rules don't match, optionally call LLM with few-shot examples

Categories:
- rag_missed       : KB had the answer but agent didn't find/use it
- rag_wrong        : KB returned wrong info (outdated, irrelevant)
- tool_failed      : tool call errored (timeout, 5xx, parse fail)
- tool_wrong       : tool returned correct data but agent misinterpreted
- policy_violation : agent violated business rules (refund >$200, etc.)
- refusal_escalate : agent should escalate but didn't (or vice versa)
- hallucination    : agent fabricated info not in KB
- tone_issue       : wrong tone, too verbose, not following style guide
- other            : unclassified
"""
from __future__ import annotations
import logging, re
from typing import Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Category labels (use these exact strings for consistency)
CATEGORIES = (
    "rag_missed", "rag_wrong", "tool_failed", "tool_wrong",
    "policy_violation", "refusal_escalate", "hallucination",
    "tone_issue", "other",
)

# Rule patterns: (regex, category). Checked in order; first match wins.
_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(not found|no results|knowledge base|kb search)\b", re.I), "rag_missed"),
    (re.compile(r"\b(outdated|stale|wrong answer|incorrect info)\b", re.I), "rag_wrong"),
    (re.compile(r"\b(timeout|5\d\d|500|503|tool.*error|call failed)\b", re.I), "tool_failed"),
    (re.compile(r"\b(wrong order|misinterpreted|wrong customer)\b", re.I), "tool_wrong"),
    (re.compile(r"\b(refund.*\$\d{3,}|policy.*violat|exceed.*limit)\b", re.I), "policy_violation"),
    (re.compile(r"\b(escalat|human agent|supervisor)\b", re.I), "refusal_escalate"),
    (re.compile(r"\b(hallucinat|fabricat|made up|invent)\b", re.I), "hallucination"),
    (re.compile(r"\b(tone|verbose|too long|style|emoji)\b", re.I), "tone_issue"),
]


@dataclass
class ClassificationResult:
    category: str
    confidence: float  # 0.0-1.0
    matched_rule: str | None = None
    llm_used: bool = False


def classify_rule_based(user_input: str, prediction: str, expected: str | None = None) -> ClassificationResult:
    """Try rule-based classification. Returns confidence 0.7 for rule match, 0.3 for 'other'."""
    text = f"{user_input} {prediction} {expected or ''}"
    for pattern, category in _RULES:
        if pattern.search(text):
            return ClassificationResult(
                category=category, confidence=0.7, matched_rule=pattern.pattern
            )
    return ClassificationResult(category="other", confidence=0.3)


def classify_with_llm(
    user_input: str,
    prediction: str,
    expected: str | None,
    llm: Any,
) -> ClassificationResult:
    """LLM-based classification. Falls back to 'other' on any error.

    Few-shot prompt with examples per category. Asks for JSON output:
        {"category": "...", "confidence": 0.0-1.0}
    """
    import json
    from langchain_core.messages import HumanMessage, SystemMessage
    try:
        system = (
            "Classify the badcase into exactly one category. "
            f"Valid categories: {', '.join(CATEGORIES)}. "
            "Respond with ONLY a JSON object: {\"category\": \"...\", \"confidence\": 0.0-1.0}."
        )
        human = (
            f"User input: {user_input}\n"
            f"Agent prediction: {prediction}\n"
            f"Expected answer: {expected or 'N/A'}\n"
        )
        out = llm.invoke([SystemMessage(content=system), HumanMessage(content=human)]).content
        # Defensive JSON parse: find first {...} block.
        import re
        m = re.search(r"\{[^{}]*\}", out, re.DOTALL)
        if not m:
            return ClassificationResult(category="other", confidence=0.3, llm_used=True)
        data = json.loads(m.group(0))
        cat = data.get("category", "other")
        if cat not in CATEGORIES:
            cat = "other"
        conf = float(data.get("confidence", 0.5))
        return ClassificationResult(category=cat, confidence=conf, llm_used=True)
    except Exception as exc:
        logger.warning("LLM classify failed: %s", exc)
        return ClassificationResult(category="other", confidence=0.3, llm_used=True)


def classify(
    user_input: str,
    prediction: str,
    expected: str | None = None,
    llm: Any | None = None,
) -> ClassificationResult:
    """Hybrid: try rule-based first; if 'other' and llm given, try LLM."""
    result = classify_rule_based(user_input, prediction, expected)
    if result.category == "other" and llm is not None:
        llm_result = classify_with_llm(user_input, prediction, expected, llm)
        # Only use LLM result if it's more confident than the 'other' default.
        if llm_result.confidence > result.confidence:
            return llm_result
    return result
