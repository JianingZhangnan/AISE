# PhyCode

PhyCode 是面向 AI4SE 期末项目的 CLI 优先 coding agent harness，核心是**策略感知工具运行时（Policy-Aware Tool Runtime）**：自研 agent 主循环、可注入 mock/stub 的 LLM 抽象层、确定性治理护栏、反馈闭环、记忆/上下文管理与凭据处理。核心机制在移除真实 LLM 后仍可由确定性单元测试验证。

## 安装

环境要求：Python >= 3.11。Windows 与 Linux 已验证，macOS 未实测。PyPI 页面使用面向最终用户的 `README_PYPI.md` 作为描述；本 README 为仓库完整文档。

**方式一：uv / uvx（推荐）。** 已安装 [uv](https://docs.astral.sh/uv/) 时免安装直接运行，或装成常驻命令：

```bash
uvx phycode version          # 免安装直接运行
uv tool install phycode      # 或安装为常驻命令
phycode version
uv tool upgrade phycode      # 升级；卸载用 uv tool uninstall phycode
```

**方式二：pip + 虚拟环境。**

```bash
python -m venv .venv
.venv\Scripts\activate       # Windows；macOS/Linux 用 source .venv/bin/activate
pip install phycode
phycode version
pip install -U phycode       # 升级到最新版
```

**方式三：离线 wheel。** 从 GitHub Releases（<https://github.com/JianingZhangnan/AISE/releases>）下载 wheel（每个 Release 说明附 SHA256 校验值），然后 `pip install ./phycode-<version>-py3-none-any.whl`。

**方式四：源码开发模式。**

```bash
git clone https://github.com/JianingZhangnan/AISE.git
cd AISE
uv sync --dev
uv run phycode version
```

安装后请按「指向真实供应商」一节在目标机器上安全配置你自己的 base URL 与 API key（key 存入操作系统钥匙串，不落明文文件）；不配置 key 时所有命令仍可在离线 EchoLLM 模式下确定性运行。

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

在真实终端中，输入 `/` 会立即展示全部候选；继续输入会实时过滤。候选同时显示命令用法、参数占位和说明。使用 ↑/↓ 选择、Tab 补全、Enter 执行、Esc 关闭菜单；Ctrl+C 取消当前输入，Ctrl+D 在空输入时退出。输入 `/model ` 后会用当前安全凭据加载真实模型候选，加载失败时仍可手工输入模型 ID。`/key` 始终进入独立隐藏输入，不显示或补全 key。非 TTY、重定向输入和测试管道自动使用整行输入回退。

不确定模型名时，先用 `phycode models`（或 chat 内 `/models`）列出你的 token 实际可用的 model id，再 `/model <exact-id>`——很多聚合网关（new-api/one-api 等）会因分组/渠道没有该模型而报 `model_not_found`，用列出的准确 id 即可。

**脚本化（非交互）等价写法**（配置写入当前目录 `phycode.toml`，key 存入操作系统钥匙串）：

```bash
phycode config set llm base_url "https://your-endpoint/v1"
phycode config set llm model "your-model"
phycode keys set openai-compatible          # 交互式录入 API key（隐藏输入，不回显）
```

说明：`chat`（交互模式）会在执行写文件/跑命令等风险动作前请求你确认；`run`（非交互模式）下这类风险动作按策略返回“需审批”而不会自动执行，因此完整能力请用 `chat` 测试。key 只存钥匙串，绝不写入 `phycode.toml`、trace 或日志。

## 内置工具

`file.read/list/inspect/write/edit`、`image.inspect`（配置视觉模型后）、`calculator.calculate`、`search.grep/glob`、`web.search/fetch`、`shell.run`、`test.run`、`workspace.status`、`memory.read/write`、`config.read/write`、`keys.status`。每个工具声明风险等级（safe/risky/dangerous），并在执行前经过策略决策（allow/ask/deny）。

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

```bash
uv build       # 生成 dist/phycode-<version>-py3-none-any.whl 与对应 sdist
uv publish     # 发布到 PyPI
```

正式发布由 GitHub Actions 完成：推送 `v*` tag 触发 `.github/workflows/release.yml`，在 CI 中跑完整测试、`uv build` 后通过 **PyPI Trusted Publishing（OIDC）**执行 `uv publish`——仓库与本机都不保存任何长期 PyPI token。每个版本同时创建 GitHub Release，附 wheel/sdist 与 SHA256。`.gitlab-ci.yml` 的 `build-package` job 在 GitLab（NJU Git 镜像）侧产出同样的构建产物。

## 目录结构

```text
AISE/
├── src/phycode/            # harness 内核：agent 主循环、策略引擎、反馈分类、
│   │                       #   trace/记忆/上下文、凭据、LLM 适配层、CLI
│   └── tools/              # 内置工具执行器（file/shell/search/web/process/state 等）
├── tests/                  # mock/stub LLM 驱动的确定性测试（不依赖网络与真实 key）
├── integrations/prbench/   # 官方 evaluator 适配器、运行脚本与评测文档
├── docs/                   # 过程文档（superpowers 计划/发现、可选评测说明）
├── course_resource/        # 课程作业要求原文
├── SPEC.md                 # 设计规约（含领域与机制设计、凭据威胁模型）
├── PLAN.md                 # 实现计划（逐 task 完成标记与 commit hash）
├── SPEC_PROCESS.md         # 规约过程记录（brainstorming 迭代、冷启动验证、评审修订）
├── AGENT_LOG.md            # 按时间顺序的 agent 过程日志
├── REFLECTION.md           # 学生个人反思报告
├── .github/workflows/      # GitHub Actions：unit-test（push/PR）与 release（tag）
├── .gitlab-ci.yml          # GitLab CI：unit-test 与 build-package job
└── pyproject.toml          # uv/hatchling 打包配置（PyPI 包 phycode）
```

## 已知限制

- **平台**：Windows 与 Linux 经过实际验证（CI 在 Ubuntu 运行全量测试，Windows 为主要开发平台，终端渲染兼容 GBK 控制台）；macOS 未实测。需要 Python >= 3.11。
- **凭据存储**：依赖操作系统钥匙串（Windows Credential Manager / macOS Keychain / Linux Secret Service）。无钥匙串后端的 headless Linux 上 `keys set` 不可用，`run`/`chat` 会按"未配置凭据"回退到离线 EchoLLM；带主密码的加密文件后备尚未实现（见 SPEC 未决事项）。
- **供应商协议**：真实模型交互仅支持 OpenAI-compatible Chat Completions 且需支持 `tools`/`tool_calls`；不兼容该协议的供应商不在支持范围内。
- **可选评测集成**：`integrations/` 下的官方 evaluator 对接与 `docs/gaia-eval.md` 描述的研究型评测属于可选能力，不在默认测试内；它们额外要求 Docker daemon、PowerShell（`pwsh` 或 Windows PowerShell）或自行下载的外部数据集，详见各自目录内的说明文档。
- **形态**：纯 CLI，无 WebUI 与线上部署；trace JSONL 的结构化格式为未来可视化预留。

## 第三方依赖与许可证

本项目自身以 **MIT 许可证**发布（见根目录 `LICENSE`）。运行时直接依赖的第三方库及其许可证（以各包发行版元数据为准）：

| 依赖 | 用途 | 许可证 |
| --- | --- | --- |
| typer | CLI 框架 | MIT |
| rich | 终端渲染 | MIT |
| pydantic | 数据模型与校验 | MIT |
| keyring | 操作系统钥匙串访问 | MIT |
| httpx | HTTP 客户端 | BSD-3-Clause |
| openai | OpenAI-compatible Chat Completions 传输客户端（仅单次补全 API，非 agent runner） | Apache-2.0 |
| prompt-toolkit | 交互式斜杠命令补全 | BSD-3-Clause |
| ddgs | web.search 工具 | MIT |
| Pillow | image.inspect 工具 | HPND |
| pypdf | file.inspect 的 PDF 支持 | BSD-3-Clause |
| openpyxl / xlrd | file.inspect 的表格支持 | MIT / BSD |

可选 `gaia` extra：faster-whisper（MIT）、pyarrow（Apache-2.0）。开发依赖：pytest / pytest-cov（MIT）。构建与工具链：uv（Apache-2.0 OR MIT）、hatchling（MIT）、pyright（MIT）。`.local/superpowers` 为指向上游 [obra/superpowers](https://github.com/obra/superpowers) 的 git submodule，官方 evaluator（`integrations/prbench/`）仅通过 adapter patch 引用上游仓库，两者许可证以各自上游为准；本仓库不包含其源码副本。
