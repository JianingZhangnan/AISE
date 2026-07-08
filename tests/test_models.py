from phycode.models import FeedbackKind, MemoryCategory, PolicyAction, ToolCall, ToolRiskLevel, ToolSpec


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
