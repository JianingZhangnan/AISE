from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_prbench_wheel_contract_matches_project_version():
    project_version = tomllib.loads(_read("pyproject.toml"))["project"]["version"]
    wheel_filename = f"phycode-{project_version}-py3-none-any.whl"
    filename_pattern = r"phycode-\d+\.\d+\.\d+-py3-none-any\.whl"
    adapter_filename = re.search(
        rf'^EXPECTED_WHEEL_FILENAME = "({filename_pattern})"$',
        _read("integrations/prbench/apply_adapter.py"),
        flags=re.MULTILINE,
    )
    main_readme = re.search(filename_pattern, _read("README.md"))
    integration_readme = re.search(
        filename_pattern, _read("integrations/prbench/README.md")
    )
    patch = _read("integrations/prbench/phycode-evaluator.patch")
    patch_copy = re.search(
        rf'self\.phycode_wheel,\s*"/tmp/({filename_pattern})"', patch
    )
    patch_install = re.search(
        rf"uv pip install --system /tmp/({filename_pattern})", patch
    )
    assert adapter_filename is not None
    assert main_readme is not None
    assert integration_readme is not None
    assert patch_copy is not None
    assert patch_install is not None

    actual_filenames = {
        "adapter": adapter_filename.group(1),
        "README.md": main_readme.group(0),
        "integrations/prbench/README.md": integration_readme.group(0),
        "patch copy target": patch_copy.group(1),
        "patch install target": patch_install.group(1),
    }

    assert actual_filenames == dict.fromkeys(actual_filenames, wheel_filename)


def test_readme_documents_all_user_facing_commands():
    readme = _read("README.md")
    required = [
        "uv sync --dev",
        "uv run phycode version",
        "uv run phycode tools list",
        'uv run phycode run "hello"',
        "uv run phycode chat",
        "uv run phycode demo guardrail",
        "uv run phycode demo feedback",
        "uv run phycode demo policy",
        "uv run phycode keys set openai-compatible",
        "uv run phycode keys status openai-compatible",
        "uv run phycode keys clear openai-compatible",
        "uv run pytest",
        "uvx pyright",
    ]
    for command in required:
        assert command in readme
    assert "操作系统钥匙串" in readme
    assert ".env" in readme
    assert "不得提交" in readme


def test_plan_marks_tasks_10_to_12_complete_and_records_scope():
    plan = _read("PLAN.md")
    assert "### Task 10: CLI Run、Chat、Config、Keys 和工具列表 - ✅" in plan
    assert "### Task 11: 确定性演示 - ✅" in plan
    assert "### Task 12: README、过程记录收尾和最终验证 - ✅" in plan
    assert "uv run pytest" in plan
    assert "uvx pyright" in plan


def test_process_documents_final_validation_and_review_ready_branch():
    process = _read("SPEC_PROCESS.md")
    assert "Task 10–12 收尾" in process
    assert "严格 CLI 测试" in process
    assert "uv run pytest" in process
    assert "uvx pyright" in process
    assert "codex/task-10-12" in process


def test_agent_log_records_task_10_to_12_execution_and_subagent_use():
    log = _read("AGENT_LOG.md")
    assert "Task 10–12" in log
    assert "gpt-5.5" in log
    assert "subagent" in log.lower()
    assert "tests/test_cli_commands.py" in log
    assert "tests/test_docs_process.py" in log


def test_prbench_readme_separates_deterministic_real_runner_and_official_smoke():
    readme = _read("README.md")
    required = [
        "phycode prbench run",
        "process.run",
        "3e5bee4545cad2138832f06302e9c98bd81f5216",
        "run_public_smoke.ps1",
        "_ground_truth",
        "Docker daemon",
        "默认 `uv run pytest`",
    ]
    for item in required:
        assert item in readme
    assert "确定性测试" in readme
    assert "真实模型 runner smoke" in readme
    assert "官方 Docker evaluator" in readme


def test_prbench_process_documents_refactor_and_real_api_acceptance_boundary():
    plan = _read("PLAN.md")
    log = _read("AGENT_LOG.md")
    process = _read("SPEC_PROCESS.md")

    for task_number in range(14, 21):
        assert f"- [x] Task {task_number}" in plan
    for document in (plan, log, process):
        assert "PRBench 运行时真正重构" in document
        assert "旧 parser" in document
        assert "真实 API" in document
        assert "aaatest_helloworld" in document
        assert "bbbtest_alphabet" in document
    assert "主 agent" in log
    assert "subagent" in log.lower()


def test_public_smoke_script_has_closed_credential_and_approval_contract():
    script = _read("integrations/prbench/run_public_smoke.ps1")

    assert "[Parameter(Mandatory=$true)][string]$EvaluatorRoot" in script
    assert "[Parameter(Mandatory=$true)][string]$WheelPath" in script
    assert (
        "[ValidateSet('aaatest_helloworld','bbbtest_alphabet')][string[]]$TaskIds"
        in script
    )
    assert set(re.findall(r"\$env:(PHYCODE_[A-Z_]+)", script)) == {
        "PHYCODE_API_KEY",
        "PHYCODE_BASE_URL",
        "PHYCODE_MODEL",
    }
    assert "NewTextDocument" not in script
    assert ".env" not in script
    assert "Write-Output" not in script
    for argument in (
        "apply_adapter.py",
        'a2a-sdk[http-server]==0.3.8',
        "--task-id",
        "--white-agent-type",
        "--green-agent-type",
        "opencode",
        "--phycode-contract",
        "--phycode-approvals",
        "--approval-wait-seconds",
        "900",
    ):
        assert argument in script
    for exact_target in ("reproduction/hello.py", "reproduction/alphabet.py"):
        assert exact_target in script
        assert f"tool_name = 'file.write'; path = '{exact_target}'" in script
        assert f"tool_name = 'file.edit'; path = '{exact_target}'" in script
    assert "data/output.csv" not in script
    assert "data/letters.csv" not in script
    assert "expected_files" not in script
    assert "process.run" not in script
    assert "/usr/local/bin/python" not in script
    assert "script_sha256" not in script
    assert "Get-FileHash" not in script
    for variable in ("OPENCODE_API_KEY", "OPENCODE_BASE_URL", "OPENCODE_MODEL"):
        assert variable in script
    assert "$env:OPENCODE_API_KEY = $env:PHYCODE_API_KEY" in script
    assert "$env:OPENCODE_BASE_URL = $env:PHYCODE_BASE_URL" in script
    assert "$env:OPENCODE_MODEL = 'openai/' + $env:PHYCODE_MODEL" in script
    assert "Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue" in script
    assert "[Environment]::SetEnvironmentVariable($name, $null, 'Process')" not in script
    assert not re.search(r"Write-(?:Host|Output).*\$env:", script, flags=re.IGNORECASE)
    readme = _read("README.md")
    assert 'a2a-sdk[http-server]==0.3.8' in readme
    for runtime_approval_term in (
        ".phycode/prbench/approval-request.json",
        "script_sha256",
        "phycode-approvals.json",
        "Config.Env",
        "6f5d75d",
    ):
        assert runtime_approval_term in readme


def test_public_full_script_has_exact_local_only_contract() -> None:
    script_path = ROOT / "integrations/prbench/run_public_full.ps1"
    script_bytes = script_path.read_bytes()
    assert script_bytes.isascii()
    script = script_bytes.decode("ascii")
    normalized = script.replace("\r\n", "\n")
    assert normalized.startswith(
        "param(\n"
        "    [Parameter(Mandatory=$true)][string]$EvaluatorRoot,\n"
        "    [Parameter(Mandatory=$true)][string]$WheelPath\n"
        ")\n"
    )
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

    expected_paths = (
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
    )
    write_paths = tuple(
        re.findall(
            r"@\{\s*tool_name = 'file\.write'; path = '([^']+)'\s*\}",
            script,
        )
    )
    edit_paths = tuple(
        re.findall(
            r"@\{\s*tool_name = 'file\.edit'; path = '([^']+)'\s*\}",
            script,
        )
    )
    assert write_paths == expected_paths
    assert edit_paths == tuple(path for path in expected_paths for _ in range(2))
    assert "$TaskIds" not in script


@pytest.mark.parametrize("powershell_name", ["pwsh", "powershell"])
def test_public_full_script_parses_with_powershell_ast(
    powershell_name: str,
) -> None:
    powershell = shutil.which(powershell_name)
    if powershell is None:
        pytest.skip(f"{powershell_name} is unavailable")

    script_path = ROOT / "integrations/prbench/run_public_full.ps1"
    parse_result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-Command",
            "$tokens=$null; $errors=$null; "
            "[void][System.Management.Automation.Language.Parser]::ParseFile("
            "$env:PHYCODE_POWERSHELL_AST_TARGET, [ref]$tokens, [ref]$errors); "
            "if ($errors.Count -ne 0) { exit 1 }",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PHYCODE_POWERSHELL_AST_TARGET": str(script_path)},
    )
    assert parse_result.returncode == 0, (parse_result.stdout, parse_result.stderr)


@pytest.mark.parametrize("fake_uv_failure", [False, True])
@pytest.mark.parametrize("preexisting_opencode", [False, True])
@pytest.mark.parametrize("powershell_name", ["pwsh", "powershell"])
def test_public_full_restores_environment_and_passes_exact_fake_uv_arguments(
    tmp_path: Path,
    fake_uv_failure: bool,
    preexisting_opencode: bool,
    powershell_name: str,
) -> None:
    powershell = shutil.which(powershell_name)
    if powershell is None:
        pytest.skip(f"{powershell_name} is unavailable")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    observation = tmp_path / "uv-observation.jsonl"
    recorder = tmp_path / "record_uv.py"
    recorder.write_text(
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "arguments = sys.argv[1:]\n"
        "provider_names = (\n"
        "    'OPENCODE_API_KEY', 'OPENCODE_BASE_URL', 'OPENCODE_MODEL'\n"
        ")\n"
        "record = {\n"
        "    'argv': arguments,\n"
        "    'cwd': os.getcwd(),\n"
        "    'opencode': {\n"
        "        name: {\n"
        "            'present': name in os.environ,\n"
        "            'value': os.environ.get(name),\n"
        "        }\n"
        "        for name in provider_names\n"
        "    },\n"
        "}\n"
        "if '--phycode-approvals' in arguments:\n"
        "    index = arguments.index('--phycode-approvals')\n"
        "    approval_path = Path(arguments[index + 1])\n"
        "    record['approvals_path'] = str(approval_path)\n"
        "    record['approvals_exists'] = approval_path.is_file()\n"
        "    if approval_path.is_file():\n"
        "        record['approvals'] = json.loads(\n"
        "            approval_path.read_text(encoding='utf-8-sig')\n"
        "        )\n"
        "with open(os.environ['FAKE_UV_OBSERVATION'], 'a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps(record, sort_keys=True) + '\\n')\n"
        "is_adapter = any(Path(value).name == 'apply_adapter.py' for value in arguments)\n"
        "if not is_adapter and os.environ.get('FAKE_UV_FAIL') == '1':\n"
        "    raise SystemExit(23)\n",
        encoding="ascii",
    )
    if os.name == "nt":
        (fake_bin / "uv.cmd").write_text(
            "@echo off\r\n"
            "\"%FAKE_UV_PYTHON%\" \"%FAKE_UV_RECORDER%\" %*\r\n"
            "exit /b %errorlevel%\r\n",
            encoding="ascii",
        )
    else:
        fake_uv = fake_bin / "uv"
        fake_uv.write_text(
            "#!/bin/sh\n"
            "exec \"$FAKE_UV_PYTHON\" \"$FAKE_UV_RECORDER\" \"$@\"\n",
            encoding="ascii",
        )
        fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IXUSR)

    evaluator = tmp_path / "evaluator"
    evaluator.mkdir()
    wheel = tmp_path / "phycode-0.1.2-py3-none-any.whl"
    wheel.write_bytes(b"fake wheel")
    wrapper = tmp_path / "invoke-full.ps1"
    wrapper.write_text(
        "param(\n"
        "  [string]$FullScript, [string]$EvaluatorRoot, [string]$WheelPath,\n"
        "  [string]$ExpectFailure, [string]$HadOriginal\n"
        ")\n"
        "$failed = $false\n"
        "try {\n"
        "  & $FullScript -EvaluatorRoot $EvaluatorRoot -WheelPath $WheelPath\n"
        "}\n"
        "catch { $failed = $true }\n"
        "if ($failed -ne ($ExpectFailure -eq '1')) { exit 81 }\n"
        "$names = @('OPENCODE_API_KEY','OPENCODE_BASE_URL','OPENCODE_MODEL')\n"
        "if ($HadOriginal -eq '1') {\n"
        "  if ($env:OPENCODE_API_KEY -ne 'original-key') { exit 82 }\n"
        "  if ($env:OPENCODE_BASE_URL -ne 'https://original.invalid') { exit 83 }\n"
        "  if ($env:OPENCODE_MODEL -ne 'openai/original-model') { exit 84 }\n"
        "}\n"
        "else {\n"
        "  foreach ($name in $names) {\n"
        "    if (Test-Path -LiteralPath ('Env:' + $name)) { exit 85 }\n"
        "  }\n"
        "}\n"
        "Write-Output 'environment-restored'\n",
        encoding="utf-8",
    )

    environment = os.environ.copy()
    environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
    environment.update(
        {
            "PHYCODE_API_KEY": "fake-phycode-key",
            "PHYCODE_BASE_URL": "https://fake.invalid/v1",
            "PHYCODE_MODEL": "fake-model",
            "FAKE_UV_FAIL": "1" if fake_uv_failure else "0",
            "FAKE_UV_OBSERVATION": str(observation),
            "FAKE_UV_PYTHON": sys.executable,
            "FAKE_UV_RECORDER": str(recorder),
        }
    )
    originals = {
        "OPENCODE_API_KEY": "original-key",
        "OPENCODE_BASE_URL": "https://original.invalid",
        "OPENCODE_MODEL": "openai/original-model",
    }
    for name, value in originals.items():
        if preexisting_opencode:
            environment[name] = value
        else:
            environment.pop(name, None)

    powershell_arguments = [powershell, "-NoProfile"]
    if powershell_name == "powershell":
        powershell_arguments.extend(["-ExecutionPolicy", "Bypass"])
    powershell_arguments.extend(
        [
            "-File",
            str(wrapper),
            "-FullScript",
            str(ROOT / "integrations/prbench/run_public_full.ps1"),
            "-EvaluatorRoot",
            str(evaluator),
            "-WheelPath",
            str(wheel),
            "-ExpectFailure",
            "1" if fake_uv_failure else "0",
            "-HadOriginal",
            "1" if preexisting_opencode else "0",
        ]
    )
    completed = subprocess.run(
        powershell_arguments,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=environment,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert "environment-restored" in completed.stdout
    calls = tuple(
        json.loads(line)
        for line in observation.read_text(encoding="utf-8").splitlines()
    )
    assert len(calls) == 2

    adapter_call, evaluator_call = calls
    adapter_arguments = adapter_call["argv"]
    assert adapter_arguments[:2] == ["run", "python"]
    assert len(adapter_arguments) == 5
    assert Path(adapter_arguments[2]).resolve() == (
        ROOT / "integrations/prbench/apply_adapter.py"
    ).resolve()
    assert Path(adapter_arguments[3]).resolve() == evaluator.resolve()
    assert Path(adapter_arguments[4]).resolve() == wheel.resolve()
    assert Path(adapter_call["cwd"]).resolve() == ROOT.resolve()

    evaluator_arguments = evaluator_call["argv"]
    assert Path(evaluator_call["cwd"]).resolve() == evaluator.resolve()
    approval_index = evaluator_arguments.index("--phycode-approvals")
    approval_path = Path(evaluator_arguments[approval_index + 1])
    contract_path = (
        ROOT
        / "integrations/prbench/public_contracts/task_white_1993.json"
    ).resolve()
    assert evaluator_arguments == [
        "run",
        "--with",
        "a2a-sdk[http-server]==0.3.8",
        "python",
        "main.py",
        "launch",
        "--task-id",
        "task_white_1993",
        "--white-agent-type",
        "phycode",
        "--green-agent-type",
        "opencode",
        "--phycode-contract",
        str(contract_path),
        "--phycode-approvals",
        str(approval_path),
        "--approval-wait-seconds",
        "900",
        "--phycode-max-tool-calls",
        "50",
        "--phycode-max-context-chars",
        "24000",
    ]

    expected_paths = (
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
    )
    assert evaluator_call["approvals_exists"] is True
    assert Path(evaluator_call["approvals_path"]) == approval_path
    manifest = evaluator_call["approvals"]
    assert set(manifest) == {"grants"}
    grants = manifest["grants"]
    assert len(grants) == 39
    assert all(set(grant) == {"tool_name", "path"} for grant in grants)
    grant_counts = Counter(
        (grant["tool_name"], grant["path"]) for grant in grants
    )
    assert grant_counts == Counter(
        {
            **{("file.write", path): 1 for path in expected_paths},
            **{("file.edit", path): 2 for path in expected_paths},
        }
    )
    assert not approval_path.exists()

    expected_adapter_values = (
        {
            "OPENCODE_API_KEY": "original-key",
            "OPENCODE_BASE_URL": "https://original.invalid",
            "OPENCODE_MODEL": "openai/original-model",
        }
        if preexisting_opencode
        else {}
    )
    for name in ("OPENCODE_API_KEY", "OPENCODE_BASE_URL", "OPENCODE_MODEL"):
        adapter_state = adapter_call["opencode"][name]
        assert adapter_state["present"] is preexisting_opencode
        assert adapter_state["value"] == expected_adapter_values.get(name)
    assert evaluator_call["opencode"] == {
        "OPENCODE_API_KEY": {"present": True, "value": "fake-phycode-key"},
        "OPENCODE_BASE_URL": {
            "present": True,
            "value": "https://fake.invalid/v1",
        },
        "OPENCODE_MODEL": {"present": True, "value": "openai/fake-model"},
    }
    for secret in (
        "fake-phycode-key",
        "https://fake.invalid/v1",
        "original-key",
        "https://original.invalid",
    ):
        assert secret not in completed.stdout
        assert secret not in completed.stderr


@pytest.mark.parametrize("powershell_name", ["pwsh", "powershell"])
def test_public_full_rejects_missing_provider_before_uv(
    tmp_path: Path,
    powershell_name: str,
) -> None:
    powershell = shutil.which(powershell_name)
    if powershell is None:
        pytest.skip(f"{powershell_name} is unavailable")

    evaluator = tmp_path / "evaluator"
    evaluator.mkdir()
    wheel = tmp_path / "phycode-0.1.2-py3-none-any.whl"
    wheel.write_bytes(b"fake wheel")
    environment = os.environ.copy()
    for name in ("PHYCODE_API_KEY", "PHYCODE_BASE_URL", "PHYCODE_MODEL"):
        environment.pop(name, None)

    powershell_arguments = [powershell, "-NoProfile"]
    if powershell_name == "powershell":
        powershell_arguments.extend(["-ExecutionPolicy", "Bypass"])
    powershell_arguments.extend(
        [
            "-File",
            str(ROOT / "integrations/prbench/run_public_full.ps1"),
            "-EvaluatorRoot",
            str(evaluator),
            "-WheelPath",
            str(wheel),
        ]
    )
    completed = subprocess.run(
        powershell_arguments,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=environment,
    )
    assert completed.returncode != 0
    assert "must be configured in the current process" in (
        completed.stdout + completed.stderr
    )


@pytest.mark.parametrize("fake_uv_failure", [False, True])
@pytest.mark.parametrize("preexisting_opencode", [False, True])
def test_public_smoke_restores_or_removes_opencode_environment_with_fake_uv(
    tmp_path: Path,
    fake_uv_failure: bool,
    preexisting_opencode: bool,
) -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell is unavailable")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    if os.name == "nt":
        (fake_bin / "uv.cmd").write_text(
            "@echo off\r\n"
            "echo %* | findstr /C:\"apply_adapter.py\" >nul\r\n"
            "if not errorlevel 1 exit /b 0\r\n"
            "if not \"%OPENCODE_API_KEY%\"==\"fake-phycode-key\" exit /b 71\r\n"
            "if not \"%OPENCODE_BASE_URL%\"==\"https://fake.invalid/v1\" exit /b 72\r\n"
            "if not \"%OPENCODE_MODEL%\"==\"openai/fake-model\" exit /b 73\r\n"
            "if \"%FAKE_UV_FAIL%\"==\"1\" exit /b 23\r\n"
            "exit /b 0\r\n",
            encoding="utf-8",
        )
    else:
        fake_uv = fake_bin / "uv"
        fake_uv.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in *apply_adapter.py*) exit 0 ;; esac\n"
            "[ \"$OPENCODE_API_KEY\" = \"fake-phycode-key\" ] || exit 71\n"
            "[ \"$OPENCODE_BASE_URL\" = \"https://fake.invalid/v1\" ] || exit 72\n"
            "[ \"$OPENCODE_MODEL\" = \"openai/fake-model\" ] || exit 73\n"
            "[ \"$FAKE_UV_FAIL\" = \"1\" ] && exit 23\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IXUSR)
    evaluator = tmp_path / "evaluator"
    evaluator.mkdir()
    wheel = tmp_path / "phycode-0.1.2-py3-none-any.whl"
    wheel.write_bytes(b"fake wheel")
    wrapper = tmp_path / "invoke-smoke.ps1"
    wrapper.write_text(
        "param(\n"
        "  [string]$SmokeScript, [string]$EvaluatorRoot, [string]$WheelPath,\n"
        "  [string]$ExpectFailure, [string]$HadOriginal\n"
        ")\n"
        "$failed = $false\n"
        "try {\n"
        "  & $SmokeScript -EvaluatorRoot $EvaluatorRoot -WheelPath $WheelPath "
        "-TaskIds aaatest_helloworld\n"
        "}\n"
        "catch { $failed = $true }\n"
        "if ($failed -ne ($ExpectFailure -eq '1')) { exit 81 }\n"
        "$names = @('OPENCODE_API_KEY','OPENCODE_BASE_URL','OPENCODE_MODEL')\n"
        "if ($HadOriginal -eq '1') {\n"
        "  if ($env:OPENCODE_API_KEY -ne 'original-key') { exit 82 }\n"
        "  if ($env:OPENCODE_BASE_URL -ne 'https://original.invalid') { exit 83 }\n"
        "  if ($env:OPENCODE_MODEL -ne 'openai/original-model') { exit 84 }\n"
        "}\n"
        "else {\n"
        "  foreach ($name in $names) {\n"
        "    if (Test-Path -LiteralPath ('Env:' + $name)) { exit 85 }\n"
        "  }\n"
        "}\n"
        "Write-Output 'environment-restored'\n",
        encoding="utf-8",
    )

    environment = os.environ.copy()
    environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
    environment.update(
        {
            "PHYCODE_API_KEY": "fake-phycode-key",
            "PHYCODE_BASE_URL": "https://fake.invalid/v1",
            "PHYCODE_MODEL": "fake-model",
            "FAKE_UV_FAIL": "1" if fake_uv_failure else "0",
        }
    )
    originals = {
        "OPENCODE_API_KEY": "original-key",
        "OPENCODE_BASE_URL": "https://original.invalid",
        "OPENCODE_MODEL": "openai/original-model",
    }
    for name, value in originals.items():
        if preexisting_opencode:
            environment[name] = value
        else:
            environment.pop(name, None)

    completed = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(wrapper),
            "-SmokeScript",
            str(ROOT / "integrations/prbench/run_public_smoke.ps1"),
            "-EvaluatorRoot",
            str(evaluator),
            "-WheelPath",
            str(wheel),
            "-ExpectFailure",
            "1" if fake_uv_failure else "0",
            "-HadOriginal",
            "1" if preexisting_opencode else "0",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert "environment-restored" in completed.stdout
    for secret in (
        "fake-phycode-key",
        "https://fake.invalid/v1",
        "original-key",
        "https://original.invalid",
    ):
        assert secret not in completed.stdout
        assert secret not in completed.stderr


def test_docs_specify_interactive_slash_completion_contract() -> None:
    readme = _read("README.md")
    spec = _read("SPEC.md")
    for text in (
        "输入 `/`",
        "实时过滤",
        "↑/↓",
        "Tab",
        "Enter",
        "Esc",
        "`/model `",
        "真实模型候选",
        "非 TTY",
    ):
        assert text in readme
    for text in (
        "斜杠命令候选",
        "参数提示",
        "非 TTY",
        "prompt_toolkit",
    ):
        assert text in spec
