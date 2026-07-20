from __future__ import annotations

import hashlib
import json
import os
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any

import typer
from pydantic import BaseModel, ConfigDict, Field

from phycode.approval import ApprovalManifest
from phycode.composition import build_agent, trusted_prbench_runtime_settings
from phycode.config import load_prbench_provider_config, validate_prbench_model_label
from phycode.execution import ArtifactSnapshot, ExecutionJournal
from phycode.llm import LLMClient, OpenAICompatibleChatAdapter
from phycode.models import AgentEventType, AgentProfile, SessionMode
from phycode.prbench_contract import ArtifactVerifier, TaskContract
from phycode.profiles import profile_spec
from phycode.redaction import redact_obj, redact_text
from phycode.tools.file_tools import DEFAULT_FILE_READ_CHARS, MAX_FILE_READ_CHARS
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

_PRBENCH_PROVIDER_TIMEOUT_SECONDS = 600.0


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
    payload = redact_obj(result.model_dump(mode="json"))
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=result_dir,
            prefix=".run_result-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _status_from_stop_reason(
    stopped_reason: str,
    terminal_blocker: str | None = None,
) -> PRBenchRunStatus:
    if stopped_reason == "completed":
        return PRBenchRunStatus.COMPLETED
    if terminal_blocker is not None:
        try:
            return PRBenchRunStatus(terminal_blocker)
        except ValueError:
            return PRBenchRunStatus.PROVIDER_ERROR
    if stopped_reason == "repeated_no_progress":
        return PRBenchRunStatus.REPEATED_NO_PROGRESS
    if stopped_reason == "artifact_verification_failed":
        return PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED
    return PRBenchRunStatus.PROVIDER_ERROR


def _safe_model_name(llm: LLMClient | None) -> str:
    if llm is None:
        return "unconfigured"
    fallback = type(llm).__name__ or "injected-provider"
    try:
        raw_label = getattr(llm, "model", fallback)
        return validate_prbench_model_label(str(raw_label))
    except Exception:
        try:
            return validate_prbench_model_label(fallback)
        except Exception:
            return "injected-provider"


def _persist_if_safe(workspace: Path, result: PRBenchRunResult) -> PRBenchRunResult:
    try:
        if not workspace.is_dir():
            raise _PRBenchBoundaryError("PRBench result workspace is unavailable")
        _write_result(workspace, result)
    except Exception:
        if result.status == PRBenchRunStatus.COMPLETED:
            return result.model_copy(
                update={"status": PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED}
            )
    return result


def _controlled_failure(
    workspace: Path,
    model_name: str,
    status: PRBenchRunStatus,
) -> PRBenchRunResult:
    result = PRBenchRunResult(
        status=status,
        model=model_name,
        tool_calls=0,
    )
    return _persist_if_safe(workspace, result)


def _policy_failure(workspace: Path, model_name: str) -> PRBenchRunResult:
    return _controlled_failure(workspace, model_name, PRBenchRunStatus.POLICY_BLOCKED)


def _validate_internal_directory(workspace: Path, path: Path) -> Path:
    resolved = _resolve_workspace_path(workspace, path, require_file=False)
    if resolved.exists() and not resolved.is_dir():
        raise _PRBenchBoundaryError("PRBench internal path is not a directory")
    return resolved


def _production_tool_call_lower_bound(contract: TaskContract) -> int:
    constrained_paths = {
        constraint.path.replace("\\", "/").casefold()
        for constraint in contract.constraints
    }
    source_artifact_writes = sum(
        path.replace("\\", "/").casefold() not in constrained_paths
        for path in contract.expected_files
    )
    return source_artifact_writes + len(contract.execution_entrypoints)


def _discovery_tool_call_cap(contract: TaskContract, max_tool_calls: int) -> int:
    return min(
        10,
        max(0, max_tool_calls - _production_tool_call_lower_bound(contract)),
    )


def _render_public_constraint(constraint) -> str:
    header = (
        json.dumps(constraint.csv_header, ensure_ascii=False)
        if constraint.csv_header is not None
        else "(not constrained)"
    )
    if constraint.csv_data_row_count is not None:
        rows = f"exact data row count: {constraint.csv_data_row_count}"
    elif constraint.csv_rows is not None:
        rows = (
            "exact rows: "
            + json.dumps(constraint.csv_rows, ensure_ascii=False)
        )
    else:
        rows = "data rows: (not constrained)"
    return f"- {constraint.path}; exact header: {header}; {rows}"


def build_prbench_task_brief(
    contract: TaskContract,
    *,
    max_tool_calls: int | None = None,
    max_discovery_tool_calls: int | None = None,
) -> str:
    total_tool_calls = (
        max_tool_calls
        if max_tool_calls is not None
        else profile_spec(AgentProfile.PRBENCH).max_tool_calls
    )
    if (
        isinstance(total_tool_calls, bool)
        or not isinstance(total_tool_calls, int)
        or total_tool_calls <= 0
    ):
        raise ValueError("max_tool_calls must be a positive integer")
    derived_discovery_cap = _discovery_tool_call_cap(contract, total_tool_calls)
    discovery_cap = (
        derived_discovery_cap
        if max_discovery_tool_calls is None
        else max_discovery_tool_calls
    )
    if (
        isinstance(discovery_cap, bool)
        or not isinstance(discovery_cap, int)
        or not 0 <= discovery_cap <= derived_discovery_cap
    ):
        raise ValueError("max_discovery_tool_calls exceeds the reserved public budget")
    production_lower_bound = _production_tool_call_lower_bound(contract)
    entrypoints = set(contract.execution_entrypoints)
    expected = ", ".join(
        f"{path}*" if path in entrypoints else path
        for path in contract.expected_files
    )
    inputs = ", ".join(contract.input_files) or "(none)"
    constraints = (
        "\n".join(_render_public_constraint(item) for item in contract.constraints)
        or "- (none)"
    )
    brief = (
        "PRBench; visible files only.\n"
        f"Total tool-call budget: {total_tool_calls}\n"
        f"Minimum production calls: {production_lower_bound} = non-constraint artifact writes "
        "+ entrypoint process.run\n"
        f"Discovery call cap: {discovery_cap}\n"
        f"Read full instruction (file.read): {contract.instruction_file}\n"
        f"Paper (targeted search.grep): {contract.paper_file}\n"
        f"Inputs: {inputs}\n"
        "CSV constraints:\n"
        f"{constraints}\n"
        f"Artifacts (*=entrypoint): {expected}\n"
        "file.read offset/limit: zero-based UTF-8 decoded characters, not lines; "
        f"default/max={DEFAULT_FILE_READ_CHARS}/{MAX_FILE_READ_CHARS}. If truncated follow "
        "next_offset; Do not overlap/re-read. Do not exhaustively page through the paper; use "
        "targeted grep. Implement via file.write/edit before discovery cap, then process.run "
        "entrypoints. Verifier reports after each successful tool."
    )
    if len(brief) >= 4_000:
        raise ValueError("PRBench task brief exceeds the public context boundary")
    return brief


def run_prbench(
    workspace: Path,
    contract_path: Path,
    approvals_path: Path,
    *,
    llm: LLMClient | None = None,
    max_tool_calls: int | None = None,
    max_context_chars: int | None = None,
    approval_wait_seconds: int = 0,
) -> PRBenchRunResult:
    model_name = _safe_model_name(llm)
    try:
        root = workspace.expanduser().resolve()
        if not root.is_dir():
            return PRBenchRunResult(
                status=PRBenchRunStatus.POLICY_BLOCKED,
                model=model_name,
                tool_calls=0,
            )
    except Exception:
        return PRBenchRunResult(
            status=PRBenchRunStatus.POLICY_BLOCKED,
            model=model_name,
            tool_calls=0,
        )
    if (
        isinstance(approval_wait_seconds, bool)
        or not isinstance(approval_wait_seconds, int)
        or not 0 <= approval_wait_seconds <= 900
    ):
        return _policy_failure(root, model_name)
    if max_context_chars is not None and (
        isinstance(max_context_chars, bool)
        or not isinstance(max_context_chars, int)
        or not 1_000 <= max_context_chars <= 64_000
    ):
        return _policy_failure(root, model_name)
    if max_tool_calls is not None and (
        isinstance(max_tool_calls, bool)
        or not isinstance(max_tool_calls, int)
        or max_tool_calls <= 0
    ):
        return _policy_failure(root, model_name)
    try:
        _ensure_no_ground_truth(root)
        _result_directory(root)
        trace_dir = _validate_internal_directory(
            root,
            root / ".phycode" / "prbench" / "traces",
        )
        safe_contract_path = _resolve_workspace_path(root, contract_path, require_file=True)
        safe_approvals_path = _resolve_workspace_path(root, approvals_path, require_file=True)
        contract = TaskContract.model_validate_json(safe_contract_path.read_text(encoding="utf-8"))
        _resolve_workspace_path(root, contract.instruction_file, require_file=True)
        _resolve_workspace_path(root, contract.paper_file, require_file=True)
        for input_file in contract.input_files:
            _resolve_workspace_path(root, input_file, require_file=True)
        for expected_file in contract.expected_files:
            _resolve_workspace_path(root, expected_file, require_file=False)
        approvals = ApprovalManifest.from_json(
            safe_approvals_path,
            root,
            approval_wait_seconds=approval_wait_seconds,
        )
        prbench_spec = profile_spec(AgentProfile.PRBENCH)
        effective_tool_calls = (
            max_tool_calls
            if max_tool_calls is not None
            else prbench_spec.max_tool_calls
        )
        discovery_cap = _discovery_tool_call_cap(contract, effective_tool_calls)
        prompt = build_prbench_task_brief(
            contract,
            max_tool_calls=effective_tool_calls,
            max_discovery_tool_calls=discovery_cap,
        )
        effective_context_chars = (
            max_context_chars
            if max_context_chars is not None
            else prbench_spec.max_context_chars
        )
        current_input_capacity = max(1_000, effective_context_chars // 3)
        if len(prompt) > current_input_capacity:
            return _policy_failure(root, model_name)
    except Exception:
        return _policy_failure(root, model_name)

    try:
        journal = ExecutionJournal(root, contract.expected_files)
        verifier = ArtifactVerifier(root, contract, journal)
    except Exception:
        return _controlled_failure(
            root,
            model_name,
            PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED,
        )

    resolved_llm = llm
    if resolved_llm is None:
        try:
            provider = load_prbench_provider_config()
            model_name = provider.model
            resolved_llm = OpenAICompatibleChatAdapter(
                api_key=provider.api_key.get_secret_value(),
                base_url=provider.base_url,
                model=provider.model,
                timeout_seconds=_PRBENCH_PROVIDER_TIMEOUT_SECONDS,
            )
        except Exception:
            result = PRBenchRunResult(
                status=PRBenchRunStatus.PROVIDER_ERROR,
                model=model_name,
                tool_calls=0,
                artifacts=(),
            )
            return _persist_if_safe(root, result)

    try:
        loop = build_agent(
            SessionMode.NON_INTERACTIVE,
            llm=_SanitizedProvider(resolved_llm),
            approval_handler=approvals,
            profile=AgentProfile.PRBENCH,
            max_tool_calls=effective_tool_calls,
            max_discovery_tool_calls=discovery_cap,
            max_context_chars=max_context_chars,
            execution_journal=journal,
            completion_verifier=verifier.verify,
            progress_fingerprint=lambda: _progress_fingerprint(journal),
            verify_after_successful_tool=True,
            runtime_settings=trusted_prbench_runtime_settings(root, trace_dir),
        )
        agent_result = loop.run(prompt)
    except Exception:
        return _controlled_failure(root, model_name, PRBenchRunStatus.PROVIDER_ERROR)

    tool_calls = sum(
        event.type == AgentEventType.TOOL_CALL_REQUESTED for event in agent_result.events
    )
    trace_path = trace_dir / f"{loop.session_store.session.id}.jsonl"
    try:
        result = PRBenchRunResult(
            status=_status_from_stop_reason(
                agent_result.stopped_reason,
                agent_result.terminal_blocker,
            ),
            model=model_name,
            tool_calls=tool_calls,
            artifacts=tuple(_artifact_summary(item) for item in journal.snapshot_artifacts()),
            trace=PRBenchTraceSummary(
                path=trace_path.relative_to(root).as_posix(),
                events=loop.trace_store.event_count(loop.session_store.session.id),
            ),
        )
    except Exception:
        result = PRBenchRunResult(
            status=PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED,
            model=model_name,
            tool_calls=tool_calls,
        )
    return _persist_if_safe(root, result)


def prbench_result_lines(result: PRBenchRunResult) -> tuple[str, ...]:
    lines = [
        f"status={result.status.value}",
        f"model={redact_text(result.model)}",
        f"tool_calls={result.tool_calls}",
    ]
    lines.extend(f"artifact={artifact.path}" for artifact in result.artifacts)
    return tuple(lines)


prbench_app = typer.Typer(help="Run a verified public PRBench task")


@prbench_app.callback()
def prbench_root() -> None:
    """PRBench runner module entry point."""


@prbench_app.command("run")
def prbench_run(
    workspace: Path = typer.Option(..., help="PRBench task workspace"),
    contract: Path = typer.Option(..., help="Public task contract JSON"),
    approvals: Path = typer.Option(..., help="Exact one-time approval manifest JSON"),
    max_tool_calls: int | None = typer.Option(None, min=1, help="Tool-call budget override"),
    max_context_chars: int | None = typer.Option(
        None,
        min=1_000,
        max=64_000,
        help="Context character budget override",
    ),
    approval_wait_seconds: int = typer.Option(
        0,
        min=0,
        max=900,
        help="Seconds to wait for a runtime hash-bound process approval",
    ),
) -> None:
    result = run_prbench(
        workspace,
        contract,
        approvals,
        max_tool_calls=max_tool_calls,
        max_context_chars=max_context_chars,
        approval_wait_seconds=approval_wait_seconds,
    )
    for line in prbench_result_lines(result):
        typer.echo(line)
    raise typer.Exit(code=result.exit_code)


if __name__ == "__main__":
    prbench_app()
