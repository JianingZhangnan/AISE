from __future__ import annotations

import json
from collections import OrderedDict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from phycode.models import AgentEvent, AgentEventType
from phycode.redaction import redact_obj, redact_text


@dataclass(frozen=True)
class ConversationProjection:
    messages: list[dict[str, object]]
    execution_state: dict[str, object]


def _clip_text(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    marker = "\n...[clipped]...\n"
    if limit <= len(marker):
        return value[:limit]
    available = limit - len(marker)
    head = (available * 2) // 3
    return value[:head] + marker + value[-(available - head) :]


def _compact_value(value: Any, string_limit: int = 1_500) -> Any:
    value = redact_obj(value)
    if isinstance(value, str):
        return _clip_text(value, string_limit)
    if isinstance(value, dict):
        return {str(key): _compact_value(item, string_limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_compact_value(item, string_limit) for item in value]
    if isinstance(value, tuple):
        return [_compact_value(item, string_limit) for item in value]
    return value


def _call_id(event: AgentEvent) -> str:
    provider_call_id = event.payload.get("provider_call_id")
    if isinstance(provider_call_id, str) and provider_call_id:
        return redact_text(provider_call_id)
    return event.id


def _tool_call_message(event: AgentEvent, call_id: str) -> dict[str, object]:
    tool_name = redact_text(str(event.payload.get("tool_name", "unknown_tool")))
    args = _compact_value(event.payload.get("args", {}))
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(args, ensure_ascii=False, sort_keys=True, default=str),
                },
            }
        ],
    }


def _event_message(event: AgentEvent) -> dict[str, object] | None:
    if event.type == AgentEventType.USER_MESSAGE:
        return {
            "role": "user",
            "content": f"User: {redact_text(str(event.payload.get('text', '')))}",
        }
    if event.type in {AgentEventType.ASSISTANT_COMMENTARY, AgentEventType.ASSISTANT_FINAL}:
        return {
            "role": "assistant",
            "content": redact_text(str(event.payload.get("text", ""))),
        }
    if event.type == AgentEventType.FEEDBACK_SIGNAL:
        content = json.dumps(
            {"runtime_feedback": _compact_value(event.payload)},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return {"role": "user", "content": content}
    return None


def _coalesced_actionable_feedback(events: Sequence[AgentEvent]) -> list[AgentEvent]:
    turn = 0
    coalesced: OrderedDict[tuple[object, ...], AgentEvent] = OrderedDict()
    for event in events:
        if event.type == AgentEventType.USER_MESSAGE:
            turn += 1
            continue
        if event.type != AgentEventType.FEEDBACK_SIGNAL:
            continue
        kind = str(event.payload.get("kind", ""))
        if kind in {"success", "stale_tool_batch"}:
            continue
        cause = str(event.payload.get("cause") or kind)
        key: tuple[object, ...]
        if kind == "artifact_verification_failed":
            key = ("global_verifier", cause)
        else:
            key = (turn, cause)
        coalesced.pop(key, None)
        coalesced[key] = event
    return list(coalesced.values())


def _conversation_units(events: Sequence[AgentEvent]) -> list[list[dict[str, object]]]:
    units: list[list[dict[str, object]]] = []
    pending: deque[tuple[str, dict[str, object]]] = deque()
    actionable_ids = {event.id for event in _coalesced_actionable_feedback(events)}
    for event in events:
        if event.type == AgentEventType.TOOL_CALL_REQUESTED:
            call_id = _call_id(event)
            pending.append((call_id, _tool_call_message(event, call_id)))
            continue
        if event.type == AgentEventType.TOOL_CALL_OUTPUT:
            content = json.dumps(
                _compact_value(event.payload),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            if pending:
                call_id, assistant = pending.popleft()
                units.append(
                    [
                        assistant,
                        {"role": "tool", "tool_call_id": call_id, "content": content},
                    ]
                )
            else:
                units.append(
                    [{"role": "user", "content": json.dumps({"tool_evidence": json.loads(content)})}]
                )
            continue
        if (
            event.type == AgentEventType.FEEDBACK_SIGNAL
            and str(event.payload.get("kind", "")) != "success"
            and event.id not in actionable_ids
        ):
            continue
        message = _event_message(event)
        if message is not None:
            units.append([message])
    for call_id, assistant in pending:
        units.append(
            [
                assistant,
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(
                        {
                            "status": "missing_runtime_result",
                            "stderr": "The runtime did not produce a result for this call",
                        },
                        sort_keys=True,
                    ),
                },
            ]
        )
    return units


def _execution_state(events: Sequence[AgentEvent]) -> dict[str, object]:
    pending: deque[AgentEvent] = deque()
    successes: OrderedDict[str, dict[str, object]] = OrderedDict()
    latest_outputs: deque[dict[str, object]] = deque(maxlen=2)

    for event in events:
        if event.type == AgentEventType.TOOL_CALL_REQUESTED:
            pending.append(event)
            continue
        if event.type == AgentEventType.TOOL_CALL_OUTPUT:
            compact_output = _compact_value(event.payload)
            latest_outputs.append(compact_output)
            request = pending.popleft() if pending else None
            if request is None or event.payload.get("status") != "ok":
                continue
            action = {
                "tool_name": str(request.payload.get("tool_name", "")),
                "args": _compact_value(request.payload.get("args", {}), string_limit=500),
            }
            key = json.dumps(action, ensure_ascii=False, sort_keys=True, default=str)
            previous = successes.pop(key, None)
            previous_count = previous.get("success_count", 0) if previous is not None else 0
            action["success_count"] = previous_count + 1 if isinstance(previous_count, int) else 1
            successes[key] = action
            continue
    actionable_feedback = [
        _compact_value(event.payload)
        for event in _coalesced_actionable_feedback(events)[-3:]
    ]

    return _compact_value(
        {
            "successful_actions": list(successes.values())[-20:],
            "latest_actionable_feedback": actionable_feedback,
            "latest_tool_outputs": list(latest_outputs),
        }
    )


def _unit_size(unit: list[dict[str, object]]) -> int:
    return len(json.dumps(unit, ensure_ascii=False, sort_keys=True, default=str))


def project_conversation(events: Sequence[AgentEvent], recent_budget: int) -> ConversationProjection:
    selected: list[list[dict[str, object]]] = []
    remaining = max(0, recent_budget)
    for unit in reversed(_conversation_units(events)):
        size = _unit_size(unit)
        if size > remaining:
            continue
        selected.append(unit)
        remaining -= size
    selected.reverse()
    messages = [message for unit in selected for message in unit]
    return ConversationProjection(messages=messages, execution_state=_execution_state(events))
