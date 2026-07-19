from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class AgentEventType(str, Enum):
    ASSISTANT_COMMENTARY = "assistant_commentary"
    REASONING_SUMMARY = "reasoning_summary"
    TOOL_CALL_REQUESTED = "tool_call_requested"
    POLICY_DECISION = "policy_decision"
    TOOL_CALL_RUNNING = "tool_call_running"
    TOOL_CALL_OUTPUT = "tool_call_output"
    FEEDBACK_SIGNAL = "feedback_signal"
    ASSISTANT_FINAL = "assistant_final"
    ERROR = "error"
    INCOMPLETE = "incomplete"
    USER_INTERRUPT = "user_interrupt"


class ToolRiskLevel(str, Enum):
    SAFE = "safe"
    RISKY = "risky"
    DANGEROUS = "dangerous"


class PolicyAction(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class FeedbackKind(str, Enum):
    SUCCESS = "success"
    ARTIFACT_VERIFICATION_FAILED = "artifact_verification_failed"
    COMMAND_FAILED = "command_failed"
    TEST_FAILED = "test_failed"
    POLICY_BLOCKED = "policy_blocked"
    POLICY_REQUIRES_APPROVAL = "policy_requires_approval"
    INVALID_TOOL_ARGS = "invalid_tool_args"
    TOOL_ERROR = "tool_error"
    TIMEOUT = "timeout"
    OUTPUT_TRUNCATED = "output_truncated"
    STALE_TOOL_BATCH = "stale_tool_batch"


class MemoryCategory(str, Enum):
    DECISION = "decision"
    PREFERENCE = "preference"
    PROJECT_FACT = "project_fact"
    TEST_COMMAND = "test_command"


class SessionMode(str, Enum):
    INTERACTIVE = "interactive"
    NON_INTERACTIVE = "non_interactive"


class AgentProfile(str, Enum):
    CODING = "coding"
    GAIA = "gaia"
    PRBENCH = "prbench"


class AgentEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evt"))
    session_id: str
    type: AgentEventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)
    redaction_status: str = "redacted"


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    risk_level: ToolRiskLevel
    mutates_state: bool = False


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: new_id("call"))
    tool_name: str
    args: dict[str, Any]
    provider_call_id: str | None = None


class PolicyDecision(BaseModel):
    tool_call_id: str
    decision: PolicyAction
    rule_id: str
    reason: str
    requires_user: bool = False


class ToolResult(BaseModel):
    tool_call_id: str
    status: str
    stdout: str = ""
    stderr: str = ""
    artifact_refs: list[str] = Field(default_factory=list)
    truncated: bool = False


class FeedbackSignal(BaseModel):
    kind: FeedbackKind
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False
    suggested_next_step: str | None = None


class MemoryEntry(BaseModel):
    id: str = Field(default_factory=lambda: new_id("mem"))
    category: MemoryCategory
    content: str
    source: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Session(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ses"))
    workspace_root: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    mode: SessionMode


class ProviderConfig(BaseModel):
    provider: str
    base_url: str
    model: str
    credential_ref: str | None = None
