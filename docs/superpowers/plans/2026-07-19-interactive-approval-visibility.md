# 交互式审批提示可见性修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让真实终端中的风险操作审批始终显示可操作的 `Approve this action? [y/N]`，同时保留正常模型等待时的 spinner，并把修复版本标识为 `0.1.1`。

**Architecture:** 修复只发生在 CLI turn 边界：`_run_turn()` 在进入原审批 handler 前停止本 turn 的 Rich Status，在 handler 返回或抛出后恢复 Status，并在 turn 结束时恢复 loop 原始 handler。策略引擎、工具运行时和审批结果不变，不引入全局状态或新模块。

**Tech Stack:** Python 3.11+、Typer、Rich Status、pytest、uv、Pyright。

## Global Constraints

- 所有项目文档使用中文；代码注释与 commit message 可使用英文。
- Python 包管理与测试一律使用 `uv`，不得使用 `pip` / `conda`。
- 必须先看到新增测试按预期失败，再写最小实现使其通过。
- 不读取真实 URL/key，不调用真实模型 API，不修改策略决策或工具权限。
- 不新增全局可变状态、新模块、线程或并行审批路径。
- 交互式审批仍为一次性 `y` / `n` 终端确认，默认值保持拒绝。
- 修复版本为 `0.1.1`；本计划不发布 GitHub Release。

---

### Task 1: Spinner 感知审批与 0.1.1 构建

**Files:**
- Modify: `tests/test_cli_commands.py`
- Modify: `tests/test_cli_smoke.py`
- Modify: `src/phycode/cli.py`
- Modify: `src/phycode/__init__.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `AGENT_LOG.md`

**Interfaces:**
- Consumes: `AgentLoop.approval_handler: ApprovalHandler | None`、Rich `Status.start()` / `Status.stop()`、既有 `_interactive_approver(call, decision) -> bool`。
- Produces: `_run_turn(loop: AgentLoop, text: str)` 保持原签名与返回值，但在真实终端中保证审批期间 Status 停止，并在审批/turn 的所有退出路径恢复状态与原 handler。

- [ ] **Step 1: 写 spinner 生命周期 RED 测试**

在 `tests/test_cli_commands.py` 增加一个记录事件的假 Console/Status 和假 loop。假 loop 的 `run()` 内调用当前 `approval_handler`；原 handler 记录 `approval` 并返回 `True`。测试调用 `_run_turn()` 后断言事件顺序至少满足：

```python
assert events == [
    "status-enter",
    "run",
    "status-stop",
    "approval",
    "status-start",
    "run-after-approval",
    "status-exit",
]
assert loop.approval_handler is approve
```

另加异常用例：原 handler 抛出 `RuntimeError("approval failed")`，断言 `_run_turn()` 传播异常，同时 `status-start`、`status-exit` 均发生，且 `loop.approval_handler is approve`。

- [ ] **Step 2: 运行聚焦测试，确认按预期 RED**

运行：

```powershell
uv run pytest tests/test_cli_commands.py -k "run_turn and approval" -q
```

预期：新增测试失败，差异明确显示旧 `_run_turn()` 没有 `status-stop` / `status-start`，而不是导入或测试夹具错误。

- [ ] **Step 3: 写最小 spinner 感知实现**

在 `src/phycode/cli.py::_run_turn()` 的 `with console.status(...) as status:` 内保存 `original_approval_handler = loop.approval_handler`。若 handler 存在，临时安装闭包：

```python
def approval_with_visible_prompt(call: ToolCall, decision: PolicyDecision) -> bool:
    status.stop()
    try:
        return original_approval_handler(call, decision)
    finally:
        status.start()
```

用外层 `try/finally` 包住 `loop.run(text)`，在 finally 中恢复 `loop.approval_handler = original_approval_handler`。没有 handler 和非终端路径保持现有行为；不得修改 `_interactive_approver()`、策略或工具运行时。

- [ ] **Step 4: 运行聚焦测试，确认 GREEN**

运行：

```powershell
uv run pytest tests/test_cli_commands.py -k "run_turn and approval" -q
```

预期：新增正常/异常路径测试全部 PASS。

- [ ] **Step 5: 写版本 RED 测试**

把 `tests/test_cli_smoke.py::test_version_command_prints_version` 收紧为同时断言：

```python
assert "phycode 0.1.1" in result.stdout.lower()
```

运行：

```powershell
uv run pytest tests/test_cli_smoke.py::test_version_command_prints_version -q
```

预期：FAIL，实际输出仍为 `phycode 0.1.0`。

- [ ] **Step 6: 更新版本并确认 GREEN**

把 `pyproject.toml` 和 `src/phycode/__init__.py` 的版本改为 `0.1.1`，然后运行：

```powershell
uv lock
uv run pytest tests/test_cli_smoke.py::test_version_command_prints_version -q
```

预期：锁文件中的本地 `phycode` 版本同步为 `0.1.1`，测试 PASS。

- [ ] **Step 7: 更新中文过程证据**

在 `AGENT_LOG.md` 记录：用户截图、真实 PTY 根因证据、Typer 与 Rich Prompt 均会被活动 Status 覆盖、RED/GREEN 命令、未读取凭据/未调用真实 API，以及 `0.1.1` 仅为待发布构建。任务通过独立复审后，由主 agent 在 `PLAN.md` 用实际 commit hash 标记 Task 26 完成。

- [ ] **Step 8: 完整验证**

依次运行：

```powershell
uv run pytest -q
uvx pyright
uv build
git diff --check
```

预期：测试全部通过；Pyright 为 0 errors / 0 warnings；`dist/` 生成 `phycode-0.1.1-py3-none-any.whl` 与 `phycode-0.1.1.tar.gz`；diff check 无输出。

- [ ] **Step 9: 提交**

```powershell
git add tests/test_cli_commands.py tests/test_cli_smoke.py src/phycode/cli.py src/phycode/__init__.py pyproject.toml uv.lock AGENT_LOG.md
git commit -m "fix(cli): show interactive approval prompt [implementer]"
```

提交报告必须包含 RED 的确切失败原因、GREEN/完整验证结果、自审结论与 commit hash。

## 自我审查

- 规约覆盖：正常审批、审批异常、handler 恢复、版本区分、本机构建均有明确步骤。
- 范围控制：未改变审批策略、命令参数、模型适配器、PRBench 审批清单或工具执行逻辑。
- 类型一致性：继续使用现有 `ToolCall`、`PolicyDecision`、`ApprovalHandler` 与 `_run_turn()` 接口。
- 红旗扫描：没有 `TBD`、`TODO`、模糊的“添加适当处理”或未定义接口。
