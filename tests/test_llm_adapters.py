from pathlib import Path
import time

import pytest
from PIL import Image

from phycode.llm import EchoLLM, FailingLLM, ScriptedLLM
from phycode.models import AgentEventType


def test_scripted_llm_returns_events_in_order():
    llm = ScriptedLLM([[{"type": "assistant_final", "payload": {"text": "done"}}]])
    events = llm.generate([], [])
    assert events[0].type == AgentEventType.ASSISTANT_FINAL
    assert events[0].payload["text"] == "done"


def test_echo_llm_returns_final_text():
    events = EchoLLM().generate([{"role": "user", "content": "hello"}], [])
    assert events[0].type == AgentEventType.ASSISTANT_FINAL
    assert "hello" in events[0].payload["text"]


def test_echo_llm_prefers_current_user_line_from_rendered_context():
    rendered_context = (
        "Workspace: demo\n"
        "Recent events:\n"
        "[{'type': 'assistant_final', 'payload': {'text': 'Echo: old turn'}}]\n"
        "User: new turn"
    )
    events = EchoLLM().generate([{"role": "user", "content": rendered_context}], [])
    assert events[0].payload["text"] == "Echo: new turn"


def test_failing_llm_raises_provider_error():
    with pytest.raises(RuntimeError):
        FailingLLM("provider down").generate([], [])


class FakeFunction:
    name = "file.read"
    arguments = '{"path": "README.md"}'


class FakeToolCall:
    id = "call_1"
    type = "function"
    function = FakeFunction()


class FakeMessage:
    content = "I will read the file."
    tool_calls = [FakeToolCall()]


class FakeChoice:
    message = FakeMessage()


class FakeResponse:
    choices = [FakeChoice()]


class FakeCompletions:
    def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeResponse()


class FakeChat:
    def __init__(self):
        self.completions = FakeCompletions()


class FakeOpenAIClient:
    def __init__(self):
        self.chat = FakeChat()


def test_openai_compatible_adapter_maps_tool_calls():
    from phycode.llm import OpenAICompatibleChatAdapter

    client = FakeOpenAIClient()
    adapter = OpenAICompatibleChatAdapter(
        base_url="http://localhost:8000/v1",
        model="qwen-coder",
        api_key="secret",
        client=client,
    )
    events = adapter.generate([{"role": "user", "content": "read README"}], [])
    assert events[0].type == AgentEventType.ASSISTANT_COMMENTARY
    assert events[1].type == AgentEventType.TOOL_CALL_REQUESTED
    assert events[1].payload["tool_name"] == "file.read"
    assert events[1].payload["args"] == {"path": "README.md"}


def test_openai_adapter_sends_provider_safe_tool_names_and_maps_them_back():
    from phycode.llm import OpenAICompatibleChatAdapter
    from phycode.models import ToolRiskLevel, ToolSpec

    class AliasedFunction:
        name = "file_read"
        arguments = '{"path": "README.md"}'

    class AliasedToolCall:
        id = "call_alias"
        function = AliasedFunction()

    class AliasedMessage:
        content = None
        tool_calls = [AliasedToolCall()]

    class AliasedResponse:
        choices = [type("Choice", (), {"message": AliasedMessage()})()]

    class AliasedCompletions:
        def __init__(self):
            self.kwargs = {}

        def create(self, **kwargs):
            self.kwargs = kwargs
            return AliasedResponse()

    class AliasedChat:
        def __init__(self):
            self.completions = AliasedCompletions()

    class AliasedClient:
        def __init__(self):
            self.chat = AliasedChat()

    client = AliasedClient()
    adapter = OpenAICompatibleChatAdapter("https://example.com/v1", "model", "secret", client=client)
    tools = [
        ToolSpec(
            name="file.read",
            description="Read a file",
            input_schema={"type": "object"},
            risk_level=ToolRiskLevel.SAFE,
        )
    ]

    events = adapter.generate([{"role": "user", "content": "read"}], tools)

    assert events[0].payload["tool_name"] == "file.read"
    assert client.chat.completions.kwargs["tools"][0]["function"]["name"] == "file_read"


def test_openai_adapter_does_not_keep_api_key_attribute():
    from phycode.llm import OpenAICompatibleChatAdapter

    adapter = OpenAICompatibleChatAdapter(
        base_url="http://localhost:8000/v1",
        model="qwen-coder",
        api_key="secret",
        client=FakeOpenAIClient(),
    )
    assert not hasattr(adapter, "api_key")


def test_openai_adapter_accepts_optional_vision_model():
    from phycode.llm import OpenAICompatibleChatAdapter

    adapter = OpenAICompatibleChatAdapter(
        base_url="http://localhost:8000/v1",
        model="qwen-coder",
        api_key="secret",
        client=FakeOpenAIClient(),
        vision_model="Qwen2.5-VL-72B-Instruct",
    )
    assert adapter.vision_model == "Qwen2.5-VL-72B-Instruct"


def test_openai_adapter_has_bounded_request_defaults():
    from phycode.llm import OpenAICompatibleChatAdapter

    adapter = OpenAICompatibleChatAdapter(
        base_url="http://localhost:8000/v1",
        model="qwen-coder",
        api_key="secret",
        client=FakeOpenAIClient(),
    )

    assert adapter.timeout_seconds == 120.0
    assert adapter.max_retries == 2


def test_openai_adapter_enforces_wall_clock_deadline():
    from phycode.llm import OpenAICompatibleChatAdapter

    class SlowCompletions:
        def create(self, **kwargs):
            del kwargs
            time.sleep(0.1)
            return FakeResponse()

    class SlowClient:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": SlowCompletions()})()

    adapter = OpenAICompatibleChatAdapter(
        base_url="http://localhost:8000/v1",
        model="slow",
        api_key="secret",
        client=SlowClient(),
        timeout_seconds=0.01,
    )

    with pytest.raises(TimeoutError, match="exceeded"):
        adapter.generate([{"role": "user", "content": "wait"}], [])


def test_openai_adapter_inspects_image_with_vision_model(tmp_path: Path):
    from phycode.llm import OpenAICompatibleChatAdapter

    image_path = tmp_path / "label.jpg"
    Image.new("RGB", (4, 4), color="white").save(image_path)
    client = FakeOpenAIClient()
    adapter = OpenAICompatibleChatAdapter(
        base_url="http://localhost:8000/v1",
        model="qwen-coder",
        api_key="secret",
        client=client,
        vision_model="Qwen2.5-VL-72B-Instruct",
    )

    assert adapter.inspect_image(image_path, "read the label") == "I will read the file."
    kwargs = client.chat.completions.kwargs
    assert kwargs["model"] == "Qwen2.5-VL-72B-Instruct"
    assert kwargs["max_tokens"] == 2_000
    content = kwargs["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "read the label"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
