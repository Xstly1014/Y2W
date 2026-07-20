"""Calculator tool — safe arithmetic via Python's `ast` module."""
from __future__ import annotations

import ast
import logging
import math
import operator as op

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Supported binary operators
_BIN_OPS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv,
    ast.Mod: op.mod,
    ast.Pow: op.pow,
}

# Supported unary operators
_UNARY_OPS = {
    ast.UAdd: op.pos,
    ast.USub: op.neg,
}

# Supported functions (whitelist to avoid arbitrary code execution).
# Each value must be a callable; constants live in _CONSTANTS below.
_FUNCS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
}

# Supported named constants. Kept separate from _FUNCS so that a bare
# `abs` (without call parens) doesn't leak the function object to the user.
_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
}

# Guards against algorithmic-complexity DoS:
#   * `2 ** 99999999` would otherwise allocate gigabytes of memory and
#     hang the agent loop. Anything beyond ~1e308 overflows to inf anyway,
#     so capping the exponent at 1000 is safe for real arithmetic.
#   * Large intermediate magnitudes also blow up memory in chained
#     operations; cap final results at 1e308 (just below float max).
_MAX_EXPONENT = 1000
_MAX_RESULT_MAGNITUDE = 1e308


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):  # numbers
        # ast.Constant covers int / float / str / bool / None / complex.
        # Reject non-numeric early — without this, `ast.parse('"hello"')`
        # would happily return a string and break downstream arithmetic.
        if isinstance(node.value, bool) or not isinstance(
            node.value, (int, float)
        ):
            raise ValueError("Non-numeric constant rejected")
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow):
            # Cap exponent magnitude to prevent `2 ** 99999999` DoS.
            if abs(right) > _MAX_EXPONENT:
                raise ValueError(
                    f"Exponent magnitude exceeds {_MAX_EXPONENT}"
                )
        result = _BIN_OPS[type(node.op)](left, right)
        if isinstance(result, complex):
            # math.sqrt(-1) etc. — keep calculator in real-number domain.
            raise ValueError("Complex results are not supported")
        if abs(result) > _MAX_RESULT_MAGNITUDE:
            raise ValueError("Result magnitude exceeds supported range")
        return result
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls are supported")
        fn_name = node.func.id
        if fn_name not in _FUNCS:
            raise ValueError(f"Unsupported function: {fn_name}")
        return _FUNCS[fn_name](*[_eval_node(a) for a in node.args])
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    raise ValueError(f"Unsupported expression: {ast.dump(node)}")


@tool
def calculator_tool(expression: str) -> str:
    """Evaluate a mathematical expression and return the result.

    Supports +, -, *, /, //, %, **, parentheses and a small whitelist of
    functions (abs, round, min, max, sqrt, sin, cos, tan, log, log10, exp)
    and constants (pi, e). Examples:
        '2 * (3 + 4)'  -> '14'
        'sqrt(16) + 1' -> '5.0'
    """
    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval_node(tree.body)
        return str(result)
    except Exception as exc:  # noqa: BLE001
        # Log full detail for debugging; return a generic message so we don't
        # leak internal paths / AST dumps to end users.
        logger.debug("calculator error on %r: %s", expression, exc)
        return "[calculator error] cannot evaluate this expression"
