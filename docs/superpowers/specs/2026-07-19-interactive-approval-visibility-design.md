# 交互式审批提示可见性修复设计

## 问题与证据

PhyCode 交互式 CLI 在真实终端中使用 Rich `Status` 显示动态 `thinking...`。当策略运行时请求人工审批时，`_interactive_approver()` 会调用 `typer.confirm()`，但活动中的 Live/Status 刷新会覆盖确认问题与输入光标。用户只能看到
`[approval needed]` 和 spinner，不知道程序其实正在等待 `y` / `n`。

该问题已在 Windows PTY 中稳定复现：`typer.confirm()` 与 Rich `Confirm.ask()` 在活动 spinner 内都不可见，输入 `y` 或 `n` 后提示才随终端回放出现。因此根因是 spinner 生命周期跨越阻塞式审批输入，而不是确认控件选型或模型输出。

## 方案比较

1. **移除 spinner。** 最稳妥，但会让所有正常模型等待失去动态反馈，属于不必要的交互退化。
2. **把 Typer 确认替换为 Rich 确认。** 最小改动，但 PTY 复现证明 Rich 确认同样会被活动 Status 覆盖，不能解决根因。
3. **审批期间暂停 spinner，并在审批结束后恢复。** 保留正常等待反馈，同时让阻塞式输入拥有独占、稳定的终端显示；这是采用方案。

## 设计

- 仅修改 `src/phycode/cli.py` 的交互式 turn 边界，不改变策略、工具运行时或审批语义。
- `_run_turn()` 在真实终端创建 spinner 后，临时把当前 `AgentLoop.approval_handler` 包装为 status-aware handler。
- 包装器调用原审批函数前执行公开的 `Status.stop()`；无论批准、拒绝还是审批函数抛出异常，都在 `finally` 中执行 `Status.start()`。
- turn 完成或异常退出时恢复原始 `approval_handler`，避免跨 turn 累积包装器或遗留对已关闭 Status 的引用。
- 非终端执行路径保持不变；没有审批 handler 的循环也不创建额外行为。
- 不新增全局可变状态、新模块或后台线程，保持当前单线程 CLI 架构。

## 测试与验收

- 先写失败测试，用记录事件的假 Status、假 loop 和真实审批回调证明旧实现没有执行 `stop -> approval -> start`。
- GREEN 后断言审批期间 spinner 已停止，审批结束后恢复，且 loop 的原始 handler 被恢复。
- 增加异常路径断言，确保审批函数抛出时 spinner 和 handler 仍被恢复。
- 运行 CLI 聚焦测试、完整测试、Pyright 与构建。
- 版本更新为 `0.1.1`，以区分已发布但存在该缺陷的 `0.1.0`；本任务不自动创建 GitHub Release。
- 合并后用新 wheel 强制更新本机 `uv tool` 安装，并验证 `phycode version` 与一次交互式审批冒烟测试。

## 流程说明

用户在缺陷诊断后明确要求按“审批前暂停 spinner、审批后恢复”的方案进行修复，因此该指令视为对此短设计的批准。为避免重复打断，书面设计完成后直接进入实施计划；这一小型修复对单独书面 spec 复核门禁的偏离会记录到 `AGENT_LOG.md`。
