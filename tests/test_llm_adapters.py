import pytest

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


def test_openai_adapter_does_not_keep_api_key_attribute():
    from phycode.llm import OpenAICompatibleChatAdapter

    adapter = OpenAICompatibleChatAdapter(
        base_url="http://localhost:8000/v1",
        model="qwen-coder",
        api_key="secret",
        client=FakeOpenAIClient(),
    )
    assert not hasattr(adapter, "api_key")
