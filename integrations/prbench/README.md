# PRBench 官方 evaluator adapter

该目录保存面向 `HET-AGI/PRBench-Eval-Handson` 固定提交
`3e5bee4545cad2138832f06302e9c98bd81f5216` 的最小适配层。它只接入
PhyCode 白色 agent。对绿色 grader 的唯一改动是凭据传递生命周期；ground truth
复制时序、评分提示、评分算法和结果解析均保持不变。

应用器先精确核对 evaluator HEAD 和 wheel，再依次执行 `git apply --check`、
`git apply`，最后把 wheel 放到 evaluator 的
`.phycode-adapter/phycode.whl`：

```powershell
uv run python integrations/prbench/apply_adapter.py <evaluator-root> <wheel-path>
```

当前 adapter 与项目版本共同固定 wheel 文件名为
`phycode-0.1.5-py3-none-any.whl`；其他名称会在修改 evaluator 前被拒绝。
若 `.phycode-adapter/phycode.whl` 已经是普通文件、symlink 或 dangling
symlink，应用器同样 fail closed，不覆盖或解析旧目标。
官方 `python:3.11-slim` task image 不含 uv，因此 patch 从 Astral 官方
`uv:0.11.28` 的 linux/amd64 manifest
`sha256:5c3ab83183a73c5d319a77009eb425b60d5bb937f339fb7876788ebf567baf48`
提取 `/uv`，再执行
`uv pip install --system`，不使用 pip 安装 PhyCode 或引导安装 uv。
容器启动前会先选择非 root 身份：POSIX 保留宿主 `getuid/getgid`，Windows 因无
对应 API 固定使用 `1000:1000`，user/group/chown 全部使用同一组值。容器创建后
若用户配置、依赖安装或 workspace 初始化抛错，adapter 会 best-effort
`remove(force=True)`，无论清理本身是否失败都清空本地 container 状态并重抛原始
异常；清理告警不拼接异常文本或 provider 值。

适配后的 evaluator 通过 `--white-agent-type phycode` 选择 PhyCode。宿主可用
`--phycode-contract` 注入公开 task contract，并用 `--phycode-approvals` 注入
本次运行经人工审定的精确审批清单。公开 smoke 的初始清单只允许两个 task
各自的 reproduction 脚本进行一次精确 `file.write` 和一次同路径 `file.edit`；
`file.edit` 是为“首版脚本只打印、收到 provenance 反馈后再补全生成逻辑”保留的
最小恢复能力。初始清单不含任何 `data/*.csv`、通配符或 `process.run` grant。
未提供审批文件时，adapter 会写入
`{"grants": []}`，使所有风险动作 fail closed；它不会根据 expected outputs
自动生成授权，也不会假定模型采用某个脚本名。
`--approval-wait-seconds` 控制 PhyCode 等待运行时审批的时间，默认 `0`，仅接受
`0..900`；超界值会在创建容器前 fail closed。该数值经 launcher 和 white
executor 原样传给容器内 `phycode prbench run`，不会追加到其他 white agent
命令。需要人工处理动态审批的官方 smoke 使用 `900`。
PhyCode 只注册为 full evaluation 的 white task-solving agent；把它选作 green
agent 或与 `--code-only` 组合会在容器创建前 fail closed。

公开 contract 只包含 instruction 明示的 expected files、CSV header 和 rows。
PRBench runner 显式开启“成功工具后验收”：每个 `status=ok` 的工具结果完成回灌后，
立即用同一个 `ArtifactVerifier` 检查 contract；只有文件路径、内容约束和 execution
provenance 全部通过才直接以唯一成功终态 `completed` 停机，不再等待模型主动 final，
也不再调用后续 `status/read`。非致命的未通过检查会作为结构化
`artifact_verification_failed` 反馈进入下一轮因果上下文，引导模型补齐脚本执行或
产物；拒绝、失败或超时工具不触发成功判定，verifier 安全异常则立即 fail closed。
该开关默认关闭，普通 coding/GAIA run 的停机语义不变。
PRBench 的确定性 policy 还在审批前拒绝 workspace `data/**/*.csv` 的
`file.write` / `file.edit`，rule 为 `prbench.direct_csv_mutation_blocked`。reason 和
结构化 feedback 只给出“修正 reproduction 脚本，再请求 `process.run`”的固定恢复
步骤，不包含目标路径、期望数据值或凭据；因此错误加入 manifest 的 CSV grant 也
无法绕过 execution provenance。PRBench 分类还把每个 component 按 Win32 规则
去除尾随 ASCII space/dot 后再 casefold，并把非 drive prefix 的冒号作为 alternate
data stream fail closed；尾随别名、嵌套反斜杠别名与
`data/output.csv::$DATA` 都在 approval handler 前 DENY。原路径的 visibility / hidden /
escape 决策仍先执行，非 PRBench profile 不采用该 alias view。
adapter 在白色 agent 启动前把 contract、审批清单、公开 instruction、paper 和
显式 input files 放入 `/workspace`。容器内始终调用同一个
`phycode prbench run`；runner 只收到 `PHYCODE_API_KEY`、
`PHYCODE_BASE_URL` 与 `PHYCODE_MODEL`，白色运行结束后由官方流程继续评分。
这些值不写入共享容器的持久环境，也不进入 `docker exec` argv；green 与 white
子进程启动时均先从继承环境移除三项变量，只有 white 的 Docker CLI 子进程
通过 name-only `-e` 临时取得值。white 的 HOME 单独固定为非敏感的
`HOME=/home/agent`，三个 `PHYCODE_*` flag 始终不含 `=` 或值。启动后
host/provider 字典立即清空。
`multiprocessing.Process.start()` 或 `subprocess.Popen()` 抛错时也走同一个
`finally` 清理路径，异常不会让 provider dict、child environment 或父进程
`PHYCODE_*` 残留。
launcher 通常把已解析的 provider mapping 传给 `start_white_agent()`；直接调用时
若 mapping 为 `None`，white 端只在 `agent_type=phycode` 分支补做一次解析。无论
mapping 是预先提供还是现场解析，其值都会传入 executor 的私有副本，三项
`PHYCODE_*` 同时从父进程环境移除。非 PhyCode 分支既不导入 provider 名单，也不
解析或清除这些变量。executor 构造完成后，white server 会在 `finally` 中立即
清空收到或现场解析出的原 mapping；构造失败也执行同一路径。executor 的私有副本
会保留到 `Popen`，随后仍由既有的 child-environment `finally` 清理。

当 white 为 PhyCode 时，green provider 的值同样不会写入共享容器的
`Config.Env`，因此 white 阶段和容器内 `/proc/1/environ` 都不可见。只有 white
结束且官方流程复制 ground truth 后，green 的 `_run_grading` 才解析一次 grader
凭据，放入专属 `subprocess.run(..., env=...)` 临时映射；`docker exec` 只携带
`-e NAME`，不携带 `NAME=value`。custom OpenAI-compatible grader 还会在同一个
child environment 中收到一次性的 `OPENCODE_CONFIG_CONTENT`：其中只注册
`openai_compat` 的模型、base URL 与 `@ai-sdk/openai-compatible`，API key 字段固定
为 `{env:OPENAI_API_KEY}` 占位符，绝不嵌入真实 key。该 JSON 不进入 Docker
`Config.Env`、命令 argv、持久 `opencode.json` 或 adapter 日志。

由于 OpenCode 运行时需要先能加载 provider 包，PhyCode white + OpenCode green
组合会在无 green 凭据的容器 setup 阶段预装 `@ai-sdk/openai-compatible`；安装失败
立即 fail closed。这个开关只在延迟 green provider 的组合启用，非 PhyCode 组合仍
沿用官方 evaluator 的原有安装、持久配置和凭据传递行为。grading 成功、非零退出、
启动 `OSError` 和超时都会在 `finally` 中清空 provider、inline config 与完整 child
environment 映射。

grader 子进程的 stdout/stderr 固定按 UTF-8 解码，无法解码的字节替换为占位符；
`grading_trace.log` 同样固定写成 UTF-8，避免 Windows 本地代码页让 reader/writer
线程污染一次已经完成的评分。在 PhyCode 触发的延迟 green 凭据路径中，写 trace
之前会按长度降序精确替换当前 provider mapping 的全部非空值（包括 key、base URL、
model 和包含 URL 的 inline config）。JSON 评分仍解析未经替换的已解码 stdout；
parser 返回后、结果进入 eval report 或消息前，再对 dict key/value、list、tuple 和
string 递归执行同一组精确替换，数字、布尔值、`None` 与容器类型保持不变。这个出口
同时覆盖 Codex output-file 和其他 grader stdout 两条 parser 路径，也阻止
`parse_failure` 的截断 summary 回显 provider 值；因此脱敏不会改变官方评分输入。
非延迟的上游组合不执行这项 provider 脱敏，原有 transport、解析和日志内容语义保持
不变。

延迟凭据与 Codex grader 组合还会治理其 last-message 文件：spawn 前只接受
`log_dir/_grading_output.txt` 这个 exact path，拒绝 symlink、非普通文件和 realpath
逃逸，并清除旧普通文件；spawn 后再次用 `lstat` 与 realpath 验证本轮新文件。原始
UTF-8 文本只读入内存，使用同一个 provider text redactor 得到脱敏副本，再在同目录
写临时文件、flush/fsync，并用 `os.replace` 原子发布；该步骤紧跟 child 正常返回，
早于 trace 写入和官方 parser。运行期显式记录“已 spawn / 已脱敏”状态：child 写文件
后超时、trace 写入失败，或任何尚未完成安全发布的退出路径，都会在 `finally` 中以
`lstat` 检查并 best-effort 删除 exact 原始文件或最终 symlink，不跟随链接，也不扫描
目录。原子发布函数单独持有并只清理自己实际创建的 `NamedTemporaryFile` 路径与 exact
输出路径；预先存在的同名前缀文件不属于本次运行，内容和文件实体均保持不变。临时写入、
原子发布或 trace 写入错误均固定化，随后由既有 `finally` 清空 provider mapping；因此
失败路径不会正常返回带原文的报告。非延迟 Codex 仍保留上游 last-message 文件内容。

`setup_docker_environment()` 从容器 `start()` 到全部 CLI 安装完成使用同一个资源
事务边界。health check、PhyCode/OpenCode 安装或其他 setup 步骤一旦失败，只对本次
刚构造的 `DockerEnvironment` 调用一次 `stop()`，由它按精确 container 对象执行
force remove 并清空 `container` / `container_id`；cleanup 自身失败不会遮蔽原始
setup 异常。`stop()` 会先快照 owned container，权限修复、stop、force remove
分别以 `BaseException` 安全的 best-effort 阶段运行；前一阶段失败不会跳过后一阶段，
最终状态始终在 `finally` 清空。各阶段只写静态分类 warning，不会把异常文本或
provider 值写入日志。由于失败调用不会完成外层
`docker_env = setup_docker_environment(...)` 赋值，evaluation 的外层 `finally`
只会看到 `container_id=None`，不会再次删除该容器或触碰其他容器。

`launch_evaluation()` 在任何 workspace/Docker 准备前检查报告路径：本 evaluator
留下的旧普通文件、file symlink 或 dangling symlink 会通过 exact-path unlink
移除；目录、路径逃逸或无法删除会 fail closed。任务发送后仅接受 `lstat` 判定为
本轮新建的非 symlink 普通文件，且 realpath 仍位于 workspace 内，才会打开并解析
`workspace/eval_logs/eval_report.json`。PhyCode 白色运行还要求本轮新生成的
`workspace/.phycode/prbench/run_result.json` 通过相同 provenance 检查、JSON 为
object 且 `status=completed`，同时报告中的 `grading` 必须为 object 且不含
`error`。旧 run result 会在资源准备前按 exact path 清除；symlink、路径逃逸、
malformed/missing JSON、非成功终态及 grader parse failure 均 fail closed。
非 PhyCode 组合保留上游“发送成功且报告存在”的兼容语义。任务或 task.yaml 缺失、
green/white 未就绪、消息发送异常或报告缺失都返回失败；进入 evaluation 资源生命
周期后的失败仍由 `finally` 导出 trace、终止两个 agent、删除本次容器并按配置归档
workspace。`main.py launch` 会把 `False` 映射为退出码 1，因此公开 smoke 的首个
任务失败后会立即停止，不会继续执行后续任务。batch 模式同样只把布尔成功报告为
`Completed`。

`public_contracts/` 中的两个 JSON 只服务公开最小 smoke；其他任务默认使用
官方 `task.yaml` 的公开文件字段构造无数值约束 contract，最终数值准确度仍由
官方 grader 判断。

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

2026-07-18 的最终真实验收使用 `deepseek-v4-pro` 和固定 upstream commit：hello
任务经 8 次工具调用、46 个 trace 事件完成；alphabet 经 6 次工具调用、32 个 trace
事件完成。两项 execution journal 均记录成功且 hash-bound 的 Python 执行，声明的
trace 计数与实际 JSONL 行数一致，产物哈希可复算，官方评分均为 1.0。真实 provider
的两组 URL/key 对项目文件、构建物、评测结果和 Git 历史的精确扫描均为 0 命中。

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
  -WheelPath D:\path\to\AISE\dist\phycode-0.1.5-py3-none-any.whl `
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

初始审批 JSON **只**包含目标 reproduction 文件各一次精确 `file.write` 与
`file.edit`：`reproduction/hello.py` 或 `reproduction/alphabet.py`。`file.edit`
用于首版脚本只完成部分工作时在同一路径内修正；它不能改写 CSV，也不含通配符。
脚本生成前不会预授权
`process.run`，smoke 脚本本身也不会计算 hash 或自动批准执行。官方命令传入
`--approval-wait-seconds 900`；模型写完脚本并首次请求执行时，runner 会原子写入
workspace 内的 `.phycode/prbench/approval-request.json` 并暂停等待。
在该固定 evaluator 中，active workspace 通常位于
`<EvaluatorRoot>\data\tasks\<TaskId>\workspace`；以 launcher 日志公布的实际路径为
准。运行中应修改这里的 `phycode-approvals.json`，而不是脚本最初创建并已被 adapter
复制的临时 manifest。

此时主 agent 必须人工完成以下门禁：

1. 读取待执行 reproduction 脚本，确认它只实现公开任务且没有越界行为。
2. 读取 `approval-request.json`，逐项核对规范化 `argv`、`cwd` 和
   `script_sha256`；`argv[1]` 就是相对 `cwd` 的脚本路径，独立计算脚本 SHA-256
   并确认与请求一致。
3. 请求对象与一次性 `process.run` grant 使用同一 schema；审核通过后可将该对象
   **原样**追加到 active workspace 的 `phycode-approvals.json` 的 `grants` 数组。
   不得批准不同参数，不得使用通配符，也不得让外部脚本替 agent 运行 reproduction。

清单刷新后 runner 会再次校验脚本内容；等待期间脚本变化、hash 不匹配、畸形
清单、重复消费或 900 秒超时都会 fail closed。CSV 只能由已审核脚本执行生成，
不会从 `expected_files` 推导授权；PRBench policy 对 workspace 的
`data/**/*.csv` 执行 `file.write` / `file.edit` 都确定性拒绝，即使 manifest 误含
对应 grant 也不能绕过。分类使用跨平台 Win32 alias view：每个路径 component
先去除尾随 ASCII space/dot 再 casefold，因此 `data. /OUTPUT.CSV... ` 不能伪装；
非盘符位置的冒号按 NTFS alternate data stream fail closed，
`data/output.csv::$DATA` 同样在审批前拒绝。原始路径仍先经过 visibility、hidden 与
escape 检查，alias view 不会改写实际工具路径；普通 coding/GAIA policy 不变。

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

## PRBench 完整公开任务（正式运行前门禁）

`task_white_1993` 是一个**完整公开任务**，用于验证从公开输入到 20 个声明产物的
端到端机制；它不是隐藏 holdout，不代表 PRBench 总榜成绩，也不等于本课程最终成绩。
本节先给出可复现入口、人工审批和成功判定，再记录最终五次正式真实 API / official
evaluator 结果；不能用确定性 GREEN、adapter apply 或部分评分替代正式成功。

运行源必须是干净 clone，并固定在 evaluator commit
`3e5bee4545cad2138832f06302e9c98bd81f5216`。先在功能分支
`codex/prbench-public-test` 构建当前 wheel，再任选本机已安装的 PowerShell 入口执行
同一个脚本；示例中的 evaluator 与 wheel 路径只使用本机绝对路径：

```powershell
uv build
pwsh -NoProfile -File .\integrations\prbench\run_public_full.ps1 `
  -EvaluatorRoot D:\path\to\PRBench-Eval-Handson `
  -WheelPath D:\path\to\AISE\dist\phycode-0.1.3-py3-none-any.whl

powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\integrations\prbench\run_public_full.ps1 `
  -EvaluatorRoot D:\path\to\PRBench-Eval-Handson `
  -WheelPath D:\path\to\AISE\dist\phycode-0.1.3-py3-none-any.whl
```

`run_public_full.ps1` 固定只运行 `task_white_1993`，显式传入最多 `50` 次工具调用、
`24000` 字（文档记作 24,000）上下文和 `900` 秒审批等待。脚本只创建 reproduction
文件的精确 write/edit 初始授权，不创建 `process.run`、CSV 或通配授权。

运行期间由**主 agent 人工**从本轮 launcher 日志确认 active workspace。固定 evaluator
内部路径明确为
`<EvaluatorRoot>\data\tasks\task_white_1993\workspace`，动态 request 位于
`<EvaluatorRoot>\data\tasks\task_white_1993\workspace\.phycode\prbench\approval-request.json`，
active manifest 位于
`<EvaluatorRoot>\data\tasks\task_white_1993\workspace\phycode-approvals.json`。仓库外的
attempt/clone 目录只用于命名每轮新的 `EvaluatorRoot`，不是 evaluator 内部的
`data/workspaces/*` 布局。对每个 request 必须按顺序完成以下门禁：

1. **路径与文件类型**：分别对 request、script 和 manifest 执行 `lstat` 与 realpath，
   证明三者都在 active workspace 内，且都是非链接普通文件。
2. **解释器**：确认 `argv[0]` 是 adapter allowlist 中本轮预期的 absolute Python，不能
   用其他解释器、相对 executable 或 PATH 搜索替代。
3. **脚本入口**：把 `argv[1]` 相对 `cwd` 解析后，确认它是 contract `expected_files` 中预期的
   `.py`，不能执行普通清单外脚本。
4. **工作目录**：确认 `cwd` 必须精确等于 active workspace，不能使用其子目录、父目录
   或其他 clone/workspace。
5. **尾随参数**：逐个检查每个尾随 argv 中的路径参数，解析后都必须在 active workspace
   内；任何 workspace 外路径都拒绝。
6. **脚本内容**：完整阅读当前脚本，明确拒绝 ground truth、凭据读取或外泄、网络外泄、
   禁用库、workspace 外访问以及其他超出公开任务的行为。
7. **内容哈希**：独立复算 SHA-256，并与 request 的 `script_sha256` 精确比较；内容变化
   后必须重新审核，旧批准不得复用。
8. **原子批准**：只有前七步全部通过后，才把 request 对象原样追加到 active manifest；
   使用临时文件、flush、fsync 与 `os.replace` 原子更新，不能手工重建或放宽对象。

不得自动批准，不得改写 request，不得生成通配 grant，不得批准直接写 CSV，也不得静态预授权
`process.run`；外部脚本不能替 agent 执行 reproduction。

正式验收最多五次，每次都使用新的 fixed-commit evaluator clone 与 workspace。Docker、
adapter、依赖或容器若在首次白色模型响应前失败，属于基础设施预响应失败，不计数；
首次白色模型响应后，本轮审批拒绝、provider/process/artifact/budget/grader 失败都计为
一次。首次同时取得 runner `completed` 和本轮新生成、可解析且 `grading` 为 object、
不含 `error` 的有效 grader report 后立即停止；两者缺一都不能宣称成功，五次失败则如实
结束。

### task_white_1993 完整公开任务真实验收

用户把正式尝试上限从 3 次扩展到 5 次，最后两次指定模型 `glm-5.2`。正式尝试次数为 5，
上限已经用尽，没有第 6 次：

1. 尝试 1：模型 `deepseek-v4-pro`，runner `tool_budget_exhausted`，50 次工具调用，`overall_score` 0.0。
2. 尝试 2：模型 `deepseek-v4-pro`，runner `provider_error`，13 次工具调用，`overall_score` 0.0。
3. 尝试 3：模型 `deepseek-v4-pro`，runner `approval_required`，42 次工具调用，20 项声明产物存在 13 项，7 项 CSV 存在 0 项，`overall_score` 0.17。
4. 尝试 4：模型 `glm-5.2`，runner `provider_error`，11 次工具调用，20 项声明产物存在 0 项，`overall_score` 0.0，约 720 秒。
5. 尝试 5：模型 `glm-5.2`，runner `provider_error`，11 次工具调用，20 项声明产物存在 0 项，white 约 662 秒、grader 约 700 秒，`overall_score` 0.0。

最佳结果仍是未成功的尝试 3。成功标准始终是 runner `completed` 与有效 green report
同时成立，五次均未满足，因此完整公开任务未跑通，不能声称成功。首次模型响应前的失败
不计入正式次数，包括两次 OpenCode 安装相关失败、一次旧 exact-equality contract
preflight 失败，以及一次手动预检后的 double-adapter clean-check 失败。

正式运行期间的修复/review 关键提交为 `4e831d1`、`a0f8df9`、`c3be45e..fb42598`、
`2011e84`、`1d30458`、`a5be873`、`1c410ab`、`f99cec8`。最终 contract spec review 为
Critical / Important / Minor = 0 / 0 / 0；quality review 为 0 / 0 / 1，Ready，唯一 Minor
是没有用任意未知组名做专门变异测试。artifact review 曾有两个非阻塞 Minor：缺少全局
CSV capture 总预算，以及缺少真实 Windows junction 集成覆盖。

当前凭据泄漏扫描结果如下：HEAD 的 109 个 tracked regular blobs（仅 mode 100644/100755，
排除 gitlink）中，两组 exact key 匹配 0、读取错误 0；本地 `.superpowers/sdd` 与 `dist`
排除 `.git`、`.venv`、`node_modules`、`_ground_truth`、`groundtruth`、`reference` 后的
1000 个文件中，两组 exact key 匹配 0、读取错误 0，其中日志/trace/report/wheel 筛选出的
81 个文件同样为两组 exact key 匹配 0、读取错误 0。7 个 provider/PRBench 相关环境变量均
absent，容器数 0；上述评测产物未提交。

Task 36 脱敏结果记录已完成；Task 36 whole-branch review 与最终复验已完成，最终结论为
Ready。Task 36 的过程门禁已经完成，但五次正式尝试仍未跑通，不能改写为成功。

evaluator clone、workspace、trace JSONL、execution journal、run result、grader 报告、
模型生成脚本/CSV 及本地扫描清单都是本机忽略产物：**评测产物不提交**，也不得执行
`git add`。代码和文档提交只留在功能分支，未经授权不合并或推送到 `main`；运行前后都
检查 feature branch 与 `main` 洁净，以**保持主分支干净**。
