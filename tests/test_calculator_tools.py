from pathlib import Path

from phycode.models import PolicyAction, ToolCall
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.calculator_tools import calculate, register_calculator_tools


def test_calculator_handles_gaia_distance_and_scale_expression():
    result = calculate("356400 / (42.195 / ((2*3600 + 60 + 9) / 3600)) / 1000")

    assert round(result) == 17


def test_calculator_supports_statistics_functions():
    result = calculate("round((pstdev([1, 2, 3]) + stdev([2, 4, 6])) / 2, 3)")

    assert result == 1.408


def test_calculator_rejects_python_object_access():
    registry = ToolRegistry()
    register_calculator_tools(registry)
    result = ToolRuntime(registry).run(
        ToolCall(tool_name="calculator.calculate", args={"expression": "().__class__.__mro__"}),
        PolicyContext(Path.cwd(), [], interactive=False),
    )

    assert result.policy.decision == PolicyAction.ALLOW
    assert result.tool_result.status == "tool_error"
    assert "Unsupported calculation syntax" in result.tool_result.stderr
