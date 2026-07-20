"""Unit tests for the calculator tool.

These tests bypass the LLM entirely — we call the underlying Python
function directly via `calculator_tool.invoke(...)`. That way they're
deterministic and free.
"""
from __future__ import annotations

import pytest

from tools.builtin.calculator import calculator_tool


@pytest.mark.parametrize("expr, expected", [
    ("2 + 3", "5"),
    ("10 - 4", "6"),
    ("6 * 7", "42"),
    ("15 / 4", "3.75"),
    ("15 // 4", "3"),
    ("17 % 5", "2"),
    ("2 ** 10", "1024"),
    ("2 * (3 + 4)", "14"),
    ("-5", "-5"),
    ("+5", "5"),
    ("abs(-7)", "7"),
    ("round(3.6)", "4"),
    ("min(1, 2, 3)", "1"),
    ("max(1, 2, 3)", "3"),
    ("sqrt(16)", "4.0"),
    ("sqrt(144) + 3", "15.0"),  # NOTE: float, matches eval fixture intent
    ("log10(1000)", "3.0"),
    ("exp(0)", "1.0"),
    ("pi", f"{__import__('math').pi}"),
    ("e", f"{__import__('math').e}"),
])
def test_calculator_valid(expr: str, expected: str) -> None:
    assert calculator_tool.invoke(expr) == expected


def test_calculator_division_by_zero() -> None:
    """Division by zero returns an error string, not a raised exception."""
    out = calculator_tool.invoke("1 / 0")
    assert "calculator error" in out


def test_calculator_rejects_unknown_function() -> None:
    """Whitelisted functions only — arbitrary calls must fail."""
    out = calculator_tool.invoke("open('foo')")
    assert "calculator error" in out


def test_calculator_rejects_assignment() -> None:
    """`x = 1` is not an expression — parse error expected."""
    out = calculator_tool.invoke("x = 1")
    assert "calculator error" in out


def test_calculator_rejects_import() -> None:
    """`__import__('os')` should be rejected — it's a Call on a non-whitelisted name."""
    out = calculator_tool.invoke("__import__('os')")
    assert "calculator error" in out


def test_calculator_handles_empty_input() -> None:
    out = calculator_tool.invoke("")
    assert "calculator error" in out


def test_calculator_rejects_bare_function_name() -> None:
    """`abs` without parens must NOT leak the function object to the user.

    Regression: previously the `ast.Name` branch fell through to _FUNCS
    and returned the abs() callable, producing output like
    '<built-in function abs>' which is both useless and a leak of internal
    implementation detail. Constants pi / e are still allowed.
    """
    out = calculator_tool.invoke("abs")
    assert "calculator error" in out
    assert "built-in" not in out


def test_calculator_error_message_does_not_leak_internals() -> None:
    """Errors must not echo raw exception text (may contain AST dumps)."""
    out = calculator_tool.invoke("__import__('os')")
    assert "calculator error" in out
    # The previous implementation returned "[calculator error] {exc}" which
    # exposed ValueError messages with `ast.dump(node)` content. The fixed
    # version returns a stable generic message.
    assert "ast." not in out
    assert "dump(" not in out
