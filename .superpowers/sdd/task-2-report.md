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

---

## Reviewer Important 修复补充（基于 `c37467f`）

用户裁决安全约束优先后，设计与计划在 `c37467f` 修订。本节追加记录 reviewer Important
的修复过程，并取代上文关于 “basename allowlist” 和 “CLI 只注入 basename” 的旧结论；
旧文字保留用于审计最初实现及其被推翻原因。

### 修复 1：absolute executable identity

先把测试 helper 的 allowlist 改成 `frozenset[Path]`，新增两个真实行为测试：

- 把当前 Python executable 复制到临时目录中的同 basename 路径，断言不能因 basename
  相同而获得执行权；
- argv[0] 使用相对 executable 名称时必须拒绝。

RED 命令：

```powershell
uv run pytest tests/test_process_approval.py -k "same_basename or relative_executable" -v
```

关键输出：

```text
tests\test_process_approval.py FF [100%]
E   AttributeError: 'WindowsPath' object has no attribute 'casefold'
2 failed, 25 deselected in 0.26s
```

失败准确证明旧 executor 仍按 basename 字符串处理 allowlist，尚未支持设计要求的 Path
identity。最小修复把注入集合改为 `frozenset[Path]` 并逐个 `resolve()`；argv[0] 必须先是绝对
路径，再 `resolve()` 后与 allowlist 中的 Path 精确比较。实际执行 argv[0] 被替换为该 canonical
executable path。CLI 改为注入 `Path(sys.executable).resolve()`。

GREEN 命令与输出：

```powershell
uv run pytest tests/test_process_approval.py -k "same_basename or relative_executable" -v
```

```text
tests\test_process_approval.py .. [100%]
2 passed, 25 deselected in 0.18s
```

### 修复 2：固定最小环境与 ToolResult 边界脱敏

测试在父进程设置测试专用的 provider/key/token/credential/proxy/PATH/HOME 变量，真实子进程
只打印自身环境变量名称而不打印值；另用测试专用 secret pattern 验证普通 stdout、stderr 与
timeout 已捕获输出均经过脱敏。

RED 命令：

```powershell
uv run pytest tests/test_process_approval.py -k "minimal_environment or redacts_stdout_and_stderr or reports_timeout" -v
```

关键输出：

```text
tests\test_process_approval.py FFF [100%]
E   AssertionError: child environment contains inherited variables outside the allowlist
E   AssertionError: stdout contains sk-stdout-reviewer-...
E   AssertionError: timeout stdout contains sk-timeout-reviewer-...
3 failed, 26 deselected in 1.53s
```

最小修复只从父进程复制以下已经存在的变量：`SYSTEMROOT`、`WINDIR`、`TEMP`、`TMP`、
`TMPDIR`、`LANG`、`LC_ALL`、`PYTHONIOENCODING`、`PYTHONUTF8`。`subprocess.run` 显式接收
该 `env`，不传 PATH、HOME、provider、key、token、credential 或任何 proxy 变量。
所有 process executor 构造的 `ToolResult` 统一通过 `_result()`，在 stdout/stderr 字段进入
模型前调用 `redact_text()`；timeout bytes 和 OSError 文本也走同一边界。

GREEN 命令与输出：

```powershell
uv run pytest tests/test_process_approval.py -k "minimal_environment or redacts_stdout_and_stderr or reports_timeout" -v
```

```text
tests\test_process_approval.py ... [100%]
3 passed, 26 deselected in 1.32s
```

### 修复 3：Windows 新路径 normcase canonicalization

为避免已存在路径在 Windows `resolve()` 时自动恢复真实大小写而掩盖问题，RED 测试使用尚未
创建的写入目标 `Reproduction/NewFile.PY` 与 `reproduction/newfile.py`。另加 POSIX 分支测试，
明确不同大小写仍是不同目标。

RED 命令与输出：

```powershell
uv run pytest tests/test_process_approval.py -k "case_variant" -v
```

```text
tests\test_process_approval.py Fs [100%]
E   assert False
1 failed, 1 skipped, 29 deselected in 0.26s
```

最小修复让 grant 加载和调用匹配共享 `_canonical_path()`：先通过 Task 1
`PathVisibilityPolicy.resolve()`，随后对 resolved 字符串调用 `os.path.normcase()`。因此 Windows
大小写变体归一为同一 key，POSIX 的 `normcase()` 保持原字符串和大小写敏感语义。

GREEN 命令与输出：

```text
tests\test_process_approval.py .s [100%]
1 passed, 1 skipped, 29 deselected in 0.17s
```

### 修复 4：完整校验成功后才消费 grant

新增 process 调用参数化用例，覆盖未知字段、非法/错误类型 timeout、缺 argv、空 argv、空/NUL
argv 项、空/NUL cwd；新增 file.write/file.edit 用例，覆盖缺少必填字段、未知字段与错误字段类型。
每个 case 都先调用无效请求，再用完全合法的同 key 调用验证 grant 仍可使用一次。另验证 manifest
在加载时拒绝空/NUL path、相对 executable、空/NUL argv/cwd 等无效 grant。

调用消费 RED：

```powershell
uv run pytest tests/test_process_approval.py -k "does_not_consume_grant" -v
```

```text
tests\test_process_approval.py FFF....F.FFFFFF [100%]
10 failed, 5 passed, 31 deselected in 0.47s
```

invalid grant RED：

```powershell
uv run pytest tests/test_process_approval.py -k "invalid_grant_target" -v
```

```text
tests\test_process_approval.py FFFFFFFF [100%]
8 failed, 46 deselected in 0.43s
```

最小修复让 `ApprovalGrant` 先验证 target shape、空值、NUL 与 process absolute executable；
`ApprovalManifest._call_key()` 在 canonical key 匹配前验证每种工具的精确字段集合、必填字段、
字段类型、argv/cwd/NUL 和 timeout `1..300`。只有完整合法 key 精确匹配才删除 grant。

GREEN 命令与输出：

```text
uv run pytest tests/test_process_approval.py -k "does_not_consume_grant" -v
15 passed, 39 deselected in 0.30s

uv run pytest tests/test_process_approval.py -k "invalid_grant_target" -v
8 passed, 46 deselected in 0.23s
```

## Reviewer 修复后的最终验证

Task 2 测试文件：

```powershell
uv run pytest tests/test_process_approval.py -v
```

```text
53 passed, 1 skipped in 2.35s
```

要求的 focused + redaction 回归：

```powershell
uv run pytest tests/test_process_approval.py tests/test_shell_and_feedback.py tests/test_policy.py tests/test_redaction.py -v
```

```text
82 passed, 1 skipped in 2.28s
```

全量回归：

```powershell
uv run pytest
```

```text
217 passed, 4 skipped in 4.57s
```

静态类型检查：

```powershell
uvx pyright
```

```text
0 errors, 0 warnings, 0 informations
```

四项 skip 中，三项是 Task 1 已记录的 Windows symlink 权限限制；一项是本轮新增的 POSIX
大小写敏感分支测试在 Windows 按平台条件跳过。Windows normcase 测试在本机实际执行并通过。

## Reviewer 修复 Self-review 与边界声明

- **不再按 basename 放行：** allowlist 和 requested executable 都 canonicalize 为绝对 Path，
  精确 identity 匹配后才执行；同名副本和相对名称均被真实测试拒绝。
- **最小环境：** 子进程只收到 brief 列出的九类父进程已有变量；PATH、HOME、provider、key、
  token、credential 与 proxy 不会继承。测试不读取任何凭据文件，也不打印 fake secret 值。
- **输出边界：** 正常、非零、timeout 与 OSError 的 process ToolResult stdout/stderr 都在构造前
  调用统一 `redact_text()`；redaction 原有 focused 回归同时通过。
- **审批消费：** 无效 grant 在加载时失败；无效调用返回 false 且不消耗。file path 或 process
  argv+canonical cwd 完整、有效并精确匹配时才消费一次。
- **平台 canonicalization：** Windows 对 resolved 新路径使用 `normcase()`，POSIX 保持大小写敏感。
- **非回归：** PRBench 继续只暴露 `process.run` 而不暴露 `shell.run`；coding/GAIA 工具集合和
  旧 shell 行为由 focused 与全量测试证明未回归。
- **明确的安全边界：** 结构化 argv、absolute executable allowlist、最小环境和输出脱敏降低了
  注入与凭据继承风险，但**不替代 OS sandbox/container**。获批脚本仍可通过 Python 文件 API
  访问当前 OS 身份可访问的资源；真实执行前，主 agent 必须人工阅读并审查待执行脚本。Task 2
  没有新增 sandbox/container，也没有提前实现 journal、verifier 或 runner。
