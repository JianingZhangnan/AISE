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

## 2026-07-18 Task 8 smoke 审批与环境恢复修复

- Task 7 独立审查推翻了两项 smoke 编排假设。第一，reproduction 脚本尚未生成时
  静态预授权 `process.run`，主 agent 无法履行“先读脚本再批准”的安全责任；初始
  manifest 现只保留精确 `file.write`。第二，在 Windows/.NET 实测
  `SetEnvironmentVariable(name, $null, 'Process')` 后 `Test-Path Env:name` 仍为
  true，值变成空字符串而非变量消失；这会让后续 provider 解析误判配置存在。
- 本轮使用 `systematic-debugging` 先复现根因：独立 probe 得到
  `exists_after_null=True`、`value_is_null=False`。随后按 TDD 增加 fake-uv 动态测试，
  覆盖 evaluator 成功/失败 × 调用前 OPENCODE 环境存在/不存在四种组合；RED 为
  3 failed/8 passed，其中两个动态负例均以退出码 85 证明空变量残留。
- 修复范围只包含 smoke 脚本、文档与 `tests/test_docs_process.py`，没有读取真实凭据、
  没有调用真实 API/evaluator，也没有修改 adapter/core。原本不存在的
  `OPENCODE_*` 现在用 `Remove-Item -LiteralPath Env:<name>` 真正删除；已有值精确
  恢复，fake provider 值不进入 stdout/stderr。
- 初始审批只允许一次 `reproduction/hello.py` 或
  `reproduction/alphabet.py` 的 `file.write`。official launch 使用
  `--approval-wait-seconds 900`；runner 写出
  `.phycode/prbench/approval-request.json` 后，主 agent 必须读取脚本并核对
  argv/cwd/`script_sha256`，再向 active workspace 的 `phycode-approvals.json` 写入
  hash-bound 一次性 `process.run` grant。smoke 脚本不生成 process grant、不计算
  hash，也不自动批准。
- green 凭据生命周期由 adapter `6f5d75d` 保证：宿主别名不会进入共享容器
  `Config.Env`，白色阶段不可见；白色结束后才为绿色 grading child 以 name-only
  Docker 环境参数延迟注入并清理。
- adapter wait 透传提交 `21ae28e` 已把 official
  `main.py launch --approval-wait-seconds 900` 依次传入 launcher、white executor 和
  容器内 `phycode prbench run --approval-wait-seconds 900`，边界校验为 0..900。
- GREEN 验证：`uv run pytest tests/test_docs_process.py -v` 为 11 passed，其中
  fake-uv 四组合全部通过；PowerShell AST 通过；共享 worktree 当时的全量
  `uv run pytest` 为 399 passed/24 skipped，`uvx pyright` 为 0 errors；Task 8 五个
  smoke/doc 文件的 diff check 通过，tracked credential scan 无输出。全量结果包含
  core agent 尚未提交但测试已 GREEN 的 hash-guard 工作，最终整分支仍由主 agent
  在 core 提交后统一复跑。

## 2026-07-18 真实 API 暴露的 contract 停机缺口

- 真实 `aaatest_helloworld` 运行生成了 reproduction 脚本和目标 CSV，官方绿色评分
  为 `overall_score=1.0`，但模型没有主动 stop，最终用满 40 次工具预算。终点
  `ArtifactVerifier` 仍报告 `script_not_executed` 与 `csv_without_provenance`：文件
  内容正确不等于 harness contract 完成，直接 `file.write` 不能替代成功脚本执行
  provenance。本任务没有放宽 verifier，也没有修改 smoke grant 或 prompt。
- 使用本地 Superpowers `systematic-debugging` 与 `test-driven-development` 定位到
  根因：loop 只在 assistant final、预算耗尽和循环退出时验收，成功进程更新 journal
  后没有 contract-aware 停机 hook。RED 为 6 个聚焦测试中的 4 failed/2 passed，
  其中真实形态回归明确观察到 write→run 后仍执行 read，共 3 次工具调用。
- 最小修复在通用 `AgentLoop` 增加默认关闭的
  `verify_after_successful_tool`，只由 PRBench runner 显式开启。工具结果完成回灌且
  `status=ok` 后调用现有 verifier；`ok=True` 立即返回唯一成功终态 `completed`，
  nonfatal false 静默继续，fatal 异常记录脱敏反馈并 fail closed。拒绝、失败和超时
  工具不调用即时验收，因此不能借旧产物误报成功；coding/GAIA 默认行为不变。
- GREEN 覆盖 write→成功执行后立即停机且不再调用 LLM/工具、错误 CSV、direct write
  无 provenance、symlink/escape、失败工具与旧产物、显式 final 兼容及默认关闭语义。
  完整门禁：`uv run pytest` 为 405 passed/71 skipped，`uvx pyright` 为 0 errors，
  `uv build` 成功生成 sdist 与 wheel。

## 2026-07-18 真实 smoke 暴露的 CSV provenance workflow 回归

- 主 agent 的真实模型 smoke 复现了另一条与评分内容正确性无关的机制失败：模型先
  写入只打印文本的 reproduction 脚本，随后直接写入内容正确的 CSV。绿色 grader
  得分为 1.0，但白色 runner 正确以 `script_not_executed` /
  `csv_without_provenance` 拒绝完成。根因不是 verifier 过严，而是提交 `7bb2a6b`
  错误地把两个 `data/*.csv` 加入初始 `file.write` grant；通用 policy 当时只返回
  `ASK`，所以错误 grant 可以让 direct write 穿过审批层。
- 本 subagent 未读取凭据、未调用 API、未修改临时 evaluator。按本地
  `systematic-debugging` 先追踪 design → smoke manifest → PolicyEngine → feedback
  数据流，再按 TDD 写 RED。稳定 RED 覆盖 prompt、大小写/嵌套/Windows 分隔符的
  CSV write/edit、错误 grant 绕过、非 PRBench 兼容、profile-aware feedback 和
  smoke 清单；确定性 runner 回放在旧实现中首个 policy 明确为 `ASK`，证明绕过真实
  存在。
- 最小修复撤回所有 CSV grant，只给两个 reproduction 脚本各一次精确
  `file.write` 与同路径 `file.edit`。`file.edit` 由回放证明必要：第一次 write 已被
  不完整脚本消费后，需要一个不扩展路径范围的恢复动作；它不能触及 CSV。没有静态
  process grant、通配符或自动 hash。
- `PolicyEngine` 仅在 PRBench profile 对 workspace `data/**/*.csv` 的
  `file.write` / `file.edit` 返回确定性 DENY
  `prbench.direct_csv_mutation_blocked`；错误 manifest grant 不能覆盖 DENY。固定
  reason 不含路径、expected value 或凭据。AgentLoop 将 profile 与 rule id 交给
  feedback classifier，结构化下一步固定为修改/重写 reproduction 脚本并请求
  `process.run`，而不是泛化为 Ask user。
- GREEN 的确定性端到端回放为：direct CSV 被拒绝 → 精确 write 初版脚本 → 精确
  edit 补全脚本 → 生成动态 approval request → 写入匹配脚本 SHA-256 的一次性
  process grant → 真实 Python 子进程执行 → 即时 verifier 返回 `completed`，后续
  read 未执行；同时断言错误 direct-write grant 不能绕过，secret-shaped 测试值不
  进入 trace/result。
- 最终门禁：`uv run pytest` 为 **420 passed / 71 skipped**；补齐测试中的 Optional
  类型收窄后 `uvx pyright` 为 0 errors；`uv build` 成功生成 sdist/wheel；PowerShell
  AST、`git diff --check` 与 tracked `.env` / PEM / key 清单检查通过。

### 独立复审 Important：Win32 path alias canonicalization

- `bd854c7` 后的独立复审指出 `c8b4d73` 的 direct CSV 分类只有 casefold，尾随 ASCII
  space/dot 和 NTFS ADS 可绕过 lexical `.csv` 判断。按 `systematic-debugging` 复现后
  发现该缺口还依赖目标是否已经存在：简单 alias 在既有文件上可能被 Win32
  `Path.resolve()` 偶然归一，而多 component / 反斜杠 alias 在错误 exact grant 下仍
  返回 ASK，不能把现有文件状态当成安全机制。
- TDD RED 覆盖 6 种 alias × write/edit 的纯 policy 分类、同一矩阵的真实
  `ToolRuntime + ApprovalManifest` wrong-grant 回放、escape/hidden 原路径优先级、
  coding/GAIA 兼容，以及非 drive colon 与正常绝对 drive prefix 的区分。真实回放
  预置 `data/output.csv` sentinel 并快照 workspace tree，要求拒绝后 bytes/tree 均
  不变。
- 最小 GREEN 仅修改 PRBench 分类：原始 path 仍先走 visibility；classification-only
  view 对每个 component 执行 `rstrip(" .")` + casefold。任何非 drive prefix 冒号
  以 `prbench.win32_stream_blocked` fail closed，覆盖
  `data/output.csv::$DATA`；view 不传入 executor，不改 approval key。POSIX 也采用同一
  fail-safe，coding/GAIA 不变。本 subagent 未读取凭据、未调用真实 API，也未修改
  temporary evaluator。
- 最终门禁使用 fresh、clean、固定 HEAD
  `3e5bee4545cad2138832f06302e9c98bd81f5216` 的 official evaluator source：
  `uv run pytest` 为 **514 passed / 14 skipped**，运行后 source 仍为 0 dirty；
  `uvx pyright` 为 0 errors；`uv build` 成功；`git diff --check` 与 tracked
  credential-like file scan 通过。

## 2026-07-18 Task 22：运行时审批清单瞬时损坏恢复

- subagent `runtime_task22_approval_refresh` 使用本地 Superpowers
  `systematic-debugging`、`test-driven-development` 与
  `verification-before-completion`。本任务未读取凭据、未调用真实 API，也未接触正在
  运行的临时 evaluator。
- 根因是 `ApprovalManifest.__call__` 在等待动态 `process.run` 审批期间，每次轮询
  `_refresh()`；清单被非原子写入而短暂出现 JSON/校验错误时，异常分支会立即返回
  `False`。这不是授权不匹配，而是读取时序竞态，导致 deadline 内稍后发布的精确
  hash-bound grant 没有机会被读取。
- TDD RED：新增真实文件轮询回归，第一次 sleep 写入损坏 JSON，第二次才写入匹配
  argv/cwd/脚本 SHA-256 的授权。旧实现以 `assert manifest(call, decision)` 失败，并且
  只发生一次 sleep，证明失败来自过早退出而非测试配置。
- 最小 GREEN 只将等待循环内的刷新异常改为继续下一轮轮询。异常轮次不会调用
  `_consume_matching()`，因此瞬时无效清单和内存中的旧状态都不能触发批准；后续只有
  成功解析且精确匹配的 grant 才能被消费。循环仍在每轮 sleep 前检查 monotonic
  deadline，所以永久无效清单最终拒绝，初始读取、请求写入、路径可见性、symlink、
  脚本哈希与执行前复验等 fail-closed 边界均未放宽。
- GREEN 证据：新增单测通过；审批刷新/损坏/超时聚焦集合为 8 passed、1 skipped；
  完整 `tests/test_process_approval.py` 通过（3 个平台相关用例 skipped）；
  `uvx pyright` 为 0 errors / 0 warnings。

## 2026-07-18 Task 23：统一动态审批请求与 grant 契约

- subagent `runtime_task23` 使用本地 Superpowers `systematic-debugging` 与
  `test-driven-development`；未读取凭据、未调用真实 API、未接触临时 evaluator。
- 根因是动态 `process.run` 请求额外输出 `script_path`，但
  `ApprovalGrant(extra="forbid")` 不接受该字段；人工把审核后的请求对象原样追加到
  `grants` 后，清单刷新会持续校验失败。该字段又是冗余信息：规范化 `argv[1]` 已是
  相对 `cwd` 的脚本路径。
- TDD RED 将实际 `approval-request.json` 对象原样作为唯一 grant 写回，旧实现稳定
  返回拒绝。最小 GREEN 仅从请求中删除冗余 `script_path`，不放宽
  `ApprovalGrant` 的 `extra="forbid"`，也不改变 absolute executable、cwd、argv 与
  `script_sha256` 的精确绑定。现在审核通过的请求对象可原样追加到 manifest 的
  `grants` 数组，并由 `ApprovalDocument` 解析、`ApprovalManifest` 匹配和一次性消费。

## 2026-07-18 官方真实 API / Docker 最终验收

- 主 agent 独占读取仓库外凭据文件；只在 evaluator 子进程内设置 `PHYCODE_*`，并由
  `finally` 清除。任何 subagent、测试、命令参数、文档和日志均未接收真实值。
- 真实失败没有被改写成成功：早期运行依次暴露扁平工具上下文导致的 `file.list`
  循环（官方 0.5）、裸 `python` 无法形成安全审批（官方 0.3），以及一次发生在模型
  调用前的 Docker exec 404。前两项分别触发原生 tool conversation/因果状态与严格
  Python alias normalizer；Docker 冷启动故障未诱发产品代码补丁。
- real8 的 `aaatest_helloworld` 经人工检查两个版本脚本并分别核对 SHA-256 后，批准
  两个 exact absolute-argv/cwd/hash grant；runner `completed`，8 次工具调用、46 个
  trace 事件、2 条成功 execution journal，官方 `overall_score=1.0`。
- real8/real9 的 alphabet 脚本逻辑正确，但人工把审批 request 原样加入 manifest 时
  多余 `script_path` 被 strict schema 拒绝，CSV 未执行生成，官方分别为 0.7/0.5。
  这确认 Task 23 是 request/grant 接口缺陷，而不是 verifier 或模型任务能力问题；未
  手工生成产物、未放宽 grant、未修改 grader。
- Task 22/23 wheel 重建后，real10 的 `bbbtest_alphabet` 请求只含
  `tool_name/argv/cwd/script_sha256`。主 agent 验证 request/script 均非链接、路径位于
  workspace、argv 为唯一 allowlisted `/usr/local/bin/python3.11` 加目标脚本，并独立
  复算 SHA-256；请求对象原样作为一次性 grant 后，runner `completed`，6 次工具调用、
  32 个 trace 事件、1 条成功 execution journal，官方 `overall_score=1.0`。
- 最终证据核验：两项 trace 声明数等于实际 JSONL 行数；run_result 中 reproduction
  脚本与 CSV 均存在且 SHA-256 可复算一致；execution journal 的脚本 hash 与人工审批
  版本一致；官方评测容器全部移除。
- 安全扫描从外部凭据文件只在内存取得两组 URL/key，分别对项目源码/构建物、real8
  与 real10 evaluator 结果和 Git 全历史做 exact scan，四项均为 0 命中。新的检查
  进程中 `PHYCODE_*` / `OPENCODE_*` 六项均不存在，未回显任何真实值。
- 最终固定-source 回归使用 clean upstream
  `3e5bee4545cad2138832f06302e9c98bd81f5216`：`uv run pytest` 为 **577 passed / 14
  skipped**，`uvx pyright` 为 0 errors / 0 warnings，`uv build` 成功生成
  sdist/wheel，`git diff --check` 通过；测试后 upstream source 仍为 clean。

## 2026-07-18 Task 24：重复失败绑定完整动作身份

- subagent `runtime_task22_approval_refresh` 接续执行 Task 24，使用本地 Superpowers
  `systematic-debugging`、`test-driven-development` 与
  `verification-before-completion`；未读取凭据、未调用真实 API、未接触 evaluator。
- final review 确认 `_failure_key()` 只返回 `(tool_name, feedback_kind)`。因此模型对
  同一个工具连续尝试不同参数时，即使每次都是新的纠错动作，也会累计同一 failure
  streak；默认阈值 3 会在第三次失败后误停，模型无法执行下一次正确修复。
- TDD RED 使用真实 `file.edit`：对同一文件依次提交三个不同且不存在的 `old` 文本，
  然后提交可成功的第四个 edit 并 final。旧实现实际返回 `repeated_failure`，而不是
  预期的 `final`，精确复现误停。既有测试继续要求完全相同的失败 edit 连续达到阈值
  时返回 `repeated_failure`。
- 最小 GREEN 未调整任何阈值，也未新增平行身份算法。AgentLoop 已在每个工具调用
  执行前生成 `_ActionIdentity`，其中包含 tool name、规范序列化 args 的 SHA-256，且
  `process.run` 额外绑定执行前脚本 SHA-256；现在 failure kind 与该完整身份共同作为
  streak key。不同动作会把 streak 重新计为 1，完全相同的动作/脚本/失败类型仍连续
  累计并按原阈值停机。
- GREEN 证据：新增恢复用例与既有重复失败用例为 2 passed；完整
  `tests/test_agent_loop.py tests/test_prbench_loop.py` 为 58 passed；`uvx pyright`
  为 0 errors / 0 warnings。

## 2026-07-18 Task 25：淘汰同一 process target 的过期 blocker

- subagent `runtime_task25` 使用本地 Superpowers `systematic-debugging` 与
  `test-driven-development`；未读取凭据、未调用真实 API、未接触临时 evaluator。
- 根因是 `_updated_blocker` 只在成功 action 与旧 blocker 的完整
  `_ActionIdentity` 相等时清除；该 identity 刻意包含脚本内容 SHA，因此正常的“旧版本
  审批/执行失败 → `file.edit` 修复 → 新版本成功执行”必然无法相等，过期 blocker 会在
  artifact 尚未完成而预算耗尽时错误覆盖 `tool_budget_exhausted`。
- TDD RED 使用真实 Python runtime 固化两条恢复链：裸 `python` 的旧版本审批失败后
  以绝对解释器重跑新版本成功，以及旧版本真实非零退出后新版本成功；旧实现分别错误
  保留 `approval_required`、`process_failed`。既有不同脚本 target、read 与无关 write
  的成功不清除 blocker 用例作为反例保留。
- 最小 GREEN 为 process action 增加独立 target identity：从原始参数解析并规范化
  workspace 内 cwd/脚本路径，同时精确保留尾随 argv；executable 与脚本内容 SHA 仍可
  属于完整 action identity，但不属于跨版本 target identity。这样裸 `python` 与绝对
  解释器可指向同一目标，且不会在 AgentLoop 重复调用工具 normalizer；计数回归证明每个
  实际 process 调用只规范化一次。仅成功 process action 的完整 identity 相等，或双方
  可证明 target identity 相等时，才淘汰旧 `approval_required` / `process_failed`；
  read/write 与不同 target 均不能清除。

## 2026-07-18 最终复审与回归收口

- 独立 reviewer 对 `49db986` / `f31112e` 窄复审：Critical 0、Important 0。聚焦回归
  证明完全相同失败仍按阈值停机，不同参数纠错不误停；同 process target 新版本成功
  能淘汰旧 blocker，不同 target/read/write 不清除；每个实际 process 调用仍只执行
  一次 normalizer。
- 使用 clean、固定 upstream source
  `3e5bee4545cad2138832f06302e9c98bd81f5216` 的最终全量回归为 **579 passed / 14
  skipped**；`uvx pyright` 为 0 errors / 0 warnings；`uv build` 成功；
  `git diff --check` 通过，测试后 upstream source 与当前 worktree 均为 clean。

## 2026-07-18 v0.1.0 发布前主线整合

- 发布前先将 `main@7c4ab1d` 的 6 个真实供应商/CLI 提交合入
  `codex/prbench-runtime-refactor`，而不是直接从功能分支打 tag。双方在 AgentLoop、
  context、CLI、demo 与测试共 8 个文件发生内容冲突；按 core 与 CLI 两组由独立
  subagent 做语义合并，未采用整文件 ours/theirs。
- core 合并保留 native tool conversation、causal feedback/blocker、stale batch、
  verifier/provenance，同时接入 main 的只读重复调用保护、可读 activity summary 和
  脱敏 event sink。CLI 合并保留 profile-aware composition/PRBench 与 main 的
  `config set`、slash commands、models、quote stripping 和 ASCII-safe live renderer。
- CLI focused 首跑暴露薄 wrapper 未显式走可注入 `_build_llm`，测试因而意外命中本机
  已配置 provider 并收到掩码 401；没有读取或记录凭据。修复为先由 CLI `_build_llm`
  解析，再传入安全 composition，重跑不再访问外部 provider。
- 固定 clean evaluator source 的合并后全量门禁为 **603 passed / 14 skipped**；
  `uvx pyright` 为 0 errors / 0 warnings；`uv build` 成功生成 v0.1.0 sdist/wheel；
  `git diff --cached --check` 通过，evaluator source 仍为 clean。

## 2026-07-18 Task 26：交互式审批提示可见性与 0.1.1 构建

- v0.1.0 发布后，用户截图与真实 Windows PTY 稳定复现：Rich `Status` 活动期间，
  Typer `confirm` 与 Rich `Prompt` 的阻塞确认提示都会被 live render 覆盖。调用链核对
  证明 `_run_turn()` 在 `console.status(...)` 内同步进入 `loop.run()`，后者调用审批
  handler 时没有停止 spinner；缺陷位于 CLI 展示生命周期，不在策略判断或工具权限。
- subagent `approval_prompt_impl` 使用本地 Superpowers `systematic-debugging`、
  `test-driven-development` 与 `verification-before-completion`。spinner 生命周期 RED
  命令为 `uv run pytest tests/test_cli_commands.py -k "run_turn and approval" -q`：修正
  一次测试夹具绑定错误后，两例均因缺少 `status-stop` / `status-start` 而失败。最小
  GREEN 在审批前停止 Status，并在审批返回或抛错时以 `finally` 重启；turn 的外层
  `finally` 恢复原 `approval_handler`。同一聚焦命令随后为 2 passed。
- 版本 RED 命令为
  `uv run pytest tests/test_cli_smoke.py::test_version_command_prints_version -q`，实际输出
  `phycode 0.1.0`，不满足 `phycode 0.1.1`；同步项目元数据、包内版本并执行 `uv lock`
  后，同一测试 GREEN。`0.1.1` 当前仅为待发布构建，本任务不创建或发布 Release。
- 本任务未读取凭据、未调用真实模型 API、未修改审批策略、工具权限或 PRBench
  evaluator。
- 提交前门禁：`uv run pytest -q` 完整进度达到 100%、exit 0；Pyright 首次指出两个
  测试替身与 `AgentLoop` 的名义类型错误，在测试调用边界显式 cast 后，spinner 聚焦
  测试仍为 2 passed，`uvx pyright` 为 0 errors / 0 warnings；`uv build` 成功生成
  `phycode-0.1.1.tar.gz` 与 `phycode-0.1.1-py3-none-any.whl`；
  `git diff --check` exit 0，仅报告 Windows 工作树 LF/CRLF 转换提示。
- 独立 reviewer 随后指出 Important：虽然项目元数据和构建已是 `0.1.1`，PRBench
  adapter 的严格 wheel 文件名、两份 smoke 文档以及 evaluator patch 的容器 copy /
  install 目标仍固定 `0.1.0`，会拒绝新 wheel 并使文档化 smoke 无法消费本次构建。
  新增版本一致性契约测试从 `pyproject.toml` 推导期望文件名，并聚合核对 adapter、
  主 README、集成 README、patch copy 与 install 五个消费端。首次因 `integrations/`
  不在安装包路径发生测试收集错误；改为静态提取 adapter 常量后得到有效 RED，失败 diff
  显示五处实际均为 `phycode-0.1.0-py3-none-any.whl`、期望均为 `0.1.1`。
- 最小 GREEN 只同步上述运行契约及受影响测试夹具到 `0.1.1`；没有替换历史上描述
  v0.1.0 已发布事实的过程文档，也没有改动 spinner 实现、审批策略或 evaluator 逻辑。
  版本一致性测试随后为 1 passed。
- reviewer 修复覆盖命令
  `uv run pytest tests/test_docs_process.py tests/test_prbench_adapter.py tests/test_cli_smoke.py -q`
  达到 100%、exit 0；`uvx pyright` 为 0 errors / 0 warnings。按修复合同未重跑全量测试，
  由主 agent 执行最终全量门禁。
- Task re-review 对 `7b22800` / `8484cdc` 给出 Spec Compliance Approved、Code
  Quality Approved、Task quality Approved；Critical 0、Important 0、Minor 0。版本契约
  测试直接从项目元数据推导 wheel 文件名，后续版本升级遗漏任一 PRBench 消费端都会
  失败，不再依赖手工复制版本常量。
- whole-branch final review 判定 Ready to merge，同时给出两个 Minor 生命周期加固项。
  Finding 1 增加同一 turn 两次审批、同一 loop 连续两 turn 的事件序列与原 handler
  身份断言；首跑聚焦集合为 3 passed，证明现实现已满足，这是覆盖增强而非有效 RED，
  未为制造失败扭曲断言。
- Finding 2 的有效 RED 在原审批抛出 `approval failed` 且 `status.start()` 再抛
  `status restart failed` 时，pytest 显示实际传播后者，确认 `finally` 覆盖原异常。
  最小 GREEN 将审批异常与成功路径分开：异常路径仍尝试 restart；若 restart 也失败，
  只给原异常附加不含底层内容的固定 note 并重新抛出原异常。审批成功后的 restart
  失败继续自然传播，未吞掉一般 UI 异常；turn 外层 handler 恢复保持不变。
- 为避免测试膨胀，GREEN 后把五组重复假 Status/Console/Loop 收敛为共享记录型 helper
  与事件序列构造器。最终
  `uv run pytest tests/test_cli_commands.py -k "run_turn and approval" -q` 为 5 passed，
  `uvx pyright` 为 0 errors / 0 warnings；未重跑全量测试，留给主 agent 最终门禁。
