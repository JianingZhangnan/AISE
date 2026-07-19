# PRBench 完整公开任务纵向接入设计

## 1. 背景与目标

PhyCode 已在固定官方 evaluator commit
`3e5bee4545cad2138832f06302e9c98bd81f5216` 上用真实模型跑通
`aaatest_helloworld` 与 `bbbtest_alphabet` 两个公开最小 smoke。两项 smoke 只验证
adapter、容器隔离、动态审批、execution provenance 和绿色 grader 生命周期，不能
代表完整论文复现任务能力。

本阶段目标是使用真实大模型 API 跑通公开完整任务 `task_white_1993`。成功标准严格为：

1. PhyCode 白色 runner 的 `run_result.json` 为 `status=completed`；
2. 官方绿色 grader 生成可解析且不含 `error` 的有效报告；
3. 不设置最低分数门槛，不根据分数挑选最好轨迹。

最多进行三次已进入白色模型调用阶段的正式尝试。Docker、adapter、依赖安装或其他在
首次模型响应前发生的基础设施失败不计入三次；每次正式尝试使用全新 evaluator
workspace，不复用上一轮生成物或 agent 状态。

## 2. 范围与非目标

### 2.1 范围

- 为声明式公开任务契约增加“需要执行的入口脚本”语义。
- 为 `task_white_1993` 提供只来源于公开 `task.yaml` / `instruction.md` 的完整 contract。
- 用紧凑任务简报替代把 81 KB 论文直接拼入首轮、随后被上下文预算裁剪的做法。
- 让完整任务显式使用 24,000 字上下文预算和 50 次总工具调用预算。
- 提供固定 evaluator 的完整公开任务运行入口、精确静态文件审批和动态进程审批流程。
- 在最多三次真实 API 尝试内取得 `completed` 与有效官方 grader report。

### 2.2 非目标

- 不读取、复制或编码 `metadata.md`、参考实现、参考 CSV 或 grader 隐藏信息。
- 不在产品代码、prompt 或 contract 中加入 DMRG 专用求解器或隐藏答案启发式。
- 不实现旧公开边界设计中的完整 `research` variant、resume、token 成本对比或三次
  baseline/final 统计实验。
- 不把本次单个公开任务结果表述为 30 题 holdout 能力或总体 PRBench 成绩。
- 不把评测 workspace、模型生成物、trace、journal、报告或构建产物提交到 Git。
- 不在未经用户后续明确授权时合并或推送到 `main`。

## 3. 方案选择

### 3.1 采用：通用完整任务纵向切片

扩展现有 contract、verifier、上下文入口和固定 adapter，使它们能承载多文件、多入口
公开任务。实现保持声明式和任务无关；`task_white_1993` 的差异只存在于公开 contract
和精确运行清单中。

选择理由：它直接解决已确认的 contract、上下文、provenance、预算和审批阻断，同时
保持改动可确定性测试，符合课程对 harness 机制必须由代码实现的要求。

### 3.2 拒绝：直接运行现有 baseline

现有通用 contract 漏掉 `reproduction/ANALYSIS.md`，并要求所有 expected `.py` 分别
直接执行；首轮长输入在 12,000 字预算下只保留少量头尾片段；40 次工具预算还需同时
承担公开资料读取、13 个文件写入和多个脚本执行。已知阻断下直接运行会浪费真实 API
尝试，不能作为可靠验收路径。

### 3.3 拒绝：一次性实现完整 research variant

论文约束账本、数值健康审计、恢复和成本归一化有长期价值，但远超本阶段“先跑通一个
完整公开 test”的成功标准。当前设计只实现完成目标所需的最小通用机制。

## 4. Contract 与 Artifact Verifier

### 4.1 契约扩展

`TaskContract` 新增：

```python
execution_entrypoints: tuple[str, ...] = ()
```

约束如下：

- 每个 entrypoint 必须同时属于 `expected_files`；
- 每个 entrypoint 必须是 `.py` 文件；
- contract path 继续使用现有公开相对路径规范化和重复路径检查；
- 未声明 entrypoint 的旧 contract 保持兼容，但两个既有 smoke contract 会显式加入各自
  的唯一 reproduction 脚本，继续锁住原有 provenance 语义。

### 4.2 验收语义

- 所有 `expected_files` 必须存在、可见、为普通非空文件。
- 只有 `execution_entrypoints` 必须存在当前 SHA-256 对应的成功执行记录。
- 代码核心模块被入口脚本导入即可，不再要求每个模块作为顶层脚本单独执行。
- expected CSV 必须由一个当前版本 entrypoint 的成功执行创建或改变；CSV 当前哈希必须
  与 execution journal 的 after snapshot 一致。
- 公开 CSV 约束只使用 instruction 明示的表头与数据行数；不加入参考数值。

### 4.3 完整公开 contract

新增 `integrations/prbench/public_contracts/task_white_1993.json`，声明：

- `reproduction/ANALYSIS.md`；
- 5 个核心模块和 7 个 figure scripts；
- 7 个 figure scripts 为 `execution_entrypoints`；
- `data/fig2.csv` 至 `data/fig8.csv`；
- instruction 明示的 CSV 表头和数据行数。

contract 不包含 `metadata.md`、参考代码、参考 CSV、隐藏数值或 task ID 分支求解逻辑。

## 5. 上下文与资源预算

### 5.1 紧凑任务简报

`run_prbench` 不再把完整 instruction 与论文正文拼入初始用户消息。它构造一个稳定、
紧凑的公开任务简报，包含：

- instruction、paper 和显式 input file 的相对路径；
- expected artifact 与 execution entrypoint 清单；
- 要求先读 instruction，再通过 `file.read` / `search.grep` 分段读取论文；
- 要求先实现核心模块和 figure scripts，再逐入口请求 `process.run`；
- final 只有在确定性 verifier 通过后才成立。

该简报必须在 4,000 字以内，保证不会被当前用户输入预算裁剪。它不总结论文公式，也不
替模型完成领域推理。

### 5.2 显式运行覆盖

既有 smoke 默认继续使用 12,000 字上下文和 40 次工具调用。完整任务通过 evaluator
显式参数使用：

- `max_context_chars=24_000`；
- `max_tool_calls=50`。

参数必须从 `main.py launch` 经 launcher、white executor、容器命令和
`phycode prbench run` 原样透传。未显式提供时保持原默认值，coding 与 GAIA profile
不受影响。

## 6. 审批与安全边界

### 6.1 静态文件审批

完整任务的初始 manifest 只包含公开 instruction 明示的 13 个 reproduction 文件：

- 每个路径一次精确 `file.write`；
- 每个路径最多两次同路径精确 `file.edit`；
- 不包含 `data/*.csv`、通配符、workspace 外路径或预先生成的 `process.run` grant。

重复 grant 只增加同一公开路径的有限消费次数，不扩大路径能力。PRBench policy 继续在
审批前拒绝直接写入或编辑 `data/**/*.csv`。

### 6.2 动态进程审批

runner 生成 `approval-request.json` 后暂停，最长等待 900 秒。主 agent 必须逐项：

1. 确认请求路径位于当前全新 workspace；
2. 确认 argv 使用 allowlisted 绝对 Python executable；
3. 确认脚本是公开 contract 中的 expected Python 文件；
4. 阅读当前脚本，确认没有 ground truth、凭据、workspace 外访问或禁用库；
5. 独立复算脚本 SHA-256；
6. 将请求对象原样加入一次性 manifest grant。

脚本内容改变、argv/cwd/hash 不匹配、清单畸形、超时或重复消费均 fail closed。运行入口
不会自动生成或批准进程 grant。

### 6.3 Ground truth 与凭据

- 白色生命周期中 ground truth 不挂载、不复制、不进入 allowlist。
- 三项 `PHYCODE_*` 只进入白色子进程；绿色凭据只在白色结束后进入 grading child。
- key、base URL 和 provider inline config 不进入 argv、Docker `Config.Env`、文件、
  trace、journal、result、报告或终端输出。
- 真实运行后对 worktree、构建物、evaluator workspace/报告与 Git 历史做 exact
  key/URL 命中计数，只输出计数。

## 7. 官方运行数据流

1. 在功能 worktree 构建 wheel，并向全新固定-commit evaluator clone 应用 adapter。
2. evaluator 创建全新 task workspace，只复制公开 instruction、论文和显式输入。
3. adapter 注入完整公开 contract 与精确静态审批 manifest。
4. runner 构造紧凑任务简报，模型通过工具读取公开资料并生成 artifact。
5. 每个进程请求等待主 agent 的 hash-bound 一次性审批。
6. 成功执行后 journal 记录脚本和 artifact before/after 哈希，verifier 立即检查完成度。
7. 所有 expected artifact、entrypoint execution 与 CSV provenance 通过后，runner 返回
   唯一成功终态 `completed`。
8. 白色 agent 退出并清理凭据后，官方 evaluator 才复制 ground truth 并启动绿色 grader。
9. evaluator 验证本轮新生成的 `run_result.json` 和 grader report 后返回成功。

## 8. 失败处理与尝试计数

- 首次白色模型响应前的 Docker、adapter、依赖或容器启动失败不计正式尝试。
- 一旦白色模型开始响应，该 workspace 计为一次，不能因审批拒绝、provider 错误或重启
  撤销。
- 每次正式尝试必须使用全新 workspace；不从失败 workspace resume，不复制 artifact。
- 失败按 provider、approval、policy、process、artifact、no-progress、tool-budget 或
  grader 分类，保留本地脱敏证据。
- 只修复通用 harness/adapter 缺陷；任何修复必须先有稳定 RED，再做最小 GREEN 和独立
  spec/quality review。
- 三次内首次满足 `completed + 有效 grader report` 后停止，不继续为更高分运行。
- 三次均失败时如实报告每次终态与根因，不把 mock、直接 runner 或部分 artifact
  表述为成功。

## 9. 测试与评审

### 9.1 Contract 与 verifier

使用合成多文件任务确定性验证：

- 核心模块存在且非空即可，不要求直接执行；
- entrypoint 缺少当前哈希执行记录时失败；
- 旧版本执行记录不能覆盖修改后的 entrypoint；
- CSV 只接受当前 entrypoint 成功执行产生的 provenance；
- entrypoint 必须属于 expected `.py`；
- 两个既有 smoke contract 非回归。

### 9.2 上下文与资源参数

- 紧凑任务简报包含全部 artifact/entrypoint 且小于 4,000 字；
- prompt 不内联论文正文或 instruction 正文；
- 24,000/50 只在显式完整任务参数中生效；
- adapter 各层精确透传并保持 smoke 默认值；
- coding/GAIA context 与预算不变。

### 9.3 Adapter 与运行脚本

- wrong evaluator commit、wrong wheel 和既有 adapter destination 继续 fail closed；
- fresh fixed clone 的 patch apply、help、参数透传和 Python 编译通过；
- PowerShell AST、环境成功/失败恢复和无 provider 负例通过；
- 初始 manifest 只含公开 reproduction 路径，不含 CSV/process grant；
- 完整任务 runner 使用显式 contract、24,000 上下文和 50 次预算。

### 9.4 最终门禁

正式运行前必须通过：

```powershell
uv run pytest
uvx pyright
uv build
git diff --check
git ls-files ".env" ".env.*" "*.pem" "*.key"
```

随后执行最多三次官方真实 API launch。成功后再次运行全量测试、Pyright、构建和凭据
扫描，并接受 whole-branch review。

## 10. 成功证据

成功必须同时具备：

- `run_result.json` 的 `status=completed`；
- `ANALYSIS.md`、12 个代码文件和 7 个 CSV 均存在且非空；
- 7 个 entrypoint 的当前 SHA-256 有成功 execution record；
- 7 个 CSV 的当前哈希具有成功执行 provenance；
- 官方 evaluator grader report 可解析且不含 `error`；
- trace 声明事件数与 JSONL 实际行数一致；
- key/URL 对 worktree、构建物、评测结果和 Git 历史均为 0 命中；
- 最终 pytest、Pyright 和构建通过。

过程记录可以包含模型名、固定 commit、尝试次数、状态、工具调用数、耗时、grader 分数
和脱敏证据位置，但不得包含 endpoint、key 或原始 provider 异常。

## 11. Git 与产物边界

所有开发提交只进入 `codex/prbench-public-test`。未经用户后续明确授权，不合并、不
fast-forward、不 push 到 `main`，保持主分支干净。

允许提交：

- PhyCode 核心源码；
- 确定性测试代码；
- 必要的公开 contract、固定 adapter 与可重复运行脚本；
- 课程要求的中文设计、PLAN、SPEC_PROCESS、AGENT_LOG 和 README 更新。

禁止提交：

- `dist/` 与解包构建物；
- evaluator clone、Docker/container 状态和 task workspace；
- `.phycode/`、trace、journal、`run_result.json`、grader report；
- 模型生成的 DMRG 源码、CSV 和 `ANALYSIS.md`；
- 任何真实 API 输出、endpoint 或 key。

仓库只保留脱敏文字结论、验证命令和实现所需的通用代码，不保存本次官方评测产物。
