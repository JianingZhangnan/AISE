# PhyCode 接入 PRBench 与公开能力边界评估设计

## 1. 背景与范围

PRBench 要求白色 task-solving agent 阅读物理论文、从零实现算法、执行数值模拟，并生成代码、分析文档和 CSV。官方公开仓库当前只包含 `aaatest_helloworld`、`bbbtest_alphabet` 两个最小任务和一个完整任务 `task_white_1993`；其余 29 个任务没有公开下载、申请表或远程提交入口。

本阶段只使用上述公开任务验证 PhyCode 的能力边界：两个最小任务用于接入、隔离、恢复和归档烟雾测试；`task_white_1993` 用于一次预注册的 baseline/final 对比。公开完整任务不得成为答案硬编码或任务专用启发式的来源，公开结果也不得表述为 30 题 holdout 成绩。完整目标仍要求未来在官方提供的开发集或远程 holdout 入口上验证至少 10 个百分点的 overall 提升，或首次取得非零 end-to-end callback。

## 2. 不可变评测协议

- PhyCode 起点：`27e19bcfc3cf69bbb8e2b62a06624456db9365a4`。
- PRBench evaluator：`HET-AGI/PRBench-Eval-Handson@3e5bee4545cad2138832f06302e9c98bd81f5216`。
- 模型：凭据文件 endpoint index 0 对应的 `deepseek-v4-pro`；baseline、final、ablation 均不得更换。
- endpoint 与 key：只在进程内从仓库外文件读取；提交物只记录 endpoint index、主机名和模型名，不记录 key。
- 采样参数：`temperature=0`；provider 不支持的采样字段必须在首轮运行前记录并从全部配置中一致移除。
- 公开完整任务运行次数：baseline 三次、final 三次；不得根据单次随机结果挑选最好轨迹。
- baseline 工具调用总预算：40；final 工具调用总预算：50，包含恢复尝试，不能因重启重置。
- 资源：沿用任务 `task.yaml` 的 Docker image、依赖、内存和超时；baseline/final 使用同一配置。
- 成本：记录每次 provider 返回的 prompt/completion token、调用次数和墙钟时间。同一模型下以总 token 作为归一化成本；final 三次运行的平均值不得超过 baseline 平均值的 1.25 倍。
- grader：baseline/final 使用完全相同的绿色 agent 类型、模型、prompt 和 evaluator commit。

## 3. 接入架构

### 3.1 PhyCode PRBench runner

新增 `phycode.prbench_eval`，提供 `python -m phycode.prbench_eval run` 和对应 `phycode prbench run` 命令。runner 只接受官方白色 agent 已生成的 `/workspace/_instruction.md`、论文文件、图片和显式 input files，工作目录固定为 `/workspace`。

runner 支持两个预注册 variant：

- `baseline`：保留现有 coding system prompt、上下文策略和 40 次工具预算，只补齐无头环境凭据、容器内受限自动批准和指标记录。这一层只解决“能运行”，不加入科研策略。
- `research`：使用同一模型和任务，在 50 次总工具预算内启用论文约束账本、科研执行、数值验证、恢复和数据来源审计。

`baseline` 和 `research` 共享 provider adapter、工具实现和 evaluator adapter，避免把接入差异误算为能力提升。

### 3.2 官方 evaluator adapter

仓库在 `integrations/prbench/` 保存针对固定 evaluator commit 的小型版本化 patch，增加 `--white-agent-type phycode`，完成以下动作：

1. 在 Docker 中安装指定 PhyCode wheel 或 Git commit。
2. 仅把白色 agent 所需的 endpoint/key/model 环境变量传给 `phycode prbench run`。
3. 让绿色 grader 继续使用官方现有 agent 路径，不调用 PhyCode 白色策略。
4. 将 `/home/agent/.phycode` trace 归档到 `eval_logs/_phycode_traces`。
5. 在 evaluator commit 不匹配时拒绝静默打补丁。

patch 必须通过 `git apply --check`、Python 编译检查和两个最小任务的官方 launch 流程。不得修改官方 task 内容、grader prompt、metadata 或 reference files。

## 4. 严格隔离与防泄漏

白色 agent 的文件、搜索和 shell 策略在确定性代码中拒绝任何包含 `_ground_truth` 路径分量的访问；即使 evaluator 生命周期发生错误，也不能读取评分材料。工作区外路径继续由现有 allowlist 策略拒绝。

凭据只作为父进程传入的环境变量存在。模型可调用工具不能读取 `.env`、key 文件、环境转储或凭据目录；trace、memory、provider 消息和异常继续经过统一脱敏。运行状态只写入 `/workspace/.phycode/prbench/`，该目录不得提交。

官方 ground truth 只允许绿色 grader 在白色进程退出后复制。baseline/final 的白色 trace、memory 和 prompt 中均不得出现 metadata、reference code、reference CSV 或 grader 输出。

## 5. Research variant 的最小通用改进

### 5.1 论文理解与约束账本

system prompt 要求 agent 先生成 `reproduction/ANALYSIS.md`，其中包含：交付物清单、原论文方法、方程与符号、单位/归一化/索引约定、论文明确给出的数值设置、未明确细节及验证假设。上下文总字符上限不高于 baseline；通过保留初始约束摘要和裁剪重复工具输出改善长期一致性，而不是扩大每轮 prompt。

### 5.2 科研代码执行

新增受控科研执行器，对 Python reproduction script 运行记录命令、退出码、墙钟时间、代码哈希，以及运行前后 `data/*.csv` 的哈希变化。长命令仍受任务总超时和单命令上限约束。执行反馈区分运行时错误、超时、非有限数值、空输出和资源异常。

### 5.3 数值验证

新增确定性 CSV/数值审计：检查文件存在性、schema、行数、NaN/Inf、全常数列、重复行和异常空数据。agent 必须在最终结束前记录至少一种与任务适配的验证证据，例如解析极限、已知对称性、守恒量、小规模精确解、网格/步长收敛或独立实现交叉检查。验证器只检查证据和数值健康度，不读取 reference data，也不假装判断隐藏答案正确性。

### 5.4 长任务恢复

每次工具调用后原子更新 `/workspace/.phycode/prbench/state.json`，保存 variant、模型、固定总预算、累计工具调用、provider usage、当前阶段、最近失败和已生成 artifact 哈希。provider 错误、进程中断或非正常停止后，`--resume` 从剩余预算继续；不得把 50 次预算按重启次数重新发放。恢复 prompt 只引用白色 agent 自己的状态和工作区 artifact。

### 5.5 防数据伪造

CSV 必须能追溯到一次成功的 reproduction script 执行及其代码哈希。直接用 `file.write`/`file.edit` 写 `data/*.csv` 被策略拒绝；未被成功执行记录覆盖的 CSV 标为 provenance failure。审计同时扫描明显的 output hardcoding 模式并给出失败反馈，但不基于公开 reference 数值建立规则。严重 provenance failure 使 PhyCode 以非零状态结束，并保留证据供 evaluator 评分。

## 6. 失败分类与迭代方法

公开 baseline 的 trace 按论文 taxonomy 和运行证据分类：接入/凭据、论文理解、公式实现、算法忠实度、方法约定、静默数值失败、资源约束、交付物缺失、恢复失败、数据来源失败。每项结论必须引用白色 trace、agent code、运行日志或 evaluator report，不能从 ground truth 反向构造 prompt。

迭代顺序固定为“分析失败 → 一个最小通用改进 → 确定性或合成测试 → 同配置复测”。`task_white_1993` 不用于多轮提示词试错；generic 改进优先在合成科学计算 fixture 和两个最小任务上验证，完整任务只执行预注册的三次 baseline 和三次 final。ablation 关闭单个机制，模型、预算和任务不变。

## 7. 结果与复现产物

运行时结果保存在被忽略的 `.phycode/prbench/`，提交仓库只保留脱敏汇总：

- 固定协议与 evaluator/PhyCode commit；
- baseline/final 各次四维分数、overall、callback、token、调用次数和墙钟时间；
- 平均成本比和是否满足 1.25 上限；
- 单机制 ablation；
- 失败分类与证据位置；
- Docker、依赖安装、adapter 应用、baseline、final、resume、audit、pytest 和 Pyright 的完整命令。

若完整 30 题仍不可获得，报告必须明确写为“公开任务能力边界”，并把 holdout 提升标准列为未验证，不能用公开任务结果替代。

## 8. 测试与验收

实现严格遵循测试先行。新增测试覆盖：环境凭据不落盘、PRBench profile 工具边界、`_ground_truth` 防御、直接 CSV 写入拒绝、执行 provenance、NaN/Inf 与空数据检查、原子 checkpoint、预算跨恢复保持、usage 统计、baseline/research 配置锁定、官方 patch 版本检查和 CLI 契约。

每次改进先运行相关单测，再运行 `uv run pytest` 与 `uvx pyright`。最终还需执行 `uv sync --extra gaia --dev`、wheel/sdist 构建、全新 Python 3.11 安装检查，以及官方 evaluator 的公开任务流程。没有 Docker 或官方数据时必须如实记录缺失证据，不能据此宣布完整目标完成。
