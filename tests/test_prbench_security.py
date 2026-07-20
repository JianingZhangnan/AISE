import json
import sys
from pathlib import Path

import pytest

from phycode.llm import ScriptedLLM
from phycode.prbench_eval import PRBenchRunStatus, run_prbench
from prbench_test_support import (
    RecordingFinalLLM,
    scripted_llm_that_writes_runs_reads_and_finishes,
    write_public_task_files,
    write_text_task_files,
)


def test_runner_ignores_workspace_allowlist_and_cannot_read_outside_file(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract, approvals = write_text_task_files(workspace)
    outside = tmp_path / "outside-secret.txt"
    outside_marker = "OUTSIDE_ALLOWLIST_MARKER"
    outside.write_text(outside_marker, encoding="utf-8")
    (workspace / "phycode.toml").write_text(
        "[workspace]\nallowlist = [" + json.dumps(str(tmp_path)) + "]\n",
        encoding="utf-8",
    )
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.read",
                        "args": {"path": str(outside)},
                    },
                }
            ],
            [{"type": "assistant_final", "payload": {"text": "done"}}],
        ]
    )

    result = run_prbench(workspace, contract, approvals, llm=llm, max_tool_calls=4)

    assert result.status == PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED
    trace = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (workspace / ".phycode/prbench/traces").glob("*.jsonl")
    )
    assert '"status": "policy_blocked"' in trace
    assert outside_marker not in trace


def test_repeated_outside_reads_end_with_policy_blocked_status(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract, approvals = write_text_task_files(workspace)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    denied = [
        {
            "type": "tool_call_requested",
            "payload": {"tool_name": "file.read", "args": {"path": str(outside)}},
        }
    ]

    result = run_prbench(
        workspace,
        contract,
        approvals,
        llm=ScriptedLLM([denied, denied, denied]),
        max_tool_calls=8,
    )

    assert result.status == PRBenchRunStatus.POLICY_BLOCKED


def test_runner_ignores_workspace_max_steps(tmp_path: Path) -> None:
    contract, approvals = write_text_task_files(
        tmp_path,
        grants=[{"tool_name": "file.write", "path": "result.txt"}],
    )
    (tmp_path / "phycode.toml").write_text("[agent]\nmax_steps = 1\n", encoding="utf-8")
    llm = ScriptedLLM(
        [
            [{"type": "assistant_final", "payload": {"text": "not ready"}}],
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.write",
                        "args": {"path": "result.txt", "content": "complete\n"},
                    },
                }
            ],
            [{"type": "assistant_final", "payload": {"text": "done"}}],
        ]
    )

    result = run_prbench(tmp_path, contract, approvals, llm=llm, max_tool_calls=4)

    assert result.status == PRBenchRunStatus.COMPLETED
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "complete\n"


def test_runner_does_not_parse_invalid_workspace_toml(tmp_path: Path) -> None:
    contract, approvals = write_text_task_files(tmp_path)
    (tmp_path / "phycode.toml").write_text("not valid = [toml", encoding="utf-8")

    result = run_prbench(
        tmp_path,
        contract,
        approvals,
        llm=ScriptedLLM([[{"type": "assistant_final", "payload": {"text": "done"}}]]),
    )

    assert result.status == PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED


def test_runner_uses_fresh_memory_and_never_reads_legacy_memory(tmp_path: Path) -> None:
    contract, approvals = write_text_task_files(tmp_path)
    marker = "LEGACY_MEMORY_PROMPT_INJECTION_MARKER"
    memory_path = tmp_path / ".phycode" / "memory.jsonl"
    memory_path.parent.mkdir()
    memory_path.write_text(
        json.dumps({"category": "project_fact", "content": marker, "source": "legacy"}) + "\n",
        encoding="utf-8",
    )

    class MessageRecorder:
        def __init__(self) -> None:
            self.messages = []

        def generate(self, messages, tools):
            del tools
            self.messages.append(messages)
            return ScriptedLLM(
                [[{"type": "incomplete", "payload": {"reason": "test stop"}}]]
            ).generate([], [])

    llm = MessageRecorder()

    result = run_prbench(tmp_path, contract, approvals, llm=llm)

    assert marker not in json.dumps(llm.messages)
    trace_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / ".phycode/prbench/traces").glob("*.jsonl")
    )
    result_text = (tmp_path / ".phycode/prbench/run_result.json").read_text(encoding="utf-8")
    assert marker not in trace_text + result_text
    assert result.status == PRBenchRunStatus.PROVIDER_ERROR


def test_ephemeral_memory_store_starts_empty_and_does_not_touch_disk(tmp_path: Path) -> None:
    from phycode.context import MemoryStore
    from phycode.models import MemoryCategory, MemoryEntry

    marker_path = tmp_path / "must-not-be-created.jsonl"
    memory = MemoryStore.ephemeral()

    assert memory.summary() == ""
    memory.append(
        MemoryEntry(category=MemoryCategory.PROJECT_FACT, content="session-only", source="test")
    )

    assert "session-only" in memory.summary()
    assert not marker_path.exists()


def test_coding_agent_keeps_default_persistent_memory(tmp_path: Path, monkeypatch) -> None:
    from phycode.cli import build_agent
    from phycode.models import SessionMode

    marker = "CODING_PERSISTENT_MEMORY_MARKER"
    memory_path = tmp_path / ".phycode" / "memory.jsonl"
    memory_path.parent.mkdir()
    memory_path.write_text(
        json.dumps({"category": "project_fact", "content": marker, "source": "coding"}) + "\n",
        encoding="utf-8",
    )

    class CodingRecorder:
        def __init__(self) -> None:
            self.messages = []

        def generate(self, messages, tools):
            del tools
            self.messages.append(messages)
            return ScriptedLLM(
                [[{"type": "assistant_final", "payload": {"text": "done"}}]]
            ).generate([], [])

    llm = CodingRecorder()
    monkeypatch.chdir(tmp_path)

    build_agent(SessionMode.NON_INTERACTIVE, llm=llm).run("hello")

    assert marker in json.dumps(llm.messages)


def test_trace_directory_symlink_escape_is_blocked_before_llm(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract, approvals = write_text_task_files(workspace)
    outside = tmp_path / "outside-traces"
    outside.mkdir()
    trace_parent = workspace / ".phycode" / "prbench"
    trace_parent.mkdir(parents=True)
    try:
        (trace_parent / "traces").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    llm = RecordingFinalLLM()

    result = run_prbench(workspace, contract, approvals, llm=llm)

    assert result.status == PRBenchRunStatus.POLICY_BLOCKED
    assert llm.calls == 0
    assert list(outside.iterdir()) == []


def test_trace_directory_resolved_escape_is_blocked_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract, approvals = write_text_task_files(workspace)
    trace_dir = workspace / ".phycode" / "prbench" / "traces"
    trace_dir.mkdir(parents=True)
    outside = tmp_path / "resolved-outside-traces"
    outside.mkdir()
    original_resolve = Path.resolve
    trace_resolve_calls = 0

    def resolve_alias(path: Path, *args, **kwargs) -> Path:
        nonlocal trace_resolve_calls
        if path == trace_dir:
            trace_resolve_calls += 1
            return outside
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_alias)
    llm = RecordingFinalLLM()

    result = run_prbench(workspace, contract, approvals, llm=llm)

    assert trace_resolve_calls >= 2
    assert result.status == PRBenchRunStatus.POLICY_BLOCKED
    assert llm.calls == 0


def test_preexisting_fixed_result_tmp_file_is_never_used(tmp_path: Path) -> None:
    contract, approvals = write_text_task_files(tmp_path)
    result_dir = tmp_path / ".phycode" / "prbench"
    result_dir.mkdir(parents=True)
    fixed_tmp = result_dir / "run_result.json.tmp"
    marker = "PREEXISTING_TMP_MARKER"
    fixed_tmp.write_text(marker, encoding="utf-8")

    result = run_prbench(
        tmp_path,
        contract,
        approvals,
        llm=ScriptedLLM([[{"type": "assistant_final", "payload": {"text": "done"}}]]),
    )

    assert result.exit_code != 0
    assert fixed_tmp.read_text(encoding="utf-8") == marker
    assert (result_dir / "run_result.json").is_file()


def test_preexisting_fixed_result_tmp_symlink_cannot_write_outside(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract, approvals = write_text_task_files(workspace)
    result_dir = workspace / ".phycode" / "prbench"
    result_dir.mkdir(parents=True)
    outside = tmp_path / "outside-result.txt"
    marker = "OUTSIDE_RESULT_SENTINEL"
    outside.write_text(marker, encoding="utf-8")
    try:
        (result_dir / "run_result.json.tmp").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    run_prbench(
        workspace,
        contract,
        approvals,
        llm=ScriptedLLM([[{"type": "assistant_final", "payload": {"text": "done"}}]]),
    )

    assert outside.read_text(encoding="utf-8") == marker


def test_nonexistent_workspace_is_not_created(tmp_path: Path) -> None:
    workspace = tmp_path / "does-not-exist"

    result = run_prbench(
        workspace,
        workspace / "contract.json",
        workspace / "approvals.json",
        llm=RecordingFinalLLM(),
    )

    assert result.exit_code != 0
    assert not workspace.exists()


@pytest.mark.parametrize("stage", ["journal", "verifier", "build_agent", "loop"])
def test_runner_lifecycle_exceptions_are_controlled_and_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    import phycode.prbench_eval as prbench_eval

    contract, approvals = write_text_task_files(tmp_path)
    secret = f"sk-{stage}-lifecycle-secret-123456789"

    def fail(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(secret)

    if stage == "journal":
        monkeypatch.setattr(prbench_eval, "ExecutionJournal", fail)
    elif stage == "verifier":
        monkeypatch.setattr(prbench_eval, "ArtifactVerifier", fail)
    elif stage == "build_agent":
        monkeypatch.setattr(prbench_eval, "build_agent", fail)
    else:
        from phycode.agent import AgentLoop

        monkeypatch.setattr(AgentLoop, "run", fail)

    result = run_prbench(
        tmp_path,
        contract,
        approvals,
        llm=ScriptedLLM([[{"type": "assistant_final", "payload": {"text": "done"}}]]),
    )

    assert result.exit_code != 0
    result_path = tmp_path / ".phycode/prbench/run_result.json"
    assert result_path.is_file()
    assert secret not in result_path.read_text(encoding="utf-8")


def test_public_input_decode_error_is_controlled_and_sanitized(tmp_path: Path) -> None:
    contract, approvals = write_text_task_files(tmp_path)
    (tmp_path / "paper.md").write_bytes(b"\xff\xfe\x00")

    result = run_prbench(tmp_path, contract, approvals, llm=RecordingFinalLLM())

    assert result.exit_code != 0
    assert (tmp_path / ".phycode/prbench/run_result.json").is_file()


def test_result_write_exception_does_not_escape_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import phycode.prbench_eval as prbench_eval

    contract, approvals = write_text_task_files(tmp_path)

    def fail_write(*args, **kwargs):
        del args, kwargs
        raise OSError("sk-result-write-secret-123456789")

    monkeypatch.setattr(prbench_eval, "_write_result", fail_write)

    result = run_prbench(
        tmp_path,
        contract,
        approvals,
        llm=ScriptedLLM([[{"type": "assistant_final", "payload": {"text": "done"}}]]),
    )

    assert result.status == PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED
    assert result.exit_code != 0


def test_completed_run_downgrades_when_final_result_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import phycode.prbench_eval as prbench_eval

    contract, approvals = write_public_task_files(tmp_path)
    secret = "sk-final-persist-secret-123456789"
    write_attempts = 0

    def fail_write(*args, **kwargs):
        nonlocal write_attempts
        del args, kwargs
        write_attempts += 1
        raise OSError(secret)

    monkeypatch.setattr(prbench_eval, "_write_result", fail_write)

    result = run_prbench(
        tmp_path,
        contract,
        approvals,
        llm=scripted_llm_that_writes_runs_reads_and_finishes(),
    )

    assert result.status == PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED
    assert result.exit_code != 0
    assert write_attempts == 1
    assert secret not in result.model_dump_json()
    assert not (tmp_path / ".phycode/prbench/run_result.json").exists()


def test_completed_result_downgrades_when_workspace_disappears_before_persistence(
    tmp_path: Path,
) -> None:
    from phycode.prbench_eval import PRBenchRunResult, _persist_if_safe

    result = _persist_if_safe(
        tmp_path / "disappeared",
        PRBenchRunResult(
            status=PRBenchRunStatus.COMPLETED,
            model="safe-model",
            tool_calls=1,
        ),
    )

    assert result.status == PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED
    assert result.exit_code != 0


def test_process_request_projection_hides_executable_args_and_workspace_from_trace_and_context(
    tmp_path: Path,
) -> None:
    contract, approvals = write_public_task_files(tmp_path, approvals=False)
    (tmp_path / "reproduce.py").write_text(
        "from pathlib import Path\n"
        "Path('result.csv').write_text('message\\nhello\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    sensitive_arg = "SENSITIVE_PROCESS_ARGUMENT_MARKER"
    argv = [sys.executable, "reproduce.py", sensitive_arg]
    approvals.write_text(
        json.dumps({"grants": [{"tool_name": "process.run", "argv": argv, "cwd": "."}]}),
        encoding="utf-8",
    )

    class TwoTurnRecorder:
        def __init__(self) -> None:
            self.messages = []

        def generate(self, messages, tools):
            del tools
            self.messages.append(messages)
            if len(self.messages) == 1:
                events = [
                    {
                        "type": "tool_call_requested",
                        "payload": {
                            "tool_name": "process.run",
                            "args": {"argv": argv, "cwd": "."},
                        },
                    }
                ]
            else:
                events = [{"type": "assistant_final", "payload": {"text": "done"}}]
            return ScriptedLLM([events]).generate([], [])

    llm = TwoTurnRecorder()

    result = run_prbench(tmp_path, contract, approvals, llm=llm, max_tool_calls=4)

    assert result.status == PRBenchRunStatus.COMPLETED
    trace = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / ".phycode/prbench/traces").glob("*.jsonl")
    )
    assert len(llm.messages) == 1
    combined = trace.replace("\\\\", "\\")
    assert str(Path(sys.executable).resolve()) not in combined
    assert sensitive_arg not in combined
    assert str(tmp_path) not in combined
    assert Path(sys.executable).name in combined
    assert "reproduce.py" in combined
    assert "[REDACTED_ARG]" in combined


def test_success_trace_and_context_recursively_project_workspace_paths(tmp_path: Path) -> None:
    contract, approvals = write_public_task_files(tmp_path, approvals=False)
    script = (
        "from pathlib import Path\n"
        "Path('result.csv').write_text('message\\nhello\\n', encoding='utf-8')\n"
    )
    argv = [sys.executable, "reproduce.py"]
    absolute_cwd = str(tmp_path.resolve())
    approvals.write_text(
        json.dumps(
            {
                "grants": [
                    {"tool_name": "file.write", "path": "reproduce.py"},
                    {"tool_name": "process.run", "argv": argv, "cwd": absolute_cwd},
                ]
            }
        ),
        encoding="utf-8",
    )

    class SuccessfulRecorder:
        def __init__(self) -> None:
            self.messages = []

        def generate(self, messages, tools):
            del tools
            self.messages.append(messages)
            if len(self.messages) == 1:
                events = [
                    {
                        "type": "tool_call_requested",
                        "payload": {
                            "tool_name": "file.write",
                            "args": {"path": "reproduce.py", "content": script},
                        },
                    }
                ]
            elif len(self.messages) == 2:
                events = [
                    {
                        "type": "tool_call_requested",
                        "payload": {
                            "tool_name": "process.run",
                            "args": {"argv": argv, "cwd": absolute_cwd},
                        },
                    }
                ]
            else:
                events = [{"type": "assistant_final", "payload": {"text": "done"}}]
            return ScriptedLLM([events]).generate([], [])

    llm = SuccessfulRecorder()

    result = run_prbench(tmp_path, contract, approvals, llm=llm, max_tool_calls=4)

    assert result.status == PRBenchRunStatus.COMPLETED
    trace_events = [
        json.loads(line)
        for path in (tmp_path / ".phycode/prbench/traces").glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    combined = (json.dumps(trace_events) + json.dumps(llm.messages)).replace("\\\\", "\\")
    assert absolute_cwd not in combined
    process_request = next(
        event
        for event in trace_events
        if event["type"] == "tool_call_requested"
        and event["payload"].get("tool_name") == "process.run"
    )
    assert process_request["payload"]["args"]["cwd"] == "."
    file_output = next(
        event
        for event in trace_events
        if event["type"] == "tool_call_output"
        and str(event["payload"].get("stdout", "")).startswith("wrote ")
    )
    assert file_output["payload"]["stdout"] == "wrote reproduce.py"
    file_feedback = next(
        event
        for event in trace_events
        if event["type"] == "feedback_signal"
        and str(event["payload"].get("evidence", {}).get("stdout", "")).startswith("wrote ")
    )
    assert file_feedback["payload"]["evidence"]["stdout"] == "wrote reproduce.py"


def test_recursive_event_projection_redacts_external_absolute_path_fields(tmp_path: Path) -> None:
    from phycode.event_projection import project_agent_event
    from phycode.models import AgentEvent, AgentEventType

    outside = (tmp_path.parent / "outside-secret.txt").resolve()
    event = AgentEvent(
        session_id="projection-test",
        type=AgentEventType.ERROR,
        payload={"nested": {"path": str(outside)}},
    )

    projected = project_agent_event(event, tmp_path.resolve())

    assert projected.payload["nested"]["path"] == "[REDACTED_PATH]"


def test_recursive_event_projection_resolves_relative_path_fields_from_workspace(
    tmp_path: Path,
) -> None:
    from phycode.event_projection import project_agent_event
    from phycode.models import AgentEvent, AgentEventType

    event = AgentEvent(
        session_id="relative-projection-test",
        type=AgentEventType.ERROR,
        payload={
            "inside": {"path": "reports/result.txt"},
            "root": {"cwd": "."},
            "escape": {"path": "../outside-secret.txt"},
        },
    )

    projected = project_agent_event(event, tmp_path.resolve())

    assert projected.payload["inside"]["path"] == "reports/result.txt"
    assert projected.payload["root"]["cwd"] == "."
    assert projected.payload["escape"]["path"] == "[REDACTED_PATH]"


def test_recursive_event_projection_blocks_relative_symlink_escape_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from phycode.event_projection import project_agent_event
    from phycode.models import AgentEvent, AgentEventType

    workspace = tmp_path.resolve()
    candidate = workspace / "linked" / "secret.txt"
    outside = (tmp_path.parent / "outside-linked" / "secret.txt").resolve()
    original_resolve = Path.resolve

    def resolve_alias(path: Path, *args, **kwargs) -> Path:
        if path == candidate:
            return outside
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_alias)
    event = AgentEvent(
        session_id="symlink-projection-test",
        type=AgentEventType.ERROR,
        payload={"path": "linked/secret.txt"},
    )

    projected = project_agent_event(event, workspace)

    assert projected.payload["path"] == "[REDACTED_PATH]"


def test_disallowed_executable_feedback_discloses_only_basename(
    tmp_path: Path,
) -> None:
    contract, approvals = write_text_task_files(tmp_path)
    outside_executable = (tmp_path.parent / "external-command.exe").resolve()
    argv = [str(outside_executable), "reproduce.py"]
    approvals.write_text(
        json.dumps(
            {
                "grants": [
                    {"tool_name": "process.run", "argv": argv, "cwd": "."},
                ]
            }
        ),
        encoding="utf-8",
    )

    class TwoTurnRecorder:
        def __init__(self) -> None:
            self.messages = []

        def generate(self, messages, tools):
            del tools
            self.messages.append(messages)
            if len(self.messages) == 1:
                events = [
                    {
                        "type": "tool_call_requested",
                        "payload": {
                            "tool_name": "process.run",
                            "args": {"argv": argv, "cwd": "."},
                        },
                    }
                ]
            else:
                events = [{"type": "assistant_final", "payload": {"text": "done"}}]
            return ScriptedLLM([events]).generate([], [])

    llm = TwoTurnRecorder()

    result = run_prbench(tmp_path, contract, approvals, llm=llm, max_tool_calls=4)

    assert result.exit_code != 0
    trace = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / ".phycode/prbench/traces").glob("*.jsonl")
    )
    combined = (trace + json.dumps(llm.messages[1])).replace("\\\\", "\\")
    assert str(outside_executable) not in combined
    assert outside_executable.name in combined
    assert "invalid_tool_args" in combined


def test_fatal_verifier_at_tool_budget_maps_to_artifact_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import phycode.prbench_eval as prbench_eval

    contract, approvals = write_text_task_files(tmp_path)

    def fail_verify(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("sk-fatal-verifier-secret-123456789")

    monkeypatch.setattr(prbench_eval.ArtifactVerifier, "verify", fail_verify)
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "file.read",
                        "args": {"path": "instruction.md"},
                    },
                }
            ]
        ]
    )

    result = run_prbench(tmp_path, contract, approvals, llm=llm, max_tool_calls=1)

    assert result.status == PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED
    assert result.exit_code != 0
    assert "fatal-verifier-secret" not in result.model_dump_json()


def test_early_policy_failure_then_success_does_not_pollute_final_artifact_status(
    tmp_path: Path,
) -> None:
    contract, approvals = write_text_task_files(tmp_path)
    outside = tmp_path.parent / "outside-policy.txt"
    outside.write_text("outside", encoding="utf-8")
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {"tool_name": "file.read", "args": {"path": str(outside)}},
                }
            ],
            [
                {
                    "type": "tool_call_requested",
                    "payload": {"tool_name": "file.read", "args": {"path": "instruction.md"}},
                }
            ],
            [{"type": "assistant_final", "payload": {"text": "done"}}],
        ]
    )

    result = run_prbench(tmp_path, contract, approvals, llm=llm, max_tool_calls=6)

    assert result.status == PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED


def test_early_process_failure_then_success_does_not_pollute_final_artifact_status(
    tmp_path: Path,
) -> None:
    contract, approvals = write_text_task_files(tmp_path)
    (tmp_path / "fail.py").write_text("raise SystemExit(7)\n", encoding="utf-8")
    (tmp_path / "recover.py").write_text("print('recovered')\n", encoding="utf-8")
    failed_argv = [sys.executable, "fail.py"]
    recovered_argv = [sys.executable, "recover.py"]
    approvals.write_text(
        json.dumps(
            {
                "grants": [
                    {"tool_name": "process.run", "argv": failed_argv, "cwd": "."},
                    {"tool_name": "process.run", "argv": recovered_argv, "cwd": "."},
                ]
            }
        ),
        encoding="utf-8",
    )
    llm = ScriptedLLM(
        [
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "process.run",
                        "args": {"argv": failed_argv, "cwd": "."},
                    },
                }
            ],
            [
                {
                    "type": "tool_call_requested",
                    "payload": {
                        "tool_name": "process.run",
                        "args": {"argv": recovered_argv, "cwd": "."},
                    },
                }
            ],
            [{"type": "assistant_final", "payload": {"text": "done"}}],
        ]
    )

    result = run_prbench(tmp_path, contract, approvals, llm=llm, max_tool_calls=6)

    assert result.status == PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED


def test_public_instruction_is_validated_without_runner_side_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import phycode.prbench_eval as prbench_eval

    contract, approvals = write_text_task_files(tmp_path)
    original_resolve = prbench_eval._resolve_workspace_path
    original_read_text = Path.read_text
    instruction = (tmp_path / "instruction.md").resolve()
    instruction_validations = 0
    instruction_reads = 0
    secret = "sk-runner-read-secret-123456789"

    def record_resolve(workspace, path, *, require_file):
        nonlocal instruction_validations
        resolved = original_resolve(workspace, path, require_file=require_file)
        if require_file and resolved == instruction:
            instruction_validations += 1
        return resolved

    def reject_instruction_read(path: Path, *args, **kwargs):
        nonlocal instruction_reads
        if path == instruction:
            instruction_reads += 1
            raise RuntimeError(secret)
        return original_read_text(path, *args, **kwargs)

    class ValidationOrderRecorder:
        def __init__(self) -> None:
            self.calls = 0
            self.validation_counts_at_calls: list[int] = []

        def generate(self, messages, tools):
            del messages, tools
            self.calls += 1
            self.validation_counts_at_calls.append(instruction_validations)
            return ScriptedLLM(
                [[{"type": "assistant_final", "payload": {"text": "done"}}]]
            ).generate([], [])

    monkeypatch.setattr(prbench_eval, "_resolve_workspace_path", record_resolve)
    monkeypatch.setattr(Path, "read_text", reject_instruction_read)
    llm = ValidationOrderRecorder()

    result = run_prbench(tmp_path, contract, approvals, llm=llm)

    assert instruction_validations == 1
    assert instruction_reads == 0
    assert llm.calls > 0
    assert llm.validation_counts_at_calls[0] == 1
    assert result.status == PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED
    persisted = (tmp_path / ".phycode/prbench/run_result.json").read_text(encoding="utf-8")
    assert secret not in persisted


@pytest.mark.parametrize("kind", ["url", "newline", "raises"])
def test_injected_provider_model_label_is_exception_safe_and_sanitized(
    tmp_path: Path,
    kind: str,
) -> None:
    from phycode.prbench_eval import prbench_result_lines

    contract, approvals = write_text_task_files(tmp_path)
    unsafe_url = "https://unsafe-model.example/v1"
    unsafe_newline = "safe-model\ninjected-output"
    property_secret = "sk-model-property-secret-123456789"

    class UnsafeModelProvider:
        @property
        def model(self):
            if kind == "raises":
                raise RuntimeError(property_secret)
            return unsafe_url if kind == "url" else unsafe_newline

        def generate(self, messages, tools):
            del messages, tools
            return ScriptedLLM(
                [[{"type": "incomplete", "payload": {"reason": "test stop"}}]]
            ).generate([], [])

    result = run_prbench(tmp_path, contract, approvals, llm=UnsafeModelProvider())

    serialized = result.model_dump_json() + "\n" + "\n".join(prbench_result_lines(result))
    assert unsafe_url not in serialized
    assert "injected-output" not in serialized
    assert property_secret not in serialized
    assert result.exit_code != 0
