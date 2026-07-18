# PhyCode

PhyCode 是面向 AI4SE 期末项目的 CLI 优先 coding agent harness，核心是**策略感知工具运行时（Policy-Aware Tool Runtime）**：自研 agent 主循环、可注入 mock/stub 的 LLM 抽象层、确定性治理护栏、反馈闭环、记忆/上下文管理与凭据处理。核心机制在移除真实 LLM 后仍可由确定性单元测试验证。

> 状态：Task 1–20 的实现与确定性验证已完成；PRBench 官方真实验收必须另行使用真实模型与 Docker 执行，不能由默认测试结果代替。

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

## GAIA evaluation

Use the general-assistant profile for research questions. It includes public web
search/fetch tools, attachment inspection, a bounded tool budget, and a final
answer format compatible with GAIA-style short answers:

```bash
uv run phycode run --profile gaia "your question"
```

The official GAIA repository is gated on Hugging Face. After accepting its data
terms and downloading it locally, install the optional Parquet and local-audio dependencies and run
the isolated evaluator without copying credentials into the repository:

```powershell
uv sync --extra gaia
uv run python -m phycode.gaia_eval `
  --metadata D:\path\to\GAIA\2023\validation\metadata.parquet `
  --dataset-root D:\path\to\GAIA `
  --credentials D:\path\to\NewTextDocument.txt `
  --limit 10 --audio-model base.en `
  --output .phycode\gaia-results.jsonl --resume
```

Use one or more `--task-id` options for targeted reruns. Audio attachments are
transcribed locally with faster-whisper; the model is downloaded to the local
Hugging Face cache on first use. Pass an empty `--audio-model` value to disable it.

For image attachments, set a vision-capable model in `phycode.toml` or pass
`--vision-model` to the evaluator. If image requests use a different gateway,
also pass its credential block with `--vision-endpoint-index`:

```toml
[llm]
vision_model = "Qwen2.5-VL-72B-Instruct"
```

## PRBench 真实模型与官方 evaluator

PRBench profile 是本项目的最小纵向集成：模型只能调用结构化
`process.run(argv)`，该工具始终以 `shell=False` 运行人工允许的绝对 executable；
`file.write`、`file.edit` 与 `process.run` 都必须匹配本次运行的一次性精确审批。
模型的 final 文本不代表成功，只有 execution journal 证明脚本成功运行、所有
expected outputs 存在且 artifact verifier 通过时，runner 才返回 `completed`。

三层验证不能混为一谈：

1. **确定性测试**：默认 `uv run pytest` 使用 mock/stub LLM 和真实临时子进程验证
   policy、审批、provenance、verifier 与停机机制；不访问网络，也不调用真实模型。
2. **真实模型 runner smoke**：在隔离公开 task workspace 中直接运行
   `phycode prbench run`，验证真实 OpenAI-compatible 模型能自主写脚本、调用
   `process.run` 并得到 `completed`；它不等于官方评分。
3. **官方 Docker evaluator**：把固定 adapter 应用到官方 evaluator，由白色
   PhyCode 完成任务，再由官方绿色 grader 生成报告。Docker daemon 必须已运行，
   smoke 脚本把同一组三项 provider 值临时映射给官方 OpenCode 绿色 agent；映射
   只存在于 evaluator 子进程期间，随后精确恢复或真正删除。

直接 runner 的命令形态如下；三个 provider 值只从当前进程环境或安全凭据后端
取得，不要写入仓库、命令参数或 `.env`：

```powershell
uv run phycode prbench run `
  --workspace D:\path\to\public-workspace `
  --contract D:\path\to\public-workspace\task_contract.json `
  --approvals D:\path\to\public-workspace\phycode-approvals.json
```

官方 smoke 固定到
`HET-AGI/PRBench-Eval-Handson@3e5bee4545cad2138832f06302e9c98bd81f5216`。
先在本仓库执行 `uv build`，再把三个 `PHYCODE_*` 值设置到当前 PowerShell
进程，最后对一份干净且位于该 commit 的官方 clone 运行：

```powershell
.\integrations\prbench\run_public_smoke.ps1 `
  -EvaluatorRoot D:\path\to\PRBench-Eval-Handson `
  -WheelPath D:\path\to\AISE\dist\phycode-0.1.0-py3-none-any.whl `
  -TaskIds aaatest_helloworld,bbbtest_alphabet
```

脚本不接收 key 文件路径，不创建 `.env`，也不回显 provider 值。白色 agent 使用
`PHYCODE_*`；绿色 model-judge 通过临时 `OPENCODE_API_KEY`、
`OPENCODE_BASE_URL` 和 `OPENCODE_MODEL=openai/<PHYCODE_MODEL>` 使用相同 endpoint，
官方 resolver 会把 custom URL 的模型前缀转为 `openai_compat/`。从 adapter 提交
`6f5d75d` 延续的隔离机制会把这些 green-only 值排除在共享容器 `Config.Env`
之外；白色阶段的容器进程
看不到它们，直到白色 runner 结束后，绿色 grading child 才通过 name-only Docker
环境参数取得临时值。custom provider registry 只通过 child-only
`OPENCODE_CONFIG_CONTENT` 注入，key 使用 `{env:OPENAI_API_KEY}` 占位符，不进入
JSON、argv 或持久配置；兼容 provider 包则在无凭据的 setup 阶段预装。宿主脚本的
`finally` 会精确恢复调用前已有的 `OPENCODE_*`；原本不存在的变量通过
`Remove-Item Env:` 真正删除，不留下空变量。

初始审批 JSON **只**包含一个目标 reproduction 文件的 `file.write`：
`reproduction/hello.py` 或 `reproduction/alphabet.py`。脚本生成前不会预授权
`process.run`，smoke 脚本本身也不会计算 hash 或自动批准执行。官方命令传入
`--approval-wait-seconds 900`；模型写完脚本并首次请求执行时，runner 会原子写入
workspace 内的 `.phycode/prbench/approval-request.json` 并暂停等待。
在该固定 evaluator 中，active workspace 通常位于
`<EvaluatorRoot>\data\tasks\<TaskId>\workspace`；以 launcher 日志公布的实际路径为
准。运行中应修改这里的 `phycode-approvals.json`，而不是脚本最初创建并已被 adapter
复制的临时 manifest。

此时主 agent 必须人工完成以下门禁：

1. 读取待执行 reproduction 脚本，确认它只实现公开任务且没有越界行为。
2. 读取 `approval-request.json`，逐项核对规范化 `argv`、`cwd`、脚本路径和
   `script_sha256`；独立计算脚本 SHA-256 并确认与请求一致。
3. 把完全相同的 `argv`、`cwd` 和 `script_sha256` 作为一次性 `process.run` grant
   写入该 active workspace 的 `phycode-approvals.json`。不得批准不同参数，不得
   使用通配符，也不得让外部脚本替 agent 运行 reproduction。

清单刷新后 runner 会再次校验脚本内容；等待期间脚本变化、hash 不匹配、畸形
清单、重复消费或 900 秒超时都会 fail closed。CSV 只能由已审核脚本执行生成，
不会从 `expected_files` 推导授权，也不允许直接 `file.write` CSV。

固定 upstream 的 `pyproject.toml` 对 `a2a-sdk` 只有下界；2026-07-18 在 fresh
环境执行普通解析会选到 `a2a-sdk 1.1.1`，与该 commit 的 import API 不兼容。
smoke 脚本因此使用 uv 临时 exact overlay
`a2a-sdk[http-server]==0.3.8` 启动官方 `main.py`，不修改 upstream
`pyproject.toml`、不生成或提交上游 lockfile，也不引入 pip 流程。

权威 ground truth 边界由官方生命周期提供：白色 task-solving agent 运行时，
`_ground_truth` 不挂载、不复制且不在 allowlist；白色运行结束并清除 provider
环境后，官方绿色 grader 才把评分材料复制进 workspace。路径拒绝仅是纵深防御，
不能替代此隔离。官方真实验收需要逐项确认 white runner 为 `completed`、公开
expected outputs 与 evaluator 报告存在，并扫描 trace、journal、result 确认不含
key/URL。该真实 API / Docker 验收不属于默认 `uv run pytest`，也不会在 CI 中自动
执行。

## 机制演示（mock LLM，确定性）

```bash
uv run phycode demo guardrail    # 危险 shell 命令被策略拒绝且从未执行
uv run phycode demo policy       # 风险编辑暂停并要求审批
uv run phycode demo feedback     # 失败的测试反馈改变 agent 的下一步动作，修复后测试通过
```

`demo feedback` 通过真实 agent 循环 + `ReactiveLLM` 复现完整闭环：`test.run` 失败 → 因反馈改选 `file.edit` 修复 → 重跑测试通过 → 结束。

`run` / `chat` 走同一个 agent 主循环：若已通过 `keys set` 配置了供应商 key，则使用 OpenAI-compatible 适配器进行真实交互；否则回退到离线 `EchoLLM`，保证无 key 时也能确定性运行。`chat` 交互模式下风险动作会暂停征求审批。

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
