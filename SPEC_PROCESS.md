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

## 仓库平台记录

助教尚未最终确认期末项目应提交到 GitHub 还是 NJU Git。当前为了开发、提交、review 和过程记录顺畅，先使用 GitHub 仓库 `JianingZhangnan/AISE`。如课程后续明确要求 NJU Git，将以 GitHub 仓库完整历史为源迁移或镜像，并在本文件和 `AGENT_LOG.md` 中记录切换原因与关键操作。
