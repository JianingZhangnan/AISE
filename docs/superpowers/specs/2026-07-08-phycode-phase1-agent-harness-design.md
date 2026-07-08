# PhyCode 第一阶段 Agent Harness 设计记录

日期：2026-07-08

本文记录 PhyCode 第一阶段经 Superpowers brainstorming 确认的设计方向。面向课程提交的正式规约以根目录 [SPEC.md](../../../SPEC.md) 为准；本文用于保留 brainstorming 过程中的关键决策，并说明这些决策如何映射到正式规约。

## 已确认方向

PhyCode 分两阶段建设。

第一阶段交付一个完整、CLI 优先、通用的 Coding Agent Harness。它实现 agent 主循环、工具分发、治理护栏、反馈回灌、记忆/上下文管理、凭据处理、测试、CI 和分发说明。即使截止前无法完成物理专用能力，第一阶段也应能独立成立并可提交。

第二阶段再以扩展形式加入物理能力：Wolfram、LaTeX、计算物理指导、文献支持和知识图谱。

## 架构决策

选定的核心机制是 **策略感知工具运行时（Policy-Aware Tool Runtime）**。

模型请求的每一次工具调用不被当作裸函数直接执行，而是必须依次经过：

1. 工具 schema 验证
2. 工作区边界检查
3. 策略决策（`allow`、`ask` 或 `deny`）
4. 执行包装
5. 输出截断与脱敏
6. 反馈分类
7. trace 记录

这把作业要求中的工具分发、治理和反馈机制合并为一个连贯的工程贡献。

## 交互决策

主要交互界面是交互式 CLI：

- `phycode` / `phycode chat`：持久会话
- `phycode run "<task>"`：一次性任务执行
- `phycode tools list`
- `phycode demo guardrail|feedback|policy`
- `phycode config ...`
- `phycode keys ...`

第一阶段不包含 WebUI。终端中的轻量呈现可以使用 Rich。

## 供应商决策

真实供应商路径使用 OpenAI-compatible Chat Completions 以及 `tools` / `tool_calls`，因为常见本地模型服务和中国开源模型服务通常暴露这种 API 形态。

第一阶段不引入备用 JSON action 解析器；不兼容 OpenAI-style `tools` / `tool_calls` 的供应商不属于当前支持范围。这样可以减少协议分叉，把精力放在自实现 harness、策略运行时、反馈闭环和可验证测试上。

产品核心不使用 OpenAI Agents SDK，因为作业要求交付自实现 harness loop。未来可以增加 Responses API 适配器，但第一阶段正确性不依赖供应商侧状态或 agent runner。

## 事件模型决策

agent 不把供应商输出简化为只有“文本”和“工具调用”两类。供应商响应会被规范化为内部事件：

- assistant commentary
- reasoning summary
- requested tool call
- policy decision
- tool running
- tool output
- feedback signal
- assistant final
- error、incomplete 和 interrupt 状态

CLI 可以展示用户可见的 commentary 和 final answer，并默认折叠 reasoning summary。

## 内建工具集合

第一阶段包括：

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

凭据变更命令如 `keys.set` 和 `keys.clear` 只作为 CLI 命令存在，不暴露给模型可调用工具。

## 安全决策

工作区默认是当前项目根目录。额外根目录必须显式加入白名单。模型可调用工具不能读取凭据文件、不能写出工作区、不能通过符号链接逃逸到允许根目录外，也不能执行危险命令。交互模式下，风险动作需要人工审批；非交互模式下，风险动作以结构化 policy feedback signal 失败。

## 上下文和记忆决策

第一阶段实现一个基础但显式的上下文系统：

- 会话历史
- trace store
- 策展型项目记忆
- 带截断和预算处理的 context builder
- 最近反馈信号纳入下一轮上下文

供应商 prompt caching 可能改善性能，但 PhyCode 不依赖它保证正确性。

## 测试决策

核心必须能在没有真实 LLM 调用的情况下验证。测试使用脚本化 mock LLM 和 fake tool executor 来验证：

- 治理护栏拒绝危险动作
- 反馈使 agent 改变下一步动作
- policy ask / approval 行为
- 上下文截断
- 凭据脱敏
- 事件规范化

必需测试命令是 `uv run pytest`。

## 分发和仓库决策

Python 与 `uv` 是主要开发和分发路径。`.gitlab-ci.yml` 必须包含 `unit-test` job。在当前使用 GitHub 开发期间，可以额外添加 GitHub Actions 作为便利 CI。

课程最终提交平台尚未确定。当前项目使用 GitHub 仓库 `JianingZhangnan/AISE`；如后续要求 NJU Git，则迁移或镜像仓库，并把切换记录写入过程文档。

## 正式规约

完整功能规约、非功能需求、数据模型、验收标准、风险和第二阶段边界见根目录 [SPEC.md](../../../SPEC.md)。
