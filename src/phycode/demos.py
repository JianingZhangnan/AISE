from __future__ import annotations

import sys
from pathlib import Path

from phycode.agent import AgentLoop, AgentRunResult
from phycode.context import ContextBuilder, MemoryStore, SessionStore
from phycode.feedback import classify_feedback
from phycode.llm import ReactiveLLM
from phycode.models import AgentEventType, PolicyDecision, Session, SessionMode, ToolCall
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.file_tools import register_file_tools
from phycode.tools.shell_tools import register_shell_tools


def _always_approve(call: ToolCall, decision: PolicyDecision) -> bool:
    return True


def _runtime(root: Path) -> ToolRuntime:
    registry = ToolRegistry()
    register_file_tools(registry)
    register_shell_tools(registry, root, "python --version")
    return ToolRuntime(registry)


def run_guardrail_demo(workspace_root: Path) -> str:
    """A dangerous shell command is denied by deterministic policy, never executed."""
    result = _runtime(workspace_root).run(
        ToolCall(tool_name="shell.run", args={"command": "rm -rf /"}),
        PolicyContext(workspace_root, [], interactive=False),
    )
    feedback = classify_feedback(result.tool_result)[0]
    return (
        "tool=shell.run command='rm -rf /'\n"
        f"decision={result.policy.decision.value} rule={result.policy.rule_id}\n"
        f"status={result.tool_result.status} feedback={feedback.kind.value}\n"
        "executed=False"
    )


def run_policy_demo(workspace_root: Path) -> str:
    """A risky edit pauses for approval instead of executing silently."""
    (Path(workspace_root) / "app.py").write_text("x = 1\n", encoding="utf-8")
    result = _runtime(workspace_root).run(
        ToolCall(tool_name="file.edit", args={"path": "app.py", "old": "x = 1", "new": "x = 2"}),
        PolicyContext(workspace_root, [], interactive=False),
    )
    feedback = classify_feedback(result.tool_result)[0]
    return (
        "tool=file.edit path=app.py\n"
        f"decision={result.policy.decision.value} requires_user={result.policy.requires_user}\n"
        f"status={result.tool_result.status} feedback={feedback.kind.value}"
    )


def _feedback_loop(workspace_root: Path) -> AgentRunResult:
    root = Path(workspace_root)
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    runner = root / "run_tests.py"
    runner.write_text(
        "import sys\n"
        "from calc import add\n"
        "if add(1, 2) == 3:\n"
        "    print('ALL_PASS')\n"
        "    sys.exit(0)\n"
        "print('FAIL')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    # -B disables bytecode caching so the post-fix re-run re-imports the edited
    # source (the +/- fix keeps file size and mtime-second identical, which would
    # otherwise let CPython serve a stale .pyc).
    test_command = f'"{sys.executable}" -B "{runner}"'

    run_tests = [{"type": "tool_call_requested", "payload": {"tool_name": "test.run", "args": {}}}]
    fix_bug = [
        {
            "type": "tool_call_requested",
            "payload": {"tool_name": "file.edit", "args": {"path": "calc.py", "old": "return a - b", "new": "return a + b"}},
        }
    ]
    finish = [{"type": "assistant_final", "payload": {"text": "tests pass after fix"}}]

    # Rules are ordered; the model's next action depends on the feedback in context.
    llm = ReactiveLLM(
        rules=[
            ("ALL_PASS", finish),              # tests now pass -> stop
            ("return a + b", run_tests),       # fix applied -> re-run tests
            ("[feedback] test_failed", fix_bug),  # failure observed -> change action
        ],
        default=run_tests,  # first action: run the tests
    )

    session_store = SessionStore(Session(workspace_root=str(root), mode=SessionMode.NON_INTERACTIVE))
    registry = ToolRegistry()
    register_file_tools(registry)
    register_shell_tools(registry, root, test_command)
    loop = AgentLoop(
        llm=llm,
        context_builder=ContextBuilder(session_store, MemoryStore(root / ".phycode" / "memory.jsonl")),
        tool_runtime=ToolRuntime(registry),
        policy_context=PolicyContext(root, [], interactive=False),
        trace_store=_trace_store(root),
        session_store=session_store,
        max_steps=8,
        approval_handler=_always_approve,
    )
    return loop.run("make the failing tests pass")


def run_feedback_demo(workspace_root: Path) -> str:
    """A real loop: a failed test feeds back and changes the next mock LLM action."""
    result = _feedback_loop(workspace_root)
    lines = _render_trace(result)
    lines.append(f"stopped_reason={result.stopped_reason}")
    return "\n".join(lines)


def _render_trace(result: AgentRunResult) -> list[str]:
    lines: list[str] = []
    pending_action: str | None = None
    for event in result.events:
        if event.type == AgentEventType.TOOL_CALL_REQUESTED:
            pending_action = str(event.payload.get("tool_name", ""))
        elif event.type == AgentEventType.FEEDBACK_SIGNAL and pending_action is not None:
            lines.append(f"action={pending_action} -> feedback={event.payload.get('kind')}")
            pending_action = None
        elif event.type == AgentEventType.ASSISTANT_FINAL:
            lines.append(f"final={event.payload.get('text')}")
    return lines


def _trace_store(root: Path):
    from phycode.trace import TraceStore

    return TraceStore(root / ".phycode" / "traces")
