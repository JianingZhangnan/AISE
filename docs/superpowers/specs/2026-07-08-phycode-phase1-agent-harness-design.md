# PhyCode Phase 1 Agent Harness Design

Date: 2026-07-08

This document records the approved Superpowers brainstorming design for Phase 1 of PhyCode. The canonical course-facing specification is [SPEC.md](../../../SPEC.md). This design note preserves the main decisions made during brainstorming and explains how they map to the formal spec.

## Approved Direction

PhyCode will be built in two stages.

Phase 1 delivers a complete, CLI-first, general-purpose Coding Agent Harness. It implements the core loop, tool dispatch, governance, feedback, memory/context management, credential handling, tests, CI, and distribution guidance. It is independently useful and acceptable even if physics-specific features are not completed before the deadline.

Phase 2 adds physics capabilities as extensions: Wolfram, LaTeX, computational physics guidance, literature support, and a knowledge graph.

## Selected Architecture

The selected approach is **Policy-Aware Tool Runtime**.

Instead of treating tools as bare functions, every model-requested tool call passes through:

1. tool schema validation
2. workspace boundary checks
3. policy decision (`allow`, `ask`, or `deny`)
4. execution wrapper
5. output truncation and redaction
6. feedback classification
7. trace recording

This combines the required mechanisms of tool dispatch, governance, and feedback into one coherent engineering contribution.

## Interface Decision

The primary interface is an interactive CLI:

- `phycode` / `phycode chat` for a persistent session
- `phycode run "<task>"` for one-shot execution
- `phycode tools list`
- `phycode demo guardrail|feedback|policy`
- `phycode config ...`
- `phycode keys ...`

No WebUI is included in Phase 1. Lightweight terminal rendering may use Rich.

## Provider Decision

The real-provider path uses OpenAI-compatible Chat Completions and `tools` / `tool_calls` because local and Chinese open-source model services commonly expose this API shape. The design also keeps a fallback JSON-action parser for imperfect local inference services.

OpenAI Agents SDK is not used as the product core because the assignment requires a self-implemented harness loop. A future Responses API adapter may be added, but Phase 1 correctness does not depend on provider-side state or agent runners.

## Event Model Decision

The agent does not split provider output into only "text" and "tool call". Provider output is normalized into internal events:

- assistant commentary
- reasoning summary
- requested tool call
- policy decision
- tool running
- tool output
- feedback signal
- assistant final
- error, incomplete, and interrupt states

The CLI can display user-visible commentary and final answers while folding reasoning summaries by default.

## Built-In Tool Set

Phase 1 includes:

- `file.read`
- `file.list`
- `file.write`
- `file.edit`
- `search.grep`
- `search.glob`
- `shell.run`
- `test.run`
- `workspace.status`
- `memory.read`
- `memory.write`
- `config.read`
- `config.write`
- `keys.status`

Credential mutation commands such as `keys.set` and `keys.clear` remain CLI-only.

## Safety Decision

The workspace defaults to the current project root. Additional roots must be explicitly allowlisted. Model-callable tools cannot read credential files, write outside the workspace, follow symlinks outside allowed roots, or execute dangerous commands. Risky actions use human approval in interactive mode and fail with a structured policy feedback signal in non-interactive mode.

## Context and Memory Decision

Phase 1 implements a basic but explicit context system:

- session history
- trace store
- curated project memory
- context builder with truncation and budget handling
- recent feedback inclusion

Provider prompt caching may improve performance, but PhyCode does not rely on it for correctness.

## Testing Decision

The core must be verified without real LLM calls. Tests use scripted mock LLMs and fake tool executors to validate:

- guardrail denial
- feedback changing the next agent action
- policy ask/approval behavior
- context truncation
- credential redaction
- event normalization

The required command is `uv run pytest`.

## Distribution and Repository Decision

Python with `uv` is the primary development and distribution path. `.gitlab-ci.yml` must include a `unit-test` job. GitHub Actions may be added for convenience while development happens on GitHub.

The course submission platform is not yet final. The project currently uses GitHub repository `JianingZhangnan/AISE`; if NJU Git becomes required, the repository will be mirrored or migrated and the switch recorded in process documents.

## Formal Spec

The full functional specification, non-functional requirements, data model, acceptance criteria, risks, and Phase 2 boundary are in [SPEC.md](../../../SPEC.md).
