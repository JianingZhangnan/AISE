from __future__ import annotations

import ast
import json
import math
import operator
import statistics
from collections.abc import Callable
from typing import Any

from phycode.models import ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from phycode.tools.base import ToolRegistry

MAX_EXPRESSION_CHARS = 4_000
MAX_AST_NODES = 500
MAX_POWER_EXPONENT = 100

_BINARY_OPERATORS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "ceil": math.ceil,
    "comb": math.comb,
    "cos": math.cos,
    "degrees": math.degrees,
    "exp": math.exp,
    "floor": math.floor,
    "gcd": math.gcd,
    "hypot": math.hypot,
    "lcm": math.lcm,
    "len": len,
    "log": math.log,
    "log10": math.log10,
    "max": max,
    "mean": statistics.mean,
    "median": statistics.median,
    "min": min,
    "perm": math.perm,
    "prod": math.prod,
    "pstdev": statistics.pstdev,
    "pvariance": statistics.pvariance,
    "radians": math.radians,
    "round": round,
    "sin": math.sin,
    "sorted": sorted,
    "sqrt": math.sqrt,
    "stdev": statistics.stdev,
    "sum": sum,
    "tan": math.tan,
    "variance": statistics.variance,
}
_CONSTANTS = {"e": math.e, "pi": math.pi, "tau": math.tau}


class CalculationError(ValueError):
    pass


def _evaluate(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, bool, str)):
        return node.value
    if isinstance(node, ast.List):
        return [_evaluate(item) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_evaluate(item) for item in node.elts)
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        value = _evaluate(node.operand)
        if not isinstance(value, (int, float)):
            raise CalculationError("Unary arithmetic requires a number")
        return _UNARY_OPERATORS[type(node.op)](value)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            raise CalculationError("Binary arithmetic requires numbers")
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_POWER_EXPONENT:
            raise CalculationError(f"Power exponent exceeds {MAX_POWER_EXPONENT}")
        return _BINARY_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS:
        if node.keywords:
            raise CalculationError("Keyword arguments are not supported")
        return _FUNCTIONS[node.func.id](*[_evaluate(argument) for argument in node.args])
    if isinstance(node, ast.Subscript):
        value = _evaluate(node.value)
        if not isinstance(value, (list, tuple, str)):
            raise CalculationError("Only literal sequences can be indexed")
        index = _evaluate(node.slice)
        if not isinstance(index, int):
            raise CalculationError("Sequence index must be an integer")
        return value[index]
    raise CalculationError(f"Unsupported calculation syntax: {type(node).__name__}")


def calculate(expression: str) -> Any:
    if not expression.strip():
        raise CalculationError("Expression cannot be blank")
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise CalculationError(f"Expression exceeds {MAX_EXPRESSION_CHARS} characters")
    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        raise CalculationError(f"Expression exceeds {MAX_AST_NODES} syntax nodes")
    return _evaluate(tree)


def register_calculator_tools(registry: ToolRegistry) -> None:
    def calculator_calculate(call: ToolCall) -> ToolResult:
        result = calculate(str(call.args["expression"]))
        return ToolResult(tool_call_id=call.id, status="ok", stdout=json.dumps(result, ensure_ascii=False))

    registry.register(
        ToolSpec(
            name="calculator.calculate",
            description=(
                "Safely evaluate one arithmetic or statistics expression; supports math functions and "
                "mean/median/stdev/pstdev without shell access"
            ),
            input_schema={
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
            risk_level=ToolRiskLevel.SAFE,
        ),
        calculator_calculate,
    )
