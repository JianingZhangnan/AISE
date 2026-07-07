# PhyCode Phase 1 Specification

## 1. Problem Statement

PhyCode is planned as a physics-oriented coding and research agent. The long-term vision includes Wolfram tools, LaTeX workflows, computational physics guidance, literature support, and a domain knowledge graph. Because those physics-specific capabilities may exceed the course deadline, Phase 1 deliberately focuses on a complete, extensible, general-purpose coding agent harness that can stand on its own as the final project deliverable.

Phase 1 will deliver a CLI-first Coding Agent Harness with a self-implemented agent loop, OpenAI-compatible model adapter, mock LLM test path, policy-aware tool runtime, feedback loop, basic memory/context management, credential handling, CI, and packaging instructions. Physics-specific tools are treated as future extensions, not as dependencies of the Phase 1 core.

The primary engineering contribution is a **Policy-Aware Tool Runtime**: every tool call goes through schema validation, workspace boundary checks, permission decisions, execution wrappers, output truncation, feedback classification, and trace recording. This integrates the three required mechanism families: tool dispatch, governance/safety, and feedback.

## 2. Goals and Non-Goals

### Goals

- Provide an interactive CLI agent session similar in spirit to coding agents such as Claude Code, while keeping the implementation lightweight and testable.
- Implement the harness core in code rather than relying on an existing agent runner.
- Support OpenAI-compatible chat completion providers, including local or Chinese open-source model services.
- Keep all core tests deterministic through mock/stub LLMs and fake tool executors.
- Enforce workspace and credential safety through deterministic policy code.
- Provide traceable evidence for tool calls, policy decisions, feedback signals, and memory changes.
- Preserve a clean extension path for Phase 2 physics capabilities.

### Non-Goals

- No WebUI in Phase 1.
- No LSP integration, MCP integration, subagent orchestration, web search, Wolfram, LaTeX compilation, literature retrieval, or knowledge graph in Phase 1.
- No dependency on OpenAI Agents SDK, LangChain AgentExecutor, AutoGen, CrewAI, LlamaIndex agents, or host coding-agent SDK loops as product core.
- No complex vector memory or semantic long-term retrieval in Phase 1.

## 3. Target Users

- A student developer building and demonstrating an AI4SE final project.
- A future user who wants a CLI coding harness that can run on a fresh machine with safe credential configuration.
- A future physics student or researcher who wants to extend the harness with domain-specific tools.

## 4. INVEST User Stories

1. As a developer, I want to start `phycode` once and continue a multi-turn interactive session, so that I do not need to relaunch the command for every prompt.

2. As a developer, I want the agent to inspect files, edit code, run commands, and run tests through registered tools, so that its actions are observable and controlled.

3. As a cautious user, I want dangerous or out-of-workspace actions to be blocked or require approval, so that the agent cannot damage unrelated files or leak secrets.

4. As a maintainer, I want every tool result to be converted into structured feedback, so that the agent can react to failed tests, command failures, invalid arguments, and policy blocks.

5. As a project reviewer, I want the core agent loop to run under a mock LLM, so that I can verify the harness without network access or a real API key.

6. As a future PhyCode extender, I want tools to be registered through a stable interface, so that Wolfram, LaTeX, literature, and knowledge-graph tools can be added later without rewriting the loop.

7. As a security-conscious user, I want API keys to be stored securely and never printed into logs, memory, traces, or config files, so that repository history remains free of credentials.

## 5. Functional Specification

### 5.1 CLI and Session Interface

Inputs:
- `phycode` or `phycode chat` starts an interactive session.
- `phycode run "<task>"` runs one non-interactive task.
- `phycode tools list` shows registered tools and risk levels.
- `phycode demo guardrail|feedback|policy` runs deterministic mock demos.
- `phycode config read|write` manages non-sensitive config.
- `phycode keys set|status|clear` manages credentials.

Behavior:
- The interactive CLI keeps a session history in memory and writes trace events to local session files.
- The CLI renders assistant commentary, final answers, tool calls, policy decisions, and feedback with lightweight terminal formatting.
- Reasoning summaries, if provided by a future adapter, are folded by default. Raw hidden reasoning is not required or exposed.
- In interactive mode, policy decisions with `ask` pause and request user approval.
- In non-interactive mode, `ask` produces a structured `policy_requires_approval` result unless an explicit safe auto-approval mode is configured.

Outputs:
- Human-readable terminal output.
- Structured trace JSONL under `.phycode/traces/`.
- Exit code `0` on successful non-interactive completion and nonzero on policy block, invalid config, or unrecoverable failure.

### 5.2 Agent Loop

The agent loop is implemented by PhyCode code. It performs:

1. Receive user input or pending approval response.
2. Build context from system instructions, tool schemas, workspace summary, memory, recent events, and current task.
3. Call an LLM adapter.
4. Normalize provider responses into an internal `AgentEvent` stream.
5. Route tool call events into the policy-aware tool runtime.
6. Convert tool results into feedback signals.
7. Append tool outputs and feedback to the next model turn.
8. Stop on final answer, max steps, user interrupt, repeated unrecoverable failures, or policy outcome requiring unavailable approval.

The loop must be testable with a scripted mock LLM.

### 5.3 LLM Adapters

Phase 1 supports:

- `ScriptedLLM` for deterministic tests and demos.
- `EchoLLM` for smoke tests.
- `FailingLLM` for provider-error tests.
- `OpenAICompatibleChatAdapter` for real interaction with `/v1/chat/completions` compatible providers.

The primary real-provider path uses OpenAI-compatible `tools` / `tool_calls`. A fallback JSON action parser may be enabled for providers with unstable tool-call support. The product core does not use OpenAI Agents SDK as its loop runner. A future Responses API adapter may be added, but Phase 1 correctness does not depend on it.

### 5.4 Internal Event Model

Provider responses are normalized into event types:

- `assistant_commentary`
- `reasoning_summary`
- `tool_call_requested`
- `policy_decision`
- `tool_call_running`
- `tool_call_output`
- `feedback_signal`
- `assistant_final`
- `error`
- `incomplete`
- `user_interrupt`

This avoids a simplistic text/tool dichotomy and gives the CLI and trace store a stable representation across providers.

### 5.5 Tool Registry and Built-In Tools

Every tool declares:

- `name`
- `description`
- `input_schema`
- `risk_level`
- `executor`
- `feedback_mapper`

Built-in tools:

- `file.read`: Read file contents with offset/limit and truncation metadata.
- `file.list`: List workspace files or directories.
- `file.write`: Create or overwrite a file.
- `file.edit`: Perform exact text replacement and return unified diff.
- `search.grep`: Search file contents, preferably through `rg`.
- `search.glob`: Locate files by glob pattern.
- `shell.run`: Run a bounded command in a workspace directory with timeout and output limits.
- `test.run`: Run configured test/lint/typecheck commands and classify results.
- `workspace.status`: Report workspace roots, allowlist, git status, and diff summary.
- `memory.read`: Read project memory summary.
- `memory.write`: Write explicit long-term memory entries.
- `config.read`: Read non-sensitive configuration.
- `config.write`: Update non-sensitive configuration.
- `keys.status`: Report credential presence without exposing secrets.

`keys.set` and `keys.clear` are CLI commands, not model-callable tools.

### 5.6 Policy and Guardrails

The policy engine returns `allow`, `ask`, or `deny` before any tool execution.

Default policy:

- `allow`: Safe reads and status operations.
- `ask`: File writes/edits, memory writes, config writes, and most shell commands.
- `deny`: Path escape, writing outside allowed roots, dangerous shell commands, credential reads, destructive system operations, and suspected credential exfiltration.

Policy requirements:

- All paths are resolved before use and must remain inside the workspace root or explicit allowlist.
- Symlink escape is treated as a boundary violation.
- Shell commands run with a configured `cwd`, timeout, and output limit.
- Dangerous command patterns are blocked by deterministic code.
- Credential-like files such as `.env`, private keys, and token stores are not readable by model-callable file tools.
- Every decision records a rule id and reason in trace.

### 5.7 Feedback Loop

Tool results are converted into `FeedbackSignal` records with:

- `kind`
- `summary`
- `evidence`
- `suggested_next_step`
- `retryable`

Feedback kinds include:

- `success`
- `command_failed`
- `test_failed`
- `policy_blocked`
- `policy_requires_approval`
- `invalid_tool_args`
- `tool_error`
- `timeout`
- `output_truncated`

The context builder includes recent high-value feedback in the next model turn. Repeated similar failures trigger stop or user intervention.

### 5.8 Context, Memory, and Trace

Stores:

- `SessionStore`: Current interactive session messages and events.
- `TraceStore`: Complete JSONL trace for review and debugging.
- `MemoryStore`: Curated long-term project memory.
- `ContextBuilder`: Deterministic context assembly.

Context order:

1. Stable system instructions.
2. Tool schemas.
3. Workspace summary.
4. Project memory summary.
5. Recent event window.
6. Recent feedback.
7. Current user input.

Budget behavior:

- Long file and command outputs are truncated before context insertion.
- Recent user intent and recent feedback are prioritized over old commentary.
- Static prompt prefix remains stable to benefit provider prompt caching when available.
- Correctness does not depend on provider caching.

Memory writes:

- `memory.write` is controlled by policy.
- Allowed categories are `decision`, `preference`, `project_fact`, and `test_command`.
- Trace is not treated as long-term memory.

### 5.9 Credentials and Configuration

Credential commands:

- `phycode keys set openai-compatible`
- `phycode keys status`
- `phycode keys clear openai-compatible`

Storage:

- Prefer OS keyring.
- If keyring is unavailable, use a local encrypted file protected by a master password.
- Environment variables and `.env` may be supported as optional sources, but documented as plaintext risks.

Sensitive data rules:

- API keys are never committed, logged, traced, written to memory, or shown in terminal output.
- Status displays presence/source/update time only.
- Error handling redacts credential-like strings.

Config:

- User config stores default provider, base URL, and model.
- Project `phycode.toml` stores workspace allowlist, test commands, enabled tools, and policy rules.
- Project policy config takes precedence for safety boundaries.

### 5.10 Distribution and CI

Primary development and distribution path:

- Python with `uv`.
- `uv sync`
- `uv run phycode`
- `uv run pytest`

Optional future packaging:

- `uvx` or package-manager installation.
- Dockerfile as an optional extra if time permits.

CI:

- `.gitlab-ci.yml` must contain a `unit-test` job running `uv run pytest`.
- GitHub Actions may be added for current GitHub development convenience, but it does not replace `.gitlab-ci.yml`.
- CI uses mock LLMs only and does not require API keys.

Repository platform:

- The project currently uses GitHub repository `JianingZhangnan/AISE` for development because the final platform is not yet confirmed.
- If NJU Git becomes required, the GitHub repository should be mirrored or migrated with history and the transition recorded in process documents.

## 6. Non-Functional Requirements

### 6.1 Performance

- Interactive CLI should show tool and policy events promptly.
- Default shell command timeout should prevent hanging runs.
- Context construction should complete quickly for ordinary course-project repositories.
- Long outputs should be truncated before entering context.

### 6.2 Security and Credential Threat Model

Threats:

- Model requests reading `.env` or private key files.
- Model emits shell command that deletes or exfiltrates data.
- Tool output or exception contains an API key.
- Trace or memory stores sensitive data.
- User accidentally commits local runtime state.

Mitigations:

- Workspace boundary enforcement.
- Credential file denylist.
- Deterministic shell command guardrails.
- Keyring/encrypted credential storage.
- Redaction before logging, tracing, and displaying.
- `.gitignore` excludes `.env`, runtime state, logs, and caches.
- Credential tests verify status and trace do not reveal secrets.

### 6.3 Usability

- Interactive CLI is the primary interface.
- Non-interactive `run` supports scripts and demos.
- Risky actions display command/diff/reason before approval.
- Error messages should identify policy or tool failure clearly.

### 6.4 Observability

- Each session writes trace JSONL.
- Trace includes event type, tool name, policy decision, feedback kind, and redacted summaries.
- Demos produce deterministic traces for review.

### 6.5 Portability

- Python package managed through `uv`.
- Core tests do not require network or provider credentials.
- Shell policy should account for Windows and POSIX command risks where feasible.

## 7. Architecture

Modules:

- `cli`: Typer commands, interactive loop shell, Rich rendering, approval prompts.
- `agent`: Main loop, stop controller, event handling.
- `llm`: OpenAI-compatible chat adapter and mock LLMs.
- `events`: Internal `AgentEvent` and provider normalization.
- `tools`: Tool registry and built-in tools.
- `policy`: Workspace policy, credential policy, shell risk rules, approval model.
- `feedback`: Tool-result classifiers.
- `context`: Session store and context builder.
- `memory`: Project memory storage.
- `trace`: JSONL trace store and redaction.
- `config`: User and project config loading.
- `credentials`: Keyring/encrypted-file secret storage.
- `extensions`: Future extension interface.

Dependency direction:

- `cli` calls `agent`.
- `agent` calls `llm`, `context`, `tools`, `trace`, and `feedback`.
- `tools` calls `policy` before executor code.
- `policy` uses `config` and workspace state.
- `context` reads `memory`, `trace` summaries, config, and session history.
- `extensions` register tools but do not control the loop.

## 8. Data Model

Core entities:

- `AgentEvent`: `id`, `session_id`, `type`, `timestamp`, `payload`, `redaction_status`.
- `ToolSpec`: `name`, `description`, `input_schema`, `risk_level`.
- `ToolCall`: `id`, `tool_name`, `args`, `provider_call_id`.
- `PolicyDecision`: `tool_call_id`, `decision`, `rule_id`, `reason`, `requires_user`.
- `ToolResult`: `tool_call_id`, `status`, `stdout`, `stderr`, `artifact_refs`, `truncated`.
- `FeedbackSignal`: `kind`, `summary`, `evidence`, `retryable`, `suggested_next_step`.
- `MemoryEntry`: `id`, `category`, `content`, `source`, `created_at`.
- `Session`: `id`, `workspace_root`, `created_at`, `mode`.
- `ProviderConfig`: `provider`, `base_url`, `model`, `credential_ref`.

Storage:

- `.phycode/traces/<session-id>.jsonl`
- `.phycode/memory.jsonl`
- `phycode.toml`
- user config directory for non-sensitive defaults
- OS keyring or encrypted local credential file for secrets

`.phycode/` is local runtime state and should not be committed by default.

## 9. External Dependencies

Planned Python stack:

- `uv` for package management.
- `typer` for CLI commands.
- `rich` for terminal rendering.
- `pytest` for tests.
- `pydantic` or equivalent schema validation for config and event models.
- `keyring` for OS credential storage, with encrypted-file fallback.
- OpenAI Python client or direct HTTP client for OpenAI-compatible chat completions.

External services:

- Optional OpenAI-compatible LLM provider for real interaction.
- No external service required for tests.

## 10. Domain and Mechanism Design for Coding Agent Harness

Coding-domain tools:

- File inspection and editing.
- Search and glob.
- Shell execution.
- Test/lint/typecheck execution.
- Workspace status.
- Memory/config operations.

Objective feedback signals:

- Test exit code and failure summary.
- Shell exit code, timeout, stderr, and output length.
- Policy allow/ask/deny.
- Edit success, no-match, multi-match, or path violation.
- Tool argument validation failure.

Dangerous actions:

- Out-of-workspace file operations.
- Destructive recursive deletion.
- System directory modification.
- Credential file reads.
- Network exfiltration commands.
- Publishing, pushing, or installing globally without approval.

Memory needs:

- Project conventions.
- User decisions.
- Preferred test commands.
- Known workspace constraints.
- Future physics extension choices.

Primary mechanism contribution:

- Policy-Aware Tool Runtime.

Implementation approach:

- All tool calls pass through a central runtime.
- Runtime validates tool args, asks policy for a decision, executes only if allowed or approved, maps result to feedback, and records trace.
- Mock tests construct tool calls directly and assert deterministic policy, feedback, and trace outcomes without a real LLM.

## 11. Acceptance Criteria

Phase 1 is accepted when:

- `phycode` starts an interactive session.
- `phycode run "<task>"` runs a non-interactive task through the same loop.
- `phycode tools list` lists built-in tools and risk levels.
- `phycode demo guardrail` shows dangerous command denial without executing the command.
- `phycode demo feedback` shows a failed test signal changing the next mock LLM action.
- `phycode demo policy` shows ask/approval behavior in the tool runtime.
- `uv run pytest` passes.
- `.gitlab-ci.yml` contains `unit-test` running the test suite.
- Mock LLM tests cover the core loop.
- Policy tests cover safe reads, risky writes, dangerous shell denial, path escape, and credential non-disclosure.
- Context tests cover truncation and recent feedback inclusion.
- Credential tests verify no plaintext key appears in status, trace, or memory.
- README explains installation, running, testing, distribution, and secure key configuration.
- SPEC, PLAN, SPEC_PROCESS, and AGENT_LOG are maintained.

## 12. Risks and Resolutions

- Scope creep into physics tools: defer to Phase 2 extensions.
- Tool-call compatibility differences across providers: support OpenAI-compatible tool calls and fallback JSON parser.
- Overusing provider state or Agents SDK: keep PhyCode loop self-implemented.
- Security becoming prompt-only: enforce guardrails in deterministic code and tests.
- Context management becoming too complex: implement session history, memory summary, truncation, and budget selection only.
- Final repository platform uncertainty: develop on GitHub now and migrate/mirror to NJU Git if required.
- Real API instability: keep CI and required demos on mock LLM.

## 13. Phase 2 Physics Extension Boundary

Phase 2 may add:

- `extensions/wolfram` for Wolfram calls.
- `extensions/latex` for compilation and linting.
- Computational physics workflow guidance as memory or knowledge packs.
- Local literature indexing and retrieval.
- Knowledge graph tools.

These must register as extensions through the tool registry and should not modify the Phase 1 agent loop.

## 14. Open Decisions

- Final submission platform remains dependent on course staff confirmation. Current development uses GitHub.
- Docker packaging is optional unless time remains after the core harness, tests, docs, and CI are complete.
- A future Responses API adapter may be added if it improves OpenAI official-model support, but Phase 1 depends on OpenAI-compatible Chat Completions for broader provider compatibility.
