# PhyCode

PhyCode 是面向 AI4SE 期末项目的 CLI 优先 coding agent harness，核心是**策略感知工具运行时（Policy-Aware Tool Runtime）**：自研 agent 主循环、可注入 mock/stub 的 LLM 抽象层、确定性治理护栏、反馈闭环、记忆/上下文管理与凭据处理。核心机制在移除真实 LLM 后仍可由确定性单元测试验证。

> 状态：Task 1–12 已完成；当前分支 `codex/task-10-12` 进入 review-ready 收尾阶段。

## 快速开始

```bash
uv sync --dev
uv run phycode version
uv run phycode tools list        # 列出内置工具及风险等级
uv run phycode run "hello"       # 执行一次性非交互任务
uv run phycode chat              # 进入交互式会话
uv run pytest                    # 运行确定性测试套件（不依赖网络/真实 LLM）
uvx pyright                      # 运行静态类型检查
```

## 机制演示（mock LLM，确定性）

```bash
uv run phycode demo guardrail    # 危险 shell 命令被策略拒绝且从未执行
uv run phycode demo policy       # 风险编辑暂停并要求审批
uv run phycode demo feedback     # 失败的测试反馈改变 agent 的下一步动作，修复后测试通过
```

`demo feedback` 通过真实 agent 循环 + `ReactiveLLM` 复现完整闭环：`test.run` 失败 → 因反馈改选 `file.edit` 修复 → 重跑测试通过 → 结束。

`run` / `chat` 走同一个 agent 主循环：若已通过 `keys set` 配置了供应商 key，则使用 OpenAI-compatible 适配器进行真实交互；否则回退到离线 `EchoLLM`，保证无 key 时也能确定性运行。`chat` 交互模式下风险动作会暂停征求审批。

## 指向真实供应商（填入你的 URL 和 API key）

**推荐：进入 `chat` 后用斜杠命令配置**（无需退出、命令短）：

```text
phycode chat
phycode: /url https://your-endpoint/v1     # 你的 OpenAI-compatible 接口
phycode: /model your-model                 # 你的模型名
phycode: /key                              # 隐藏输入录入 API key（存入钥匙串）
phycode: /status                           # 确认已配置（不显示明文）
phycode: 帮我读 README 并总结              # 直接开始对话；改动会自动重载生效
```

`chat` 内可用斜杠命令：`/model`、`/url`、`/key`、`/models`、`/config`、`/status`、`/help`、`/exit`。

不确定模型名时，先用 `phycode models`（或 chat 内 `/models`）列出你的 token 实际可用的 model id，再 `/model <exact-id>`——很多聚合网关（new-api/one-api 等）会因分组/渠道没有该模型而报 `model_not_found`，用列出的准确 id 即可。

**脚本化（非交互）等价写法**（配置写入当前目录 `phycode.toml`，key 存入操作系统钥匙串）：

```bash
phycode config set llm base_url "https://your-endpoint/v1"
phycode config set llm model "your-model"
phycode keys set openai-compatible          # 交互式录入 API key（隐藏输入，不回显）
```

说明：`chat`（交互模式）会在执行写文件/跑命令等风险动作前请求你确认；`run`（非交互模式）下这类风险动作按策略返回“需审批”而不会自动执行，因此完整能力请用 `chat` 测试。key 只存钥匙串，绝不写入 `phycode.toml`、trace 或日志。

## 内置工具

`file.read/list/write/edit`、`search.grep/glob`、`shell.run`、`test.run`、`workspace.status`、`memory.read/write`、`config.read/write`、`keys.status`。每个工具声明风险等级（safe/risky/dangerous），并在执行前经过策略决策（allow/ask/deny）。

## 凭据与安全边界

- API key 默认通过操作系统钥匙串（`keyring`）存储，绝不提交、绝不写入 trace/记忆/日志；`phycode keys status` 只显示存在性/来源/更新时间，不回显明文。
- Key 管理命令：

```bash
uv run phycode keys set openai-compatible
uv run phycode keys status openai-compatible
uv run phycode keys clear openai-compatible
```

- `.env` 只作为本地明文回退来源，包含 API key、token 或私钥时不得提交。
- 工作区边界强制执行：路径逃逸、工作区外写入、凭据文件读取（含 shell 引用 `.env`/私钥）、危险 shell 命令由确定性代码拒绝。
- trace、记忆和 LLM 消息在写入/构造前统一经过脱敏出口；`.env` 与 `.phycode/` 运行时状态不提交。

## 分发

主要分发形态为 PyPI 包（`uv publish` 发布，用户 `uvx phycode` 或 `pip install phycode` 安装）。
