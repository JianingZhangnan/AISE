# PhyCode

PhyCode 是一个 CLI 优先的 coding agent harness，核心是**策略感知工具运行时（Policy-Aware Tool Runtime）**：把「调用 LLM → 解析动作 → 策略裁决 → 执行工具 → 回灌反馈 → 停机判断」整条链路实现为确定性代码，移除真实 LLM 后核心机制仍可由单元测试验证。

## 核心特性

- **自研 agent 主循环**：上下文组织、OpenAI-compatible tool calls 解析、工具分发、反馈回灌、停机控制全部自实现，不依赖任何现成 agent 框架的高层循环。
- **策略护栏**：每次工具调用先经确定性策略裁决（allow / ask / deny）；危险 shell 命令、路径逃逸、凭据文件读取直接拦截，风险动作暂停等待人工审批。
- **反馈闭环**：测试与命令结果被分类为结构化反馈回灌给模型，驱动下一步自我修正。
- **凭据安全**：API key 存操作系统钥匙串，录入走隐藏输入；trace、日志、LLM 上下文统一脱敏，key 永不落明文文件。
- **离线可用**：不配置 key 时回退确定性 EchoLLM；`phycode demo` 系列演示无需网络即可复现护栏拦截、审批与反馈闭环。

## 安装

环境要求：Python >= 3.11。Windows 与 Linux 已验证，macOS 未实测。

### 方式一：uv / uvx（推荐）

已安装 [uv](https://docs.astral.sh/uv/) 的话，免安装直接运行：

```bash
uvx phycode version
uvx phycode chat
```

还没有 uv，先用官方脚本安装：

```bash
# Windows（PowerShell）
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

想把 `phycode` 装成常驻命令：

```bash
uv tool install phycode
phycode version
# 之后升级 / 卸载：
uv tool upgrade phycode
uv tool uninstall phycode
```

### 方式二：pip + 虚拟环境

```bash
python -m venv .venv
# Windows：
.venv\Scripts\activate
# macOS / Linux：
source .venv/bin/activate

pip install phycode
phycode version
# 升级到最新版：
pip install -U phycode
```

### 方式三：离线 wheel

从 [GitHub Releases](https://github.com/JianingZhangnan/AISE/releases) 下载 `phycode-<version>-py3-none-any.whl`（每个 Release 说明里附 SHA256 校验值），然后：

```bash
pip install ./phycode-<version>-py3-none-any.whl
```

### 方式四：源码开发模式

```bash
git clone https://github.com/JianingZhangnan/AISE.git
cd AISE
uv sync --dev
uv run phycode version
uv run pytest        # 确定性测试套件，不需要网络与 API key
```

## 快速开始

```bash
phycode version              # 查看版本
phycode tools list           # 列出内置工具及风险等级
phycode demo guardrail       # 演示：危险命令被确定性拦截（无需 key）
phycode demo feedback        # 演示：失败反馈改变 agent 的下一步动作（无需 key）
phycode run "hello"          # 一次性任务（未配置 key 时走离线 EchoLLM）
phycode chat                 # 交互式会话（支持 / 斜杠命令实时补全）
```

## 配置真实供应商

进入 `chat` 后用斜杠命令配置（key 隐藏输入、存入操作系统钥匙串）：

```text
phycode chat
phycode: /url https://your-endpoint/v1     # 你的 OpenAI-compatible 接口
phycode: /model your-model                 # 你的模型名
phycode: /key                              # 隐藏输入录入 API key
phycode: /status                           # 确认已配置（不回显明文）
```

或脚本化等价写法：

```bash
phycode config set llm base_url "https://your-endpoint/v1"
phycode config set llm model "your-model"
phycode keys set openai-compatible      # 交互式隐藏输入
phycode keys status openai-compatible   # 查看状态（不回显明文）
phycode keys clear openai-compatible    # 清除
```

要求供应商支持 OpenAI-compatible Chat Completions 的 `tools` / `tool_calls`。不确定模型名时，先用 `phycode models`（或 chat 内 `/models`）列出你的 token 实际可用的模型 ID。

## 安全边界

- key 只存操作系统钥匙串（Windows Credential Manager / macOS Keychain / Linux Secret Service），绝不写入配置文件、trace 或日志；状态查询不回显明文。
- 工具调用被限制在工作区内：路径逃逸、凭据文件读取、危险 shell 命令由确定性代码拒绝，风险写入需审批。
- `chat` 交互模式下风险动作会逐条征求确认；`run` 非交互模式按策略返回「需审批」而不会自动执行。

## 链接

- 源码与完整文档：<https://github.com/JianingZhangnan/AISE>
- Releases（wheel/sdist + SHA256）：<https://github.com/JianingZhangnan/AISE/releases>
- 许可证：MIT
