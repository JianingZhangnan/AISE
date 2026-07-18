from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError
import pytest

from phycode.execution import ExecutionJournal, ExecutionJournalError
from phycode.models import AgentProfile, ToolCall
from phycode.policy import PolicyContext
from phycode.prbench_contract import ArtifactConstraint, ArtifactVerifier, TaskContract
from phycode.profiles import profile_spec
from phycode.tools import ToolRegistry, ToolRuntime
from phycode.tools.process_tools import register_process_tools
from phycode.visibility import is_credential_path


def _contract() -> TaskContract:
    return TaskContract(
        instruction_file="instruction.md",
        paper_file="paper.md",
        expected_files=("reproduction/generate.py", "data/output.csv"),
        constraints=(
            ArtifactConstraint(
                path="data/output.csv",
                csv_header=("a", "b"),
                csv_rows=(("1", "2"),),
            ),
        ),
    )


def _run_python(tmp_path: Path, journal: ExecutionJournal) -> str:
    registry = ToolRegistry()
    register_process_tools(
        registry,
        tmp_path,
        frozenset({Path(sys.executable).resolve()}),
        journal=journal,
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, "reproduction/generate.py"], "cwd": "."},
    )
    context = PolicyContext(tmp_path, [], False, profile_spec(AgentProfile.PRBENCH))
    return ToolRuntime(registry).run(call, context, approved=True).tool_result.status


def _run_call(
    tmp_path: Path,
    journal: ExecutionJournal,
    *,
    argv: list[str],
    timeout: int = 30,
    cwd: str = ".",
) -> str:
    registry = ToolRegistry()
    register_process_tools(
        registry,
        tmp_path,
        frozenset({Path(sys.executable).resolve()}),
        journal=journal,
    )
    call = ToolCall(
        tool_name="process.run",
        args={"argv": [sys.executable, *argv], "cwd": cwd, "timeout": timeout},
    )
    context = PolicyContext(tmp_path, [], False, profile_spec(AgentProfile.PRBENCH))
    return ToolRuntime(registry).run(call, context, approved=True).tool_result.status


def test_csv_requires_successful_script_provenance(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data/output.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    result = ArtifactVerifier(
        tmp_path,
        _contract(),
        ExecutionJournal(tmp_path, ("data/output.csv",)),
    ).verify()

    assert not result.ok
    assert {issue.code for issue in result.issues} == {
        "script_not_executed",
        "csv_without_provenance",
    }


def test_real_script_execution_establishes_csv_provenance(tmp_path: Path) -> None:
    (tmp_path / "reproduction").mkdir()
    (tmp_path / "reproduction/generate.py").write_text(
        "import csv, pathlib\n"
        "pathlib.Path('data').mkdir(exist_ok=True)\n"
        "with open('data/output.csv','w',newline='') as f:\n"
        " w=csv.writer(f); w.writerow(['a','b']); w.writerow([1,2])\n",
        encoding="utf-8",
    )
    journal = ExecutionJournal(tmp_path, ("data/output.csv",))

    assert _run_python(tmp_path, journal) == "ok"
    result = ArtifactVerifier(tmp_path, _contract(), journal).verify()

    assert result.ok
    with (tmp_path / "data/output.csv").open(newline="", encoding="utf-8") as handle:
        assert list(csv.reader(handle)) == [["a", "b"], ["1", "2"]]


def test_failed_script_cannot_establish_csv_provenance(tmp_path: Path) -> None:
    (tmp_path / "reproduction").mkdir()
    (tmp_path / "reproduction/generate.py").write_text(
        "import pathlib, sys\n"
        "pathlib.Path('data').mkdir(exist_ok=True)\n"
        "pathlib.Path('data/output.csv').write_text('a,b\\n1,2\\n', encoding='utf-8')\n"
        "sys.exit(7)\n",
        encoding="utf-8",
    )
    journal = ExecutionJournal(tmp_path, ("data/output.csv",))

    assert _run_python(tmp_path, journal) == "command_failed"
    result = ArtifactVerifier(tmp_path, _contract(), journal).verify()

    assert not result.ok
    assert {issue.code for issue in result.issues} == {"script_not_executed", "csv_without_provenance"}


def test_contract_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ArtifactConstraint.model_validate({"path": "data/output.csv", "unexpected": True})
    with pytest.raises(ValidationError):
        TaskContract.model_validate(
            {
                "instruction_file": "instruction.md",
                "paper_file": "paper.md",
                "expected_files": ("data/output.csv",),
                "unexpected": True,
            }
        )


def test_unchanged_csv_is_not_claimed_by_successful_script(tmp_path: Path) -> None:
    (tmp_path / "reproduction").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data/output.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "reproduction/generate.py").write_text("pass\n", encoding="utf-8")
    journal = ExecutionJournal(tmp_path, ("data/output.csv",))

    assert _run_python(tmp_path, journal) == "ok"
    result = ArtifactVerifier(tmp_path, _contract(), journal).verify()

    assert not result.ok
    assert {issue.code for issue in result.issues} == {"csv_without_provenance"}


def test_successful_process_without_script_provenance_cannot_claim_csv(tmp_path: Path) -> None:
    (tmp_path / "reproduction").mkdir()
    (tmp_path / "reproduction/generate").write_text(
        "import pathlib\n"
        "pathlib.Path('data').mkdir(exist_ok=True)\n"
        "pathlib.Path('data/output.csv').write_text('a,b\\n1,2\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    contract = TaskContract(
        instruction_file="instruction.md",
        paper_file="paper.md",
        expected_files=("data/output.csv",),
        constraints=(
            ArtifactConstraint(
                path="data/output.csv",
                csv_header=("a", "b"),
                csv_rows=(("1", "2"),),
            ),
        ),
    )
    journal = ExecutionJournal(tmp_path, ("data/output.csv",))

    assert _run_call(tmp_path, journal, argv=["reproduction/generate"]) == "ok"
    result = ArtifactVerifier(tmp_path, contract, journal).verify()

    assert not result.ok
    assert {issue.code for issue in result.issues} == {"csv_without_provenance"}


def test_csv_provenance_must_share_record_with_current_script_hash(tmp_path: Path) -> None:
    (tmp_path / "reproduction").mkdir()
    script = tmp_path / "reproduction/generate.py"
    script.write_text(
        "import pathlib\n"
        "pathlib.Path('data').mkdir(exist_ok=True)\n"
        "pathlib.Path('data/output.csv').write_text('a,b\\n1,2\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    journal = ExecutionJournal(tmp_path, ("data/output.csv",))
    assert _run_python(tmp_path, journal) == "ok"
    script.write_text("pass\n", encoding="utf-8")

    assert _run_python(tmp_path, journal) == "ok"
    result = ArtifactVerifier(tmp_path, _contract(), journal).verify()

    assert not result.ok
    assert {issue.code for issue in result.issues} == {"csv_without_provenance"}


def test_any_current_expected_script_can_support_csv_in_same_record(tmp_path: Path) -> None:
    (tmp_path / "reproduction").mkdir()
    passive_script = tmp_path / "reproduction/passive.py"
    passive_script.write_text("pass\n", encoding="utf-8")
    generating_script = tmp_path / "reproduction/generate.py"
    generating_script.write_text(
        "import pathlib\n"
        "pathlib.Path('data').mkdir(exist_ok=True)\n"
        "pathlib.Path('data/output.csv').write_text('a,b\\n1,2\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    contract = TaskContract(
        instruction_file="instruction.md",
        paper_file="paper.md",
        expected_files=(
            "reproduction/passive.py",
            "reproduction/generate.py",
            "data/output.csv",
        ),
        constraints=(
            ArtifactConstraint(
                path="data/output.csv",
                csv_header=("a", "b"),
                csv_rows=(("1", "2"),),
            ),
        ),
    )
    journal = ExecutionJournal(tmp_path, ("data/output.csv",))

    assert _run_call(tmp_path, journal, argv=["reproduction/passive.py"]) == "ok"
    assert _run_call(tmp_path, journal, argv=["reproduction/generate.py"]) == "ok"

    assert ArtifactVerifier(tmp_path, contract, journal).verify().ok


def test_timeout_cannot_establish_csv_provenance(tmp_path: Path) -> None:
    (tmp_path / "reproduction").mkdir()
    (tmp_path / "reproduction/generate.py").write_text(
        "import pathlib, time\n"
        "pathlib.Path('data').mkdir(exist_ok=True)\n"
        "pathlib.Path('data/output.csv').write_text('a,b\\n1,2\\n', encoding='utf-8')\n"
        "time.sleep(5)\n",
        encoding="utf-8",
    )
    journal = ExecutionJournal(tmp_path, ("data/output.csv",))

    assert _run_call(tmp_path, journal, argv=["reproduction/generate.py"], timeout=1) == "timeout"
    result = ArtifactVerifier(tmp_path, _contract(), journal).verify()

    assert not result.ok
    assert {issue.code for issue in result.issues} == {"script_not_executed", "csv_without_provenance"}


def test_pre_execution_journal_failure_prevents_script_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "reproduction").mkdir()
    (tmp_path / "reproduction/generate.py").write_text(
        "from pathlib import Path\nPath('executed.txt').write_text('unsafe', encoding='utf-8')\n",
        encoding="utf-8",
    )
    journal = ExecutionJournal(tmp_path, ("data/output.csv",))

    def fail_snapshot() -> tuple[()]:
        raise ExecutionJournalError("test-only snapshot failure")

    monkeypatch.setattr(journal, "snapshot_artifacts", fail_snapshot)

    assert _run_python(tmp_path, journal) == "invalid_tool_args"
    assert not (tmp_path / "executed.txt").exists()
    assert journal.records == []


def test_post_execution_journal_failure_cannot_create_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "reproduction").mkdir()
    (tmp_path / "reproduction/generate.py").write_text(
        "import pathlib\n"
        "pathlib.Path('data').mkdir(exist_ok=True)\n"
        "pathlib.Path('data/output.csv').write_text('a,b\\n1,2\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    journal = ExecutionJournal(tmp_path, ("data/output.csv",))
    original_snapshot = journal.snapshot_artifacts
    calls = 0

    def fail_second_snapshot():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ExecutionJournalError("test-only post-execution failure")
        return original_snapshot()

    monkeypatch.setattr(journal, "snapshot_artifacts", fail_second_snapshot)

    assert _run_python(tmp_path, journal) == "tool_error"
    assert (tmp_path / "data/output.csv").is_file()
    assert journal.records == []


@pytest.mark.parametrize(
    "path",
    [
        "../outside.csv",
        "..\\outside.csv",
        "_ground_truth/output.csv",
        "secrets/.env",
        "secrets/id_rsa",
        "secrets/provider.pem",
    ],
)
def test_contract_rejects_non_public_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        TaskContract(
            instruction_file="instruction.md",
            paper_file="paper.md",
            expected_files=(path,),
        )


def test_netrc_is_classified_as_a_shared_credential_path() -> None:
    assert is_credential_path(".netrc")
    assert is_credential_path("nested/.netrc")


def test_contract_rejects_netrc_expected_artifact() -> None:
    with pytest.raises(ValidationError):
        TaskContract(
            instruction_file="instruction.md",
            paper_file="paper.md",
            expected_files=(".netrc",),
        )


def test_execution_journal_rejects_netrc_artifact(tmp_path: Path) -> None:
    with pytest.raises(ExecutionJournalError):
        ExecutionJournal(tmp_path, (".netrc",))


def test_contract_and_journal_reject_absolute_artifact_path(tmp_path: Path) -> None:
    absolute = str(tmp_path / "data/output.csv")

    with pytest.raises(ValidationError):
        TaskContract(
            instruction_file="instruction.md",
            paper_file="paper.md",
            expected_files=(absolute,),
        )
    with pytest.raises(ExecutionJournalError):
        ExecutionJournal(tmp_path, (absolute,))


@pytest.mark.parametrize(
    "path",
    ["C:/private/output.csv", "C:\\private\\output.csv", "//server/share/output.csv"],
)
def test_contract_rejects_windows_absolute_path_forms(path: str) -> None:
    with pytest.raises(ValidationError):
        TaskContract(
            instruction_file="instruction.md",
            paper_file="paper.md",
            expected_files=(path,),
        )


@pytest.mark.parametrize("hidden_target", ["_ground_truth/secret.csv", "private/.env"])
def test_verifier_rejects_symlink_alias_to_hidden_path(tmp_path: Path, hidden_target: str) -> None:
    target = tmp_path / hidden_target
    target.parent.mkdir(parents=True)
    target.write_text("a,b\n1,2\n", encoding="utf-8")
    alias = tmp_path / "public.csv"
    try:
        alias.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    contract = TaskContract(
        instruction_file="instruction.md",
        paper_file="paper.md",
        expected_files=("public.csv",),
    )

    result = ArtifactVerifier(tmp_path, contract, ExecutionJournal(tmp_path, ())).verify()

    assert not result.ok
    assert {issue.code for issue in result.issues} == {"invalid_artifact_path"}


@pytest.mark.parametrize("hidden_target", ["_ground_truth/secret.csv", "private/.env"])
def test_verifier_rechecks_deterministically_resolved_hidden_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hidden_target: str,
) -> None:
    target = tmp_path / hidden_target
    target.parent.mkdir(parents=True)
    target.write_text("a,b\n1,2\n", encoding="utf-8")
    alias = tmp_path / "public.csv"
    contract = TaskContract(
        instruction_file="instruction.md",
        paper_file="paper.md",
        expected_files=("public.csv",),
    )
    journal = ExecutionJournal(tmp_path, ())
    original_resolve = Path.resolve

    def resolve_alias(path: Path, *args, **kwargs) -> Path:
        if path == alias:
            return target
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_alias)

    result = ArtifactVerifier(tmp_path, contract, journal).verify()

    assert not result.ok
    assert {issue.code for issue in result.issues} == {"invalid_artifact_path"}


def test_verifier_turns_stat_oserror_into_structured_issue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "result.txt"
    artifact.write_text("complete", encoding="utf-8")
    original_is_file = Path.is_file
    original_stat = Path.stat

    def is_file(path: Path) -> bool:
        if path == artifact:
            return True
        return original_is_file(path)

    def fail_stat(path: Path, *args, **kwargs):
        if path == artifact:
            raise OSError("test-only inaccessible artifact")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", is_file)
    monkeypatch.setattr(Path, "stat", fail_stat)
    contract = TaskContract(
        instruction_file="instruction.md",
        paper_file="paper.md",
        expected_files=("result.txt",),
    )

    result = ArtifactVerifier(tmp_path, contract, ExecutionJournal(tmp_path, ())).verify()

    assert not result.ok
    assert {issue.code for issue in result.issues} == {"artifact_read_error"}


def test_verifier_turns_read_oserror_into_structured_issue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "output.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    def fail_hash(path: Path) -> str:
        raise OSError("test-only unreadable artifact")

    monkeypatch.setattr("phycode.prbench_contract.file_sha256", fail_hash)
    contract = TaskContract(
        instruction_file="instruction.md",
        paper_file="paper.md",
        expected_files=("output.csv",),
    )

    result = ArtifactVerifier(tmp_path, contract, ExecutionJournal(tmp_path, ())).verify()

    assert not result.ok
    assert {issue.code for issue in result.issues} == {"artifact_read_error"}


def test_execution_jsonl_contains_only_relative_sanitized_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment_sentinel = "journal-environment-sentinel-527641"
    monkeypatch.setenv("TASK3_PRIVATE_VALUE", environment_sentinel)
    (tmp_path / "reproduction").mkdir()
    (tmp_path / "reproduction/generate.py").write_text(
        "import pathlib\n"
        "pathlib.Path('data').mkdir(exist_ok=True)\n"
        "pathlib.Path('data/output.csv').write_text('a,b\\n1,2\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    credential_path = str(tmp_path / "private/.env")
    journal = ExecutionJournal(tmp_path, ("data/output.csv",))

    assert _run_call(
        tmp_path,
        journal,
        argv=["reproduction/generate.py", credential_path],
    ) == "ok"
    journal_path = tmp_path / ".phycode/prbench/execution.jsonl"
    raw = journal_path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    assert environment_sentinel not in raw
    assert credential_path not in raw
    assert ".env" not in raw
    assert payload["cwd"] == "."
    assert payload["argv"][1:] == ["reproduction/generate.py", "[REDACTED_ARG]"]
    assert all(not Path(snapshot["path"]).is_absolute() for snapshot in payload["artifacts_after"])


def test_execution_argv_uses_minimum_disclosure_for_url_token_and_resolved_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "reproduction").mkdir()
    script = tmp_path / "reproduction/generate.py"
    script.write_text(
        "import pathlib\n"
        "pathlib.Path('data').mkdir(exist_ok=True)\n"
        "pathlib.Path('data/output.csv').write_text('a,b\\n1,2\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    hidden_target = tmp_path / "_ground_truth/private-token.txt"
    hidden_target.parent.mkdir()
    hidden_target.write_text("not-read", encoding="utf-8")
    alias = tmp_path / "public-alias.txt"
    url = "https://private-provider.example/v1"
    opaque_token = "opaque-review-token-948215"
    journal = ExecutionJournal(tmp_path, ("data/output.csv",))
    original_resolve = Path.resolve

    def resolve_alias(path: Path, *args, **kwargs) -> Path:
        if path == alias:
            return hidden_target
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_alias)

    assert _run_call(
        tmp_path,
        journal,
        argv=["reproduction/generate.py", url, opaque_token, str(alias)],
    ) == "ok"
    raw = (tmp_path / ".phycode/prbench/execution.jsonl").read_text(encoding="utf-8")
    record = journal.records[-1]

    assert record.argv[1:] == (
        "reproduction/generate.py",
        "[REDACTED_ARG]",
        "[REDACTED_ARG]",
        "[REDACTED_ARG]",
    )
    for secret in (url, opaque_token, "_ground_truth", "private-token.txt"):
        assert secret not in raw
        assert secret not in json.dumps(record.model_dump(mode="json"))


@pytest.mark.parametrize("sensitive_cwd", [".ssh", ".aws", ".env"])
def test_sensitive_cwd_is_rejected_before_script_execution(
    tmp_path: Path,
    sensitive_cwd: str,
) -> None:
    (tmp_path / sensitive_cwd).mkdir()
    (tmp_path / "reproduction").mkdir()
    script = tmp_path / "reproduction/sentinel.py"
    script.write_text(
        "from pathlib import Path\n"
        "(Path(__file__).resolve().parents[1] / 'executed.txt').write_text('unsafe', encoding='utf-8')\n",
        encoding="utf-8",
    )
    journal = ExecutionJournal(tmp_path, ())

    status = _run_call(tmp_path, journal, argv=[str(script)], cwd=sensitive_cwd)

    assert status == "invalid_tool_args"
    assert not (tmp_path / "executed.txt").exists()
    assert journal.records == []


def test_resolved_sensitive_cwd_alias_is_rejected_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hidden_cwd = tmp_path / ".ssh"
    hidden_cwd.mkdir()
    alias = tmp_path / "public-cwd"
    (tmp_path / "reproduction").mkdir()
    script = tmp_path / "reproduction/sentinel.py"
    script.write_text(
        "from pathlib import Path\n"
        "(Path(__file__).resolve().parents[1] / 'executed.txt').write_text('unsafe', encoding='utf-8')\n",
        encoding="utf-8",
    )
    journal = ExecutionJournal(tmp_path, ())
    original_resolve = Path.resolve

    def resolve_alias(path: Path, *args, **kwargs) -> Path:
        if path == alias:
            return hidden_cwd
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_alias)

    status = _run_call(tmp_path, journal, argv=[str(script)], cwd="public-cwd")

    assert status == "invalid_tool_args"
    assert not (tmp_path / "executed.txt").exists()
    assert journal.records == []


def test_process_registration_rejects_journal_from_different_workspace(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    journal_root = tmp_path / "journal"
    runtime_root.mkdir()
    journal_root.mkdir()
    registry = ToolRegistry()
    journal = ExecutionJournal(journal_root, ())

    with pytest.raises(ValueError, match="workspace"):
        register_process_tools(
            registry,
            runtime_root,
            frozenset({Path(sys.executable).resolve()}),
            journal=journal,
        )

    assert registry.spec_for("process.run") is None


def test_csv_constraints_compare_header_and_rows_exactly(tmp_path: Path) -> None:
    (tmp_path / "reproduction").mkdir()
    (tmp_path / "reproduction/generate.py").write_text(
        "import pathlib\n"
        "pathlib.Path('data').mkdir(exist_ok=True)\n"
        "pathlib.Path('data/output.csv').write_text('a,c\\n1,3\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    journal = ExecutionJournal(tmp_path, ("data/output.csv",))

    assert _run_python(tmp_path, journal) == "ok"
    result = ArtifactVerifier(tmp_path, _contract(), journal).verify()

    assert not result.ok
    assert {issue.code for issue in result.issues} == {"csv_header_mismatch", "csv_rows_mismatch"}


def test_constraint_path_must_belong_to_expected_files() -> None:
    with pytest.raises(ValidationError, match="expected_files"):
        TaskContract(
            instruction_file="instruction.md",
            paper_file="paper.md",
            expected_files=("result.txt",),
            constraints=(ArtifactConstraint(path="constraint-only.csv", csv_header=("a",)),),
        )


def test_duplicate_expected_paths_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        TaskContract(
            instruction_file="instruction.md",
            paper_file="paper.md",
            expected_files=("data/output.csv", "data/output.csv"),
        )


def test_duplicate_constraint_paths_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        TaskContract(
            instruction_file="instruction.md",
            paper_file="paper.md",
            expected_files=("data/output.csv",),
            constraints=(
                ArtifactConstraint(path="data/output.csv", csv_header=("a",)),
                ArtifactConstraint(path="data/output.csv", csv_header=("b",)),
            ),
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows paths are case-insensitive")
def test_constraint_path_membership_uses_windows_case_semantics() -> None:
    contract = TaskContract(
        instruction_file="instruction.md",
        paper_file="paper.md",
        expected_files=("Data/Output.csv",),
        constraints=(ArtifactConstraint(path="data/output.CSV", csv_header=("a",)),),
    )

    assert contract.constraints[0].path == "data/output.CSV"
