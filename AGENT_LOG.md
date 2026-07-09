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

## 2026-07-09 真实供应商测试反馈：API key 非 ASCII 崩溃

- 用户填入真实 URL/key 测试失败。查 `<workspace>/.phycode/traces/*.jsonl` 见 `error` 事件：`'ascii' codec can't encode characters in position 7-30`。position 7 = `"Bearer "` 之后，即 `Authorization: Bearer <key>` 头里 key 含非 ASCII/不可见字符（常见于从网页复制 key 带入零宽空格/全角字符）。已用 httpx 复现同款报错。
- 修复：新增 `_clean_api_key`——录入 key 时 strip 空白、拒绝空值、拒绝非 ASCII（含零宽空格），给出可读提示；`keys set` 与 chat 内 `/key` 均接入。TDD 覆盖非 ASCII 拒绝与前后空白裁剪。验证：`uv run pytest` 全绿、`uvx pyright` 0 errors。
- 用户侧处理：`phycode keys clear openai-compatible` 后重新 `/key` 干净粘贴（或手打）即可。

## 2026-07-09 真实测试改进：防兜圈子 + 上下文可读化 + 终端可视化

- 真实供应商跑通后观察到模型在只读工具上兜圈子（同一 file.list/workspace.status 反复调用 6–17 次直到额度耗尽），且终端只在最后才显示错误、过程无任何提示。按用户要求做三项改进（均 TDD）：
  1. 无进展重复调用护栏：`AgentLoop` 统计 `(tool, args)` 签名；成功的可变更工具（write/edit/shell/test 等）视为进展并清零，纯只读重复达 `max_repeated_calls`（默认 5）即以 `repeated_calls` 停机。合法的“编辑→重测”迭代不受影响（重测前有可变更成功会重置）。
  2. 上下文可读化：`ContextBuilder` 不再把最近事件以 Python dict 的 `repr` 塞进 prompt，改为逐条 `[tool call]/[tool result]/[feedback]/[policy]/[assistant]/[error]` 文本，弱模型更易理解、更省 token。相应更新 `ReactiveLLM` 演示/测试触发词。
  3. 终端实时可视化：`AgentLoop` 增加 `event_sink` 回调；CLI 用它把 commentary/思考、工具调用、ask/deny 策略、工具结果、错误逐条流式渲染（真终端下带 `thinking...` spinner）。适配器新增解析 `reasoning_content`，对 reasoning 模型显示“thinking”。
- Windows 终端修复：渲染标记原用 `→/✓/✗` 和 braille spinner，在用户的 GBK(cp936) 控制台 `✓` 直接 `UnicodeEncodeError` 崩溃。改为纯 ASCII 标记（`->`/`[ok]`/`[!]`/`[error]`）+ ASCII `line` spinner，并加 `_safe_print`（遇到不可编码字符降级为 ascii replace）。已在 `PYTHONIOENCODING=gbk` 下复现并验证不再崩溃。
- 验证：`uv run pytest` 全绿、`uvx pyright` 0 errors；GBK 模拟下活动流正常输出且护栏在第 5 次重复调用时以 `repeated_calls` 停机。
