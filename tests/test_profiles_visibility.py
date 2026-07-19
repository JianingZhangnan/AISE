import json
from pathlib import Path

import pytest

from phycode.approval import ApprovalManifest
from phycode.models import AgentProfile
from phycode.models import PolicyAction, SessionMode, ToolCall
from phycode.policy import PolicyContext, PolicyEngine
from phycode.profiles import profile_spec
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.file_tools import register_file_tools
from phycode.tools.search_tools import register_search_tools
from phycode.visibility import PathVisibilityPolicy, VisibilityViolation


WIN32_CSV_ALIAS_PATHS = (
    "data/output.csv ",
    "data/output.csv...   ",
    "data. /output.csv",
    "DATA... /Nested. /OUTPUT.CSV... ",
    r"DATA. \nested... \output.csv. ",
    "data/output.csv::$DATA",
)


def test_prbench_profile_is_single_source_of_runtime_limits() -> None:
    spec = profile_spec(AgentProfile.PRBENCH)
    assert spec.max_context_chars == 12_000
    assert spec.max_tool_calls == 40
    assert spec.hidden_path_components == frozenset({"_ground_truth"})
    assert "process.run" in spec.tool_names
    assert "shell.run" not in spec.tool_names
    assert "final is accepted only after artifact verification" in spec.system_prompt
    assert "Do not write or edit expected CSV files" in spec.system_prompt
    assert "process.run" in spec.system_prompt
    assert "hash-bound approval" in spec.system_prompt


@pytest.mark.parametrize("tool_name", ["file.write", "file.edit"])
@pytest.mark.parametrize(
    "path",
    [
        "data/output.csv",
        "DATA/OUTPUT.CSV",
        "data/nested/output.csv",
        r"data\nested\output.csv",
        "data/temporary/../output.csv",
        *WIN32_CSV_ALIAS_PATHS,
    ],
)
def test_prbench_policy_denies_direct_csv_mutation(
    tmp_path: Path,
    tool_name: str,
    path: str,
) -> None:
    args = {"path": path, "content": "a,b\n1,2\n"}
    if tool_name == "file.edit":
        args = {"path": path, "old": "1,2", "new": "3,4"}

    decision = PolicyEngine().decide(
        ToolCall(tool_name=tool_name, args=args),
        PolicyContext(
            tmp_path,
            [],
            False,
            profile_spec=profile_spec(AgentProfile.PRBENCH),
        ),
    )

    assert decision.decision == PolicyAction.DENY
    if ":" in path:
        assert decision.rule_id == "prbench.win32_stream_blocked"
        assert "alternate data stream" in decision.reason
    else:
        assert decision.rule_id == "prbench.direct_csv_mutation_blocked"
        assert "reproduction script" in decision.reason
        assert "process.run" in decision.reason


def test_prbench_direct_csv_grant_cannot_bypass_policy(tmp_path: Path) -> None:
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps(
            {"grants": [{"tool_name": "file.write", "path": "data/output.csv"}]}
        ),
        encoding="utf-8",
    )
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    registry = ToolRegistry()
    register_file_tools(registry)
    call = ToolCall(
        tool_name="file.write",
        args={"path": "data/output.csv", "content": "a,b\n1,2\n"},
    )

    result = ToolRuntime(registry).run(
        call,
        PolicyContext(
            tmp_path,
            [],
            False,
            profile_spec=profile_spec(AgentProfile.PRBENCH),
        ),
        approval_handler=manifest,
    )

    assert result.policy.decision == PolicyAction.DENY
    assert result.tool_result.status == "policy_blocked"
    assert not (tmp_path / "data/output.csv").exists()


@pytest.mark.parametrize("tool_name", ["file.write", "file.edit"])
@pytest.mark.parametrize("alias_path", WIN32_CSV_ALIAS_PATHS)
def test_prbench_win32_csv_alias_exact_grant_cannot_mutate_real_output(
    tmp_path: Path,
    tool_name: str,
    alias_path: str,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    output = data / "output.csv"
    output.write_text("sentinel\n", encoding="utf-8")
    approvals = tmp_path / "approvals.json"
    approvals.write_text(
        json.dumps({"grants": [{"tool_name": tool_name, "path": alias_path}]}),
        encoding="utf-8",
    )
    manifest = ApprovalManifest.from_json(approvals, tmp_path)
    before = sorted(
        path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")
    )
    registry = ToolRegistry()
    register_file_tools(registry)
    args = {"path": alias_path, "content": "mutated\n"}
    if tool_name == "file.edit":
        args = {"path": alias_path, "old": "sentinel", "new": "mutated"}

    result = ToolRuntime(registry).run(
        ToolCall(tool_name=tool_name, args=args),
        PolicyContext(
            tmp_path,
            [],
            False,
            profile_spec=profile_spec(AgentProfile.PRBENCH),
        ),
        approval_handler=manifest,
    )

    assert result.policy.decision == PolicyAction.DENY
    assert result.tool_result.status == "policy_blocked"
    assert output.read_text(encoding="utf-8") == "sentinel\n"
    assert sorted(
        path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")
    ) == before


@pytest.mark.parametrize("profile", [AgentProfile.CODING, AgentProfile.GAIA])
def test_direct_csv_policy_is_prbench_only(tmp_path: Path, profile: AgentProfile) -> None:
    decision = PolicyEngine().decide(
        ToolCall(
            tool_name="file.write",
            args={"path": "data/output.csv", "content": "a,b\n1,2\n"},
        ),
        PolicyContext(tmp_path, [], False, profile_spec=profile_spec(profile)),
    )

    assert decision.decision == PolicyAction.ASK
    assert decision.rule_id == "tool.risky_default"


@pytest.mark.parametrize("profile", [AgentProfile.CODING, AgentProfile.GAIA])
@pytest.mark.parametrize("tool_name", ["file.write", "file.edit"])
@pytest.mark.parametrize("alias_path", ["data/output.csv... ", "data/output.csv::$DATA"])
def test_win32_alias_fail_safe_is_prbench_only(
    tmp_path: Path,
    profile: AgentProfile,
    tool_name: str,
    alias_path: str,
) -> None:
    args = {"path": alias_path, "content": "content"}
    if tool_name == "file.edit":
        args = {"path": alias_path, "old": "before", "new": "after"}

    decision = PolicyEngine().decide(
        ToolCall(tool_name=tool_name, args=args),
        PolicyContext(tmp_path, [], False, profile_spec=profile_spec(profile)),
    )

    assert decision.decision == PolicyAction.ASK
    assert decision.rule_id == "tool.risky_default"


@pytest.mark.parametrize(
    ("path", "expected_rule"),
    [
        ("../data/output.csv. ", "workspace.path_escape"),
        ("_ground_truth/data/output.csv. ", "prbench.hidden_path_blocked"),
    ],
)
def test_prbench_win32_alias_keeps_visibility_decision_first(
    tmp_path: Path,
    path: str,
    expected_rule: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []
    original_resolve = PathVisibilityPolicy.resolve

    def record_original_path(policy: PathVisibilityPolicy, candidate: str | Path) -> Path:
        observed.append(str(candidate))
        return original_resolve(policy, candidate)

    monkeypatch.setattr(PathVisibilityPolicy, "resolve", record_original_path)
    decision = PolicyEngine().decide(
        ToolCall(tool_name="file.write", args={"path": path, "content": "blocked"}),
        PolicyContext(
            tmp_path,
            [],
            False,
            profile_spec=profile_spec(AgentProfile.PRBENCH),
        ),
    )

    assert observed == [path]
    assert decision.decision == PolicyAction.DENY
    assert decision.rule_id == expected_rule


@pytest.mark.parametrize("tool_name", ["file.write", "file.edit"])
def test_prbench_non_drive_colon_fails_closed_but_drive_prefix_does_not(
    tmp_path: Path,
    tool_name: str,
) -> None:
    context = PolicyContext(
        tmp_path,
        [],
        False,
        profile_spec=profile_spec(AgentProfile.PRBENCH),
    )
    stream = PolicyEngine().decide(
        ToolCall(
            tool_name=tool_name,
            args=(
                {"path": "reproduction/script.py::$DATA", "content": "blocked"}
                if tool_name == "file.write"
                else {
                    "path": "reproduction/script.py::$DATA",
                    "old": "before",
                    "new": "after",
                }
            ),
        ),
        context,
    )
    ordinary_absolute = PolicyEngine().decide(
        ToolCall(
            tool_name=tool_name,
            args=(
                {
                    "path": str(tmp_path / "reproduction/script.py"),
                    "content": "approval required",
                }
                if tool_name == "file.write"
                else {
                    "path": str(tmp_path / "reproduction/script.py"),
                    "old": "before",
                    "new": "after",
                }
            ),
        ),
        context,
    )

    assert stream.decision == PolicyAction.DENY
    assert stream.rule_id == "prbench.win32_stream_blocked"
    assert ordinary_absolute.decision == PolicyAction.ASK
    assert ordinary_absolute.rule_id == "tool.risky_default"


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


def test_policy_blocks_visible_symlink_alias_to_resolved_credential(tmp_path: Path) -> None:
    credential = tmp_path / ".env"
    credential.write_text("TEST_ONLY=value", encoding="utf-8")
    alias = tmp_path / "public.txt"
    try:
        alias.symlink_to(credential)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    decision = PolicyEngine().decide(
        ToolCall(tool_name="file.read", args={"path": "public.txt"}),
        PolicyContext(tmp_path, [], False),
    )

    assert decision.decision == PolicyAction.DENY
    assert decision.rule_id == "credential.read_blocked"


def test_policy_classifies_resolved_credential_target_at_visibility_boundary(tmp_path: Path, monkeypatch) -> None:
    resolved_credential = tmp_path / ".env"
    original_resolve = PathVisibilityPolicy.resolve

    def resolve_alias(policy: PathVisibilityPolicy, path: str | Path) -> Path:
        if str(path) == "public.txt":
            return resolved_credential
        return original_resolve(policy, path)

    monkeypatch.setattr(PathVisibilityPolicy, "resolve", resolve_alias)
    decision = PolicyEngine().decide(
        ToolCall(tool_name="file.read", args={"path": "public.txt"}),
        PolicyContext(tmp_path, [], False),
    )

    assert decision.decision == PolicyAction.DENY
    assert decision.rule_id == "credential.read_blocked"


def test_policy_blocks_netrc_file_read(tmp_path: Path) -> None:
    (tmp_path / ".netrc").write_text("NETRC_SENTINEL", encoding="utf-8")

    decision = PolicyEngine().decide(
        ToolCall(tool_name="file.read", args={"path": ".netrc"}),
        PolicyContext(tmp_path, [], False),
    )

    assert decision.decision == PolicyAction.DENY
    assert decision.rule_id == "credential.read_blocked"


def test_prbench_policy_denies_explicit_hidden_path(tmp_path: Path) -> None:
    context = PolicyContext(
        tmp_path,
        [],
        False,
        profile_spec=profile_spec(AgentProfile.PRBENCH),
    )
    decision = PolicyEngine().decide(
        ToolCall(tool_name="file.read", args={"path": "_ground_truth/sentinel.txt"}),
        context,
    )
    assert decision.decision == PolicyAction.DENY
    assert decision.rule_id == "prbench.hidden_path_blocked"


@pytest.mark.parametrize("tool_name", ["search.grep", "search.glob"])
def test_search_tools_do_not_expose_hidden_tree(tmp_path: Path, tool_name: str) -> None:
    hidden = tmp_path / "_ground_truth"
    hidden.mkdir()
    (hidden / "sentinel.txt").write_text("SENTINEL", encoding="utf-8")
    (tmp_path / "public.txt").write_text("PUBLIC", encoding="utf-8")
    visibility = PathVisibilityPolicy(tmp_path, hidden_components=frozenset({"_ground_truth"}))
    registry = ToolRegistry()
    register_search_tools(registry, workspace_root=tmp_path, visibility=visibility)
    args = {"pattern": "SENTINEL"} if tool_name == "search.grep" else {"pattern": "**/*"}
    result = ToolRuntime(registry).run(
        ToolCall(tool_name=tool_name, args=args),
        PolicyContext(tmp_path, [], False),
    )
    assert "sentinel.txt" not in result.tool_result.stdout
    assert "SENTINEL" not in result.tool_result.stdout


@pytest.mark.parametrize("tool_name", ["search.grep", "search.glob"])
def test_search_tools_do_not_expose_netrc(tmp_path: Path, tool_name: str) -> None:
    (tmp_path / ".netrc").write_text("NETRC_SENTINEL", encoding="utf-8")
    registry = ToolRegistry()
    register_search_tools(registry, workspace_root=tmp_path)
    args = {"pattern": "NETRC_SENTINEL"} if tool_name == "search.grep" else {"pattern": "**/*"}

    result = ToolRuntime(registry).run(
        ToolCall(tool_name=tool_name, args=args),
        PolicyContext(tmp_path, [], False),
    )

    assert ".netrc" not in result.tool_result.stdout
    assert "NETRC_SENTINEL" not in result.tool_result.stdout


def test_build_agent_reuses_one_profile_spec_for_runtime_settings(tmp_path: Path, monkeypatch) -> None:
    import phycode.cli as cli
    import phycode.composition as composition
    from phycode.llm import EchoLLM
    from phycode.profiles import ProfileSpec

    expected = ProfileSpec(
        profile=AgentProfile.PRBENCH,
        tool_names=frozenset({"calculator.calculate"}),
        system_prompt="profile prompt sentinel",
        max_context_chars=3_333,
        max_tool_calls=7,
        hidden_path_components=frozenset({"hidden-sentinel"}),
    )
    calls: list[AgentProfile] = []

    def tracked_profile_spec(profile: AgentProfile) -> ProfileSpec:
        calls.append(profile)
        return expected

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(composition, "profile_spec", tracked_profile_spec)
    loop = cli.build_agent(SessionMode.NON_INTERACTIVE, llm=EchoLLM(), profile=AgentProfile.PRBENCH)

    assert calls == [AgentProfile.PRBENCH]
    assert loop.context_builder.system_prompt == expected.system_prompt
    assert loop.context_builder.max_chars == expected.max_context_chars
    assert loop.max_tool_calls == expected.max_tool_calls
    assert loop.policy_context.profile_spec is expected
    assert {spec.name for spec in loop.tool_runtime.registry.list_specs()} == expected.tool_names
