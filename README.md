# PhyCode

PhyCode 是面向 AI4SE 期末项目的 CLI 优先 coding agent harness，核心是**策略感知工具运行时（Policy-Aware Tool Runtime）**：自研 agent 主循环、可注入 mock/stub 的 LLM 抽象层、确定性治理护栏、反馈闭环、记忆/上下文管理与凭据处理。核心机制在移除真实 LLM 后仍可由确定性单元测试验证。

> 状态：Task 1–11 已完成（脚手架、模型/脱敏、配置/凭据、策略引擎、工具运行时、shell/反馈、trace/上下文/记忆、LLM 适配器、agent 循环、扩展工具、确定性演示）。`phycode run/chat` 交互入口与完整 README 收尾属 Task 10/12，仍在推进中。

## 快速开始

```bash
uv sync --dev
uv run phycode version
uv run phycode tools list        # 列出内置工具及风险等级
uv run pytest                    # 运行确定性测试套件（不依赖网络/真实 LLM）
```

## 机制演示（mock LLM，确定性）

```bash
uv run phycode demo guardrail    # 危险 shell 命令被策略拒绝且从未执行
uv run phycode demo policy       # 风险编辑暂停并要求审批
uv run phycode demo feedback     # 失败的测试反馈改变 agent 的下一步动作，修复后测试通过
```

`demo feedback` 通过真实 agent 循环 + `ReactiveLLM` 复现完整闭环：`test.run` 失败 → 因反馈改选 `file.edit` 修复 → 重跑测试通过 → 结束。

## 内置工具

`file.read/list/write/edit`、`search.grep/glob`、`shell.run`、`test.run`、`workspace.status`、`memory.read/write`、`config.read/write`、`keys.status`。每个工具声明风险等级（safe/risky/dangerous），并在执行前经过策略决策（allow/ask/deny）。

## 凭据与安全边界

- API key 通过操作系统钥匙串（`keyring`）存储，绝不提交、绝不写入 trace/记忆/日志；`phycode keys status` 只显示存在性/来源/更新时间，不回显明文。
- 工作区边界强制执行：路径逃逸、工作区外写入、凭据文件读取（含 shell 引用 `.env`/私钥）、危险 shell 命令由确定性代码拒绝。
- trace、记忆和 LLM 消息在写入/构造前统一经过脱敏出口；`.env` 与 `.phycode/` 运行时状态不提交。

## 分发

主要分发形态为 PyPI 包（`uv publish` 发布，用户 `uvx phycode` 或 `pip install phycode` 安装）。
