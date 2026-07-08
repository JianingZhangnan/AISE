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
