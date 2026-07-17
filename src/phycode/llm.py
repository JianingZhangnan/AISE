from __future__ import annotations

import base64
import json
import queue
import re
import threading
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Protocol

from phycode.models import AgentEvent, AgentEventType, ToolSpec

ReactiveTrigger = Callable[[str], bool]
_PROVIDER_TOOL_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9_-]")
_MAX_PROVIDER_TOOL_NAME_LENGTH = 64


def _provider_tool_aliases(tools: list[ToolSpec]) -> tuple[dict[str, str], dict[str, str]]:
    original_to_alias: dict[str, str] = {}
    alias_to_original: dict[str, str] = {}
    for spec in tools:
        base = _PROVIDER_TOOL_NAME_PATTERN.sub("_", spec.name) or "tool"
        alias = base[:_MAX_PROVIDER_TOOL_NAME_LENGTH]
        if alias in alias_to_original and alias_to_original[alias] != spec.name:
            suffix = sha256(spec.name.encode("utf-8")).hexdigest()[:8]
            alias = f"{base[:_MAX_PROVIDER_TOOL_NAME_LENGTH - len(suffix) - 1]}_{suffix}"
        original_to_alias[spec.name] = alias
        alias_to_original[alias] = spec.name
    return original_to_alias, alias_to_original


class LLMClient(Protocol):
    def generate(self, messages: list[dict[str, object]], tools: list[ToolSpec]) -> list[AgentEvent]: ...


def _event_from_dict(data: dict[str, Any]) -> AgentEvent:
    return AgentEvent(
        session_id=str(data.get("session_id", "scripted")),
        type=AgentEventType(data["type"]),
        payload=data.get("payload", {}),
    )


class ScriptedLLM:
    def __init__(self, turns: list[list[dict[str, Any]]]) -> None:
        self.turns = turns
        self.index = 0

    def generate(self, messages: list[dict[str, object]], tools: list[ToolSpec]) -> list[AgentEvent]:
        if self.index >= len(self.turns):
            return [
                AgentEvent(
                    session_id="scripted",
                    type=AgentEventType.ASSISTANT_FINAL,
                    payload={"text": "No scripted turns remain"},
                )
            ]
        events = [_event_from_dict(item) for item in self.turns[self.index]]
        self.index += 1
        return events


class EchoLLM:
    def generate(self, messages: list[dict[str, object]], tools: list[ToolSpec]) -> list[AgentEvent]:
        last = str(messages[-1]["content"]) if messages else ""
        if "\nUser: " in last:
            last = last.rsplit("\nUser: ", 1)[1]
        return [AgentEvent(session_id="echo", type=AgentEventType.ASSISTANT_FINAL, payload={"text": f"Echo: {last}"})]


class ReactiveLLM:
    """Deterministic mock whose output depends on the rendered context.

    Rules are ``(trigger, events)`` pairs evaluated in order; the first trigger that
    matches the concatenated message text produces that turn's events. A trigger is
    either a substring or a predicate over the text. Because the returned action
    depends on what feedback is present in the context, this mock lets the feedback
    loop be verified end-to-end without a real model.
    """

    def __init__(
        self,
        rules: list[tuple[str | ReactiveTrigger, list[dict[str, Any]]]],
        default: list[dict[str, Any]] | None = None,
    ) -> None:
        self.rules = rules
        self.default = default if default is not None else [
            {"type": "assistant_final", "payload": {"text": "no rule matched"}}
        ]

    def generate(self, messages: list[dict[str, object]], tools: list[ToolSpec]) -> list[AgentEvent]:
        text = "\n".join(str(message.get("content", "")) for message in messages)
        for trigger, events in self.rules:
            matched = trigger(text) if callable(trigger) else (trigger in text)
            if matched:
                return [_event_from_dict(item) for item in events]
        return [_event_from_dict(item) for item in self.default]


class FailingLLM:
    def __init__(self, message: str) -> None:
        self.message = message

    def generate(self, messages: list[dict[str, object]], tools: list[ToolSpec]) -> list[AgentEvent]:
        raise RuntimeError(self.message)


class OpenAICompatibleChatAdapter:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        client: Any | None = None,
        vision_model: str | None = None,
        timeout_seconds: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.vision_model = vision_model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        if client is not None:
            self.client = client
            return

        from openai import OpenAI

        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    def _call_with_deadline(self, call: Callable[[], Any]) -> Any:
        results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def invoke() -> None:
            try:
                results.put((True, call()))
            except Exception as exc:
                results.put((False, exc))

        threading.Thread(target=invoke, daemon=True, name="phycode-provider-call").start()
        try:
            succeeded, value = results.get(timeout=self.timeout_seconds)
        except queue.Empty as exc:
            raise TimeoutError(f"Provider call exceeded {self.timeout_seconds:g} seconds") from exc
        if not succeeded:
            if isinstance(value, BaseException):
                raise value
            raise RuntimeError(str(value))
        return value

    def generate(self, messages: list[dict[str, object]], tools: list[ToolSpec]) -> list[AgentEvent]:
        original_to_alias, alias_to_original = _provider_tool_aliases(tools)
        tool_payload = [
            {
                "type": "function",
                "function": {
                    "name": original_to_alias[spec.name],
                    "description": spec.description,
                    "parameters": spec.input_schema,
                },
            }
            for spec in tools
        ]
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
        if tool_payload:
            kwargs["tools"] = tool_payload

        response = self._call_with_deadline(lambda: self.client.chat.completions.create(**kwargs))
        message = response.choices[0].message
        events: list[AgentEvent] = []
        content = getattr(message, "content", None)
        tool_calls = getattr(message, "tool_calls", None)

        if content:
            event_type = AgentEventType.ASSISTANT_COMMENTARY if tool_calls else AgentEventType.ASSISTANT_FINAL
            events.append(AgentEvent(session_id="provider", type=event_type, payload={"text": content}))

        for tool_call in tool_calls or []:
            function = tool_call.function
            args = json.loads(function.arguments or "{}")
            events.append(
                AgentEvent(
                    session_id="provider",
                    type=AgentEventType.TOOL_CALL_REQUESTED,
                    payload={
                        "provider_call_id": tool_call.id,
                        "tool_name": alias_to_original.get(function.name, function.name),
                        "args": args,
                    },
                )
            )

        if not events:
            return [AgentEvent(session_id="provider", type=AgentEventType.INCOMPLETE, payload={"reason": "empty provider response"})]
        return events

    def inspect_image(self, path: Path, prompt: str) -> str:
        from PIL import Image, ImageOps

        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((1600, 1600))
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=82, optimize=True)
        data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        response = self._call_with_deadline(
            lambda: self.client.chat.completions.create(
                model=self.vision_model or self.model,
                max_tokens=2_000,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                        ],
                    }
                ],
            )
        )
        content = getattr(response.choices[0].message, "content", None)
        if not content:
            raise RuntimeError("vision provider returned no text")
        return str(content)
