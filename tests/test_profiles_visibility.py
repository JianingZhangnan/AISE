from pathlib import Path

import pytest

from phycode.models import AgentProfile
from phycode.models import PolicyAction, SessionMode, ToolCall
from phycode.policy import PolicyContext, PolicyEngine
from phycode.profiles import profile_spec
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.search_tools import register_search_tools
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


def test_build_agent_reuses_one_profile_spec_for_runtime_settings(tmp_path: Path, monkeypatch) -> None:
    import phycode.cli as cli
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
    monkeypatch.setattr(cli, "profile_spec", tracked_profile_spec)
    loop = cli.build_agent(SessionMode.NON_INTERACTIVE, llm=EchoLLM(), profile=AgentProfile.PRBENCH)

    assert calls == [AgentProfile.PRBENCH]
    assert loop.context_builder.system_prompt == expected.system_prompt
    assert loop.context_builder.max_chars == expected.max_context_chars
    assert loop.max_tool_calls == expected.max_tool_calls
    assert loop.policy_context.profile_spec is expected
    assert {spec.name for spec in loop.tool_runtime.registry.list_specs()} == expected.tool_names
