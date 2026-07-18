from __future__ import annotations

import hashlib
import json
import os
from enum import Enum
from pathlib import Path
from typing import Any

import typer
from pydantic import BaseModel, ConfigDict, Field

from phycode.approval import ApprovalManifest
from phycode.cli import build_agent
from phycode.config import load_prbench_provider_config
from phycode.execution import ArtifactSnapshot, ExecutionJournal
from phycode.llm import LLMClient, OpenAICompatibleChatAdapter
from phycode.models import AgentEventType, AgentProfile, SessionMode
from phycode.prbench_contract import ArtifactVerifier, TaskContract
from phycode.redaction import redact_obj, redact_text
from phycode.visibility import (
    PRBENCH_HIDDEN_PATH_COMPONENTS,
    has_hidden_path_component,
    is_sensitive_path,
)


class PRBenchRunStatus(str, Enum):
    COMPLETED = "completed"
    APPROVAL_REQUIRED = "approval_required"
    POLICY_BLOCKED = "policy_blocked"
    PROVIDER_ERROR = "provider_error"
    PROCESS_FAILED = "process_failed"
    ARTIFACT_VERIFICATION_FAILED = "artifact_verification_failed"
    REPEATED_NO_PROGRESS = "repeated_no_progress"
    TOOL_BUDGET_EXHAUSTED = "tool_budget_exhausted"


_EXIT_CODES = {
    PRBenchRunStatus.COMPLETED: 0,
    PRBenchRunStatus.APPROVAL_REQUIRED: 2,
    PRBenchRunStatus.POLICY_BLOCKED: 3,
    PRBenchRunStatus.PROVIDER_ERROR: 4,
    PRBenchRunStatus.PROCESS_FAILED: 5,
    PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED: 6,
    PRBenchRunStatus.REPEATED_NO_PROGRESS: 7,
    PRBenchRunStatus.TOOL_BUDGET_EXHAUSTED: 8,
}


class PRBenchArtifactSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    exists: bool
    size: int | None = None
    sha256: str | None = None


class PRBenchTraceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    events: int = Field(ge=0)


class PRBenchRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: PRBenchRunStatus
    model: str
    tool_calls: int = Field(ge=0)
    artifacts: tuple[PRBenchArtifactSummary, ...] = ()
    trace: PRBenchTraceSummary | None = None

    @property
    def exit_code(self) -> int:
        return _EXIT_CODES[self.status]


class _PRBenchBoundaryError(RuntimeError):
    pass


class _SanitizedProvider:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def generate(self, messages, tools):
        try:
            return self._llm.generate(messages, tools)
        except Exception:
            raise RuntimeError("Provider request failed") from None


def _artifact_summary(snapshot: ArtifactSnapshot) -> PRBenchArtifactSummary:
    return PRBenchArtifactSummary.model_validate(snapshot.model_dump())


def _progress_fingerprint(journal: ExecutionJournal) -> str:
    payload: dict[str, Any] = {
        "artifacts": [item.model_dump(mode="json") for item in journal.snapshot_artifacts()],
        "last_successful_script": next(
            (
                record.script_sha256
                for record in reversed(journal.records)
                if record.status == "ok" and record.script_sha256 is not None
            ),
            None,
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _is_within_workspace(path: Path, workspace: Path) -> bool:
    try:
        path.relative_to(workspace)
    except ValueError:
        return False
    return True


def _resolve_workspace_path(
    workspace: Path,
    path: Path | str,
    *,
    require_file: bool,
) -> Path:
    raw = Path(path).expanduser()
    if is_sensitive_path(str(raw), PRBENCH_HIDDEN_PATH_COMPONENTS):
        raise _PRBenchBoundaryError("unsafe PRBench path")
    candidate = raw if raw.is_absolute() else workspace / raw
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise _PRBenchBoundaryError("PRBench path cannot be resolved") from exc
    if not _is_within_workspace(resolved, workspace):
        raise _PRBenchBoundaryError("PRBench path escapes the workspace")
    relative = resolved.relative_to(workspace).as_posix()
    if is_sensitive_path(relative, PRBENCH_HIDDEN_PATH_COMPONENTS):
        raise _PRBenchBoundaryError("unsafe resolved PRBench path")
    if require_file and not resolved.is_file():
        raise _PRBenchBoundaryError("required public PRBench input is not a file")
    return resolved


def _ensure_no_ground_truth(workspace: Path) -> None:
    if not workspace.is_dir():
        raise _PRBenchBoundaryError("workspace is not a directory")
    if has_hidden_path_component(workspace, PRBENCH_HIDDEN_PATH_COMPONENTS):
        raise _PRBenchBoundaryError("workspace path contains hidden data")

    def fail_scan(error: OSError) -> None:
        raise _PRBenchBoundaryError("workspace visibility scan failed") from error

    try:
        for current, directory_names, file_names in os.walk(
            workspace,
            followlinks=False,
            onerror=fail_scan,
        ):
            current_path = Path(current)
            for name in (*directory_names, *file_names):
                candidate = current_path / name
                relative = candidate.relative_to(workspace)
                resolved = candidate.resolve(strict=False)
                if has_hidden_path_component(
                    relative, PRBENCH_HIDDEN_PATH_COMPONENTS
                ) or has_hidden_path_component(resolved, PRBENCH_HIDDEN_PATH_COMPONENTS):
                    raise _PRBenchBoundaryError("workspace contains hidden PRBench data")
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, _PRBenchBoundaryError):
            raise
        raise _PRBenchBoundaryError("workspace visibility scan failed") from exc


def _result_directory(workspace: Path) -> Path:
    result_dir = _resolve_workspace_path(
        workspace,
        workspace / ".phycode" / "prbench",
        require_file=False,
    )
    if result_dir.exists() and not result_dir.is_dir():
        raise _PRBenchBoundaryError("PRBench result directory is not a directory")
    return result_dir


def _write_result(workspace: Path, result: PRBenchRunResult) -> None:
    result_dir = _result_directory(workspace)
    result_dir.mkdir(parents=True, exist_ok=True)
    destination = result_dir / "run_result.json"
    temporary = result_dir / "run_result.json.tmp"
    payload = redact_obj(result.model_dump(mode="json"))
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)


def _status_from_stop_reason(
    stopped_reason: str,
    events: list,
    max_tool_calls: int,
) -> PRBenchRunStatus:
    if stopped_reason == "completed":
        return PRBenchRunStatus.COMPLETED
    if stopped_reason == "repeated_no_progress":
        return PRBenchRunStatus.REPEATED_NO_PROGRESS
    if stopped_reason in {"error", "incomplete", "user_interrupt"}:
        return PRBenchRunStatus.PROVIDER_ERROR

    output_statuses: list[str] = []
    process_failed = False
    current_tool: str | None = None
    tool_calls = 0
    for event in events:
        if event.type == AgentEventType.TOOL_CALL_REQUESTED:
            current_tool = str(event.payload.get("tool_name", ""))
            tool_calls += 1
        elif event.type == AgentEventType.TOOL_CALL_OUTPUT:
            status = str(event.payload.get("status", ""))
            output_statuses.append(status)
            if current_tool == "process.run" and status in {
                "command_failed",
                "timeout",
                "tool_error",
                "invalid_tool_args",
            }:
                process_failed = True
            current_tool = None

    if "policy_blocked" in output_statuses:
        return PRBenchRunStatus.POLICY_BLOCKED
    if "policy_requires_approval" in output_statuses:
        return PRBenchRunStatus.APPROVAL_REQUIRED
    if process_failed:
        return PRBenchRunStatus.PROCESS_FAILED
    if stopped_reason in {"tool_budget", "artifact_verification_failed"} and tool_calls >= max_tool_calls:
        return PRBenchRunStatus.TOOL_BUDGET_EXHAUSTED
    if stopped_reason in {"artifact_verification_failed", "max_steps", "repeated_failure"}:
        return PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED
    return PRBenchRunStatus.PROVIDER_ERROR


def _policy_failure(workspace: Path, model_name: str) -> PRBenchRunResult:
    result = PRBenchRunResult(
        status=PRBenchRunStatus.POLICY_BLOCKED,
        model=model_name,
        tool_calls=0,
    )
    try:
        _write_result(workspace, result)
    except (OSError, RuntimeError, _PRBenchBoundaryError):
        pass
    return result


def run_prbench(
    workspace: Path,
    contract_path: Path,
    approvals_path: Path,
    *,
    llm: LLMClient | None = None,
    max_tool_calls: int | None = None,
) -> PRBenchRunResult:
    root = workspace.expanduser().resolve()
    model_name = str(getattr(llm, "model", type(llm).__name__ if llm is not None else "unconfigured"))
    try:
        _ensure_no_ground_truth(root)
        _result_directory(root)
        safe_contract_path = _resolve_workspace_path(root, contract_path, require_file=True)
        safe_approvals_path = _resolve_workspace_path(root, approvals_path, require_file=True)
        contract = TaskContract.model_validate_json(safe_contract_path.read_text(encoding="utf-8"))
        _resolve_workspace_path(root, contract.instruction_file, require_file=True)
        _resolve_workspace_path(root, contract.paper_file, require_file=True)
        for input_file in contract.input_files:
            _resolve_workspace_path(root, input_file, require_file=True)
        for expected_file in contract.expected_files:
            _resolve_workspace_path(root, expected_file, require_file=False)
        approvals = ApprovalManifest.from_json(safe_approvals_path, root)
    except Exception:
        return _policy_failure(root, model_name)
    journal = ExecutionJournal(root, contract.expected_files)
    verifier = ArtifactVerifier(root, contract, journal)
    if llm is None:
        try:
            provider = load_prbench_provider_config()
            model_name = provider.model
            llm = OpenAICompatibleChatAdapter(
                api_key=provider.api_key.get_secret_value(),
                base_url=provider.base_url,
                model=provider.model,
            )
        except Exception:
            result = PRBenchRunResult(
                status=PRBenchRunStatus.PROVIDER_ERROR,
                model=model_name,
                tool_calls=0,
                artifacts=tuple(_artifact_summary(item) for item in journal.snapshot_artifacts()),
            )
            _write_result(root, result)
            return result

    instruction = (root / contract.instruction_file).read_text(encoding="utf-8")
    paper = (root / contract.paper_file).read_text(encoding="utf-8")
    prompt = (
        "PRBench public task instruction:\n"
        f"{instruction}\n\n"
        "PRBench public paper:\n"
        f"{paper}\n\n"
        f"Public input files: {', '.join(contract.input_files) or '(none)'}"
    )
    trace_dir = root / ".phycode" / "prbench" / "traces"
    loop = build_agent(
        SessionMode.NON_INTERACTIVE,
        llm=_SanitizedProvider(llm),
        approval_handler=approvals,
        profile=AgentProfile.PRBENCH,
        max_tool_calls=max_tool_calls,
        workspace_root=root,
        execution_journal=journal,
        completion_verifier=verifier.verify,
        progress_fingerprint=lambda: _progress_fingerprint(journal),
        trace_dir=trace_dir,
    )
    agent_result = loop.run(prompt)
    tool_calls = sum(
        event.type == AgentEventType.TOOL_CALL_REQUESTED for event in agent_result.events
    )
    trace_path = trace_dir / f"{loop.session_store.session.id}.jsonl"
    result = PRBenchRunResult(
        status=_status_from_stop_reason(
            agent_result.stopped_reason,
            agent_result.events,
            loop.max_tool_calls,
        ),
        model=model_name,
        tool_calls=tool_calls,
        artifacts=tuple(_artifact_summary(item) for item in journal.snapshot_artifacts()),
        trace=PRBenchTraceSummary(
            path=trace_path.relative_to(root).as_posix(),
            events=len(agent_result.events),
        ),
    )
    _write_result(root, result)
    return result


def prbench_result_lines(result: PRBenchRunResult) -> tuple[str, ...]:
    lines = [
        f"status={result.status.value}",
        f"model={redact_text(result.model)}",
        f"tool_calls={result.tool_calls}",
    ]
    lines.extend(f"artifact={artifact.path}" for artifact in result.artifacts)
    return tuple(lines)


module_app = typer.Typer(help="Run a verified public PRBench task")


@module_app.callback()
def module_root() -> None:
    """PRBench runner module entry point."""


@module_app.command("run")
def module_run(
    workspace: Path = typer.Option(..., help="PRBench task workspace"),
    contract: Path = typer.Option(..., help="Public task contract JSON"),
    approvals: Path = typer.Option(..., help="Exact one-time approval manifest JSON"),
    max_tool_calls: int | None = typer.Option(None, min=1, help="Tool-call budget override"),
) -> None:
    result = run_prbench(
        workspace,
        contract,
        approvals,
        max_tool_calls=max_tool_calls,
    )
    for line in prbench_result_lines(result):
        typer.echo(line)
    raise typer.Exit(code=result.exit_code)


if __name__ == "__main__":
    module_app()
