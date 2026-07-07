# [CLAUDE.md](http://CLAUDE.md)

本项目是《智能化软件工程师》课程期末大作业，选题为 **A · Coding Agent Harness**。

完整作业要求以以下两个文件为准，所有实现、文档、测试、分发与过程记录都必须对齐：

- `course_resource\AI4SE_Final_Project_通用要求.md`
- `course_resource\AI4SE_Final_Project_A_Coding_Agent_Harness.md`

Superpowers 作为本项目本地资源放在 `.local\superpowers`。需要使用 Superpowers 流程时，先阅读 `.local\superpowers\skills\using-superpowers\SKILL.md` 和对应 skill 的 `SKILL.md`；不要假设存在用户级全局安装。

## Agent 工作原则

- 本项目不是普通应用包装，而是要交付一个**自己编码实现的 Coding Agent Harness 内核**。
- 开发工具可以使用 Codex / Claude Code / Cursor / Gemini CLI 等智能体与 Superpowers；但交付产物不得寄生于现成 agent 框架的高层循环。
- 实现取舍优先考虑工程深度、可验证机制、凭据治理、分发可用性与过程证据，而不是代码行数。
- 不做纯 demo / 玩具级项目；至少保持 3 个职责清晰的功能模块和一键测试命令。

## 阶段门禁

- 在 `SPEC.md` 与 `PLAN.md` 完成，并通过“陌生 agent 冷启动验证”之前，禁止编写任何实现代码。
- 必须遵循 Superpowers 七步工作流：`brainstorming` -> `writing-plans` -> `using-git-worktrees` -> `subagent-driven-development` / `executing-plans` -> `test-driven-development` -> `requesting-code-review` -> `finishing-a-development-branch`。
- 如有合理偏离，必须记录到 `AGENT_LOG.md` 并解释原因。
- TDD 是硬性要求：先写失败测试并看到红色结果，再写最少实现变绿，最后重构。不得先写实现再补测试。

## Harness 实现边界

必须自己实现：

- agent 主循环：组织上下文 -> 调用 LLM -> 解析动作 -> 分发执行 -> 回灌结果 -> 停机判断。
- 可注入 mock / stub 的 LLM 抽象层。
- 工具分发、治理护栏、反馈回灌、记忆读写、停机等核心机制。
- 至少一个机制密集维度作为主要贡献深入实现，优先考虑治理、反馈闭环、工具分发 / 多 agent 编排，或自研记忆 / 上下文工程。

不允许：

- 直接使用 LangChain `AgentExecutor`、AutoGen、CrewAI、LlamaIndex agent、宿主编码智能体 SDK agent runner 等现成高层 agent loop 作为产品核心。
- 用提示词代替代码机制。危险动作拦截、反馈信号、验证器、状态机等必须是确定性代码。
- 让配置文件、规则文件、技能文件、提示词文件冒充 harness 内核实现。

判定标准：移除真实 LLM 后，核心机制仍应能通过确定性单元测试验证。

## SPEC / PLAN 要求

`SPEC.md` 必须包含：

- 问题陈述、至少 5 个 INVEST 用户故事、模块化功能规约。
- 非功能需求：性能、安全（含凭据威胁模型）、可用性、可观测性。
- 系统架构、数据流、外部依赖、数据模型。
- 凭据与分发设计：key 存储、录入 / 更新 / 清除流程、目标机器上的安全配置方式。
- 技术选型与理由；若含前端，说明 Open Design 设计系统与 skill。
- 验收标准、风险与未决问题。
- A 题额外章节：**领域与机制设计**，说明 coding 场景下的工具、反馈信号、危险动作、记忆需求、重点维度及其代码实现方案。

`PLAN.md` 必须由 `writing-plans` 沉淀：

- 每个 task 颗粒度应能由一个 subagent 在一次会话内完成。
- 每个 task 写清目标、涉及文件、实现要点、验证步骤，以及将要先写的失败测试。
- 显式标出依赖关系和可并行 worktree 部分。
- 每完成一个 task，标记完成并附 commit hash。

`SPEC_PROCESS.md` 必须记录：

- brainstorming 的关键追问与至少 3 轮关键迭代。
- 哪些 AI 建议被采纳、推翻或修正，以及原因。
- 陌生 agent 冷启动试运行暴露的问题、产出偏差、SPEC / PLAN 修订前后关键 diff。

## 测试与演示

- 必须提供一键测试命令（如 `make test` 或等价命令），覆盖核心功能。
- harness 核心机制必须有 mock / stub LLM 驱动的确定性单元测试，且不依赖网络与真实 LLM。
- 必须提交机制演示，能在 mock LLM 下确定性复现：
  - 治理护栏拦截危险动作。
  - 注入一次失败后，反馈闭环使 agent 收到反馈并改变下一步动作。
  - 重点维度的一个确定性行为。
- CI 必须通过；`.gitlab-ci.yml` 中应包含名为 `unit-test` 的 job。

## 凭据与安全

- API key / token 绝不硬编码、绝不提交到 Git、绝不写入日志 / 终端 history / 明文配置文件。
- 至少实现一种安全存储：操作系统钥匙串、密钥管理服务，或带主密码的加密文件。
- 环境变量和 `.env` 只能作为来源之一，必须说明明文风险；`.env` 不得提交。
- 首次运行应引导用户安全录入 key，并支持查看状态、更新、清除；查看状态不得回显明文。
- 提交前检查 `.env`、历史、日志和配置文件，确保无真实凭据。

## 分发与 README

- 必须选择至少一种分发形态：Docker / OCI 镜像、原生单文件二进制、或包管理器分发。
- README 必须写清项目简介、安装、运行、分发命令、目录结构、安全边界说明。
- README 必须说明别人如何获取并运行项目，以及如何在目标机器上安全配置自己的 key。
- 如果有服务端 / WebUI，最终交付必须提供截止前可访问的线上部署 URL。

## 过程与仓库纪律

- 使用 git worktrees 隔离独立功能 / 大模块，每个 worktree 对应一个 PR。
- 每个 task 由新鲜 subagent 完成单一任务；完成后先做 spec 合规检查，再做代码质量检查。
- `AGENT_LOG.md` 按时间顺序记录 task 编号、Superpowers 技能、关键 prompt / context、subagent 关键输出或 commit hash、人工干预与教训。
- commit message / PR 描述应标注由哪个 subagent 完成、人工修改了哪些部分。
- 需要维护的工程交付物包括 `SPEC.md`、`PLAN.md`、`SPEC_PROCESS.md`、源代码、README、`AGENT_LOG.md`、CI 配置、分发产物 / 说明、线上 URL。
- `REFLECTION.md` 属于学生个人反思报告；agent 可以辅助整理素材或提纲，但不要代写正文。

## Python 要求

- 如使用 Python，一律使用 `uv` 进行包管理，禁用 `pip` / `conda` 等包管理流程。
