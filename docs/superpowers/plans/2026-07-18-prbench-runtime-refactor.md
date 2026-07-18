# PRBench Runtime Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从干净基线重建 PRBench 最小纵向切片，以结构化进程执行、一次性审批、执行 provenance、产物验收和固定版本 evaluator adapter 跑通两个官方公开最小任务。

**Architecture:** `ProfileSpec` 是 profile 配置的单一来源，`PathVisibilityPolicy` 负责文件系统可见性，`process.run(argv)` 以 `shell=False` 执行。PRBench runner 用 `ExecutionJournal` 和 `ArtifactVerifier` 判定完成，AgentLoop 只把连续无进展重复视为停机；官方 adapter 在白色 agent 生命周期中不暴露 ground truth。

**Tech Stack:** Python 3.11+、Pydantic、Typer、OpenAI-compatible Chat Completions、uv、pytest、Pyright、Git patch、Docker 官方 PRBench evaluator。

## Global Constraints

- 所有项目文档使用中文；代码注释和 commit message 可使用英文。
- Python 包管理、测试和构建一律使用 `uv`；不得使用 `pip` 或 Conda 安装流程。
- 不使用 LangChain `AgentExecutor`、AutoGen、CrewAI、LlamaIndex agent 或宿主编码智能体 SDK agent runner。
- 每项生产代码变更严格执行 RED→GREEN→REFACTOR；必须先看到对应测试因缺失目标行为而失败。
- 白色 agent 生命周期内 `_ground_truth` 不挂载、不复制、不通过 allowlist 暴露；字符串规则只作纵深防御。
- PRBench profile 不公开自由文本 `shell.run`，结构化进程必须使用 `subprocess.run(argv, shell=False, ...)`。
- 真实运行的风险动作只使用主 agent 人工审定的一次性精确审批；未匹配调用 fail closed。
- API key/URL 不得写入 Git、配置、trace、memory、journal、result 或终端输出。
- 官方 evaluator 固定为 `HET-AGI/PRBench-Eval-Handson@3e5bee4545cad2138832f06302e9c98bd81f5216`；commit 不匹配时拒绝应用 adapter。
- 不运行或调优 `task_white_1993`；真实验收范围为 `aaatest_helloworld` 与 `bbbtest_alphabet`。
- coding 与 GAIA profile 必须保持非回归。

---

## 文件结构

- `src/phycode/profiles.py`：不可变 `ProfileSpec` 及三个内建 profile。
- `src/phycode/visibility.py`：workspace、allowlist、隐藏分量和 symlink 可见性。
- `src/phycode/approval.py`：规范化一次性审批 grant 与 manifest。
- `src/phycode/tools/process_tools.py`：PRBench 结构化进程工具，始终 `shell=False`。
- `src/phycode/execution.py`：artifact 快照与 `ExecutionJournal`。
- `src/phycode/prbench_contract.py`：公开任务契约、声明式约束和 verifier。
- `src/phycode/prbench_eval.py`：PRBench runner、结果状态和模块入口。
- `integrations/prbench/`：固定 commit adapter、公开 smoke contract 和应用脚本。
- `tests/test_profiles_visibility.py`：profile 单一来源与真实路径可见性。
- `tests/test_process_approval.py`：真实 argv 执行和一次性审批。
- `tests/test_execution_verifier.py`：真实脚本、journal、CSV provenance 和约束。
- `tests/test_prbench_loop.py`：完成验证反馈和连续无进展停机。
- `tests/test_prbench_runner.py`：runner 状态、脱敏结果和 CLI 契约。
- `tests/test_prbench_adapter.py`：固定 commit 和 adapter patch 契约。

---

### Task 1: Profile 单一来源与路径可见性

**依赖：** 无。

**Files:**
- Create: `src/phycode/profiles.py`
- Create: `src/phycode/visibility.py`
- Create: `tests/test_profiles_visibility.py`
- Modify: `src/phycode/models.py`
- Modify: `src/phycode/policy.py`
- Modify: `src/phycode/tools/search_tools.py`
- Modify: `src/phycode/cli.py`

**Interfaces:**
- Produces: `ProfileSpec`, `profile_spec(profile: AgentProfile) -> ProfileSpec`。
- Produces: `PathVisibilityPolicy.resolve(path: str | Path) -> Path`、`is_visible(path) -> bool`。
- Produces: `PolicyContext.profile_spec` 和 `PolicyContext.visibility`，后续任务不得再单独传 profile 状态。

- [ ] **Step 1: 写 profile 单一来源失败测试**

在 `tests/test_profiles_visibility.py` 写入：

```python
from pathlib import Path

import pytest

from phycode.models import AgentProfile
from phycode.profiles import profile_spec
from phycode.visibility import PathVisibilityPolicy, VisibilityViolation


def test_prbench_profile_is_single_source_of_runtime_limits() -> None:
    spec = profile_spec(AgentProfile.PRBENCH)
    assert spec.max_context_chars == 12_000
    assert spec.max_tool_calls == 40
    assert spec.hidden_path_components == frozenset({"_ground_truth"})
    assert "process.run" in spec.tool_names
    assert "shell.run" not in spec.tool_names
    assert "final is accepted only after artifact verification" in spec.system_prompt


def test_visibility_rejects_resolved_file_symlink_into_hidden_tree(tmp_path: Path) -> None:
    hidden = tmp_path / "_ground_truth"
    hidden.mkdir()
    secret = hidden / "sentinel.txt"
    secret.write_text("SENTINEL", encoding="utf-8")
    link = tmp_path / "public.txt"
    try:
        link.symlink_to(secret)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    visibility = PathVisibilityPolicy(tmp_path, hidden_components=frozenset({"_ground_truth"}))
    with pytest.raises(VisibilityViolation):
        visibility.resolve("public.txt")


def test_visibility_rejects_directory_symlink_during_search(tmp_path: Path) -> None:
    hidden = tmp_path / "_ground_truth"
    hidden.mkdir()
    (hidden / "sentinel.txt").write_text("SENTINEL", encoding="utf-8")
    link = tmp_path / "visible"
    try:
        link.symlink_to(hidden, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    visibility = PathVisibilityPolicy(tmp_path, hidden_components=frozenset({"_ground_truth"}))
    assert not visibility.is_visible(link / "sentinel.txt")
```

- [ ] **Step 2: 验证 RED**

运行：`uv run pytest tests/test_profiles_visibility.py -v`

预期：collection 因缺少 `phycode.profiles` / `phycode.visibility` 或 `AgentProfile.PRBENCH` 失败；不得因测试语法错误失败。

- [ ] **Step 3: 实现 `ProfileSpec` 和 visibility**

`src/phycode/profiles.py` 的公开形态：

```python
from __future__ import annotations

from dataclasses import dataclass

from phycode.context import CODING_SYSTEM_PROMPT, GAIA_SYSTEM_PROMPT
from phycode.models import AgentProfile

PRBENCH_SYSTEM_PROMPT = """You are PhyCode reproducing a public PRBench task.
Use only visible workspace inputs. Generate data by running reproduction scripts.
Inspect required artifacts before finishing; final is accepted only after artifact verification."""


@dataclass(frozen=True)
class ProfileSpec:
    profile: AgentProfile
    tool_names: frozenset[str]
    system_prompt: str
    max_context_chars: int
    max_tool_calls: int
    hidden_path_components: frozenset[str] = frozenset()


def profile_spec(profile: AgentProfile) -> ProfileSpec:
    return _PROFILE_SPECS[profile]
```

三个 spec 使用现有 GAIA/coding 工具集合；PRBench 工具精确为：`calculator.calculate`、`file.edit`、`file.inspect`、`file.list`、`file.read`、`file.write`、`image.inspect`、`process.run`、`search.glob`、`search.grep`、`workspace.status`。

`src/phycode/visibility.py` 实现不可变 policy：保存 resolved workspace/allowlist；先检查词法分量，再 `resolve(strict=False)`，再检查 resolved 分量与允许根。

- [ ] **Step 4: 把 policy、search 和 CLI 接到同一个 spec**

`PolicyContext` 保留现有前三个位置参数以兼容 coding/GAIA 测试，并增加：

```python
profile_spec: ProfileSpec = field(default_factory=lambda: profile_spec(AgentProfile.CODING))

@property
def visibility(self) -> PathVisibilityPolicy:
    return PathVisibilityPolicy(
        self.workspace_root,
        self.allowlist,
        self.profile_spec.hidden_path_components,
    )
```

`PolicyEngine` 对所有 path 参数调用 `context.visibility.resolve()`；显式隐藏路径返回 `prbench.hidden_path_blocked`。`register_search_tools` 接收 `visibility: PathVisibilityPolicy | None`，每个候选在展示/读取前检查 `visibility.is_visible()`，并在 `os.walk` 进入目录前剪枝。

`build_agent` 只调用一次 `spec = profile_spec(profile)`，registry subset、prompt、context chars、tool budget 和 `PolicyContext` 均使用该对象。

- [ ] **Step 5: 验证 GREEN 和非回归**

运行：

```powershell
uv run pytest tests/test_profiles_visibility.py tests/test_policy.py tests/test_extended_tools.py tests/test_cli_commands.py -v
uvx pyright
```

预期：全部通过，Pyright 0 errors；coding/GAIA 工具行为不变。

- [ ] **Step 6: 提交**

```powershell
git add src/phycode/models.py src/phycode/profiles.py src/phycode/visibility.py src/phycode/policy.py src/phycode/tools/search_tools.py src/phycode/cli.py tests/test_profiles_visibility.py
git commit -m "refactor(prbench): centralize profile and visibility"
```

---

### Task 2: 结构化进程执行与一次性审批

**依赖：** Task 1。

**Files:**
- Create: `src/phycode/approval.py`
- Create: `src/phycode/tools/process_tools.py`
- Create: `tests/test_process_approval.py`
- Modify: `src/phycode/cli.py`
- Modify: `src/phycode/policy.py`

**Interfaces:**
- Produces: `ApprovalGrant`, `ApprovalManifest.from_json(path, workspace_root)`，对象可作为现有 `ApprovalHandler` 调用。
- Produces: `register_process_tools(registry, workspace_root, allowed_executables, journal=None)`。
- `process.run` schema 固定为 `argv: list[str]`、`cwd: str`、`timeout: int`。

- [ ] **Step 1: 写真实执行和审批失败测试**

```python
import json
import sys
from pathlib import Path

from phycode.approval import ApprovalManifest
from phycode.models import AgentProfile, PolicyAction, ToolCall
from phycode.policy import PolicyContext, PolicyEngine
from phycode.profiles import profile_spec
from phycode.tools import ToolRegistry, ToolRuntime
from phycode.tools.process_tools import register_process_tools


def test_process_run_passes_metacharacters_as_literal_argv(tmp_path: Path) -> None:
    script = tmp_path / "argv.py"
    script.write_text(
        "import pathlib, sys\npathlib.Path('seen.txt').write_text(sys.argv[1], encoding='utf-8')\n",
        encoding="utf-8",
    )
    registry = ToolRegistry()
    register_process_tools(registry, tmp_path, frozenset({Path(sys.executable).name.casefold()}))
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "argv.py", "literal & not-a-shell"], "cwd": "."},
    )
    context = PolicyContext(tmp_path, [], False, profile_spec(AgentProfile.PRBENCH))
    result = ToolRuntime(registry).run(call, context, approved=True)
    assert result.tool_result.status == "ok"
    assert (tmp_path / "seen.txt").read_text(encoding="utf-8") == "literal & not-a-shell"


def test_exact_approval_is_consumed_once(tmp_path: Path) -> None:
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": [{"tool_name": "file.write", "path": "reproduction/a.py"}]}), encoding="utf-8")
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    call = ToolCall(tool_name="file.write", args={"path": "reproduction/a.py", "content": "print(1)"})
    decision = PolicyEngine().decide(call, PolicyContext(tmp_path, [], False))
    assert decision.decision == PolicyAction.ASK
    assert manifest(call, decision)
    assert not manifest(call, decision)


def test_approval_does_not_match_different_argv(tmp_path: Path) -> None:
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"grants": [{"tool_name": "process.run", "argv": ["python", "reproduction/a.py"], "cwd": "."}]}), encoding="utf-8")
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    decision = PolicyEngine().decide(ToolCall(tool_name="process.run", args={"argv": ["python", "reproduction/b.py"], "cwd": "."}), PolicyContext(tmp_path, [], False))
    assert not manifest(ToolCall(tool_name="process.run", args={"argv": ["python", "reproduction/b.py"], "cwd": "."}), decision)
```

- [ ] **Step 2: 验证 RED**

运行：`uv run pytest tests/test_process_approval.py -v`

预期：缺少 approval/process 模块而失败。

- [ ] **Step 3: 实现精确审批 canonicalization**

`ApprovalGrant` 使用 Pydantic：

```python
class ApprovalGrant(BaseModel):
    tool_name: str
    path: str | None = None
    argv: tuple[str, ...] | None = None
    cwd: str | None = None
```

加载时把 path/cwd 通过 Task 1 visibility 解析为绝对规范化字符串；调用时对 `file.write`/`file.edit` 或 `process.run` 构造相同 canonical tuple。grant 匹配后从内部剩余列表删除。JSON 顶层只允许 `{"grants": [...]}`，未知字段由 `extra="forbid"` 拒绝。

- [ ] **Step 4: 实现 `process.run`**

执行前验证：argv 非空且每项为非空无 NUL 字符串；cwd 可见；timeout 在 1..300；`Path(argv[0]).name.casefold()` 位于注入的 executable allowlist。执行必须是：

```python
completed = subprocess.run(
    argv,
    cwd=resolved_cwd,
    shell=False,
    text=True,
    capture_output=True,
    timeout=timeout,
)
```

PRBench registry 注册 `process.run`，policy 将其列为 `RISKY_TOOLS`；PRBench spec 不含 `shell.run`。coding profile 旧 shell 工具保持不变。

- [ ] **Step 5: 验证 GREEN、安全负例和非回归**

运行：

```powershell
uv run pytest tests/test_process_approval.py tests/test_shell_and_feedback.py tests/test_policy.py -v
uvx pyright
```

预期：全部通过；真实 argv 测试创建的 `seen.txt` 内容保留 `&` 字面量。

- [ ] **Step 6: 提交**

```powershell
git add src/phycode/approval.py src/phycode/tools/process_tools.py src/phycode/cli.py src/phycode/policy.py tests/test_process_approval.py
git commit -m "feat(prbench): add structured approved process runtime"
```

---

### Task 3: Execution journal、公开任务契约与 artifact verifier

**依赖：** Task 2。

**Files:**
- Create: `src/phycode/execution.py`
- Create: `src/phycode/prbench_contract.py`
- Create: `tests/test_execution_verifier.py`
- Modify: `src/phycode/tools/process_tools.py`

**Interfaces:**
- Produces: `ArtifactSnapshot`, `ProcessExecutionRecord`, `ExecutionJournal.record_process(...)`。
- Produces: `ArtifactConstraint`, `TaskContract`, `VerificationIssue`, `VerificationResult`, `ArtifactVerifier.verify()`。
- `register_process_tools(..., journal: ExecutionJournal | None)` 在真实执行前后调用 journal。

- [ ] **Step 1: 写真实脚本 provenance 失败测试**

```python
import csv
import sys
from pathlib import Path

from phycode.execution import ExecutionJournal
from phycode.models import AgentProfile, ToolCall
from phycode.policy import PolicyContext
from phycode.prbench_contract import ArtifactConstraint, ArtifactVerifier, TaskContract
from phycode.profiles import profile_spec
from phycode.tools import ToolRegistry, ToolRuntime
from phycode.tools.process_tools import register_process_tools


def _contract() -> TaskContract:
    return TaskContract(
        instruction_file="instruction.md",
        paper_file="paper.md",
        expected_files=("reproduction/generate.py", "data/output.csv"),
        constraints=(ArtifactConstraint(path="data/output.csv", csv_header=("a", "b"), csv_rows=(("1", "2"),)),),
    )


def test_csv_requires_successful_script_provenance(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data/output.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    result = ArtifactVerifier(tmp_path, _contract(), ExecutionJournal(tmp_path, ("data/output.csv",))).verify()
    assert not result.ok
    assert {issue.code for issue in result.issues} == {"script_not_executed", "csv_without_provenance"}


def test_real_script_execution_establishes_csv_provenance(tmp_path: Path) -> None:
    (tmp_path / "reproduction").mkdir()
    (tmp_path / "reproduction/generate.py").write_text(
        "import csv, pathlib\npathlib.Path('data').mkdir(exist_ok=True)\n"
        "with open('data/output.csv','w',newline='') as f:\n"
        " w=csv.writer(f); w.writerow(['a','b']); w.writerow([1,2])\n",
        encoding="utf-8",
    )
    journal = ExecutionJournal(tmp_path, ("data/output.csv",))
    registry = ToolRegistry()
    register_process_tools(registry, tmp_path, frozenset({Path(sys.executable).name.casefold()}), journal=journal)
    call = ToolCall(tool_name="process.run", args={"argv": [sys.executable, "reproduction/generate.py"], "cwd": "."})
    context = PolicyContext(tmp_path, [], False, profile_spec(AgentProfile.PRBENCH))
    assert ToolRuntime(registry).run(call, context, approved=True).tool_result.status == "ok"
    result = ArtifactVerifier(tmp_path, _contract(), journal).verify()
    assert result.ok
    with (tmp_path / "data/output.csv").open(newline="", encoding="utf-8") as handle:
        assert list(csv.reader(handle)) == [["a", "b"], ["1", "2"]]
```

- [ ] **Step 2: 验证 RED**

运行：`uv run pytest tests/test_execution_verifier.py -v`

预期：缺少 execution/contract 模块而失败。

- [ ] **Step 3: 实现 journal**

artifact 快照只覆盖 contract 关注路径，记录相对路径、存在性、size、SHA-256。`ExecutionJournal` 在内存保存 records，并把每条脱敏 JSON 追加到 `.phycode/prbench/execution.jsonl`；父目录自动创建。成功执行记录 script 相对路径/哈希和 changed artifact 列表，失败执行也记录退出状态但不能建立 provenance。

- [ ] **Step 4: 实现 contract/verifier**

Pydantic 模型禁止未知字段：

```python
class ArtifactConstraint(BaseModel):
    path: str
    csv_header: tuple[str, ...] | None = None
    csv_rows: tuple[tuple[str, ...], ...] | None = None


class TaskContract(BaseModel):
    instruction_file: str
    paper_file: str
    input_files: tuple[str, ...] = ()
    expected_files: tuple[str, ...]
    constraints: tuple[ArtifactConstraint, ...] = ()
```

verifier 检查 expected file 存在/非空；每个 expected `.py` 至少有相同脚本哈希的成功执行；每个 expected `.csv` 位于成功 record 的 changed artifacts；有 constraint 时用 `csv.reader` 精确比较 header/rows。

- [ ] **Step 5: 验证 GREEN 与 trace 脱敏**

运行：

```powershell
uv run pytest tests/test_execution_verifier.py tests/test_process_approval.py tests/test_redaction.py -v
uvx pyright
```

预期：全部通过；journal 不包含当前环境变量或绝对凭据路径。

- [ ] **Step 6: 提交**

```powershell
git add src/phycode/execution.py src/phycode/prbench_contract.py src/phycode/tools/process_tools.py tests/test_execution_verifier.py
git commit -m "feat(prbench): verify artifacts with execution provenance"
```

---

### Task 4: AgentLoop 完成门禁与连续无进展停机

**依赖：** Task 3。

**Files:**
- Create: `tests/test_prbench_loop.py`
- Modify: `src/phycode/agent.py`
- Modify: `src/phycode/models.py`
- Modify: `src/phycode/feedback.py`

**Interfaces:**
- AgentLoop 新增可选 `completion_verifier: Callable[[], VerificationResult] | None`。
- AgentLoop 新增可选 `progress_fingerprint: Callable[[], str] | None`。
- PRBench 成功停止原因为 `completed`；验收失败反馈 kind 为 `artifact_verification_failed`。

- [ ] **Step 1: 写真实闭环失败测试**

测试使用完整 ScriptedLLM event schema，不断言 mock 自身；断言真实 AgentLoop 的停机与反馈：

```python
def test_final_with_missing_artifact_feeds_back_and_continues(tmp_path: Path) -> None:
    llm = ScriptedLLM([
        [{"type": "assistant_final", "payload": {"text": "done"}}],
        [{"type": "tool_call_requested", "payload": {"tool_name": "file.write", "args": {"path": "result.txt", "content": "ok"}}}],
        [{"type": "assistant_final", "payload": {"text": "verified"}}],
    ])
    loop = build_loop_with_file_contract(tmp_path, llm, expected="result.txt")
    result = loop.run("create result")
    assert result.stopped_reason == "completed"
    assert any(event.type.value == "feedback_signal" and event.payload.get("kind") == "artifact_verification_failed" for event in result.events)


def test_interleaved_same_status_calls_do_not_trigger_repeat_stop(tmp_path: Path) -> None:
    loop = build_loop_for_interleaved_progress(tmp_path)
    result = loop.run("create two files")
    assert result.stopped_reason == "completed"


def test_three_consecutive_identical_no_progress_actions_stop_as_failure(tmp_path: Path) -> None:
    loop = build_loop_for_repeated_status(tmp_path)
    result = loop.run("inspect workspace")
    assert result.stopped_reason == "repeated_no_progress"
```

helper 必须组装真实 `AgentLoop`、`ToolRuntime`、`SessionStore` 和临时 trace；只把 LLM 外部依赖替换为 ScriptedLLM。

- [ ] **Step 2: 验证 RED**

运行：`uv run pytest tests/test_prbench_loop.py -v`

预期：首个 final 直接返回 `final`，或 interleaved status 被旧全局计数提前结束。

- [ ] **Step 3: 实现 verifier 门禁**

收到 `ASSISTANT_FINAL` 时：无 verifier 保持旧行为；有 verifier 且成功返回 `completed`；失败时记录：

```python
AgentEventType.FEEDBACK_SIGNAL,
{
    "kind": "artifact_verification_failed",
    "summary": "Required artifacts are incomplete or unverifiable",
    "evidence": {"issues": [issue.model_dump(mode="json") for issue in verification.issues]},
    "retryable": True,
    "suggested_next_step": "Create, run, and inspect the missing reproduction artifacts",
}
```

然后继续下一轮，不能保留为成功 final。

- [ ] **Step 4: 实现连续无进展计数**

删除 `action_result_counts`。维护 `last_action_result_key`、`consecutive_repeat_count` 和 `last_progress_fingerprint`。动作 key 改变或 fingerprint 变化时计数重置；只有相同 key 且 fingerprint 未变才递增。达到阈值直接返回 `repeated_no_progress`，不得调用 `_finalize_from_evidence` 把失败包装成 final。

工具预算耗尽时若 verifier 存在：先验证；成功返回 `completed`，失败返回 `artifact_verification_failed`。无 verifier 时维持现有 coding/GAIA finalization。

- [ ] **Step 5: 验证 GREEN 和既有 loop 非回归**

运行：

```powershell
uv run pytest tests/test_prbench_loop.py tests/test_agent_loop.py tests/test_cli_commands.py -v
uvx pyright
```

预期：全部通过；原有无 verifier 的 final 行为不变。

- [ ] **Step 6: 提交**

```powershell
git add src/phycode/agent.py src/phycode/models.py src/phycode/feedback.py tests/test_prbench_loop.py
git commit -m "fix(agent): gate completion on verified progress"
```

---

### Task 5: PRBench runner 与 CLI 状态契约

**依赖：** Task 4。

**Files:**
- Create: `src/phycode/prbench_eval.py`
- Create: `tests/test_prbench_runner.py`
- Modify: `src/phycode/cli.py`
- Modify: `src/phycode/config.py`

**Interfaces:**
- Produces: `PRBenchRunStatus`、`PRBenchRunResult`、`run_prbench(...) -> PRBenchRunResult`。
- Produces CLI: `phycode prbench run --workspace ... --contract ... --approvals ...`。
- Module entry: `uv run python -m phycode.prbench_eval run ...`。

- [ ] **Step 1: 写 runner 失败测试**

```python
import json
from pathlib import Path

from phycode.llm import ScriptedLLM
from phycode.prbench_eval import PRBenchRunStatus, run_prbench


def test_runner_returns_non_success_when_final_artifacts_are_missing(tmp_path: Path) -> None:
    _write_public_task_files(tmp_path)
    llm = ScriptedLLM([[{"type": "assistant_final", "payload": {"text": "done"}}]])
    result = run_prbench(tmp_path, tmp_path / "contract.json", tmp_path / "approvals.json", llm=llm, max_tool_calls=2)
    assert result.status == PRBenchRunStatus.ARTIFACT_VERIFICATION_FAILED
    assert result.exit_code != 0


def test_runner_executes_script_and_writes_sanitized_result(tmp_path: Path) -> None:
    _write_public_task_files(tmp_path)
    llm = scripted_llm_that_writes_runs_reads_and_finishes()
    result = run_prbench(tmp_path, tmp_path / "contract.json", tmp_path / "approvals.json", llm=llm, max_tool_calls=8)
    assert result.status == PRBenchRunStatus.COMPLETED
    payload = json.loads((tmp_path / ".phycode/prbench/run_result.json").read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert "api_key" not in json.dumps(payload).casefold()
```

helpers 写入真实 instruction/paper、contract、精确 approvals；ScriptedLLM 只替代 provider，file/process/verifier 使用真实实现。

- [ ] **Step 2: 验证 RED**

运行：`uv run pytest tests/test_prbench_runner.py -v`

预期：缺少 `prbench_eval` 而失败。

- [ ] **Step 3: 实现 runner**

`PRBenchRunStatus` 精确包含：`completed`、`approval_required`、`policy_blocked`、`provider_error`、`process_failed`、`artifact_verification_failed`、`repeated_no_progress`、`tool_budget_exhausted`。

`run_prbench`：加载 contract/approval；拒绝 workspace 中已存在 `_ground_truth`；构造 journal/verifier/profile/registry/loop；prompt 由 instruction 与 paper 内容组成；把 AgentLoop stop reason 映射为 status；通过临时文件加 `Path.replace()` 原子写入脱敏 `run_result.json`。

未注入 `llm` 时只从以下环境变量构造 adapter：`PHYCODE_API_KEY`、`PHYCODE_BASE_URL`、`PHYCODE_MODEL`。缺失任一项即 provider error，不得回退 EchoLLM。

- [ ] **Step 4: 接入 CLI**

新增 Typer `prbench` 子命令组。命令只输出 status、模型名、工具计数和 artifact 相对路径；不输出 base URL。退出码使用 `PRBenchRunResult.exit_code`。

- [ ] **Step 5: 验证 GREEN、CLI 和秘密扫描**

运行：

```powershell
uv run pytest tests/test_prbench_runner.py tests/test_cli_smoke.py tests/test_redaction.py -v
uv run phycode prbench run --help
uvx pyright
```

预期：测试通过，help 含 workspace/contract/approvals，Pyright 0 errors。

- [ ] **Step 6: 提交**

```powershell
git add src/phycode/prbench_eval.py src/phycode/cli.py src/phycode/config.py tests/test_prbench_runner.py tests/test_cli_smoke.py
git commit -m "feat(prbench): add verified task runner"
```

---

### Task 6: 固定版本官方 evaluator adapter

**依赖：** Task 5。

**Files:**
- Create: `integrations/prbench/README.md`
- Create: `integrations/prbench/apply_adapter.py`
- Create: `integrations/prbench/phycode-evaluator.patch`
- Create: `integrations/prbench/public_contracts/aaatest_helloworld.json`
- Create: `integrations/prbench/public_contracts/bbbtest_alphabet.json`
- Create: `tests/test_prbench_adapter.py`

**Interfaces:**
- Produces: `EXPECTED_EVALUATOR_COMMIT` 常量和 `apply_adapter(repo: Path, wheel: Path) -> None`。
- Patch 给官方 `white-agent-type` 增加 `phycode`，不改变 green grader、ground-truth copy 时序或 grading rubric。

- [ ] **Step 1: 写 fixed-commit 与 patch 失败测试**

```python
import subprocess
from pathlib import Path

import pytest

from integrations.prbench.apply_adapter import EXPECTED_EVALUATOR_COMMIT, AdapterError, apply_adapter


def test_adapter_rejects_wrong_evaluator_commit(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("wrong", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "wrong"], cwd=tmp_path, check=True, capture_output=True)
    with pytest.raises(AdapterError, match=EXPECTED_EVALUATOR_COMMIT):
        apply_adapter(tmp_path, tmp_path / "phycode.whl")


def test_patch_does_not_change_ground_truth_copy_order() -> None:
    patch = Path("integrations/prbench/phycode-evaluator.patch").read_text(encoding="utf-8")
    assert "_copy_ground_truth_to_workspace" not in patch
    assert "grading.dimensions" not in patch
    assert "phycode prbench run" in patch
```

- [ ] **Step 2: 验证 RED**

运行：`uv run pytest tests/test_prbench_adapter.py -v`

预期：缺少 integration module/patch 而失败。

- [ ] **Step 3: 实现 adapter 应用器**

`apply_adapter.py` 使用 `git rev-parse HEAD` 精确比对固定 commit；检查 wheel 存在；执行 `git apply --check` 后再 `git apply`；把 wheel 复制到 evaluator 内 `.phycode-adapter/phycode.whl`。任何 subprocess 非零都转为不含环境值的 `AdapterError`。

- [ ] **Step 4: 生成并审计版本化 patch**

在固定 commit 的官方临时 clone 中最小修改：

- `main.py` 的 agent type help/validation 接受 `phycode` white type；
- `src/white_agent/agent.py` 的 white CLI branch 调用容器内 `phycode prbench run`；
- `src/my_util/docker_manager.py` 安装 adapter wheel；
- white workspace 构造公开 `task_contract.json`，且不引用 metadata/data/reproduce ground truth；
- 保持 `_copy_ground_truth_to_workspace` 所在 `src/green_agent/agent.py` 不变。

用 `git diff --binary` 生成 `phycode-evaluator.patch`。两个 public contract JSON 分别声明公开 instruction 中的 header/rows；不得包含 metadata 内容。

- [ ] **Step 5: 验证 GREEN 与 patch check**

运行：

```powershell
uv run pytest tests/test_prbench_adapter.py -v
uv run python integrations/prbench/apply_adapter.py --help
uvx pyright
```

另在固定官方 clone 副本执行 `git apply --check <absolute-patch-path>`，预期退出 0；对错误 commit 执行应用器，预期非零且仓库无改动。

- [ ] **Step 6: 提交**

```powershell
git add integrations/prbench tests/test_prbench_adapter.py
git commit -m "feat(prbench): add pinned evaluator adapter"
```

---

### Task 7: 文档、过程证据与可重复真实 smoke 命令

**依赖：** Task 6。

**Files:**
- Modify: `README.md`
- Modify: `PLAN.md`
- Modify: `AGENT_LOG.md`
- Modify: `SPEC_PROCESS.md`
- Create: `integrations/prbench/run_public_smoke.ps1`
- Modify: `tests/test_docs_process.py`

**Interfaces:**
- Produces不含凭据的官方 adapter 构建、审批清单、runner 和两个 smoke task 命令。
- smoke 脚本只读取 `PHYCODE_API_KEY`、`PHYCODE_BASE_URL`、`PHYCODE_MODEL`；不得接收或保存 key 文件路径。

- [ ] **Step 1: 写文档契约失败测试**

在 `tests/test_docs_process.py` 增加断言：README 包含 `phycode prbench run`、`process.run`、官方固定 commit、`run_public_smoke.ps1`、ground truth 生命周期边界、真实测试不属于默认 pytest；PLAN/AGENT_LOG/SPEC_PROCESS 包含此次重构、旧 parser 被拒绝原因和真实 API 验收边界。

- [ ] **Step 2: 验证 RED**

运行：`uv run pytest tests/test_docs_process.py -v`

预期：README/过程文档缺少新命令而失败。

- [ ] **Step 3: 编写 smoke 脚本**

脚本参数精确为：

```powershell
param(
    [Parameter(Mandatory=$true)][string]$EvaluatorRoot,
    [Parameter(Mandatory=$true)][string]$WheelPath,
    [ValidateSet('aaatest_helloworld','bbbtest_alphabet')][string[]]$TaskIds
)
```

脚本首先验证三个 `PHYCODE_*` 环境变量非空，只输出变量是否配置；调用 adapter；为每个 task 使用对应 public contract 和人工审批清单启动官方 evaluator。脚本不得 `Write-Output` 环境变量值，不得创建 `.env`。

- [ ] **Step 4: 更新中文文档与过程记录**

README 明确区分 deterministic tests、真实模型 runner smoke、官方 Docker evaluator；记录 Docker daemon 必须运行。AGENT_LOG 按 task 记录技能、RED/GREEN、提交和审查；PLAN 标记 Task 14-20；SPEC_PROCESS 记录真实测试如何推翻旧 parser/假完成方案。

- [ ] **Step 5: 验证文档、全量测试与类型检查**

运行：

```powershell
uv run pytest tests/test_docs_process.py -v
uv run pytest
uvx pyright
git diff --check f2817ab..HEAD
git ls-files ".env" ".env.*" "*.pem" "*.key"
```

预期：测试全部通过、Pyright 0 errors、diff check 0、凭据跟踪扫描无输出。

- [ ] **Step 6: 提交**

```powershell
git add README.md PLAN.md AGENT_LOG.md SPEC_PROCESS.md integrations/prbench/run_public_smoke.ps1 tests/test_docs_process.py
git commit -m "docs(prbench): document verified evaluator workflow"
```

---

## 最终真实验收（主 agent 执行，不委派凭据）

1. 从 `D:\BaiduSyncdisk\NewTextDocument.txt` 在当前 PowerShell 进程内读取 URL/key/model，设置三个 `PHYCODE_*` 环境变量；不得输出值。
2. 构建 wheel：`uv build`。
3. 启动并确认 Docker daemon；clone 官方 evaluator 并 checkout 固定 commit。
4. 主 agent 逐项审阅两个 smoke task 的精确 approvals；任何额外调用不加入清单。
5. 分别运行 `aaatest_helloworld` 和 `bbbtest_alphabet` 官方流程。
6. 验收每项 white runner status 为 `completed`、expected outputs 存在、官方 evaluator 生成报告、trace/result 不含 key/URL。
7. 若真实模型失败，按 `systematic-debugging` 先定位根因；任何修复先添加可复现的失败测试，不进行 prompt/规则打地鼠。
8. 真实验收通过后，运行最终全量 `uv run pytest`、`uvx pyright`、凭据扫描，并请求 whole-branch review。

## 依赖关系

- Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6 → Task 7。
- 每个 task 完成后必须先进行 spec 合规与代码质量双阶段审查，Critical/Important 清零后才进入下一项。
- 真实 API 与官方 evaluator 验收只在 Task 7 和全量测试通过后执行。

## 计划自审

- **Spec coverage：** profile、visibility、结构化进程、一次性审批、journal、verifier、stop controller、runner、固定 adapter、真实两个 smoke task 均有对应 task。
- **Placeholder scan：** 本计划没有未决占位标记；后续明确排除项来自已批准设计的非目标。
- **Type consistency：** `ProfileSpec` 由 Task 1 定义并贯穿 Task 2-5；`ExecutionJournal`/`TaskContract`/`VerificationResult` 由 Task 3 定义并由 Task 4-5 消费；runner status 由 Task 5 定义并由 Task 6-7 使用。
