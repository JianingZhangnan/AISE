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

适配后的 evaluator 通过 `--white-agent-type phycode` 选择 PhyCode。宿主可用
`--phycode-contract` 注入公开 task contract，并用 `--phycode-approvals` 注入
本次运行经人工审定的精确审批清单。未提供审批文件时，adapter 会写入
`{"grants": []}`，使所有风险动作 fail closed；它不会根据 expected outputs
自动生成授权，也不会假定模型采用某个脚本名。
`--approval-wait-seconds` 控制 PhyCode 等待运行时审批的时间，默认 `0`，仅接受
`0..900`；超界值会在创建容器前 fail closed。该数值经 launcher 和 white
executor 原样传给容器内 `phycode prbench run`，不会追加到其他 white agent
命令。需要人工处理动态审批的官方 smoke 使用 `900`。
PhyCode 只注册为 full evaluation 的 white task-solving agent；把它选作 green
agent 或与 `--code-only` 组合会在容器创建前 fail closed。

公开 contract 只包含 instruction 明示的 expected files、CSV header 和 rows。
adapter 在白色 agent 启动前把 contract、审批清单、公开 instruction、paper 和
显式 input files 放入 `/workspace`。容器内始终调用同一个
`phycode prbench run`；runner 只收到 `PHYCODE_API_KEY`、
`PHYCODE_BASE_URL` 与 `PHYCODE_MODEL`，白色运行结束后由官方流程继续评分。
这些值不写入共享容器的持久环境，也不进入 `docker exec` argv；green 与 white
子进程启动时均先从继承环境移除三项变量，只有 white 的 Docker CLI 子进程
通过 name-only `-e` 临时取得值。启动后 host/provider 字典立即清空。
`multiprocessing.Process.start()` 或 `subprocess.Popen()` 抛错时也走同一个
`finally` 清理路径，异常不会让 provider dict、child environment 或父进程
`PHYCODE_*` 残留。

当 white 为 PhyCode 时，green provider 的值同样不会写入共享容器的
`Config.Env`，因此 white 阶段和容器内 `/proc/1/environ` 都不可见。只有 white
结束且官方流程复制 ground truth 后，green 的 `_run_grading` 才解析一次 grader
凭据，放入专属 `subprocess.run(..., env=...)` 临时映射；`docker exec` 只携带
`-e NAME`，不携带 `NAME=value`。成功、非零退出、启动 `OSError` 和超时都会在
`finally` 中清空 provider 与 child-environment 映射。非 PhyCode white/green
组合仍沿用官方 evaluator 原有的共享环境行为。

`public_contracts/` 中的两个 JSON 只服务公开最小 smoke；其他任务默认使用
官方 `task.yaml` 的公开文件字段构造无数值约束 contract，最终数值准确度仍由
官方 grader 判断。
