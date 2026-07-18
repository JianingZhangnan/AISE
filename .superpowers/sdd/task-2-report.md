# Task 2 实施报告：结构化进程执行与一次性审批

## 状态

已完成。实现基于 Task 1 审查通过后的 `468ac42`，工作目录始终为
`D:\projects\AISE\.worktrees\prbench-runtime-refactor`。

本任务仅实现一次性审批清单、结构化 `process.run`、policy 风险分类与 cwd
可见性检查、CLI registry 接线。没有实现 execution journal、artifact verifier、
PRBench runner 或新的 shell parser；`journal` 参数仅作为 Task 3 的接口扩展点保留。

## TDD 记录

### 第一轮：Task 2 公开接口与核心行为

先新增 `tests/test_process_approval.py`，覆盖：

- 真实 Python 子进程把 `&` 作为普通 argv 字面量传递；
- `process.run` 的固定结构化 schema；
- argv、cwd、timeout、executable allowlist 安全负例；
- 非零退出的 stdout/stderr/status；
- file path 与 process argv/cwd 的 canonical grant 匹配；
- grant 只消费一次；
- 顶层 manifest 与 grant 都拒绝未知字段；
- PRBench registry 包含 `process.run` 且排除 `shell.run`，coding 保留旧 shell 工具。

写测试时尚未创建任何 Task 2 生产模块。

RED 命令：

```powershell
uv run pytest tests/test_process_approval.py -v
```

关键输出：

```text
collected 0 items / 1 error
tests\test_process_approval.py:10: in <module>
    from phycode.approval import ApprovalManifest
E   ModuleNotFoundError: No module named 'phycode.approval'
ERROR tests/test_process_approval.py
1 error in 0.21s
```

失败原因符合预期：Task 2 的 approval/process 模块尚不存在，不是测试拼写或 fixture
错误。

最小实现：

- `ApprovalGrant` 与 manifest 顶层模型均使用 Pydantic `extra="forbid"`；
- 加载 grant 时通过 Task 1 的 `PathVisibilityPolicy` 把 path/cwd 解析为绝对规范路径；
- 调用时用同一 visibility 生成 file path 或 process argv+cwd canonical key；
- 精确匹配后从剩余 grant 列表删除，后续相同调用不再获批；
- `process.run` 只接受结构化 argv/cwd/timeout，并按 basename casefold allowlist 检查 executable；
- 真实执行固定使用 `subprocess.run(argv, cwd=..., shell=False, text=True,
  capture_output=True, timeout=...)`；
- policy 把 `process.run` 分类为 risky，并在审批前检查 PRBench profile 的 cwd visibility；
- CLI 默认 registry 注册 process executor，profile subset 保证 coding/GAIA 工具集合不变。

第一轮 GREEN 命令：

```powershell
uv run pytest tests/test_process_approval.py -v
```

输出：

```text
collected 22 items
tests\test_process_approval.py ...................... [100%]
22 passed in 0.98s
```

### 第二轮：未知参数与 timeout 映射

自审发现 schema 虽然声明 `additionalProperties: false`，但 executor 仍会忽略未知参数；
同时 timeout 特判最初没有独立的真实回归证据。按 TDD 要求先移除未被测试证明的 timeout
特判，再增加两个测试。

RED 命令：

```powershell
uv run pytest tests/test_process_approval.py -k "unknown_arguments or reports_timeout" -v
```

关键输出：

```text
collected 24 items / 22 deselected / 2 selected
tests\test_process_approval.py FF [100%]
E   AssertionError: assert 'ok' == 'invalid_tool_args'
E   AssertionError: assert 'tool_error' == 'timeout'
2 failed, 22 deselected in 1.42s
```

最小 GREEN：executor 在执行前拒绝 `argv`、`cwd`、`timeout` 之外的字段；只捕获
`subprocess.TimeoutExpired` 并保留已捕获的 stdout/stderr，将状态映射为 `timeout`。

GREEN 命令：

```powershell
uv run pytest tests/test_process_approval.py -k "unknown_arguments or reports_timeout" -v
```

输出：

```text
collected 24 items / 22 deselected / 2 selected
tests\test_process_approval.py .. [100%]
2 passed, 22 deselected in 1.19s
```

### 第三轮：cwd 解析 OS 错误 fail closed

本机 `Path.resolve(strict=False)` 接受包含 NUL 的词法路径，因此最初仅使用 NUL 的测试没有
复现 OS 边界异常。随后把测试修正为只替换 `PathVisibilityPolicy.resolve` 这一 OS 边界，使
其对目标 cwd 抛出 `OSError`；测试仍调用真实 `PolicyEngine` 和 `ToolRuntime`，不对 mock
调用次数或返回值做断言。

RED 命令：

```powershell
uv run pytest tests/test_process_approval.py -k cannot_be_resolved -v
```

关键输出：

```text
collected 25 items / 24 deselected / 1 selected
tests\test_process_approval.py F [100%]
E   OSError: malformed cwd
1 failed, 24 deselected in 0.29s
```

最小 GREEN：process policy 的 cwd visibility 检查捕获 `OSError`、`RuntimeError` 和
`VisibilityViolation`；hidden violation 保留 `prbench.hidden_path_blocked`，其余解析失败
统一 fail closed 为 `workspace.path_escape`。

GREEN 命令：

```powershell
uv run pytest tests/test_process_approval.py -k cannot_be_resolved -v
```

输出：

```text
collected 25 items / 24 deselected / 1 selected
tests\test_process_approval.py . [100%]
1 passed, 24 deselected in 0.24s
```

## 最终验证

brief 指定 focused 回归：

```powershell
uv run pytest tests/test_process_approval.py tests/test_shell_and_feedback.py tests/test_policy.py -v
```

输出：

```text
collected 52 items
tests\test_process_approval.py ......................... [ 48%]
tests\test_shell_and_feedback.py ..                      [ 51%]
tests\test_policy.py .........................           [100%]
52 passed in 2.21s
```

全量回归：

```powershell
uv run pytest
```

输出：

```text
189 passed, 3 skipped in 4.38s
```

三项 skip 是 Task 1 已记录的当前 Windows 会话 symlink 权限限制，没有新增失败或 skip。

静态类型检查：

```powershell
uvx pyright
```

输出：

```text
0 errors, 0 warnings, 0 informations
```

`git diff --check` 没有发现空白错误；仅报告仓库 Windows checkout 的 LF/CRLF 提示。

## 变更文件

- `src/phycode/approval.py`：Pydantic grant、manifest 加载、canonical matching 与一次性消费。
- `src/phycode/tools/process_tools.py`：结构化进程 schema、参数治理、allowlist 和真实 subprocess 执行。
- `src/phycode/policy.py`：`process.run` risky 分类与 profile-aware cwd visibility。
- `src/phycode/cli.py`：默认 registry 注册 process executor，profile subset 维持工具隔离。
- `tests/test_process_approval.py`：25 项真实行为、安全负例、审批和 profile 非回归测试。
- `.superpowers/sdd/task-2-report.md`：本报告。

## Self-review

- **真实结构化执行：** 唯一进程入口是 `subprocess.run(argv, shell=False, ...)`；测试实际启动
  Python 子进程并证明 shell 元字符没有被解释。
- **执行前治理：** argv 必须为非空字符串列表且无 NUL；cwd 必须是非空无 NUL 字符串并通过
  visibility；timeout 只接受非 bool 的 `1..300` 整数；argv[0] 的 basename 必须位于注入的
  casefold allowlist；未知字段 fail closed。
- **一次性精确审批：** file grant 只绑定完整 resolved path；process grant 只绑定完整 argv tuple
  与 resolved cwd。匹配成功立即删除；Pydantic 拒绝未知字段与不支持的工具/target 组合。
- **profile 隔离：** PRBench registry 有 `process.run`、无 `shell.run`；coding 仍有旧
  `shell.run` 且不会暴露 `process.run`；GAIA 全量回归通过。
- **Task 1 复用：** approval、executor 和 policy 都复用 `PathVisibilityPolicy`，没有复制路径逃逸
  逻辑，也没有新增 shell lexer/parser。
- **Task 2 边界：** 没有实现或导入 execution journal、artifact verifier、stop controller、runner；
  `journal=None` 仅维持 brief 约定的后续扩展签名。
- **凭据安全：** 实现、测试和报告没有读取或输出凭据文件，没有记录环境变量值，也没有接触真实
  LLM 或网络。
- **已知限制：** `process.run` 的 executable allowlist 由调用者注入；当前 CLI 默认只注入当前
  Python executable 的 basename。后续 PRBench runner 应按其隔离任务环境显式构造 allowlist，
  不应在 process executor 中扩大为隐式 PATH 通配。
