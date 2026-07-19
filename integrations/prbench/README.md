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
`phycode-0.1.0-py3-none-any.whl`；其他名称会在修改 evaluator 前被拒绝。
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
也不再调用后续 `status/read`。非致命的未通过检查保持静默，让模型继续工作；拒绝、
失败或超时工具不触发该成功判定，verifier 安全异常则立即 fail closed。该开关默认
关闭，普通 coding/GAIA run 的停机语义不变。
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
写临时文件、flush/fsync，并用 `os.replace` 原子发布，之后才把内存中的原文交给官方
parser。临时写入或原子发布失败时会 best-effort 删除临时文件与 exact 原始文件，抛出
固定错误并由既有 `finally` 清空 provider mapping；因此失败路径不会正常返回带原文的
报告。非延迟 Codex 仍保留上游 last-message 文件内容。

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
