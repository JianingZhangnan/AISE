from typing import Any, cast

import pytest

from phycode.models import (
    FeedbackKind,
    FileReadConfig,
    MemoryCategory,
    PolicyAction,
    ToolCall,
    ToolRiskLevel,
    ToolSpec,
)


@pytest.mark.parametrize("field", ["default_limit", "max_limit"])
@pytest.mark.parametrize("invalid_limit", [True, 0, -1, 1.5])
def test_file_read_config_rejects_non_positive_integer_limits(
    field: str,
    invalid_limit: object,
) -> None:
    invalid = cast(Any, invalid_limit)
    with pytest.raises(
        ValueError,
        match=rf"{field} must be a positive integer or None",
    ):
        if field == "default_limit":
            FileReadConfig(default_limit=invalid)
        else:
            FileReadConfig(max_limit=invalid)


def test_file_read_config_rejects_default_above_maximum() -> None:
    with pytest.raises(
        ValueError,
        match="default_limit cannot exceed max_limit",
    ):
        FileReadConfig(
            default_limit=1_200,
            max_limit=600,
            emit_next_offset=True,
        )


def test_file_read_config_requires_finite_default_to_emit_next_offset() -> None:
    with pytest.raises(
        ValueError,
        match="emit_next_offset requires a finite default_limit",
    ):
        FileReadConfig(max_limit=1_200, emit_next_offset=True)


def test_tool_spec_uses_declared_risk_levels():
    spec = ToolSpec(
        name="file.read",
        description="Read a file",
        input_schema={"type": "object"},
        risk_level=ToolRiskLevel.SAFE,
    )
    assert spec.name == "file.read"
    assert spec.risk_level == ToolRiskLevel.SAFE


def test_tool_call_preserves_provider_id():
    call = ToolCall(tool_name="file.read", args={"path": "README.md"}, provider_call_id="call_1")
    assert call.provider_call_id == "call_1"
    assert call.args["path"] == "README.md"


def test_required_enums_match_spec_values():
    assert {item.value for item in PolicyAction} == {"allow", "ask", "deny"}
    assert {item.value for item in ToolRiskLevel} == {"safe", "risky", "dangerous"}
    assert {item.value for item in MemoryCategory} == {"decision", "preference", "project_fact", "test_command"}
    assert "test_failed" in {item.value for item in FeedbackKind}
