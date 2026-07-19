from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from phycode.agent import AgentLoop, CompletionVerification
from phycode.config import AgentConfig, LLMConfig, ProjectConfig, TestConfig, WorkspaceConfig
from phycode.config import load_project_config
from phycode.context import ContextBuilder, MemoryStore, SessionStore
from phycode.credentials import CredentialStore
from phycode.execution import ExecutionJournal
from phycode.llm import EchoLLM, LLMClient, OpenAICompatibleChatAdapter
from phycode.models import AgentProfile, Session, SessionMode, ToolCall
from phycode.policy import PolicyContext
from phycode.profiles import profile_spec
from phycode.tools import ToolRegistry, ToolRuntime
from phycode.tools.calculator_tools import register_calculator_tools
from phycode.tools.file_tools import register_file_tools
from phycode.tools.media_tools import register_media_tools
from phycode.tools.process_tools import register_process_tools
from phycode.tools.search_tools import register_search_tools
from phycode.tools.shell_tools import register_shell_tools
from phycode.tools.state_tools import register_state_tools
from phycode.tools.web_tools import register_web_tools
from phycode.trace import TraceStore
from phycode.visibility import PathVisibilityPolicy


@dataclass(frozen=True)
class AgentRuntimeSettings:
    project_config: ProjectConfig
    memory_store: MemoryStore
    trace_dir: Path
    workspace_label: str | None = None


def build_default_registry(
    workspace_root: Path | None = None,
    test_command: str | None = None,
    memory_store: MemoryStore | None = None,
    vision_inspector=None,
    visibility: PathVisibilityPolicy | None = None,
    execution_journal: ExecutionJournal | None = None,
    process_execution_guard: Callable[[ToolCall], bool] | None = None,
) -> ToolRegistry:
    """Compose the built-in registry without consulting project config when dependencies are explicit."""
    registry = ToolRegistry()
    fallback_config = None
    if workspace_root is None or test_command is None:
        fallback_config = load_project_config(Path.cwd())
    root = (
        workspace_root
        if workspace_root is not None
        else fallback_config.workspace.root if fallback_config is not None else Path.cwd()
    ).resolve()
    configured_test_command = (
        test_command
        if test_command is not None
        else fallback_config.test.command if fallback_config is not None else "uv run pytest"
    )
    register_file_tools(registry)
    register_calculator_tools(registry)
    register_search_tools(registry, workspace_root=root, visibility=visibility)
    register_process_tools(
        registry,
        root,
        frozenset({Path(sys.executable).resolve()}),
        journal=execution_journal,
        execution_guard=process_execution_guard,
    )
    register_shell_tools(registry, workspace_root=root, test_command=configured_test_command)
    register_state_tools(registry, workspace_root=root, memory_store=memory_store)
    register_web_tools(registry)
    register_media_tools(registry, vision_inspector)
    return registry


def registry_subset(registry: ToolRegistry, names: frozenset[str]) -> ToolRegistry:
    selected = ToolRegistry()
    for spec in registry.list_specs():
        if spec.name not in names:
            continue
        executor = registry.executor_for(spec.name)
        if executor is not None:
            selected.register(
                spec,
                executor,
                normalizer=registry.normalizer_for(spec.name),
            )
    return selected


def build_llm(
    config: ProjectConfig,
    credential_store: CredentialStore | None = None,
) -> LLMClient:
    """Build the configured provider, falling back to the deterministic offline client."""
    store = credential_store if credential_store is not None else CredentialStore()
    try:
        api_key = store.get_key(config.llm.provider)
    except Exception:
        api_key = None
    if api_key:
        return OpenAICompatibleChatAdapter(
            base_url=config.llm.base_url,
            model=config.llm.model,
            api_key=api_key,
            vision_model=config.llm.vision_model,
            timeout_seconds=config.llm.timeout_seconds,
            max_retries=config.llm.max_retries,
        )
    return EchoLLM()


def build_agent(
    mode: SessionMode,
    llm: LLMClient | None = None,
    approval_handler=None,
    profile: AgentProfile = AgentProfile.CODING,
    max_tool_calls: int | None = None,
    vision_inspector=None,
    workspace_root: Path | None = None,
    execution_journal: ExecutionJournal | None = None,
    completion_verifier: Callable[[], CompletionVerification] | None = None,
    progress_fingerprint: Callable[[], str] | None = None,
    verify_after_successful_tool: bool = False,
    trace_dir: Path | None = None,
    runtime_settings: AgentRuntimeSettings | None = None,
    max_context_chars: int | None = None,
) -> AgentLoop:
    """Compose an agent loop from explicit runtime dependencies or normal project config."""
    spec = profile_spec(profile)
    effective_context_chars = (
        max_context_chars if max_context_chars is not None else spec.max_context_chars
    )
    config = (
        runtime_settings.project_config
        if runtime_settings is not None
        else load_project_config(workspace_root if workspace_root is not None else Path.cwd())
    )
    root = config.workspace.root
    phycode_dir = root / ".phycode"
    session_store = SessionStore(Session(workspace_root=str(root), mode=mode))
    memory_store = (
        runtime_settings.memory_store
        if runtime_settings is not None
        else MemoryStore(phycode_dir / "memory.jsonl")
    )
    resolved_llm = llm if llm is not None else build_llm(config)
    configured_vision = vision_inspector
    if configured_vision is None and getattr(resolved_llm, "vision_model", None):
        configured_vision = getattr(resolved_llm, "inspect_image", None)
    policy_context = PolicyContext(
        workspace_root=root,
        allowlist=config.workspace.allowlist,
        interactive=mode == SessionMode.INTERACTIVE,
        profile_spec=spec,
    )
    candidate_execution_guard = getattr(approval_handler, "validate_execution", None)
    process_execution_guard = cast(
        Callable[[ToolCall], bool] | None,
        candidate_execution_guard if callable(candidate_execution_guard) else None,
    )
    registry = build_default_registry(
        root,
        config.test.command,
        memory_store,
        configured_vision,
        visibility=policy_context.visibility,
        execution_journal=execution_journal,
        process_execution_guard=process_execution_guard,
    )
    registry = registry_subset(registry, spec.tool_names)
    return AgentLoop(
        llm=resolved_llm,
        context_builder=ContextBuilder(
            session_store,
            memory_store,
            max_chars=effective_context_chars,
            system_prompt=spec.system_prompt,
            workspace_label=(
                runtime_settings.workspace_label if runtime_settings is not None else None
            ),
        ),
        tool_runtime=ToolRuntime(registry),
        policy_context=policy_context,
        trace_store=TraceStore(
            runtime_settings.trace_dir
            if runtime_settings is not None
            else trace_dir if trace_dir is not None else phycode_dir / "traces"
        ),
        session_store=session_store,
        max_steps=config.agent.max_steps,
        max_tool_calls=max_tool_calls if max_tool_calls is not None else spec.max_tool_calls,
        approval_handler=approval_handler,
        completion_verifier=completion_verifier,
        progress_fingerprint=progress_fingerprint,
        verify_after_successful_tool=verify_after_successful_tool,
    )


def trusted_prbench_runtime_settings(
    workspace_root: Path,
    trace_dir: Path,
) -> AgentRuntimeSettings:
    root = workspace_root.expanduser().resolve()
    return AgentRuntimeSettings(
        project_config=ProjectConfig(
            workspace=WorkspaceConfig(root=root, allowlist=[]),
            agent=AgentConfig(max_steps=50),
            test=TestConfig(command="uv run pytest"),
            llm=LLMConfig(),
        ),
        memory_store=MemoryStore.ephemeral(),
        trace_dir=trace_dir,
        workspace_label=".",
    )
