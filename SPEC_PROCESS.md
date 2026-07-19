# SPEC 过程记录

## 记录目的

本文记录 PhyCode 第一阶段从选题构想到可执行规约的形成过程，包括 brainstorming 迭代、AI 建议取舍、规范修订和实现前冷启动验证。它与 `SPEC.md`、`PLAN.md`、`AGENT_LOG.md` 一起作为课程过程证据。

## Brainstorming 关键迭代

1. **从物理专用工具收敛为两阶段方案。**
   初始愿景是面向物理专业的 PhyCode，内置 Wolfram、LaTeX、计算物理指导、理论书籍/文献和知识图谱。考虑提交截止和实现风险后，方案收敛为第一阶段先交付通用 Coding Agent Harness，第二阶段再加入物理扩展。

2. **从功能清单收敛为一个核心机制贡献。**
   工具调用、治理护栏、反馈回灌一开始像是三个并列功能。后续将它们整合为“策略感知工具运行时（Policy-Aware Tool Runtime）”，使项目重点变成一个可验证、可解释的 harness 内核机制。

3. **从 WebUI/多界面收敛为 CLI 优先。**
   用户明确没有足够时间做 WebUI，希望类似 Claude Code 的交互式 CLI。最终确定 `phycode chat` 作为持久交互会话，`phycode run "<task>"` 作为一次性任务入口，Rich 只承担轻量 TUI 渲染。

4. **从 provider 兼容性担忧收敛为 OpenAI-compatible tool calls。**
   早期方案考虑自定义 JSON action 协议以兼容 tool calling 支持不稳定的服务。经讨论和 Claude review 后，第一阶段改为只支持 OpenAI-compatible Chat Completions 的 `tools` / `tool_calls`，不引入备用 JSON action 解析器；不兼容该 API 的供应商不在当前支持范围内。

5. **从“是否需要缓存”收敛为显式上下文管理。**
   讨论确认 provider prompt caching 只能作为性能优化，不能替代 harness 自己的上下文机制。第一阶段实现会话历史、trace、策展型记忆、截断和最近反馈纳入，不引入向量数据库。

6. **从两类消息收敛为内部事件模型。**
   用户指出输出不应只有普通文本和 tool call，还应考虑 thinking、tool call 运行中状态和输出阶段。最终规约定义 `assistant_commentary`、`reasoning_summary`、`tool_call_requested`、`policy_decision`、`tool_call_running`、`tool_call_output`、`feedback_signal`、`assistant_final`、`error` 等事件。

## AI 建议取舍

| 建议 | 处理 | 原因 |
| --- | --- | --- |
| 分两阶段：第一阶段通用 harness，第二阶段物理扩展 | 采纳 | 降低截止前风险，同时保留 PhyCode 的长期方向 |
| 使用 Python + uv + Typer + Rich + pytest | 采纳 | 与课程 Python/uv 要求、CLI 形态和测试需求匹配 |
| 使用 OpenAI Agents SDK 作为核心 | 拒绝 | 作业要求自实现 Coding Agent Harness 内核，不能寄生于现成 agent loop |
| 使用 WebUI | 推迟 | 时间有限，用户明确偏向 CLI |
| 为不兼容 tool calls 的供应商实现 JSON action fallback | 修正为拒绝 | 第一阶段收敛到 OpenAI-compatible `tools` / `tool_calls`，减少协议分叉 |
| 加入 Wolfram、LaTeX、文献和知识图谱 | 推迟 | 属于第二阶段物理扩展，不阻塞通用 harness 交付 |
| 使用 mock LLM 作为核心验证路径 | 采纳 | 确保测试和 CI 不依赖网络、真实模型或 API key |

## 关键规范修订记录

- `72fa5df`：加入第一版 `SPEC.md` 和 Superpowers spec 记录。
- `11d2883`：Claude review 后将 `SPEC.md` 翻译为中文，补充架构图、技术理由和机制深度。
- 当前合流：接受 Claude 对 `SPEC.md` / `PLAN.md` 的中文化和供应商策略收敛修改；同步 `docs/superpowers` 下的发现文档；把 `SPEC_PROCESS.md`、`AGENT_LOG.md` 和冷启动验证移动到实现前门禁。

## 冷启动验证

当前状态：**冷启动验证已通过；允许进入 Task 1。**

进入 Task 1 编写实现代码之前，必须完成一次陌生 agent 冷启动验证。验证方式如下：

1. 向未参与前期讨论的 agent 提供 `CLAUDE.md`、`SPEC.md`、`PLAN.md` 和当前仓库状态。
2. 要求它只分析，不写实现代码。
3. 要求它复述 Task 1 的目标、要创建/修改的文件、红绿测试路径、实现边界和可能歧义。
4. 若发现 SPEC / PLAN 不足，先修订文档，再把问题、偏差和修订摘要记录在本节。

### 第一次冷启动验证记录

- 执行方式：用户使用 Cursor agent 进行外部冷启动验证，并将报告贴回仓库维护对话。
- 报告 verdict：`FAIL`。
- 报告识别的阻塞项：
  - B1：`docs/superpowers/specs/2026-07-08-phycode-phase1-agent-harness-design.md` 仍为英文。
  - B2：`docs/superpowers/plans/2026-07-08-phycode-phase1-agent-harness.md` 仍为英文。
- 当前仓库复核：`main` 上述两个文件已经在 `ccc52d0` 中改为中文，且不再保留旧的 fallback JSON action parser 设计。报告读取到的 docs 状态与当前 `origin/main` 不一致，推测是外部验证环境未同步最新提交。
- 报告识别的应修项：
  - R1：认为 Task 1 的 `"No tools registered yet"` 断言会与后续工具注册矛盾。复核结论：Task 1 中 CLI 尚不注册工具，Task 5 才引入 `file.read` 并更新断言，因此当前 Task 1 断言成立，不需要修改。
  - R2：Task 0 步骤 4 未明确冷启动验证由谁执行，以及 FAIL 后如何处理。处理结论：采纳，已在 `PLAN.md` Task 0 步骤 4 中补充外部 agent 只输出报告、维护者修订并复验的规则。
  - R3：认为 Task 1 提交命令遗漏 `README.md`。复核结论：当前 `PLAN.md` Task 1 步骤 6 的 `git add` 已包含 `README.md`，不需要修改。
- 复验要求：请在外部 Cursor 工作区执行 `git pull` 后重新运行冷启动验证。复验报告 verdict 为 `PASS` 或可接受的 `PASS_WITH_NOTES` 后，才能进入 Task 1。

### 第二次冷启动验证记录

- 执行方式：用户再次使用 Cursor agent 进行外部复验，并将报告贴回仓库维护对话。
- 报告 verdict：`FAIL`。
- 报告识别的阻塞项：
  - B1：认为 `docs/superpowers/specs/2026-07-08-phycode-phase1-agent-harness-design.md` 仍为英文。
  - B2：认为 Task 0 冷启动验证仍未通过。
- 当前仓库复核：维护者在本地 `main` 和 `origin/main` 分别读取该文件，文件开头均为 `# PhyCode 第一阶段 Agent Harness 设计记录`，正文为中文；`git ls-remote origin refs/heads/main` 返回 `eda48914c22cd0c83289cff9360da3c399dddbee`，与当前 `origin/main` 一致。因此第二次报告仍未读取到最新远端状态，或 Cursor 工作区存在未提交旧文件覆盖。
- 处理结论：不修改 `docs/superpowers/specs` 内容；在 `PLAN.md` Task 0 步骤 4 增加冷启动报告的证据要求，要求外部 agent 报告开头输出 `git rev-parse HEAD`、`git status --short --branch` 和目标 docs 文件前 5 行。
- 复验要求：下一次 Cursor 验证前必须确认工作区位于 `eda4891` 或更新的 `main`，并在报告中附上述命令输出。只有该证据与当前仓库一致时，verdict 才可作为门禁依据。

### 第三次冷启动验证记录

- 执行方式：用户确认外部 Cursor agent 冷启动复验已成功。
- 报告 verdict：通过。
- 处理结论：冷启动门禁解除，允许按 `PLAN.md` 从 Task 1 开始实现。
- 后续要求：Task 1 必须继续遵守 TDD 红绿路径，不得在没有失败测试证据的情况下编写生产代码。

## 代码评审与修订（2026-07-09）

Task 0–9 实现完成后，应用户要求对既有代码做一次全面评审（对齐作业要求与 Superpowers 流程），再严格按结论修复、提交、合并。

关键发现与处理：

- **P0（最严重，触及判定标准）：反馈闭环无法在移除真实 LLM 后被确定性验证。**
  `ScriptedLLM` 完全忽略 `messages`，只按序号返回预设 turns，因此「反馈回灌改变下一步动作」在 mock 下无法证明；更严重的是 `PLAN.md` Task 11 计划的 `run_feedback_demo` 把「下一步动作」硬编码为字符串 `"file.edit"`，对应测试只做字符串拼接断言。这正是 CLAUDE.md 禁止的「用提示词/配置代替代码机制」，且违反「移除真实 LLM 后核心机制仍应能通过确定性单元测试验证」。
  处理：新增确定性的 `ReactiveLLM`（输出取决于上下文中出现的反馈），用真实 agent loop 重写反馈 demo，呈现 `test.run` 失败 → 因失败反馈改选 `file.edit` 修复 → 重跑测试通过 → `assistant_final` 的完整闭环；并补充直接单测证明同一 `ReactiveLLM` 在「有/无失败反馈」两种上下文下产出不同动作。**采纳并推翻了 PLAN 原定的占位实现。**
- **P1：真实运行时缺陷（被 mock 掩盖）。** 工具 spec 从未传给 LLM、审批路径未接线、停机控制器只处理 final/max_steps、runtime 缺 `validate_args`（`invalid_tool_args` 为死路径且缺参会崩溃）。逐项以 TDD 修复。
- **P2：安全与一致性。** shell 可绕过凭据文件拒绝、危险命令模式表偏薄、policy 集合与 registry 不同步。逐项修复：新增 `credential.shell_read_blocked`、扩充危险模式、补齐 7 个工具执行器使 both-way diff 为空。

采纳/推翻记录：

| 评审建议 | 处理 | 原因 |
| --- | --- | --- |
| 用 reactive mock LLM 让反馈真正改变动作 | 采纳 | 满足判定标准，替换硬编码占位 demo |
| 把工具 schema 传给 LLM 并纳入上下文 | 采纳 | 否则真实供应商路径下模型无法发起 tool_call |
| 接入可注入审批处理器 | 采纳 | 交互式审批与「安全自动审批模式」的确定性验证入口 |
| 停机控制器补充 error/repeated_failure/interrupt | 采纳 | 对齐 SPEC §5.2/§5.7 |
| runtime 增加 `validate_args` 与执行器异常兜底 | 采纳 | 对齐 SPEC §10 流水线，消除崩溃与死路径 |
| shell 凭据读取拒绝 + 扩充危险模式 | 采纳 | 收敛威胁模型 §6.2 与深度维度承诺 §10 |
| 实现缺失工具以对齐 policy/registry | 采纳 | 消除「policy 放行但 runtime 报 tool_error」的不一致 |

过程偏离与验证方式记录于 `AGENT_LOG.md`（2026-07-09 条目）。

## Task 10–12 收尾

Task 10–12 的收尾目标是把已实现的 CLI 入口、确定性 demo 和课程过程证据整理到 review-ready 状态。当前收尾分支为 `codex/task-10-12`。

- Task 10 使用严格 CLI 测试覆盖用户可见命令：`phycode run`、`phycode chat`、`phycode tools list`、`phycode config read`、`phycode keys set/status/clear`。测试文件为 `tests/test_cli_commands.py`，重点验证输出脱敏、trace 不泄露 secret、工具列表完整性、key 管理不回显明文，以及异常停机时返回非零退出码。
- Task 11 保留代码评审后形成的真实机制 demo：guardrail 与 policy 走 `ToolRuntime`，feedback 走真实 agent loop + `ReactiveLLM`，不再使用硬编码字符串模拟下一步动作。
- Task 12 增加严格文档测试 `tests/test_docs_process.py`，要求 README、PLAN、SPEC_PROCESS、AGENT_LOG 同步记录用户命令、安全边界、Task 10–12 完成状态、严格 CLI 测试策略和后续 review 流程。
- 最终验证命令记录为 `uv run pytest` 和 `uvx pyright`；文档收尾阶段至少定向运行 `uv run pytest tests/test_docs_process.py -v`，完整验收仍以全套 pytest 和 pyright 为准。
- review-ready 状态：`codex/task-10-12` 待 Claude 审核后再合并或提交课程最终交付。

## PRBench 运行时真正重构（2026-07-18）

### 真实试运行推翻的旧方案

PRBench 初始分支把 `_ground_truth` 防护主要实现为旧 parser：字符串级 shell
lexer/state machine 逐步覆盖 POSIX/Windows 引号、转义、glob、环境变量和 `env`
option。真实 API 试运行说明这条路线既臃肿又不能证明安全：它仍无法覆盖嵌套
解释器、运行时字符串拼接、变量展开与 symlink 别名；继续追加匹配规则只会增加
误报、漏报和 parser-specific 测试，不能形成权威隔离。

同一轮真实模型测试还推翻了“看到 assistant final 就算完成”的假完成方案：
`deepseek-v4-pro` 可能诚实报告未完成，也可能在没有执行脚本和生成 CSV 时结束；
旧循环仍会把 final 包装为成功。全局累计相同成功动作也会在 workspace 已有进展后
错误停机。因此本轮不是给 parser 打补丁，而是从 `f2817ab` 干净基线重建。

### 三轮关键修订

1. **从字符串 shell 安全转向结构化能力。** PRBench profile 移除 `shell.run`，
   只暴露 `process.run(argv)`；绝对 executable identity、workspace cwd、最小环境和
   `shell=False` 都由确定性代码约束，写入/执行使用一次性精确审批。
2. **从 final 文本转向可验证完成。** 新增 execution journal、公开 task contract、
   script hash 与 CSV provenance；artifact verifier 未通过时回灌结构化反馈，只有
   expected outputs、执行证据和公开约束全部成立才返回 `completed`。重复停机同时
   改为“连续无进展”，artifact 或 verifier issue 改善会重置计数。
3. **从路径匹配器转向 evaluator 生命周期。** 白色 agent 生命周期内不挂载、不
   复制、不 allowlist `_ground_truth`；白色结束并释放 provider 环境后，官方绿色
   grader 才复制评分材料。visibility 对显式路径和 symlink 的拒绝保留为纵深防御，
   不再冒充解释器语义分析。

### Task 14–20 与审查门禁

Task 14–19 分别交付 profile/visibility、结构化进程与审批、journal/verifier、
AgentLoop 完成门禁、PRBench runner 和固定版本 evaluator adapter。每项由新鲜
subagent 按 TDD 完成，再经过独立 spec/quality review；审查修复均进入对应提交，
Task 19 最终固定在 `31f58a4`、`1b0a448`。Task 20 增加中文使用说明、无凭据 smoke
脚本和文档契约测试，不读取或调用真实 provider。

官方 evaluator 固定为
`HET-AGI/PRBench-Eval-Handson@3e5bee4545cad2138832f06302e9c98bd81f5216`。
fresh 环境暴露出独立的 upstream 风险：无 lockfile 的普通 uv 解析会选择
`a2a-sdk 1.1.1`，而固定 commit 使用 0.3.x import API。主 agent 动态确认
`uv run --with "a2a-sdk[http-server]==0.3.8" python main.py --help` 可用；最终 smoke
通过该临时 exact overlay 运行，不修改上游 `pyproject.toml` 或扩大 adapter 范围。
绿色 model-judge 显式选择 OpenCode，并只在 evaluator 子进程期间把相同三项
`PHYCODE_*` 临时映射成 `OPENCODE_*`，结束后恢复/清除且不回显。

### 独立审查后的运行中审批修订

Task 7 的独立审查发现，文档所谓“精确审批”仍把固定 argv 在 reproduction 脚本
生成前写入 manifest。虽然 argv 没有通配符，这仍绕过了“执行前读脚本”的人工
安全门禁，因此该建议被推翻：初始清单现在只允许目标 reproduction 文件的
`file.write`，绝不包含 `process.run`。

runner 在模型首次请求执行时把规范化 argv（其中 `argv[1]` 是脚本路径）、cwd 与
`script_sha256` 写入 `.phycode/prbench/approval-request.json`，并在显式 900 秒上限内
等待。请求与一次性 process grant 共用同一 schema；主 agent 读取脚本、独立核验
hash 后，才把请求对象原样追加到 active workspace 的 `phycode-approvals.json`
`grants` 数组。smoke 编排不读取脚本、不计算 hash、不
自动批准；脚本变化、hash/参数不匹配、清单畸形、重复消费或超时均 fail closed。

审查同时用 Windows probe 证明 `.NET SetEnvironmentVariable(..., $null,
'Process')` 会留下空环境变量，而不是删除名称。修复改用 PowerShell Env provider
的 `Remove-Item -LiteralPath Env:<name>`；fake-uv 动态测试覆盖官方子命令成功与
失败，以及调用前 aliases 存在/不存在的四种组合，要求已有值精确恢复、无原值时
名称完全消失。

绿色凭据也从“宿主映射即容器配置”进一步收紧。adapter `6f5d75d` 把 green-only
值排除于共享容器 `Config.Env`，白色阶段的容器进程不可见；白色 runner 结束后，
绿色 grading child 才通过 name-only Docker 环境参数取得临时值，并在成功、失败、
超时路径清理。宿主 smoke 的 `OPENCODE_*` aliases 只用于给 evaluator 提供这一
延迟解析来源，不能据此声称白色阶段已注入 green provider。

### 真实 API 验收边界

确定性 mock/stub 测试仍是默认 `uv run pytest` 和 CI 的核心，因为它能在无网络、
无 key 时证明机制；它不再被描述为真实模型能力。直接 `phycode prbench run` 的
真实模型 smoke 能验证 agent loop，但不等于官方评分。最终官方验收只由主 agent
执行：凭据仅进入当前子进程内存，Docker daemon 必须运行，依次评测
`aaatest_helloworld` 与 `bbbtest_alphabet`，审批不得加入任何模型临时请求。

只有每项 white runner 为 `completed`、expected outputs 存在、官方 evaluator
生成报告，并且 trace、journal、result 扫描不含 key/URL 时，才能记录真实 API
验收成功。adapter apply、mock GREEN、直接 runner 或单个 task 成功都不满足这一
边界；如真实模型失败，必须先用可复现测试定位机制根因，不能恢复旧 parser 或按
task ID 写 solver/prompt 补丁。

### 第四轮修订：从“模型主动结束”到 contract-aware 即时停机

第一次官方真实 API 运行进一步修正了停机假设。模型生成的 reproduction 脚本与
CSV 获得官方绿色 grader 的满分内容评价，却没有主动请求 stop，而是继续反复读取
状态直到 40 次工具预算耗尽。终点 verifier 同时给出 `script_not_executed` 与
`csv_without_provenance`，证明“内容看起来正确”与“具备安全执行 provenance”是两个
独立条件。由此推翻两项建议：既不能等待模型自觉 final，也不能因为文件存在或 grader
内容得分而放宽 contract。

采用的修订是在工具结果与 stop controller 的既有边界增加 PRBench 显式 opt-in：
成功工具结果完成回灌、进程 journal 已更新后立即调用同一个 `ArtifactVerifier`；只有
完整 contract 与 provenance 都通过才返回 `completed`。中间阶段的 nonfatal 未通过
通过结构化 `artifact_verification_failed` 回灌到下一轮因果上下文，使模型知道缺少
脚本执行或产物；拒绝、失败和超时动作不触发即时成功，verifier 安全异常仍 fail
closed。该开关默认关闭，因此没有把 PRBench contract 语义扩散到普通 coding/GAIA
loop，也没有新增第二套验证器、任务专用 solver 或提示词补丁。

### 第五轮修订：从“精确 CSV 审批”到不可绕过的 provenance workflow

第二次真实 smoke 暴露了配置与机制之间的矛盾：`7bb2a6b` 为两个公开 CSV 增加了
精确 `file.write` grant，模型因此能直接写出评分内容正确的 CSV；但 reproduction
脚本只打印而未生成数据，verifier 正确报告 `script_not_executed` 与
`csv_without_provenance`。绿色 grader 的 1.0 只能证明内容，不证明执行来源，因此
本轮再次拒绝“放宽 provenance”或“把正确输出视为成功”的建议。

系统性追踪确认根因有两层：错误 smoke grant 扩大了能力；通用 `PolicyEngine` 对
所有 PRBench 文件写入仅返回 `ASK`，使该 grant 可以生效。同时通用 structured
feedback 不携带 profile/rule 上下文，无法给出 provenance-specific 恢复步骤。
修订后，初始 manifest 只允许目标 reproduction 脚本的一次精确 write 和同路径
edit；后者只服务首版脚本不完整时的最小恢复，不允许改其他路径。

确定性代码门禁在审批前拒绝 PRBench workspace `data/**/*.csv` 的 direct
write/edit，大小写、Windows 分隔符、规范化路径与既有 escape/hidden 防护都纳入
测试；即使 manifest 错含精确 CSV grant 也不能绕过。profile-aware feedback 只按
该 rule 引导模型“修改或重写 reproduction 脚本，再请求 `process.run`”，固定文本
不泄露 expected value、文件内容或凭据。端到端回放进一步证明动态请求绑定最终脚本
SHA-256，批准后真实执行建立 provenance，并由既有即时 verifier 在下一轮模型调用前
返回 `completed`。

独立复审随后指出首版分类仍只做 casefold，未覆盖 Win32 尾随空格/点别名与 NTFS
ADS。该 Important 被采纳，因为 exact grant 与目标存在性会影响 `Path.resolve()`
结果，使同一个逻辑目标在不同 workspace 状态下可能得到 ASK 或 DENY。修订保持原
path 先走 visibility，仅额外构造不进入 executor 的分类 view：逐 component
`rstrip(" .")` 后 casefold；非盘符冒号 fail closed。确定性 wrong-grant 回放覆盖
write/edit、多个尾随字符、每层 component、大小写、反斜杠、嵌套路径与
`data/output.csv::$DATA`，同时证明 coding/GAIA 与正常 drive prefix 不受影响。

### 第六轮修订：原生工具对话、因果状态与安全别名

后续真实运行证明，仅修补 policy 仍不足以让模型稳定使用 harness。一次运行中模型把
历史工具交互看成扁平文本，反复 `file.list`，官方仅得 0.5。由此删除旧的拼接式上下文，
独立引入 conversation projection：assistant 事件恢复为 provider 原生
`tool_calls`，结果使用配对的 tool role，显式 user turn 与可行动反馈分别保留。成功
动作账本、审批/进程失败 blocker 和 no-progress epoch 改为因果状态，不再依赖可能被
截断的原始历史；同一 provider batch 在 mutation、失败或纠正反馈后剩余调用标记为
`stale_tool_batch`，不得在过期前提下继续执行。

下一次运行生成了正确脚本，却因模型使用裸 `python` 而无法形成动态审批，官方得
0.3。修订没有恢复 shell/PATH 解析，而是在 ToolRegistry 的 policy 前增加一次性
normalizer：只有 allowlist 中恰好存在一个 Python 绝对 executable 时，大小写精确的
裸 `python` 才规范化为该路径；`./python`、`python.exe`、多候选和 PATH 搜索全部拒绝。
normalizer 前先快照 id/tool/provider-call identity，原地篡改身份立即 fail closed；
规范化后的同一调用才进入 policy、approval、executor、journal 与 guard。

### 第七轮修订：审批请求与 grant 的单一契约

真实 hello 任务随后达到官方 1.0，但 alphabet 审批失败暴露了接口断裂：运行时请求
含 `script_path`，严格的 `ApprovalGrant(extra="forbid")` 却不接受该字段。主 agent
把已核验请求原样追加后，清单刷新正确地 fail closed，官方两次分别只得到 0.7 与
0.5；这不是模型代码错误，也不能通过手工生成 CSV 掩盖。

TDD 修订做了两件事：等待期间遇到瞬时无效清单时，该轮不消费任何旧授权，但在
deadline 内继续轮询；更关键的是删除请求中冗余的 `script_path`，让请求与 process
grant 共用严格 schema。`argv[1]` 仍给出相对 `cwd` 的脚本，absolute executable、
cwd、argv、SHA-256 和一次性消费均未放宽。修复后的 alphabet 白色 runner 返回
`completed`，官方 grader 为 1.0。

最终有效验收组合为 hello（real8）与 alphabet（real10）：两项官方
`overall_score=1.0`，trace 计数、execution journal、产物存在性及哈希均复核通过。
期间一次 Docker exec 404 发生在模型/API 启动前，按外部冷启动故障记录且未触发代码
修改。两组真实 URL/key 对源码、构建物、两个有效 evaluator 结果和 Git 历史的精确
扫描均为 0 命中；宿主 `PHYCODE_*` / `OPENCODE_*` 进程变量和评测容器均已清理。

最终整分支独立 review 又发现 causal state 的两个边缘问题。第一，连续失败原先只按
tool/feedback kind 计数，同一工具使用不同参数纠错也会误停；修订复用完整
`_ActionIdentity`，不同动作重置 streak，完全相同失败仍按阈值停机。第二，旧脚本的
`approval_required` / `process_failed` blocker 包含内容 SHA，导致同一路径的新版本
成功执行后仍残留；修订额外定义不含 executable/内容版本的 process target
（canonical cwd、脚本路径、尾随 argv），只允许成功的同 target process 淘汰旧
blocker。不同脚本及 read/write 不能清除，AgentLoop 也不会二次调用 normalizer。
两项修复后的窄复审为 Critical 0 / Important 0。

## PRBench 完整公开任务设计迭代（2026-07-19，正式运行前）

本轮目标是单个**完整公开任务** `task_white_1993`，不是 holdout，也不能推导课程最终
成绩。正式真实 API / official evaluator 尚未运行；本节只记录为何推翻旧 baseline、
如何把确定性机制扩展到完整任务，以及用户批准的成功、尝试和 Git 边界。

### 第一轮：从全 `.py` provenance 到显式执行入口

旧 baseline 把所有 expected `.py` 都视为可执行 provenance 来源。完整任务包含分析代码、
被导入模块和七个真正生成 CSV 的入口；继续按扩展名推断会允许普通 Python artifact 为
CSV 背书，也无法精确表达每个 CSV 的数据行数。Task 32 因此采纳
`execution_entrypoints` 与 `csv_data_row_count`：只有声明入口的当前 SHA-256、成功 journal
record、changed artifact 和 after snapshot 同时匹配，CSV 才有有效来源。独立审查要求的
负例与 mutation 证明普通 expected Python 不能越权，最终范围
`bfae0be..959eb44` review clean。

### 第二轮：从长 prompt 到紧凑公开简报

旧 baseline 把 instruction、paper 和文件正文长篇内联，并使用默认 12,000 字上下文；
完整任务会挤压工具结果与反馈，截断后仍调用 provider 还可能让模型在不完整合同上行动。
Task 33 改为只发送经路径验证的 compact brief，正文由受治理的 `file.read` /
`search.grep` 按需读取；显式 24,000 上下文用于完整任务，预算不足则在首次 provider 调用
前 fail closed。审查进一步加入完整简报容量门。随后分支级回归纠正删除了已过期的
runner-side read 假设，并由 `0d4582b`、`7547db2` 锁定 instruction 验证发生在 provider
之前；692 项回归与复审均通过。

### 第三轮：从 smoke-only 审批到完整任务入口

旧 baseline 的 smoke-only 审批只覆盖两个小任务、默认 40 次工具预算和少量 reproduction
路径，不能证明 20 个 artifact、7 个 CSV 与 7 个执行入口的完整合同，也没有给正式尝试
计数和 grader 有效性建立统一标准。Task 34 增加声明式 contract 和
`run_public_full.ps1`，完整任务显式使用 50 次工具调用、24,000 字上下文与 900 秒动态
审批等待；静态 manifest 仍只含精确 reproduction write/edit，不预授权 process、CSV 或
通配路径。双 PowerShell、fake uv、fresh fixed-commit patch 与默认参数链经确定性测试后，
`7fe73aa..e51a82c` 最终 review clean。

用户批准的边界是：每个 exact `argv` / `cwd` / script SHA-256 请求均由主 agent 阅读脚本
后人工审批，并把请求对象原样原子追加到 active workspace；最多三次，每次使用新的固定
commit clone/workspace，首次白色模型响应前的基础设施失败不计数。成功必须同时具备 runner
`completed` 与本轮新生成的有效 grader report；部分产物、mock GREEN 或单独 grader 片段
都不能冒充成功。所有 evaluator workspace、trace、journal、报告和模型生成物保持本地
ignored，评测产物不提交；所有提交只在 `codex/prbench-public-test`，未经授权不进入
`main`，保持主分支干净。

### 第四轮：Task 35 独立 review 收紧运行手册与文档合同

Task 35 主实现 commit `71656cf630ee1f7e87b1805b53e502596818b707` 的独立 review
结论为 **Changes requested**，0 Critical / 3 Important / 0 Minor。审查核对固定
evaluator 的 `DATA_DIR` 与 launcher 后确认，新章节误把仓库外 attempt/clone 命名习惯
写成 evaluator 内部 `data/workspaces/*`，实际 active workspace 必须由本轮 launcher
日志确认，并位于
`<EvaluatorRoot>\data\tasks\task_white_1993\workspace`。审查还指出审批文字没有锁定
contract expected Python、cwd 等于 workspace、尾随 argv 路径与完整脚本拒绝项；跨全文
字符串测试因此在错误手册上仍然 GREEN。

采纳的修订先把测试限制到“PRBench 完整公开任务（正式运行前门禁）”单一 H2 section，
自然 RED 在同一次失败中同时列出错误 workspace 与缺失审批条件。README 随后按八步顺序
写明路径/文件类型、解释器、脚本入口、精确 cwd、尾随参数、脚本拒绝项、内容哈希与最后
原子批准；外部 attempt/clone 目录与 evaluator 内部 workspace 分开。正式运行仍未发生，
本轮只修正文档与确定性合同。修正文档初步 GREEN 后，受控文本 mutation 临时把正确的
active workspace 句改回旧 `data/workspaces/task_white_1993_*`；section-scoped 测试得到
1 failed，failure 精确显示 `missing=[]` 且 forbidden 只含旧路径。反向补丁恢复 README
后，同一聚焦测试重新为 1 passed，证明合同能杀死本次真实路径回归。完整文档套件随后
为 27 passed，全仓 693 项达到 100% exit 0，Pyright 为 0 errors / 0 warnings；diff、
凭据文件名/高置信模式与新增 evaluator/runtime artifact 扫描均通过。修复后复审仍待
执行，不能把本轮 GREEN 记录为 review clean 或正式 evaluator 成功。

## 仓库平台记录

助教尚未最终确认期末项目应提交到 GitHub 还是 NJU Git。当前为了开发、提交、review 和过程记录顺畅，先使用 GitHub 仓库 `JianingZhangnan/AISE`。如课程后续明确要求 NJU Git，将以 GitHub 仓库完整历史为源迁移或镜像，并在本文件和 `AGENT_LOG.md` 中记录切换原因与关键操作。
