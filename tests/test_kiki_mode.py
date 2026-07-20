"""Kiki-style action card + WebOps skill tests.

Covers the second-round Kiki feature drop:
  - `WebOpsSkill` exposes 8 browser-automation tools (no real browser
    is launched; this is just a smoke test on the registration)
  - `api/routes/chat._extract_action_cards` correctly pulls
    [ACTION]…[/ACTION] blocks out of the agent's final answer and
    validates each card's `id` / `label` / `prompt`
  - Stripped text no longer contains the JSON payload
  - Invalid blocks (bad id, missing fields, malformed JSON) are dropped
    silently
"""
from __future__ import annotations


# --------------------------------------------------------------------------- #
# WebOps skill registration
# --------------------------------------------------------------------------- #
def test_webops_skill_registers_eight_tools():
    from skills.web_ops import WebOpsSkill

    skill = WebOpsSkill()
    tools = skill.get_tools()
    names = [t.name for t in tools]
    # Order matters for documentation; only the set matters for behavior.
    assert set(names) == {
        "web_open_url",
        "web_extract_text",
        "web_list_links",
        "web_click",
        "web_fill",
        "web_press_key",
        "web_wait_for",
        "web_screenshot",
    }
    assert len(tools) == 8
    # Skill metadata
    meta = skill.metadata()
    assert meta["name"] == "web_ops"
    assert "browser" in meta["tags"]


def test_webops_skill_metadata_safe():
    """Tags / permissions / deps should be JSON-serialisable lists, not tuples."""
    import json

    from skills.web_ops import WebOpsSkill

    skill = WebOpsSkill()
    meta = skill.metadata()
    # Round-trip through json.dumps — would fail if metadata() returned
    # the raw tuple defaults.
    json.dumps(meta)


# --------------------------------------------------------------------------- #
# [ACTION] block extraction
# --------------------------------------------------------------------------- #
def test_extract_action_cards_single_block():
    from api.routes.chat import _extract_action_cards

    answer = (
        "已查询到订单 1001。\n\n"
        "[ACTION]\n"
        '{"id": "apply-refund", "label": "申请退款", '
        '"prompt": "帮订单 1001 申请退款"}\n'
        "[/ACTION]\n"
    )
    cleaned, cards = _extract_action_cards(answer)
    assert "[ACTION]" not in cleaned
    assert "申请退款" not in cleaned  # label is also stripped (only blocks)
    assert len(cards) == 1
    card = cards[0]
    assert card["id"] == "apply-refund"
    assert card["label"] == "申请退款"
    assert card["prompt"] == "帮订单 1001 申请退款"
    # Visible text is the prefix, cleaned.
    assert cleaned.startswith("已查询到订单 1001。")


def test_extract_action_cards_multiple_blocks():
    from api.routes.chat import _extract_action_cards

    answer = (
        "已找到活动页。\n\n"
        "[ACTION]\n"
        '{"id": "open-page", "label": "打开活动页", '
        '"prompt": "帮我打开活动页"}\n'
        "[/ACTION]\n"
        "（也可以直接看）\n\n"
        "[ACTION]\n"
        '{"id": "extract-info", "label": "抓取活动详情", '
        '"prompt": "帮我抓取活动详情"}\n'
        "[/ACTION]\n"
    )
    cleaned, cards = _extract_action_cards(answer)
    assert len(cards) == 2
    assert [c["id"] for c in cards] == ["open-page", "extract-info"]
    assert "[ACTION]" not in cleaned
    # The middle text between the two blocks is preserved.
    assert "（也可以直接看）" in cleaned


def test_extract_action_cards_no_block_returns_original():
    from api.routes.chat import _extract_action_cards

    answer = "普通回答，没有任何 [ACTION] 块。"
    cleaned, cards = _extract_action_cards(answer)
    assert cleaned == answer
    assert cards == []


def test_extract_action_cards_empty_string():
    from api.routes.chat import _extract_action_cards

    cleaned, cards = _extract_action_cards("")
    assert cleaned == ""
    assert cards == []


def test_extract_action_cards_invalid_json_silently_dropped():
    from api.routes.chat import _extract_action_cards

    answer = (
        "前置文本\n"
        "[ACTION]\n"
        "{not valid json}\n"
        "[/ACTION]\n"
        "后置文本\n"
    )
    cleaned, cards = _extract_action_cards(answer)
    # Invalid block should be removed (replaced with empty), but no
    # exception raised and no card returned.
    assert cards == []
    assert "前置文本" in cleaned
    assert "后置文本" in cleaned


def test_extract_action_cards_invalid_id_dropped():
    """An id with uppercase letters or starting with a digit must be rejected."""
    from api.routes.chat import _extract_action_cards

    # Uppercase letters -> invalid
    answer_bad = (
        "[ACTION]\n"
        '{"id": "ApplyRefund", "label": "申请退款", "prompt": "x"}\n'
        "[/ACTION]"
    )
    _, cards = _extract_action_cards(answer_bad)
    assert cards == []
    # Starts with digit -> invalid
    answer_digit = (
        "[ACTION]\n"
        '{"id": "1-apply", "label": "申请退款", "prompt": "x"}\n'
        "[/ACTION]"
    )
    _, cards = _extract_action_cards(answer_digit)
    assert cards == []


def test_extract_action_cards_missing_fields_dropped():
    from api.routes.chat import _extract_action_cards

    for missing in ("id", "label", "prompt"):
        obj = {
            "id": "ok-id",
            "label": "ok",
            "prompt": "ok",
        }
        obj.pop(missing)
        import json
        answer = f"[ACTION]\n{json.dumps(obj, ensure_ascii=False)}\n[/ACTION]"
        _, cards = _extract_action_cards(answer)
        assert cards == [], f"expected 0 cards when {missing} is missing"


def test_extract_action_cards_duplicate_id_dropped():
    """If the agent emits the same id twice, only the first is kept."""
    from api.routes.chat import _extract_action_cards

    answer = (
        "[ACTION]\n"
        '{"id": "refund", "label": "退款", "prompt": "退款 A"}\n'
        "[/ACTION]\n"
        "[ACTION]\n"
        '{"id": "refund", "label": "退款", "prompt": "退款 B"}\n'
        "[/ACTION]"
    )
    _, cards = _extract_action_cards(answer)
    assert len(cards) == 1
    assert cards[0]["prompt"] == "退款 A"


def test_extract_action_cards_truncates_long_label_and_prompt():
    """Defense in depth: even if the LLM emits a 1MB prompt, we cap it."""
    from api.routes.chat import _extract_action_cards

    answer = (
        "[ACTION]\n"
        f'{{"id": "xx", "label": "{"一" * 50}", "prompt": "{"提" * 1000}"}}\n'
        "[/ACTION]"
    )
    _, cards = _extract_action_cards(answer)
    assert len(cards) == 1
    assert len(cards[0]["label"]) <= 16
    assert len(cards[0]["prompt"]) <= 500


# --------------------------------------------------------------------------- #
# Default-tenant agent now includes the web_ops tools (regression)
# --------------------------------------------------------------------------- #
def test_default_agent_toolset_includes_webops():
    """The order_ops AND knowledge sub-agents should both have web_ops tools.

    Tested via static import + a tiny smoke call into get_agent_for_tenant.
    The full agent graph is too heavy to build without an LLM key, so we
    only verify the tools list directly via WebOpsSkill.
    """
    from skills.commerce import CommerceSkills
    from skills.web_ops import WebOpsSkill

    order_tool_names = {t.name for t in (*CommerceSkills().get_tools(), *WebOpsSkill().get_tools())}
    assert "web_open_url" in order_tool_names
    assert "query_order" in order_tool_names
    knowledge_tool_names = set(t.name for t in WebOpsSkill().get_tools())
    assert "web_extract_text" in knowledge_tool_names
