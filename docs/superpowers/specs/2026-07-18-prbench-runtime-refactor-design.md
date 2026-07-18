# PRBench 运行时重构与官方最小任务纵向接入设计

## 1. 背景与问题

PhyCode 已完成 PRBench 公开能力边界调研，但上一条初始实现分支把 `_ground_truth` 保密边界过多地放进了字符串级 shell 匹配器。该实现从简单路径检查演化为跨 POSIX/Windows、引号、转义、glob、环境变量和 `env` option 的自制 lexer/state machine，仍无法覆盖嵌套 shell、变量展开、解释器内字符串拼接和符号链接别名。

真实 `deepseek-v4-pro` 测试还暴露出两个独立问题：相同成功动作被全局累计而过早触发停机；模型返回 `assistant_final` 时，系统没有验证脚本执行和任务产物，因而可能“诚实地报告未完成”或“错误地声称完成”，但运行结果仍被标为 `final`。

本重构不继续修补旧 parser。实现从 PRBench 公开边界设计提交 `f2817ab` 建立新分支，旧分支 `codex/prbench-initial-tests` 保留为审计证据。

## 2. 目标

本阶段交付一个完整、可验证的 PRBench 最小纵向切片：

1. 集中定义 PRBench profile，不再让 CLI、policy 和工具构造各自维护 profile 状态。
2. 用结构化进程执行替代模型可控的自由文本 shell。
3. 把 ground truth 的权威保密边界放回 evaluator/container 生命周期隔离。
4. 让风险动作经过显式、可审计、运行级的一次性审批。
5. 让停机判断感知真实进展，而不是全局累计重复动作。
6. 让任务完成由 artifact contract 和执行 provenance 验证，而不是由模型的 final 文本决定。
7. 实现固定版本官方 evaluator adapter，并用真实模型跑通两个公开最小任务。

## 3. 非目标

- 本阶段不运行或调优完整任务 `task_white_1993`。
- 本阶段不实现通用 POSIX/PowerShell/cmd 解析器。
- 本阶段不承诺覆盖 PRBench 未公开的 29 个任务，也不声称取得 holdout 成绩。
- 本阶段不重写现有 GAIA profile 或通用 coding loop；只做保持兼容所必需的接口调整。
- 本阶段不引入 LangChain、AutoGen、CrewAI、LlamaIndex agent 或其他高层 agent runner。

## 4. 方案比较与选择

### 4.1 继续修补旧 policy parser

优点是表面改动小；缺点是无法从字符串静态分析证明任意解释器不会访问隐藏文件，测试会继续围绕 shell 拼写膨胀。拒绝此方案。

### 4.2 从干净基线重建纵向切片

保留已有 AgentLoop、工具 registry、LLM adapter 和 trace 基础设施，重建 PRBench profile、结构化执行、审批、验收和 evaluator adapter。该方案能直接回答“真实任务是否完成”，同时将安全责任放回正确层级。采用此方案。

### 4.3 重写整个 harness

可以获得最整齐的接口，但会扩大到 GAIA、CLI、凭据和所有工具，超出本阶段目标，也破坏已有稳定测试。拒绝此方案。

## 5. 权威安全边界

### 5.1 Ground truth 生命周期隔离

白色 task-solving agent 运行期间，workspace 中不得存在 `_ground_truth`：不挂载、不复制，也不通过 allowlist 暴露。白色进程结束并释放其凭据后，evaluator 才能把评分材料交给绿色 grader。

路径 policy 仍会拒绝显式 `_ground_truth` 分量，作为可解释的纵深防御和生命周期错误告警；它不再声称可以解析任意 shell 或解释器语义。

### 5.2 结构化进程执行

PRBench profile 不公开自由文本 `shell.run`。新增模型工具：

```python
process.run(
    argv: list[str],
    cwd: str = ".",
    timeout: int = 30,
) -> ToolResult
```

执行器必须使用 `subprocess.run(argv, shell=False, ...)`。`argv[0]` 必须是人工批准并由 runner 注入的规范化绝对可执行文件路径；不得仅按 basename 放行同名程序。`cwd` 必须解析到 workspace 内。参数不得包含 NUL，timeout 必须有上限。通用 coding profile 的旧 `shell.run` 暂时保持兼容，但 PRBench registry 不暴露它。

子进程不得继承父进程的完整环境。executor 只传递 Python 和操作系统启动所需的固定最小环境变量集合，明确不传递任何 provider、key、token、credential 或代理变量。stdout/stderr 在构造 `ToolResult` 前经过统一文本脱敏。真实运行仍要求主 agent 在批准前阅读待执行脚本；结构化执行和环境清理不冒充 OS/container sandbox。

`test.run` 只执行项目配置中的固定命令，不接受模型覆盖 command。PRBench 最小任务不依赖 `test.run`。

### 5.3 路径可见性

新增通用 `PathVisibilityPolicy`，所有 file/search 候选在读取或展示前都执行：

1. 把相对路径绑定到 workspace；
2. `resolve()` 处理符号链接；
3. 检查解析后路径仍在 workspace；
4. 检查词法路径和解析后路径均不包含不可见分量；
5. 递归搜索在进入目录前剪枝，并在读取每个文件前重复检查。

工具层只依赖 visibility predicate，不反向导入 PRBench policy 常量。

## 6. 组件设计

### 6.1 `ProfileSpec`

新增不可变 profile 描述对象，至少包含：

- `profile`；
- `tool_names`；
- `system_prompt`；
- `max_context_chars`；
- `max_tool_calls`；
- `hidden_path_components`。

`build_agent`、registry 和 `PolicyContext` 必须从同一个 `ProfileSpec` 构造，不能分别接收可能不一致的 profile 配置。PRBench prompt 明确要求运行生成脚本、检查产物，并说明 final 会经过确定性验收。

### 6.2 `ApprovalGateway`

风险工具返回规范化 `ApprovalRequest`，包含 call ID、工具名、解析后的 cwd、目标路径或 argv、policy reason 和请求摘要。

真实测试使用由主 agent 在每次运行前人工审定的运行级审批清单。清单中的每一项绑定完整的规范化路径/argv/cwd，只允许一次；未匹配或已消费的审批不得执行。Windows 路径按平台语义做大小写规范化。参数缺失、未知字段、非法 timeout 或其他不能执行的请求不得匹配或消费 grant。审批清单不得包含 key、URL 或模型可写入的通配表达式。

真实 smoke 不得在 reproduction 脚本生成前预授权 `process.run`。runner 在没有匹配执行 grant 时，把规范化 argv/cwd 与待执行脚本的 SHA-256 原子写入 `.phycode/prbench/approval-request.json`，并在显式启用的有限等待窗口内重新读取审批清单。主 agent 必须先读取脚本，再写入包含同一脚本 SHA-256 的一次性执行 grant；脚本内容在等待期间变化、清单畸形、超时或重复消费均 fail closed。默认测试与普通非交互运行不等待。

本阶段官方最小任务的审批只允许：

- 在当前临时 workspace 内写入任务明确要求的 reproduction 源文件和分析文件；
- 运行当前 workspace 内已写入的 Python reproduction 脚本；
- 不允许访问凭据、workspace 外路径或任何 `_ground_truth` 路径。

### 6.3 `ExecutionJournal`

每次 `process.run` 记录：

- call ID、规范化 argv 和 cwd；
- 被执行脚本的执行前 SHA-256；
- 开始/结束时间、退出码和结果状态；
- 执行前后任务关注 artifact 的 SHA-256 差异。

Journal 进入脱敏 trace，不记录环境变量值。CSV provenance 只在一次退出码为 0 的脚本执行后，目标 CSV 新增或哈希发生变化时成立。

### 6.4 `TaskContract` 与 `ArtifactVerifier`

`TaskContract` 从官方 `task.yaml` 的公开字段构造，只读取 instruction、paper、input files 和 expected outputs；不得读取 metadata、reference data 或 reference code。它还可以接收一个显式的公开 `ArtifactConstraint` 清单。该清单是 runner 的普通输入，不由产品代码按 task ID 分支生成。

两个公开 smoke task 的 adapter 测试在 `integrations/prbench/public_contracts/` 保存人工转录自公开 `instruction.md` 的约束文件，用于检查 CSV header、行数和指令已明示的值。约束文件不得包含 metadata、reference data 或从 grader 结果反推的数值。其他任务没有约束文件时，verifier 只检查 expected outputs、非空 CSV 和 execution provenance，数值准确度仍由官方绿色 grader 判断。

`ArtifactVerifier` 在模型请求 final、工具预算耗尽以及 runner 退出前执行；PRBench runner 还会显式开启成功工具结果后的即时验收。它至少检查：

- 所有必需文件存在且位于 workspace；
- 必需 Python 脚本至少成功执行一次；
- CSV 具有 execution journal 支持的 provenance；
- 对两个公开最小任务，header、数据行数和任务指令中声明的值成立。

final、预算耗尽或 runner 退出时，验收失败生成结构化 `artifact_verification_failed` 反馈，列出缺失或无 provenance 的路径。成功工具结果后的即时验收只有在 verifier 返回成功时才直接结束；非致命失败保持静默，避免把每次正常的中间写入或读取都变成反馈噪声，verifier 安全异常仍立即 fail closed。拒绝、失败和超时工具不触发即时成功判定。只有 verifier 返回成功，运行结果才是 `completed`。

### 6.5 `StopController`

重复动作检测从“整个 run 中相同结果累计次数”改为“连续无进展重复”。进展指纹至少包含 workspace 关注文件的路径、大小和 SHA-256，以及最近一次成功执行记录。

发生以下任一变化时清零重复计数：

- 创建、删除或修改 workspace artifact；
- 成功执行新的脚本哈希；
- verifier 的失败项减少。

连续三次相同动作且进展指纹不变时，返回 `repeated_no_progress`。该状态不得被包装为成功 final。

### 6.6 `PRBenchRunner`

新增 `python -m phycode.prbench_eval run` 和 `phycode prbench run`：

- 接受 task workspace、variant、模型配置和运行级审批文件；
- 只从环境/现有安全凭据后端取得 key；
- 生成 `.phycode/prbench/` 下的脱敏状态、journal 和 trace；
- 成功时退出 0，provider、审批、policy、预算或 artifact 验收失败时使用不同非零退出码；
- 输出机器可读的 `run_result.json`，不得包含凭据。

本阶段只实现 `baseline` variant；`research`、resume、完整数值审计留在后续机制切片。

### 6.7 官方 evaluator adapter

`integrations/prbench/` 保存针对
`HET-AGI/PRBench-Eval-Handson@3e5bee4545cad2138832f06302e9c98bd81f5216`
的版本化 patch 和应用脚本：

1. commit 不匹配时立即失败；
2. 注册 `phycode` white-agent 类型；
3. 安装当前 PhyCode wheel；
4. 只把所需 provider endpoint/key/model 环境变量传给 runner；
5. 白色 agent 结束后再沿用官方绿色 grader 流程；
6. 导出脱敏 trace 和 runner result。

adapter 不修改官方 grading rubric，不把 ground truth 传给 PhyCode runner。

当白色 agent 为 PhyCode、绿色 grader 使用另一 provider 时，绿色凭据不得写入共享容器 `Config.Env`，也不得在白色阶段进入任何容器进程。绿色 agent 只能在白色 runner 退出并释放其凭据后，为 grading 子进程构造临时 child environment，并用 name-only `docker exec -e NAME` 注入；成功、失败和超时路径都必须清除临时映射。宿主 smoke 脚本新增的 provider alias 必须在 `finally` 中恢复原值或真正删除，而不是留下空值变量。

## 7. 运行数据流

1. evaluator 创建隔离 workspace，只复制 instruction、paper 和显式 input files。
2. runner 读取公开任务字段，构造 `TaskContract`、`ProfileSpec` 和审批 gateway。
3. AgentLoop 调用真实 LLM，接收结构化工具请求。
4. visibility/policy 决策先执行；文件写入消耗一次性审批，进程执行在主 agent 审阅脚本并按 SHA-256 批准后才继续。
5. 工具结果和结构化反馈进入 session/trace；进程执行同时更新 journal。PRBench 对成功工具结果立即运行 verifier，contract 与 provenance 均通过时直接结束，不再请求下一轮模型输出。
6. 未被即时验收结束时，模型请求 final 仍运行 verifier：失败则回灌，成功才结束。
7. 白色 runner 退出、凭据释放后，官方 evaluator 才复制 ground truth，并仅向绿色 grading 子进程临时注入 green provider。

## 8. 错误与停止语义

运行结果必须区分：

- `completed`：artifact contract 通过；
- `approval_required`：动作未获人工审批；
- `policy_blocked`：违反确定性边界；
- `provider_error`：LLM provider 失败；
- `process_failed`：必需进程执行失败且预算内未恢复；
- `artifact_verification_failed`：退出前产物仍不合格；
- `repeated_no_progress`：连续无进展重复；
- `tool_budget_exhausted`：预算耗尽。

任何非 `completed` 状态均不得因为存在 assistant final 文本而退出 0。

## 9. 测试策略

### 9.1 确定性测试

使用 Scripted/Reactive LLM 验证机制，但不把它们表述为真实能力测试：

- profile 单一来源和精确工具集合；
- `process.run` 的 argv、cwd、timeout、`shell=False`；
- 一次性审批匹配、消费和拒绝；
- visibility 对路径逃逸、文件/目录 symlink、搜索递归的处理；
- execution journal 和 CSV provenance；
- verifier 失败反馈后继续、成功后完成；
- 连续无进展停机与有进展重置。

### 9.2 真实执行集成测试

测试必须运行真实临时 workspace 和 Python 子进程，验证生成脚本实际创建 CSV。安全测试断言 sentinel 不会通过 file/search/process/trace 泄漏；不再断言几十种 shell 拼写由 matcher 识别。

### 9.3 真实模型与官方公开任务

凭据仅从 `D:\BaiduSyncdisk\NewTextDocument.txt` 读入当前测试子进程内存，不回显、不写入配置或 trace。使用 `deepseek-v4-pro` 依次运行：

- `aaatest_helloworld`；
- `bbbtest_alphabet`。

两项必须由 AgentLoop 自己写入并执行脚本，runner 结束前 artifact verifier 通过。外部测试脚本不得替 agent 补跑生成脚本。真实测试报告记录模型、commit、耗时、工具调用类型、停止状态和脱敏 artifact 摘要，不记录 endpoint/key。

## 10. 删除与迁移

新分支不移植旧分支的 shell lexer/state machine，也不移植围绕 env option、引号、续行、glob 和 assignment 的 parser-specific 测试。

保留的需求迁移为：

- 显式 `_ground_truth` path 分量拒绝；
- 搜索 visibility 与 symlink 集成测试；
- PRBench 精确工具/profile 契约；
- CSV 直接 file write 拒绝和 execution provenance；
- coding/GAIA 非回归测试。

## 11. 验收标准

1. `policy.py` 不包含自制 shell lexer/state machine，职责仍是 workspace、凭据、危险动作和审批策略。
2. PRBench registry 不包含模型可控的自由文本 `shell.run`。
3. 两个官方公开最小任务在真实 `deepseek-v4-pro` 下由 AgentLoop 自主执行并返回 `completed`。
4. 结束时所有 expected outputs 存在，CSV provenance 和内容约束通过。
5. evaluator adapter 对固定 commit 可应用，对其他 commit fail closed。
6. 白色运行 trace、memory、journal、result 和 Git 中无 key/URL 明文。
7. `uv run pytest` 全部通过，`uvx pyright` 为 0 errors。
8. 代码审查确认没有用另一套复杂 parser、按 task ID 分支的 solver 逻辑或隐藏 reference 数值替代旧实现；公开 smoke instruction 的声明式验收约束不属于 solver 逻辑。

## 12. 风险与缓解

- **真实模型仍可能不调用执行工具**：verifier 回灌明确缺失项，stop controller 只在连续无进展时停机。
- **人工审批清单过宽**：审批绑定完整规范化参数并一次性消费；运行前由主 agent 审核，意外调用拒绝。
- **官方 evaluator 环境差异**：adapter 固定 commit，最小任务先在隔离临时目录测试，再在 Docker 中验证。
- **Docker daemon 不可用**：实现和本地真实 fixture 可先完成，但最终验收必须启动 Docker 后运行官方流程；不可把直接 fixture 测试冒充官方 evaluator 成绩。
- **接口改动影响 coding/GAIA**：保留旧 profile 工具行为并运行全量非回归测试。
