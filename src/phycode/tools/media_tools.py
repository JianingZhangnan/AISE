from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from phycode.models import ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from phycode.tools.base import ToolRegistry

VisionInspector = Callable[[Path, str], str]
MAX_VISION_OUTPUT_CHARS = 20_000


def register_media_tools(registry: ToolRegistry, vision_inspector: VisionInspector | None) -> None:
    if vision_inspector is None:
        return

    def image_inspect(call: ToolCall) -> ToolResult:
        prompt = str(call.args.get("prompt", "Describe the image and extract the facts needed to answer the user's question."))
        content = vision_inspector(Path(call.args["path"]), prompt)
        return ToolResult(
            tool_call_id=call.id,
            status="ok",
            stdout=content[:MAX_VISION_OUTPUT_CHARS],
            truncated=len(content) > MAX_VISION_OUTPUT_CHARS,
        )

    registry.register(
        ToolSpec(
            name="image.inspect",
            description="Send a workspace image to the configured vision model and extract visible facts",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}, "prompt": {"type": "string"}},
                "required": ["path"],
            },
            risk_level=ToolRiskLevel.RISKY,
        ),
        image_inspect,
    )
