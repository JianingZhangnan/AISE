# Agent 日志

## 2026-07-08

- 使用本项目本地 Superpowers 资源：`.local/superpowers`。
- 完成 brainstorming：项目从物理专用 PhyCode 愿景收敛为“两阶段”方案，第一阶段交付通用 CLI Coding Agent Harness，第二阶段扩展 Wolfram、LaTeX、文献和知识图谱等物理能力。
- 完成核心机制选择：将工具调用、治理护栏、反馈回灌整合为策略感知工具运行时（Policy-Aware Tool Runtime）。
- 完成接口选择：采用交互式 CLI；保留 `phycode run "<task>"` 一次性入口；第一阶段不做 WebUI。
- 完成技术选择：Python、uv、Typer、Rich、pytest、Pydantic、keyring/cryptography、OpenAI-compatible Chat Completions。
- 完成供应商策略修订：第一阶段只支持 OpenAI-compatible `tools` / `tool_calls`，不实现备用 JSON action 解析器。
- 完成仓库策略记录：当前使用 GitHub `JianingZhangnan/AISE`；如课程要求 NJU Git，则后续迁移或镜像并记录过程。
- 已有提交：
  - `f74483f`：初始化 AISE 项目工作区。
  - `dc6fe14`：记录临时 GitHub 仓库策略。
  - `72fa5df`：加入第一阶段 agent harness specification。
  - `11d2883`：Claude review 并修订 `SPEC.md`，改为中文并补充架构、技术理由和机制深度。
  - `e5d1ffc`：加入第一版实施计划；该提交在本次整理前本地领先远端。
- 本次整理使用技能：`using-superpowers`、`writing-plans`、`verification-before-completion`。
- 本次整理范围：接受 Claude 对 `SPEC.md` / `PLAN.md` 的修改，中文化并同步 `docs/superpowers` 发现文档，新增 `SPEC_PROCESS.md` 和 `AGENT_LOG.md`，将实现前冷启动门禁写入 `PLAN.md`。
- 实现代码状态：尚未开始。进入 Task 1 前必须先完成陌生 agent 冷启动验证。
- 冷启动验证第一次执行：用户使用 Cursor agent 生成外部验证报告，verdict 为 `FAIL`。维护者复核后发现 B1/B2 基于未同步的旧 docs 状态，当前 `origin/main` 已由 `ccc52d0` 中文化相关文档；R2 关于冷启动执行主体和 FAIL 后处理流程的建议有效，已修订 `PLAN.md` 并记录到 `SPEC_PROCESS.md`。实现代码仍未开始，进入 Task 1 前需要让外部 agent 在最新 `main` 上复验。
- 冷启动验证第二次执行：Cursor agent 复验仍给出 `FAIL`，理由是 `docs/superpowers/specs/...design.md` 为英文。维护者用本地文件、`git show origin/main:...` 和 `git ls-remote origin refs/heads/main` 复核，确认当前远端 `eda4891` 中该文件为中文。已在 `PLAN.md` Task 0 步骤 4 增加报告证据要求：外部 agent 必须输出 HEAD、status 和目标文档前 5 行后再给 verdict。实现代码仍未开始。
- 冷启动验证第三次执行：用户确认外部 Cursor agent 冷启动复验已成功。Task 0 门禁解除；下一步可以进入 Task 1，但必须在隔离 worktree 中按 TDD 执行。
- Task 1 执行：在 `.worktrees/task-1-scaffold` 的 `codex/task-1-scaffold` 分支中执行 `executing-plans` + `test-driven-development`。先创建 `tests/test_cli_smoke.py`，运行 `uv run pytest tests/test_cli_smoke.py -v`，看到 `ModuleNotFoundError: No module named 'phycode'` 的红灯；随后创建 `pyproject.toml`、`README.md`、CI 配置、`src/phycode` CLI 骨架和 `uv.lock`，再次运行同一测试得到 2 passed。补充验证：`uv run pytest` 为 2 passed，`uv run phycode version` 输出 `phycode 0.1.0`，`uv run phycode tools list` 输出 `No tools registered yet`。实现提交：`0ed8f0f chore: scaffold phycode package`。
- Task 2 执行：先由 Codex 在 `codex/task-2-tests` 写入 RED 测试 `tests/test_models.py` 和 `tests/test_redaction.py`，提交 `dcb47e6 test: add task 2 red tests`。Cursor agent 随后实现 `src/phycode/models.py` 和 `src/phycode/redaction.py`，提交 `304ab79 feat: add core event models and redaction`。Codex 验收时运行 `uv run pytest tests/test_models.py tests/test_redaction.py -v`，结果 5 passed；运行 `uv run pytest`，结果 7 passed。审查确认变更范围只覆盖 Task 2 的模型和脱敏模块，未进入配置、policy、tool runtime 或 agent loop。
- Task 3 执行：先由 Codex 在 `codex/task-3-tests` 写入 RED 测试 `tests/test_config.py` 和 `tests/test_credentials.py`，提交 `0d1157c test: add task 3 red tests`；RED 验证为 `ModuleNotFoundError: No module named 'phycode.config'` 和 `No module named 'phycode.credentials'`。Claude agent 随后实现 `src/phycode/config.py`、`src/phycode/credentials.py` 和 CLI 子命令，提交 `d8dc44f feat: add config and credential foundations`。Codex 验收时运行 `uv run pytest tests/test_config.py tests/test_credentials.py -v`，结果 4 passed；运行 `uv run pytest`，结果 11 passed；运行 `uv run phycode config read` 和 `uv run phycode keys status openai-compatible`，均返回 0。审查确认凭据状态不输出 secret，变更范围只覆盖 Task 3 的配置、凭据和 CLI 状态命令。
- Task 3 后续安全与环境检查：确认 `docs/superpowers` 仍被冷启动流程和过程记录引用，暂不删除；新增 `pyrightconfig.json` 以便 IDE/Pyright 识别 `.venv` 与 `src` 布局；为 `CredentialStore()` 默认使用 keyring 而不是 `InMemoryCredentialBackend` 增加防回归测试；在 `SPEC.md` 和 `PLAN.md` 中补充统一安全出口 TODO，明确 `redaction.py` 只能作为兜底，不能替代日志、trace、错误报告、LLM 消息历史和 CLI 输出的统一脱敏路径。验证：`uv run pytest tests/test_credentials.py -v` 为 3 passed；`uv run pytest` 为 12 passed；`uvx pyright` 为 0 errors；`uv run python -m compileall src tests`、`uv run phycode config read`、`uv run phycode keys status openai-compatible` 均返回 0。
- Task 4 执行：在 `.worktrees/task-4-policy` 的 `codex/task-4-policy` 分支中执行 TDD。先创建 `tests/test_policy.py`，运行 `uv run pytest tests/test_policy.py -v`，看到 `ModuleNotFoundError: No module named 'phycode.policy'` 的红灯；随后实现 `src/phycode/policy.py`，包含 `PolicyContext`、`PolicyEngine`、`WorkspaceViolation`、`resolve_workspace_path`、凭据文件拒绝、路径逃逸拒绝和危险 shell 模式拒绝。验证：`uv run pytest tests/test_policy.py -v` 为 5 passed；`uv run pytest` 为 17 passed；`uvx pyright` 为 0 errors。审查确认变更范围只覆盖 Task 4 的策略模块和测试，未进入工具注册表、文件工具或 agent loop。
- Task 5 执行：在 `.worktrees/task-5-tools` 的 `codex/task-5-tools` 分支中执行 TDD。先创建 `tests/test_tool_registry.py`、`tests/test_file_tools.py` 并更新 `tests/test_cli_smoke.py`，运行 `uv run pytest tests/test_tool_registry.py tests/test_file_tools.py tests/test_cli_smoke.py -v`，看到 `ModuleNotFoundError: No module named 'phycode.tools'` 的红灯；随后实现 `src/phycode/tools/base.py`、`src/phycode/tools/file_tools.py`、`src/phycode/tools/__init__.py` 并连接 `phycode tools list`。实现中修正计划草稿的路径隐患：`ToolRuntime` 在策略通过后将 `path` 解析为 `PolicyContext.workspace_root` 或 allowlist 内的安全绝对路径，再交给文件工具执行。验证：定向测试为 7 passed；`uv run pytest` 为 22 passed；`uvx pyright` 为 0 errors。审查确认变更范围只覆盖 Task 5 的工具注册表、文件工具和 CLI 列表，未进入 shell/state/feedback 或 agent loop。
- Task 6 执行：在 `.worktrees/task-6-shell-feedback` 的 `codex/task-6-shell-feedback` 分支中执行 TDD。先创建 `tests/test_shell_and_feedback.py` 和 `tests/test_state_tools.py`，运行 `uv run pytest tests/test_shell_and_feedback.py tests/test_state_tools.py -v`，看到 `ModuleNotFoundError: No module named 'phycode.feedback'` 和 `No module named 'phycode.tools.state_tools'` 的红灯；随后实现 `src/phycode/feedback.py`、`src/phycode/tools/shell_tools.py`、`src/phycode/tools/state_tools.py` 并在默认 CLI registry 中注册 shell/test/workspace 状态工具。验证：`uv run pytest tests/test_shell_and_feedback.py tests/test_state_tools.py tests/test_cli_smoke.py -v` 为 5 passed；`uv run pytest` 为 25 passed；`uvx pyright` 为 0 errors。审查确认变更范围只覆盖 Task 6 的 shell/state/feedback 与 CLI 注册，未进入 trace/context 或 agent loop。
- Task 7 执行：在 `.worktrees/task-7-context-trace` 的 `codex/task-7-context-trace` 分支中执行 TDD。先创建 `tests/test_trace_context_memory.py`，除计划中的 trace/context 测试外，额外覆盖 MemoryStore 写入前脱敏和 ContextBuilder 构造 LLM 消息前脱敏；运行 `uv run pytest tests/test_trace_context_memory.py -v`，看到 `ModuleNotFoundError: No module named 'phycode.context'` 的红灯。随后实现 `src/phycode/trace.py` 和 `src/phycode/context.py`，将 trace、memory 和 LLM message 构建统一接到 `redact_text` 出口。验证：`uv run pytest tests/test_trace_context_memory.py -v` 为 4 passed；`uv run pytest` 为 29 passed；`uvx pyright` 为 0 errors。审查确认变更范围只覆盖 Task 7 的 trace/context/memory 基础模块，未进入 LLM 适配器或 agent loop。
- Task 8 执行：在 `.worktrees/task-8-llm-adapters` 的 `codex/task-8-llm-adapters` 分支中执行 TDD。先创建 `tests/test_llm_adapters.py`，覆盖 `ScriptedLLM`、`EchoLLM`、`FailingLLM` 和 OpenAI-compatible tool call 映射，并额外断言 `OpenAICompatibleChatAdapter` 不保留 `api_key` 属性；运行 `uv run pytest tests/test_llm_adapters.py -v`，看到 `ModuleNotFoundError: No module named 'phycode.llm'` 的红灯。随后实现 `src/phycode/llm.py`，提供 deterministic mock LLM 和 OpenAI-compatible Chat Completions 适配器。验证：`uv run pytest tests/test_llm_adapters.py -v` 为 5 passed；`uv run pytest` 为 34 passed；`uvx pyright` 为 0 errors。审查确认变更范围只覆盖 Task 8 的 LLM 适配层，未进入 agent loop。
- Task 9 执行：在 `.worktrees/task-9-agent-loop` 的 `codex/task-9-agent-loop` 分支中执行 TDD。先创建 `tests/test_agent_loop.py`，运行 `uv run pytest tests/test_agent_loop.py -v`，看到 `ModuleNotFoundError: No module named 'phycode.agent'` 的红灯；随后实现 `src/phycode/agent.py`，将 `LLMClient`、`ContextBuilder`、`ToolRuntime`、`PolicyContext`、`TraceStore` 和 `classify_feedback` 串成可 mock 测试的 agent 循环。验证：`uv run pytest tests/test_agent_loop.py -v` 为 2 passed；`uv run pytest` 为 36 passed；`uvx pyright` 为 0 errors。审查确认变更范围只覆盖 Task 9 的 agent loop 基础模块，未进入 CLI run/chat。

## 2026-07-09

- 代码评审驱动的修复批次：应用户要求，先对 Task 0–9 的既有实现做全面检查，再严格按检查结论修复、提交并合并。评审结论详见 `SPEC_PROCESS.md`「代码评审与修订（2026-07-09）」小节。
- 偏离说明（须记录）：
  - 本批次未按每个 task 单独 worktree + 新鲜 subagent 的方式执行，而是把一组**相互耦合的内核修复**集中在单一分支 `fix/review-findings` 上，由主 agent 按 TDD 顺序完成。原因：用户明确要求「直接修改、提交、合并」，且这些修复（reactive mock LLM、审批接线、停机控制器、runtime 校验、demo）在同一调用链上强耦合，拆成并行 worktree 会互相冲突且降低一致性。
  - 本机 shell 中 `uv` 不可用，无法执行 `uv run pytest`；改用与 CI `uv run pytest` 等价的隔离虚拟环境（安装同一组 `pyproject.toml` 依赖）运行同一测试套件进行验证。项目自身的包管理仍以 `uv` + `pyproject.toml` + `uv.lock` 为准，未引入 pip 到项目工作流。CI（`.gitlab-ci.yml` 的 `unit-test` 与 GitHub Actions）仍按 `uv sync --dev && uv run pytest` 执行。
- 修复项与提交（TDD 红→绿，每项先写失败测试）：
  1. `dc8ca66` feat(policy)：shell 凭据读取拒绝（新规则 `credential.shell_read_blocked`）+ 扩充危险命令模式（`rmdir/rd /s`、`git push --force`、`mkfs`、`dd of=/dev/`、`shutdown`、`chmod -R 777 /`、`wget|sh`、fork bomb），并把危险检查从 `shell.run` 扩展到所有带 `command` 的工具（P2-5、P2-6）。
  2. `86af4d9` feat(runtime)：`ToolRuntime` 增加 `validate_args`（按 `input_schema.required` 校验）产出 `invalid_tool_args`，并用 try/except 捕获执行器异常为 `tool_error`，避免缺参崩溃；为 `file.*`/`shell.run` 补齐真实 JSON Schema；新增 `ToolRegistry.spec_for`（P1-4，激活此前的死路径 `INVALID_TOOL_ARGS`）。
  3. `6aa2b6e` feat(tools)：实现 `search.grep`、`search.glob`、`memory.read/write`、`config.read/write`、`keys.status`，使 policy 的 `SAFE_TOOLS`/`RISKY_TOOLS` 与 registry 完全一致（一致性脚本核验 both-way diff 为空）；`config.write` 仅接受非敏感白名单键，`keys.status` 不回显明文（P2-7）。
  4. `eb0391f` feat(agent)：新增 `ReactiveLLM`（输出取决于上下文中的反馈），使反馈闭环可在无真实 LLM 下确定性验证（P0）；loop 现在把已注册工具 spec 传给 LLM 并纳入上下文（P1-1）；接入可注入 `approval_handler`（P1-2）；停机控制器捕获 provider 异常为 `error` 事件、处理 `error/incomplete/user_interrupt` 终止事件、并在同一工具重复失败达阈值时以 `repeated_failure` 停机（P1-3）。
  5. `64d0b9c` feat(demos)：以真实 ToolRuntime/agent loop 实现 `run_guardrail_demo`/`run_policy_demo`/`run_feedback_demo`，替换 PLAN Task 11 中把「下一步动作」硬编码为 `"file.edit"` 字符串的占位实现（P0）；新增 `phycode demo guardrail|feedback|policy`（隔离临时工作区）。反馈 demo 的确定性坑：`-`→`+` 的修复不改变文件大小与 mtime 秒，CPython 会命中过期 `.pyc`，改用 `python -B` 运行测试子进程规避。
- 验证：隔离 venv 中 `pytest` 全套 65 passed（起点 36）；policy↔registry 一致性脚本 both-way diff 为空；三个 demo 命令输出人工核验（guardrail 拒绝且未执行；policy 需审批；feedback 呈现 test_failed→file.edit→success→final）。
- 代码评审（`requesting-code-review`）：对整分支 diff 运行 `/code-review`（xhigh，3 个独立 finder subagent）。**评审发现本批次新引入的真实缺陷，已逐项 TDD 修复**（commit `c62fc97`）：
  1. 严重安全漏洞：`search.grep`（SAFE/自动放行）会返回 `.env`/私钥/`*.pem`/`*.key` 内容，绕过 `file.read` 的凭据拒绝；`search.glob` 可用 `../` 逃出工作区。修复：搜索工具跳过凭据文件、glob 限制在工作区内、grep 路径展示不再对 allowlist 目录崩溃。经复验漏洞已关闭且合法文件仍可搜。
  2. 循环崩溃：`MemoryStore/TraceStore` 对「序列化后的 JSON 字符串」做正则脱敏，贪婪 secret 模式会吃掉结构字符产生非法 JSON，`entries()` 下次构建上下文时 `JSONDecodeError` 崩溃整个循环（经 `memory.write` 可触发）。修复：新增 `redact_obj`，在序列化「之前」对字符串叶子脱敏；读取端容忍坏行。
  3. 数据损坏：`config.write` 的手写 TOML dumper 遇到换行值/浮点/子表/顶层非表会崩溃或静默损坏文件。修复：先渲染成字符串、成功才写入（失败不触碰文件），拒绝控制字符，处理浮点，非表顶层键 fail-safe。
  4. 误报：`shutdown` 裸词、`.key`/`.pem` 子串会误拒 `grep shutdown`/`jq '.key'` 等常见命令；`rm` 规则漏掉 `-fr`/`~`/`.`。已收敛正则。
- 评审结论与采纳/推翻记录另见 `SPEC_PROCESS.md`。此轮评审也印证了 harness 判定标准的价值：确定性测试 + 独立评审在合并前拦下了 secret 外泄与循环崩溃两个高危缺陷。
- 标准流程验收（补充，撤销前述「uv 不可用」的临时偏离）：应用户要求先安装真正的 `uv`（官方脚本安装到 `C:\Users\Zhang\.local\bin`，uv 0.11.28），再在 `fix/review-findings` 分支上按标准流程验收：`uv sync --dev` 成功创建 `.venv`；`uv run pytest` 为 **81 passed**；`uv run phycode version/tools list` 正常（14 个工具带风险等级）；`uv run phycode demo guardrail|policy|feedback` 三个演示均按预期确定性复现（guardrail 拒绝未执行；policy 需审批；feedback 呈现 test_failed→file.edit→success→final）。`.venv` 已被 gitignore，`uv.lock` 无变化（本批次未新增依赖，仅用标准库 `re`/`tomllib`/`sys`/`tempfile`/`json`）。
- Codex 复核记录：`origin/fix/review-findings` 已在本地 `main` 以 fast-forward 方式合并；复核时补跑 `uvx pyright` 发现 `tests/test_tool_registry.py` 中一处 optional member access 类型检查问题，随后追加本地修复并复验。当前尚未推送 `origin/main`。

## 2026-07-09 Task 10–12 收尾

- 执行方式：主 agent 在 `codex/task-10-12` 上收口 Task 10–12；使用 gpt-5.5 subagent 聚焦严格 CLI 行为与文档验收清单，随后由当前文档 worker 只修改 `README.md`、`PLAN.md`、`SPEC_PROCESS.md`、`AGENT_LOG.md`，不触碰 `src` 或 `tests`。
- Task 10 记录：严格 CLI 测试文件为 `tests/test_cli_commands.py`，覆盖 `run`/`chat`、完整工具列表、`config read`、`keys set/status/clear`、trace 脱敏、key 明文不回显和非 final 退出码。实现提交为 `e858161`。测试策略是先用用户可见命令锁住行为，再由 README 逐条列出可运行命令，避免只记录内部 API。
- Task 11 记录：确定性 demo 已按代码评审结论使用真实 `ToolRuntime` 和 `ReactiveLLM`，README 收尾列出 `uv run phycode demo guardrail`、`uv run phycode demo feedback`、`uv run phycode demo policy` 三个命令。
- Task 12 记录：新增严格文档测试 `tests/test_docs_process.py`，要求 README 包含安装、运行、demo、key 管理、`uv run pytest`、`uvx pyright` 与安全边界；PLAN 标记 Task 10、Task 11、Task 12 完成；SPEC_PROCESS 记录 Task 10–12 收尾、严格 CLI 测试、最终验证命令和 review-ready 分支。
- 主 agent 复核补充：在合并 subagent 产物后，发现 `chat` 为避免 EchoLLM 递归回显而改写 session event 的实现过绕，改为让 `EchoLLM` 在渲染后的上下文中优先 echo 当前 `User:` 行，并用 `tests/test_llm_adapters.py` 增加回归测试锁定该行为。
- 验证范围：`uv run pytest tests/test_cli_commands.py tests/test_docs_process.py -v` 为 11 passed；`uv run pytest tests/test_cli_commands.py tests/test_llm_adapters.py tests/test_docs_process.py -v` 为 17 passed；`uv run pytest` 为 **93 passed**；`uvx pyright` 为 0 errors。真实 CLI smoke 覆盖 `phycode run "hello"`、`tools list`、`config read`、`demo guardrail|feedback|policy`、`keys status`。后续由 Claude 审核 `codex/task-10-12`，重点确认文档、CLI 测试和安全边界陈述一致。

## 2026-07-09 Claude review 后的 run/chat 收口

- Claude 审核 `codex/task-10-12` 结论：基于关系正确（= `fix/review-findings` + Task 10–12），未回退任何安全修复；`uv run pytest` 93 passed、`uvx pyright` 0 errors、真实 CLI（run/chat/keys）行为与脱敏均经实机核验通过。审出两处非阻塞局限，应用户要求「顺手修复」：
  1. `run`/`chat` 原固定用 `EchoLLM`，真实供应商适配器未接入。修复：新增 `_build_llm(config, credential_store)`——配置了 key 时返回 `OpenAICompatibleChatAdapter`（真实交互），否则回退 `EchoLLM`；keyring 查询 best-effort，后端异常按「无凭据」处理，保证离线/无 keyring 环境仍走 EchoLLM。
  2. `chat` 原在任一非 final 轮 `raise Exit(1)` 会杀死整个会话。修复：改为打印 `[stopped: <reason>]` 并继续会话。同时为交互式 `chat` 接入 `_interactive_approver`（`typer.confirm`），使真实适配器下的风险动作可被用户审批。
- TDD：先加 4 个失败测试（`_build_llm` 无 key→Echo / 有 key→adapter、chat 非 final 存活、`_interactive_approver` 走 confirm），并给两个 echo 测试加 `_force_no_credentials`（monkeypatch `CredentialStore`）使其不受本机钥匙串状态影响；再实现变绿。
- 验证：`uv run pytest` 为 **97 passed**；`uvx pyright` 为 0 errors；实机 `phycode run "hello again"` 无 key 时回退输出 `Echo: hello again`。
- 合并：应用户要求，`codex/task-10-12` 以 fast-forward 合并到 `main` 并推送 `origin/main`（这批含全部评审修复 + Task 10–12 + 本次 run/chat 收口才首次落到主线）。

## 2026-07-18 PRBench 运行时真正重构（Task 14–20）

- 工作流：在 `codex/prbench-runtime-refactor` 隔离 worktree 中使用本地
  Superpowers `subagent-driven-development` 与 `test-driven-development`。每个
  Task 由新鲜 subagent 执行 RED→GREEN，自审后提交，再由独立 reviewer 同时给出
  spec 合规与代码质量结论；进度保存在 `.superpowers/sdd/progress.md`。
- 重构原因：真实 API 试运行证明旧 parser 路线同时存在安全与正确性缺陷——自制
  shell lexer/state machine 越补越复杂，仍不能成为 ground truth 的权威隔离；全局
  重复计数会误停机；assistant final 未经产物验证也可能被误报成功。因此从
  `f2817ab` 干净基线重建结构化执行纵向切片，没有移植 parser-specific 补丁。
- Task 14：建立 profile 单一来源与 symlink-aware path visibility。实现/修复提交
  `6f8dd6a`、`468ac42`，spec/quality review clean。
- Task 15：新增 `process.run(argv)`（`shell=False`、绝对 executable identity、最小
  子进程环境）与一次性精确审批。提交 `7ed3423`、`c37467f`、`65fca5c`、
  `d198f5c`，spec/quality review clean。
- Task 16：新增 execution journal、公开 task contract、真实脚本 provenance 与
  artifact verifier。提交 `4bd700f`、`a429dbe`、`67b7c3e`，spec/quality review
  clean。
- Task 17：把 final 接到 verifier，重复检测改为连续无进展，并保持 fatal blocker
  优先级。提交 `5d561b3`、`9b7fa50`，spec/quality review clean。
- Task 18：新增 `phycode prbench run`、机器可读状态、可信 composition 边界和递归
  trace/result 脱敏。实现及审查修复从 `537b8dc` 到 `148df4f`；定向 144 passed、
  全量 341 passed/10 skipped、Pyright 0 errors，spec/quality review clean。
- Task 19：为官方固定 commit
  `3e5bee4545cad2138832f06302e9c98bd81f5216` 增加最小 patch/apply adapter、两份
  public contract、provider 生命周期清理与 pinned uv 安装。提交 `31f58a4`、
  `1b0a448`；定向 18 passed、全量 359 passed/10 skipped、Pyright 0 errors，
  fresh clone apply/help 与 wheel 检查通过，spec/quality review clean。
- Task 20 subagent 范围：只修改中文文档、`tests/test_docs_process.py` 和
  `integrations/prbench/run_public_smoke.ps1`；没有读取、索取或接收真实凭据，没有
  调用真实 API，也没有运行 official evaluator。RED 命令
  `uv run pytest tests/test_docs_process.py -v` 得到 3 failed/4 passed，失败原因精确
  为 README/过程证据缺少新契约以及 smoke 脚本尚不存在。
- Task 20 GREEN：同一 focused 命令为 7 passed；PowerShell AST 解析通过，清空三项
  provider 环境后的负例在任何 adapter/文件操作前以通用缺配置消息 fail closed；
  `uv run pytest` 为 367 passed/12 skipped，`uvx pyright` 为 0 errors；
  `git diff --check` 通过，`git ls-files ".env" ".env.*" "*.pem" "*.key"` 无输出。
- Task 20 审批清单：`aaatest_helloworld` 只允许一次写
  `reproduction/hello.py` 和一次运行
  `[/usr/local/bin/python, reproduction/hello.py]`；`bbbtest_alphabet` 对应
  `reproduction/alphabet.py`。脚本没有从 expected outputs 自动派生授权，所有额外
  调用 fail closed，临时 manifest 用后删除。
- 上游环境风险：固定 commit 没有 lockfile 且只声明 `a2a-sdk>=0.3.8`；fresh
  普通解析会选择 1.1.1 并破坏 upstream imports。主 agent 已动态验证 uv 临时
  overlay `a2a-sdk[http-server]==0.3.8` 可解析官方 CLI；smoke 脚本采用该 exact
  overlay，不修改 upstream `pyproject.toml`，也不扩大 adapter 到上游依赖维护。
- 官方绿色 grader：脚本仍只读取三项 `PHYCODE_*`，但在 launch 子进程期间临时
  映射为 `OPENCODE_API_KEY`、`OPENCODE_BASE_URL` 与
  `OPENCODE_MODEL=openai/<PHYCODE_MODEL>`，并显式选择
  `--green-agent-type opencode`。`finally` 恢复原有 `OPENCODE_*` 或清除临时值，
  任何值都不进入脚本输出。
- 真实 API 验收边界：Task 20 文档与确定性测试通过后，只由主 agent 在当前子进程
  内载入 provider 值，启动 Docker daemon，并依次运行 `aaatest_helloworld`、
  `bbbtest_alphabet`。必须同时看到 runner `completed`、expected outputs、官方
  evaluator 报告且 trace/result 无 key/URL，才可记录为真实验收成功；mock、直接
  runner 或 adapter apply 成功均不能冒充官方结果。
