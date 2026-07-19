# PRBench 完整公开任务实施计划

> **面向智能体工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实施本计划。步骤使用复选框（`- [ ]`）语法进行追踪。

**目标：** 在不读取 ground truth、不加入任务专用求解逻辑且不污染 `main` 的前提下，让 PhyCode 使用真实大模型 API 在固定官方 evaluator 上完成 `task_white_1993`，取得白色 runner `completed` 与有效绿色 grader report。

**架构：** `TaskContract.execution_entrypoints` 区分需要直接执行的 figure scripts 与只需存在的核心模块，`ArtifactVerifier` 只接受当前入口脚本执行产生的 CSV provenance。PRBench runner 使用小于 4,000 字的紧凑任务简报，并通过显式参数把完整任务的 24,000 字上下文与 50 次工具预算逐层传入固定 evaluator；静态文件审批精确限定公开 reproduction 路径，进程审批继续绑定绝对 argv、cwd 与脚本 SHA-256。

**技术栈：** Python 3.11+、Pydantic v2、Typer、OpenAI-compatible Chat Completions、uv、pytest、Pyright、PowerShell、Git patch、Docker、固定 PRBench evaluator commit。

## 全局约束

- 所有项目文档使用中文；代码注释和 commit message 可使用英文。
- Python 包管理、测试和构建一律使用 `uv`；不得使用 pip 或 Conda 工作流。
- 不使用 LangChain `AgentExecutor`、AutoGen、CrewAI、LlamaIndex agent 或宿主编码智能体 SDK agent runner。
- 每项生产代码变更严格执行 RED→GREEN→REFACTOR；先观察目标行为缺失导致的失败。
- 白色 agent 生命周期中 `_ground_truth` 不挂载、不复制、不通过 allowlist 暴露。
- 不读取、复制或编码 `metadata.md`、参考实现、参考 CSV 或 grader 隐藏信息。
- 不加入 DMRG 专用求解代码、隐藏答案启发式或按 task ID 分支的 solver 逻辑。
- PRBench profile 不公开自由文本 `shell.run`；进程执行保持 `subprocess.run(argv, shell=False, ...)`。
- `process.run` 只接受人工审核、脚本 SHA-256 绑定的一次性精确审批。
- API key 和 base URL 不得进入 Git、配置、trace、memory、journal、result、报告、命令参数或终端输出。
- 固定 evaluator 为 `HET-AGI/PRBench-Eval-Handson@3e5bee4545cad2138832f06302e9c98bd81f5216`。
- 既有 smoke 默认仍为 12,000 字上下文与 40 次工具调用；完整任务显式使用 24,000 字与 50 次工具调用。
- 最多三次进入白色模型响应阶段的正式尝试；每次使用全新 evaluator workspace。
- 所有提交只进入 `codex/prbench-public-test`；未经用户明确授权不得合并、fast-forward 或 push 到 `main`。
- `dist/`、evaluator clone、Docker/workspace、`.phycode/`、trace、journal、run result、grader report 和模型生成物不得提交。

---

## 文件结构

- `src/phycode/prbench_contract.py`：完整任务 artifact 角色、entrypoint 与 CSV 公开约束。
- `src/phycode/prbench_eval.py`：紧凑任务简报、完整任务运行参数与 runner CLI。
- `src/phycode/composition.py`：显式上下文预算覆盖并保持 profile 默认值。
- `integrations/prbench/public_contracts/*.json`：三个公开任务的声明式 contract。
- `integrations/prbench/phycode-evaluator.patch`：固定 evaluator 参数逐层透传。
- `integrations/prbench/run_public_full.ps1`：只运行 `task_white_1993` 的可重复官方入口。
- `tests/test_execution_verifier.py`：entrypoint、当前哈希、CSV provenance 和 row-count 测试。
- `tests/prbench_test_support.py`：确定性 runner fixture 的 contract 迁移。
- `tests/test_prbench_runner.py`：紧凑简报、上下文预算、CLI 和 runner 回归。
- `tests/test_prbench_adapter.py`：固定 patch、完整 contract 与参数透传。
- `tests/test_docs_process.py`：完整任务脚本、文档、产物与主分支边界。
- `README.md`、`PLAN.md`、`SPEC_PROCESS.md`、`AGENT_LOG.md`：中文用户与过程证据。

---

### Task 32: Contract entrypoint 与完整 artifact verifier

**依赖：** 已批准设计 `docs/superpowers/specs/2026-07-19-prbench-public-full-test-design.md`。

**文件：**
- 修改：`src/phycode/prbench_contract.py`
- 修改：`tests/test_execution_verifier.py`
- 修改：`tests/prbench_test_support.py`
- 修改：`integrations/prbench/public_contracts/aaatest_helloworld.json`
- 修改：`integrations/prbench/public_contracts/bbbtest_alphabet.json`
- 修改：`tests/test_prbench_adapter.py`

**接口：**
- 产出：`TaskContract.execution_entrypoints: tuple[str, ...] = ()`。
- 产出：`ArtifactConstraint.csv_data_row_count: int | None = None`，计数不含 header。
- 保持：`ArtifactVerifier.verify() -> VerificationResult`。
- 语义：expected 普通文件只要求存在且非空；entrypoint 额外要求当前 SHA-256 成功执行；CSV provenance 只接受当前 entrypoint 的同一成功 record。

- [ ] **步骤 1：写 contract 校验 RED**

在 `tests/test_execution_verifier.py` 增加：

```python
def test_contract_rejects_entrypoint_outside_expected_files() -> None:
    with pytest.raises(ValidationError, match="entrypoint"):
        TaskContract(
            instruction_file="instruction.md",
            paper_file="paper.md",
            expected_files=("reproduction/core.py",),
            execution_entrypoints=("reproduction/run.py",),
        )


def test_contract_rejects_non_python_and_duplicate_entrypoints() -> None:
    with pytest.raises(ValidationError, match="Python"):
        TaskContract(
            instruction_file="instruction.md",
            paper_file="paper.md",
            expected_files=("data/output.csv",),
            execution_entrypoints=("data/output.csv",),
        )
    with pytest.raises(ValidationError, match="duplicate"):
        TaskContract(
            instruction_file="instruction.md",
            paper_file="paper.md",
            expected_files=("reproduction/run.py",),
            execution_entrypoints=("reproduction/run.py", "reproduction/run.py"),
        )
```

- [ ] **步骤 2：运行 contract RED**

运行：

```powershell
uv run pytest tests/test_execution_verifier.py -k "entrypoint_outside or non_python_and_duplicate" -v
```

预期：`TaskContract` 因未知字段 `execution_entrypoints` 失败，尚未得到计划要求的 entrypoint 语义。

- [ ] **步骤 3：写多文件 provenance RED**

把 `_contract()` 改为显式声明 `execution_entrypoints=("reproduction/generate.py",)`，并增加：

```python
def test_imported_core_module_does_not_require_direct_execution(tmp_path: Path) -> None:
    reproduction = tmp_path / "reproduction"
    reproduction.mkdir()
    (reproduction / "core.py").write_text("VALUE = 2\n", encoding="utf-8")
    (reproduction / "generate.py").write_text(
        "from pathlib import Path\n"
        "from reproduction.core import VALUE\n"
        "Path('data').mkdir(exist_ok=True)\n"
        "Path('data/output.csv').write_text(f'a,b\\n1,{VALUE}\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    contract = TaskContract(
        instruction_file="instruction.md",
        paper_file="paper.md",
        expected_files=(
            "reproduction/core.py",
            "reproduction/generate.py",
            "data/output.csv",
        ),
        execution_entrypoints=("reproduction/generate.py",),
        constraints=(
            ArtifactConstraint(
                path="data/output.csv",
                csv_header=("a", "b"),
                csv_data_row_count=1,
            ),
        ),
    )
    journal = ExecutionJournal(tmp_path, contract.expected_files)

    assert _run_python(tmp_path, journal) == "ok"
    assert ArtifactVerifier(tmp_path, contract, journal).verify().ok


def test_current_entrypoint_hash_is_required_after_edit(tmp_path: Path) -> None:
    reproduction = tmp_path / "reproduction"
    reproduction.mkdir()
    script = reproduction / "generate.py"
    script.write_text("print('first')\n", encoding="utf-8")
    contract = TaskContract(
        instruction_file="instruction.md",
        paper_file="paper.md",
        expected_files=("reproduction/generate.py",),
        execution_entrypoints=("reproduction/generate.py",),
    )
    journal = ExecutionJournal(tmp_path, contract.expected_files)
    assert _run_python(tmp_path, journal) == "ok"
    script.write_text("print('second')\n", encoding="utf-8")

    result = ArtifactVerifier(tmp_path, contract, journal).verify()
    assert not result.ok
    assert {issue.code for issue in result.issues} == {"script_not_executed"}
```

同时把既有 `test_any_current_expected_script_can_support_csv_in_same_record` 改名为
`test_any_current_entrypoint_can_support_csv_in_same_record`，并在其 contract 中显式声明
真正生成 CSV 的脚本为 entrypoint。

- [ ] **步骤 4：运行 provenance RED**

运行：

```powershell
uv run pytest tests/test_execution_verifier.py -k "imported_core or current_entrypoint or current_entrypoint_can" -v
```

预期：旧 verifier 对 `core.py` 报 `script_not_executed`，并仍把所有 expected Python
误当成 CSV provenance 来源。

- [ ] **步骤 5：实现最小 contract 与 verifier GREEN**

在 `src/phycode/prbench_contract.py` 扩展模型：

```python
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ArtifactConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    csv_header: tuple[str, ...] | None = None
    csv_rows: tuple[tuple[str, ...], ...] | None = None
    csv_data_row_count: int | None = Field(default=None, ge=0)


class TaskContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instruction_file: str
    paper_file: str
    input_files: tuple[str, ...] = ()
    expected_files: tuple[str, ...]
    execution_entrypoints: tuple[str, ...] = ()
    constraints: tuple[ArtifactConstraint, ...] = ()
```

将 `execution_entrypoints` 接到相同的公开相对路径 validator。model validator 必须：

```python
entrypoint_keys = tuple(_path_key(path) for path in self.execution_entrypoints)
if len(set(entrypoint_keys)) != len(entrypoint_keys):
    raise ValueError("execution_entrypoints contains duplicate paths")
if any(key not in expected_key_set for key in entrypoint_keys):
    raise ValueError("execution entrypoints must belong to expected_files")
if any(Path(path).suffix.casefold() != ".py" for path in self.execution_entrypoints):
    raise ValueError("execution entrypoints must be Python files")
```

verifier 使用 entrypoint key 集合：

```python
entrypoint_keys = {_path_key(path) for path in self.contract.execution_entrypoints}
if self._key(relative_path) in entrypoint_keys and not self._script_has_provenance(relative_path, path):
    issues.append(
        self._issue("script_not_executed", relative_path, "Entrypoint was not executed successfully")
    )
```

缺失 artifact 统一返回 `missing_artifact`；`_current_expected_script_hashes()` 改为
`_current_entrypoint_hashes()`，只遍历 `execution_entrypoints`。CSV 约束增加：

```python
if constraint.csv_data_row_count is not None and len(actual_rows) != constraint.csv_data_row_count:
    issues.append(
        self._issue("csv_row_count_mismatch", constraint.path, "CSV data row count does not match")
    )
```

同步更新既有断言：缺失 Python 文件属于 `missing_artifact`；只有已存在但没有当前
成功执行记录的 entrypoint 才属于 `script_not_executed`。

- [ ] **步骤 6：迁移确定性 fixture 与两个 smoke contract**

`tests/prbench_test_support.py::write_public_task_files()` 的 contract 增加：

```python
"execution_entrypoints": ["reproduce.py"],
```

两个公开 JSON 分别增加：

```json
"execution_entrypoints": ["reproduction/hello.py"]
```

和：

```json
"execution_entrypoints": ["reproduction/alphabet.py"]
```

其他测试中只要 contract 期待 CSV provenance，就显式声明实际生成 CSV 的入口脚本；
纯文本 artifact contract 保持空 entrypoint。

- [ ] **步骤 7：运行 GREEN 与相关回归**

运行：

```powershell
uv run pytest tests/test_execution_verifier.py tests/test_prbench_loop.py tests/test_prbench_runner.py tests/test_prbench_adapter.py -q
uvx pyright
```

预期：全部通过；Pyright 为 0 errors / 0 warnings。

- [ ] **步骤 8：提交**

```powershell
git add src/phycode/prbench_contract.py tests/test_execution_verifier.py tests/prbench_test_support.py integrations/prbench/public_contracts/aaatest_helloworld.json integrations/prbench/public_contracts/bbbtest_alphabet.json tests/test_prbench_adapter.py
git commit -m "feat(prbench): model executable artifact entrypoints"
```

---

### Task 33: 紧凑公开任务简报与显式上下文预算

**依赖：** Task 32。

**文件：**
- 修改：`src/phycode/prbench_eval.py`
- 修改：`src/phycode/composition.py`
- 修改：`tests/test_prbench_runner.py`
- 修改：`tests/test_cli_smoke.py`

**接口：**
- 产出：`build_prbench_task_brief(contract: TaskContract) -> str`。
- 修改：`build_agent(..., max_context_chars: int | None = None) -> AgentLoop`。
- 修改：`run_prbench(..., max_context_chars: int | None = None) -> PRBenchRunResult`。
- 产出 CLI：`phycode prbench run --max-context-chars`，有效范围 `1_000..64_000`。

- [ ] **步骤 1：写紧凑简报 RED**

在 `tests/test_prbench_runner.py` 增加：

```python
def test_task_brief_lists_contract_without_inlining_public_documents(tmp_path: Path) -> None:
    from phycode.prbench_eval import build_prbench_task_brief
    from phycode.prbench_contract import TaskContract

    contract = TaskContract(
        instruction_file="instruction.md",
        paper_file="white1993.md",
        expected_files=tuple(f"reproduction/file_{index}.py" for index in range(12)),
        execution_entrypoints=("reproduction/file_11.py",),
    )

    brief = build_prbench_task_brief(contract)

    assert len(brief) < 4_000
    assert "instruction.md" in brief
    assert "white1993.md" in brief
    assert "reproduction/file_0.py" in brief
    assert "reproduction/file_11.py" in brief
    assert "file.read" in brief
    assert "search.grep" in brief
```

把既有 `test_prompt_contains_only_public_instruction_paper_and_input_names` 收紧为：

```python
def test_prompt_references_public_files_without_inlining_contents(tmp_path: Path) -> None:
    contract, approvals = _write_public_task_files(tmp_path, approvals=False)
    instruction_secret = "PUBLIC-INSTRUCTION-BODY-MUST-NOT-BE-INLINED"
    paper_secret = "PUBLIC-PAPER-BODY-MUST-NOT-BE-INLINED"
    (tmp_path / "instruction.md").write_text(instruction_secret, encoding="utf-8")
    (tmp_path / "paper.md").write_text(paper_secret, encoding="utf-8")
    llm = _PromptRecorder()

    run_prbench(tmp_path, contract, approvals, llm=llm)

    rendered = json.dumps(llm.messages)
    assert "instruction.md" in rendered
    assert "paper.md" in rendered
    assert instruction_secret not in rendered
    assert paper_secret not in rendered
```

将 prompt recorder 提升为测试模块级 `_PromptRecorder`，供该回归直接复用；它只保存
传给 provider 的 messages，不修改 runner 行为。

- [ ] **步骤 2：运行简报 RED**

运行：

```powershell
uv run pytest tests/test_prbench_runner.py -k "task_brief or references_public_files" -v
```

预期：缺少 `build_prbench_task_brief`，且旧 runner 把两份正文直接写入 provider 消息。

- [ ] **步骤 3：写上下文覆盖 RED**

增加：

```python
def test_build_agent_applies_explicit_prbench_context_budget(tmp_path: Path) -> None:
    from phycode.composition import build_agent, trusted_prbench_runtime_settings
    from phycode.models import AgentProfile, SessionMode

    trace_dir = tmp_path / ".phycode/prbench/traces"
    loop = build_agent(
        SessionMode.NON_INTERACTIVE,
        llm=ScriptedLLM([]),
        profile=AgentProfile.PRBENCH,
        max_context_chars=24_000,
        runtime_settings=trusted_prbench_runtime_settings(tmp_path, trace_dir),
    )

    assert loop.context_builder.max_chars == 24_000


@pytest.mark.parametrize("max_context_chars", [999, 64_001])
def test_runner_rejects_context_budget_outside_public_bounds(
    tmp_path: Path, max_context_chars: int
) -> None:
    contract, approvals = _write_public_task_files(tmp_path, approvals=False)
    llm = _RecordingFinalLLM()

    result = run_prbench(
        tmp_path,
        contract,
        approvals,
        llm=llm,
        max_context_chars=max_context_chars,
    )

    assert result.status == PRBenchRunStatus.POLICY_BLOCKED
    assert llm.calls == 0
```

- [ ] **步骤 4：运行上下文 RED**

运行：

```powershell
uv run pytest tests/test_prbench_runner.py -k "explicit_prbench_context or context_budget_outside" -v
```

预期：`build_agent` / `run_prbench` 不接受 `max_context_chars`。

- [ ] **步骤 5：实现紧凑简报与预算覆盖**

在 `src/phycode/prbench_eval.py` 增加：

```python
def build_prbench_task_brief(contract: TaskContract) -> str:
    expected = "\n".join(f"- {path}" for path in contract.expected_files)
    entrypoints = "\n".join(f"- {path}" for path in contract.execution_entrypoints) or "- (none)"
    inputs = ", ".join(contract.input_files) or "(none)"
    brief = (
        "Complete this public PRBench task using only visible workspace files.\n"
        f"Read the full instruction with file.read: {contract.instruction_file}\n"
        f"Read or search the public paper in bounded chunks: {contract.paper_file}\n"
        f"Public input files: {inputs}\n"
        "Required artifacts:\n"
        f"{expected}\n"
        "Execution entrypoints:\n"
        f"{entrypoints}\n"
        "Use file.read and search.grep to recover details from the public files. "
        "Implement core modules before entrypoints. Request process.run for each entrypoint; "
        "completion is accepted only after deterministic artifact verification."
    )
    if len(brief) >= 4_000:
        raise ValueError("PRBench task brief exceeds the public context boundary")
    return brief
```

`run_prbench` 继续验证 instruction/paper/input 都是 workspace 内普通文件，但不读取两份
正文；prompt 改为 `build_prbench_task_brief(contract)`。简报过长或构造失败必须进入现有
controlled policy failure 路径，不能在写 `run_result.json` 前向外抛出原始异常。

在 `composition.build_agent` 增加参数并选择有效预算：

```python
effective_context_chars = (
    max_context_chars if max_context_chars is not None else spec.max_context_chars
)
```

随后把 `ContextBuilder(..., max_chars=effective_context_chars)`。`run_prbench` 对显式值做
bool/type 与 `1_000..64_000` 检查，并原样传给 `build_agent`。

- [ ] **步骤 6：接入 runner CLI**

在 `prbench_run` 增加：

```python
max_context_chars: int | None = typer.Option(
    None,
    min=1_000,
    max=64_000,
    help="Context character budget override",
),
```

并传给 `run_prbench`。更新 `tests/test_cli_smoke.py`，断言：

```python
assert "--max-context-chars" in result.stdout
```

- [ ] **步骤 7：运行 GREEN 与非回归**

运行：

```powershell
uv run pytest tests/test_prbench_runner.py tests/test_cli_smoke.py tests/test_agent_loop.py tests/test_gaia_eval.py -q
uvx pyright
```

预期：全部通过；smoke 默认仍为 12,000/40；coding/GAIA 不变；Pyright 0/0。

- [ ] **步骤 8：提交**

```powershell
git add src/phycode/prbench_eval.py src/phycode/composition.py tests/test_prbench_runner.py tests/test_cli_smoke.py
git commit -m "feat(prbench): add compact full-task context"
```

---

### Task 34: 固定 evaluator 完整任务参数与运行入口

**依赖：** Task 33。

**文件：**
- 修改：`integrations/prbench/phycode-evaluator.patch`
- 创建：`integrations/prbench/public_contracts/task_white_1993.json`
- 创建：`integrations/prbench/run_public_full.ps1`
- 修改：`tests/test_prbench_adapter.py`
- 修改：`tests/test_docs_process.py`

**接口：**
- 产出 evaluator 参数：`--phycode-max-tool-calls`，范围 `1..100`，默认 `40`。
- 产出 evaluator 参数：`--phycode-max-context-chars`，范围 `1_000..64_000`，默认 `12_000`。
- 产出完整任务脚本：`run_public_full.ps1`，只接受强制字符串参数 `EvaluatorRoot` 与
  `WheelPath`。
- 产出声明式 contract：`task_white_1993.json`。

- [ ] **步骤 1：写完整公开 contract RED**

在 `tests/test_prbench_adapter.py` 增加：

```python
def test_full_public_contract_uses_only_instruction_declared_artifacts() -> None:
    path = Path("integrations/prbench/public_contracts/task_white_1993.json")
    raw = path.read_text(encoding="utf-8")
    contract = TaskContract.model_validate_json(raw)

    assert contract.instruction_file == "instruction.md"
    assert contract.paper_file == "white1993.md"
    assert "reproduction/ANALYSIS.md" in contract.expected_files
    assert len(contract.expected_files) == 20
    assert contract.execution_entrypoints == tuple(
        f"reproduction/fig{figure}_compute.py" for figure in range(2, 9)
    )
    assert len(contract.constraints) == 7
    assert {item.csv_data_row_count for item in contract.constraints} == {15, 24, 50, 60}
    assert "metadata" not in raw.casefold()
    assert "ground_truth" not in raw.casefold()
    assert "reference" not in raw.casefold()
```

- [ ] **步骤 2：运行 contract RED**

运行：

```powershell
uv run pytest tests/test_prbench_adapter.py -k full_public_contract -v
```

预期：完整 contract 文件不存在。

- [ ] **步骤 3：创建完整 contract**

创建 JSON，`expected_files` 精确按公开 task.yaml 顺序包含：

```json
[
  "reproduction/ANALYSIS.md",
  "reproduction/operators.py",
  "reproduction/block.py",
  "reproduction/superblock.py",
  "reproduction/dmrg_infinite.py",
  "reproduction/dmrg_finite.py",
  "reproduction/fig2_compute.py",
  "reproduction/fig3_compute.py",
  "reproduction/fig4_compute.py",
  "reproduction/fig5_compute.py",
  "reproduction/fig6_compute.py",
  "reproduction/fig7_compute.py",
  "reproduction/fig8_compute.py",
  "data/fig2.csv",
  "data/fig3.csv",
  "data/fig4.csv",
  "data/fig5.csv",
  "data/fig6.csv",
  "data/fig7.csv",
  "data/fig8.csv"
]
```

`execution_entrypoints` 精确为 7 个 `fig*_compute.py`。7 个 constraint 只写 instruction
明示的 header 与 data row count：fig2=50、fig3=24、fig4=24、fig5=15、fig6=60、
fig7=60、fig8=50；不写参考数值行。

- [ ] **步骤 4：写 evaluator 参数透传 RED**

扩展既有 `test_official_launch_cli_exposes_and_bounds_approval_wait`，要求 help 同时包含：

```python
assert "--phycode-max-tool-calls" in help_result.stdout
assert "--phycode-max-context-chars" in help_result.stdout
```

增加超界 CLI 负例，并扩展
`test_official_main_and_white_runner_pass_approval_wait_only_to_phycode`：

```python
launch(
    task_id="public-task",
    green_port=0,
    white_port=0,
    code_only=False,
    agent_type="claude",
    white_agent_type="phycode",
    green_agent_type="opencode",
    phycode_contract="contract.json",
    phycode_approvals="approvals.json",
    approval_wait_seconds=900,
    phycode_max_tool_calls=50,
    phycode_max_context_chars=24_000,
    no_archive=True,
    results_subdir=None,
)
assert launch_calls[0]["phycode_max_tool_calls"] == 50
assert launch_calls[0]["phycode_max_context_chars"] == 24_000

# Fake executor for phycode
executor.max_tool_calls = 50
executor.max_context_chars = 24_000
assert "--max-tool-calls 50" in commands[0][-1]
assert "--max-context-chars 24000" in commands[0][-1]
assert "--max-tool-calls" not in str(commands[1])
assert "--max-context-chars" not in str(commands[1])
```

- [ ] **步骤 5：运行参数 RED**

运行：

```powershell
uv run pytest tests/test_prbench_adapter.py -k "launch_cli_exposes or main_and_white_runner" -v
```

预期：固定 patch 尚未暴露或传递两个新参数。

- [ ] **步骤 6：在 pinned clone 修改并重新生成 patch**

对全新、clean、detached 固定 commit clone：

1. 应用当前 `phycode-evaluator.patch`；
2. 使用 `apply_patch` 修改已 patch 的 `main.py`、`src/launcher.py` 和
   `src/white_agent/agent.py`；
3. `main.py launch` 增加两个带 Typer min/max 的 PhyCode-only 参数；
4. `launch_evaluation` 在任何 workspace/Docker 创建前验证范围；
5. 把值传入 white executor 的私有整数副本；
6. 只在 `agent_type == "phycode"` 的容器命令追加：

```python
f"--max-tool-calls {self.max_tool_calls} "
f"--max-context-chars {self.max_context_chars} "
```

7. 非 PhyCode white agent 命令不包含这两个 flag；
8. 使用 `git diff --binary --output=<临时绝对路径>` 生成新 patch，经人工检查后替换仓库
   内 patch；不得修改 ground-truth copy、grading rubric 或 green parser。

默认值保持 40 与 12,000；完整任务脚本显式传入 50 与 24,000。

- [ ] **步骤 7：写完整任务 PowerShell 脚本 RED**

在 `tests/test_docs_process.py` 增加静态合同：

```python
def test_public_full_script_has_exact_local_only_contract() -> None:
    script = _read("integrations/prbench/run_public_full.ps1")
    assert "task_white_1993" in script
    assert "--phycode-max-tool-calls" in script
    assert "50" in script
    assert "--phycode-max-context-chars" in script
    assert "24000" in script
    assert "task_white_1993.json" in script
    assert "data/fig" not in script
    assert "process.run" not in script
    assert "script_sha256" not in script
    assert "Get-FileHash" not in script
    assert "NewTextDocument" not in script
    assert ".env" not in script
```

并断言 13 个公开 reproduction 路径各有一次 write、两次 edit grant；没有其他 path。

- [ ] **步骤 8：运行脚本 RED**

运行：

```powershell
uv run pytest tests/test_docs_process.py -k public_full_script -v
```

预期：`run_public_full.ps1` 不存在。

- [ ] **步骤 9：实现完整任务脚本**

脚本参数精确为：

```powershell
param(
    [Parameter(Mandatory=$true)][string]$EvaluatorRoot,
    [Parameter(Mandatory=$true)][string]$WheelPath
)
```

复用 smoke 的凭据存在性检查、adapter 应用和 OPENCODE 环境保存/恢复。公开文件清单
固定为 `ANALYSIS.md` + 12 个 Python 文件；对每个路径构造一次 write 与两次 edit。
manifest 保存在系统临时目录，official launch 参数精确为：

```powershell
& uv run --with 'a2a-sdk[http-server]==0.3.8' python main.py launch `
    --task-id task_white_1993 `
    --white-agent-type phycode `
    --green-agent-type opencode `
    --phycode-contract $ContractPath `
    --phycode-approvals $ApprovalPath `
    --approval-wait-seconds 900 `
    --phycode-max-tool-calls 50 `
    --phycode-max-context-chars 24000
```

脚本不创建 process grant、不读取脚本、不计算哈希；主 agent 在 active workspace 中处理
动态 request。`finally` 精确恢复或删除 OPENCODE 变量，并删除系统临时 manifest 目录。

- [ ] **步骤 10：增加 fake-uv 环境与参数测试**

参照既有 smoke fake-uv 测试，新测试调用 `run_public_full.ps1`，fake uv 必须观察到：

- task ID 为 `task_white_1993`；
- contract 路径以 `task_white_1993.json` 结尾；
- 50 / 24,000 / 900 三个参数存在；
- OPENCODE 三项只在 evaluator 子进程期间映射；
- 成功与失败、调用前环境存在与不存在四种组合都精确恢复；
- stdout/stderr 不含 fake key 或 fake URL。

- [ ] **步骤 11：验证 patch、脚本和完整 contract GREEN**

运行：

```powershell
uv run pytest tests/test_prbench_adapter.py tests/test_docs_process.py -q
uv run python integrations/prbench/apply_adapter.py --help
uvx pyright
```

另对全新 fixed clone 执行：

```powershell
uv run python integrations/prbench/apply_adapter.py <fresh-fixed-clone> dist/phycode-0.1.2-py3-none-any.whl
uv run --with 'a2a-sdk[http-server]==0.3.8' python <fresh-fixed-clone>/main.py launch --help
```

预期：apply/help exit 0，help 含两个新参数；fresh clone 除 adapter patch 与 staged wheel
外没有意外文件修改；Pyright 0/0。

- [ ] **步骤 12：提交**

```powershell
git add integrations/prbench/phycode-evaluator.patch integrations/prbench/public_contracts/task_white_1993.json integrations/prbench/run_public_full.ps1 tests/test_prbench_adapter.py tests/test_docs_process.py
git commit -m "feat(prbench): add full public evaluator run"
```

---

### Task 35: 中文文档、过程证据与确定性发布门禁

**依赖：** Task 34 通过 spec/quality review。

**文件：**
- 修改：`README.md`
- 修改：`PLAN.md`
- 修改：`SPEC_PROCESS.md`
- 修改：`AGENT_LOG.md`
- 修改：`tests/test_docs_process.py`

**接口：**
- 产出：完整公开任务的构建、运行、动态审批、三次尝试和本地产物边界文档。
- 产出：Task 32–35 的技能、RED/GREEN、commit 与 review 记录。
- 保持：真实 API 不属于默认 pytest/CI。

- [ ] **步骤 1：写文档合同 RED**

在 `tests/test_docs_process.py` 增加：

```python
def test_docs_define_full_public_task_and_local_artifact_boundary() -> None:
    readme = _read("README.md")
    plan = _read("PLAN.md")
    process = _read("SPEC_PROCESS.md")
    log = _read("AGENT_LOG.md")

    for document in (readme, plan, process, log):
        assert "task_white_1993" in document
        assert "完整公开任务" in document
    for phrase in (
        "run_public_full.ps1",
        "24,000",
        "50",
        "最多三次",
        "评测产物不提交",
        "保持主分支干净",
    ):
        assert phrase in readme
    assert "codex/prbench-public-test" in plan
    assert "旧 baseline" in process
    assert "主 agent" in log
```

- [ ] **步骤 2：运行文档 RED**

运行：

```powershell
uv run pytest tests/test_docs_process.py -k full_public_task -v
```

预期：根 README 与过程文档尚未说明完整任务切片和本地产物边界。

- [ ] **步骤 3：更新中文文档**

README 增加“PRBench 完整公开任务”小节，写清：

- 这是单个公开任务，不是 holdout 成绩；
- 固定 evaluator commit、wheel 构建和 `run_public_full.ps1` 命令；
- 50 / 24,000 / 900 参数；
- 主 agent 如何监控 `.phycode/prbench/approval-request.json`、审核脚本并把请求原样追加
  到 active workspace 的 `phycode-approvals.json`；
- 成功必须同时具备 runner `completed` 与有效 grader report；
- 最多三次，基础设施预响应失败不计数；
- evaluator clone、workspace、trace、journal、报告和模型生成物全部本地忽略且不提交；
- 所有提交只在功能分支，未经授权不进入 `main`。

PLAN 新增 Task 32–35 概览，完成后写入实际 commit hash。SPEC_PROCESS 记录为什么旧
baseline 的长 prompt、全 `.py` provenance、40 次预算和 smoke-only 审批不适合完整
任务，以及用户批准的成功/尝试/Git 边界。AGENT_LOG 按时间顺序记录每个 subagent、
TDD RED/GREEN、review 和 commit。

- [ ] **步骤 4：运行文档 GREEN**

运行：

```powershell
uv run pytest tests/test_docs_process.py -v
```

预期：全部通过。

- [ ] **步骤 5：运行确定性全量门禁**

使用 clean fixed evaluator source，依次运行：

```powershell
uv run pytest
uvx pyright
uv build
git diff --check
git ls-files ".env" ".env.*" "*.pem" "*.key"
```

再验证：PowerShell AST 解析；fresh clone adapter apply/help；构建 wheel 解包检查；
worktree 与 fixed evaluator source 均无意外 dirty；tracked credential-like file scan 无
输出。任何失败都先定位并按 TDD 修复，不进入真实 API 阶段。

- [ ] **步骤 6：提交**

```powershell
git add README.md PLAN.md SPEC_PROCESS.md AGENT_LOG.md tests/test_docs_process.py
git commit -m "docs(prbench): document full public evaluation"
```

---

## 主 agent 专属：最多三次官方真实 API 验收

该阶段不是 subagent implementation task。任何 subagent 不得读取、索取或接收真实
provider 值；主 agent 只把脱敏状态和结果摘要交给后续 reviewer/文档 worker。

### 运行前

1. 记录功能分支 HEAD、fixed evaluator commit、wheel SHA-256 和干净状态。
2. 从用户已授权的仓库外安全来源在单一 PowerShell 进程内解析 key、base URL 和模型；
   只输出三项是否存在以及模型名，不输出 key/URL。
3. 用相同进程设置 `PHYCODE_*`，调用结束后在 `finally` 真正删除；临时 OPENCODE 映射由
   运行脚本恢复。
4. 每次尝试创建新的固定-commit evaluator clone；不得复用上一轮 workspace。

### 动态审批循环

official launch 运行期间轮询最新
`data/workspaces/task_white_1993_*/.phycode/prbench/approval-request.json`。每个 request：

1. 使用 `lstat`/realpath 确认 request、script 与 manifest 都是当前 workspace 内非链接
   普通文件；
2. 只接受 expected Python 路径；
3. 确认 absolute executable 属于 adapter allowlist、cwd 为 workspace、尾随 argv 无
   workspace 外路径；
4. 完整阅读当前脚本，拒绝 ground truth、凭据、网络外泄、禁用库和 workspace 外访问；
5. 独立复算 SHA-256 并与 request 比较；
6. 以临时文件 + flush/fsync + `os.replace` 把 request 对象原样追加到 grants；
7. 不自动批准、不生成通配 grant、不批准 direct CSV write/edit。

### 尝试计数与失败处理

- Docker/adapter/依赖/容器在首次白色模型响应前失败不计数。
- 首次白色模型响应后立即记为一次；审批拒绝、provider/process/artifact/budget/grader
  失败均保留该次数。
- 每次结束收集本地脱敏 status、工具调用数、耗时和 grader 分数；不把原始 provider
  错误交给 subagent。
- 若暴露通用代码缺陷，停止真实运行，按 `systematic-debugging` + TDD 修复、review、
  重跑确定性门禁，再用新的 evaluator/workspace 开始下一次。
- 若三次内首次得到 runner `completed` 与有效 grader report，立即停止，不为分数重跑。
- 三次均失败时如实结束，不能把 mock、部分 artifact 或绿色评分片段冒充成功。

### 成功后本地核验

1. `ANALYSIS.md`、12 个代码文件和 7 个 CSV 均为当前 workspace 内非链接普通非空文件。
2. 7 个 entrypoint 当前 SHA-256 与成功 journal record 相符。
3. 7 个 CSV 当前 SHA-256 与同一成功 record 的 after snapshot 相符。
4. `run_result.json` 为 object、`status=completed`，trace 声明事件数等于 JSONL 行数。
5. grader report 为本轮新文件、可解析、grading 为 object 且无 `error`。
6. 从内存中的真实 key/URL 对 worktree、解包构建物、成功 evaluator workspace/报告与
   Git 全历史做 exact 扫描，只输出每类命中计数，全部必须为 0。
7. 结束新 PowerShell 进程后确认 `PHYCODE_*` / `OPENCODE_*` 六项均不存在。

所有 evaluator clone、workspace、trace、journal、report、模型生成物和本地扫描清单
保持未跟踪/已忽略状态，不执行 `git add`。

---

### Task 36: 脱敏真实结果记录与 whole-branch 收尾

**依赖：** 主 agent 完成最多三次真实验收，并提供不含 key/URL/原始异常的摘要：模型名、
正式尝试次数、每次 runner status、成功尝试工具调用数/耗时、grader 分数、泄漏扫描
计数和最终门禁结果。

**文件：**
- 修改：`PLAN.md`
- 修改：`SPEC_PROCESS.md`
- 修改：`AGENT_LOG.md`
- 修改：`tests/test_docs_process.py`

**接口：**
- 消费：主 agent 的脱敏结果摘要，不消费 evaluator workspace 或任何凭据。
- 产出：真实完整公开任务的可审计文字结论；不提交评测产物。

- [ ] **步骤 1：写结果记录 RED**

增加文档测试：

```python
def test_process_docs_record_full_public_result_without_artifacts() -> None:
    plan = _read("PLAN.md")
    process = _read("SPEC_PROCESS.md")
    log = _read("AGENT_LOG.md")
    for document in (plan, process, log):
        assert "task_white_1993 完整公开任务真实验收" in document
        assert "正式尝试次数" in document
        assert "评测产物未提交" in document
        assert "凭据泄漏扫描" in document
    assert any(
        f"最终终态：`{status}`" in log
        for status in (
            "completed",
            "approval_required",
            "policy_blocked",
            "provider_error",
            "process_failed",
            "artifact_verification_failed",
            "repeated_no_progress",
            "tool_budget_exhausted",
            "grader_failed",
        )
    )
```

- [ ] **步骤 2：运行 RED**

运行：

```powershell
uv run pytest tests/test_docs_process.py -k full_public_result -v
```

预期：过程文档尚未包含本轮真实结果。

- [ ] **步骤 3：写入脱敏实际结果**

只根据主 agent 摘要写入实际模型名、尝试次数、各次结构化终态、成功工具调用数/耗时、
grader 分数、扫描计数和最终验证命令。不得打开 evaluator workspace，不得写 endpoint、
key、原始 provider 错误、生成代码/数据内容或本地绝对证据路径。PLAN 将 Task 32–36 标为
实际状态并附 commit hash；三次均失败时保留失败状态，不写成功措辞。

- [ ] **步骤 4：运行 GREEN 与最终门禁**

运行：

```powershell
uv run pytest tests/test_docs_process.py -v
uv run pytest
uvx pyright
uv build
git diff --check
git status --short --branch
```

预期：测试 exit 0、Pyright 0/0、构建成功；Git 只显示本任务刻意的文档/测试变更，
没有 evaluator、`.phycode`、`dist` 或模型生成物。

- [ ] **步骤 5：提交**

```powershell
git add PLAN.md SPEC_PROCESS.md AGENT_LOG.md tests/test_docs_process.py
git commit -m "docs(prbench): record full public evaluation"
```

- [ ] **步骤 6：whole-branch review 与分支边界检查**

从 `588aa08ab56f929b4ac61895227574306a16ee13` 到 HEAD 生成完整 review package，派发
最强可用 reviewer 检查 spec 合规、代码质量、安全、任务专用硬编码、凭据/ground truth
边界和产物污染。Critical/Important 必须清零并复审；随后再次运行完整门禁。

分支只保留/推送 `codex/prbench-public-test`。未经用户明确授权，不 merge、不 rebase
onto working `main`、不更新 `main` ref、不创建指向 `main` 的 tag。

---

## 依赖关系

- Task 32 → Task 33 → Task 34 → Task 35 → 主 agent 真实验收 → Task 36。
- 每个实现 task 由新鲜 subagent 完成 RED→GREEN、自审和提交。
- 每个 task 完成后先进行 spec 合规审查，再进行代码质量审查；Critical/Important 清零
  后才进入下一 task。
- 真实 API 仅在 Task 35 的确定性全量门禁和独立 review 通过后执行。
- Task 36 的 subagent 只接收脱敏摘要，不接收凭据、workspace 或原始报告。

## 计划自审

- **Spec coverage：** entrypoint/provenance 由 Task 32；紧凑上下文与预算由 Task 33；
  fixed adapter、完整 contract、审批脚本由 Task 34；中文文档和确定性门禁由 Task 35；
  最多三次真实验收由主 agent 专属阶段；脱敏结果和 whole-branch 收尾由 Task 36。
- **Placeholder scan：** 所有生产改动均给出准确接口、测试、命令、边界和预期失败/成功；
  真实结果只能来自实际运行，因此 Task 36 明确消费经过字段限制的脱敏摘要，不预写结果。
- **Type consistency：** `execution_entrypoints` 与 `csv_data_row_count` 在 Task 32 定义并由
  Task 33/34 消费；`max_context_chars` 由 composition → runner → CLI → evaluator patch
  使用相同名称；evaluator 外部 flag 固定为 `--phycode-max-context-chars`，容器内 runner
  flag 固定为 `--max-context-chars`。
