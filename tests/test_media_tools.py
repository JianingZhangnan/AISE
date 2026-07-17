from pathlib import Path

from phycode.models import PolicyAction, ToolCall
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.media_tools import register_media_tools


def test_image_inspect_routes_to_configured_vision_client(tmp_path: Path):
    image = tmp_path / "label.jpg"
    image.write_bytes(b"not decoded by the fake client")
    registry = ToolRegistry()
    register_media_tools(registry, lambda path, prompt: f"{path.name}: 62g")
    result = ToolRuntime(registry).run(
        ToolCall(tool_name="image.inspect", args={"path": "label.jpg", "prompt": "read fat"}),
        PolicyContext(tmp_path, [], interactive=False),
        approved=True,
    )

    assert result.policy.decision == PolicyAction.ASK
    assert result.tool_result.status == "ok"
    assert "62g" in result.tool_result.stdout
