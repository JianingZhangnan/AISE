from pathlib import Path

import pytest

from phycode.models import PolicyAction, ToolCall
from phycode.policy import PolicyContext, PolicyEngine, WorkspaceViolation, resolve_workspace_path


def test_safe_read_is_allowed(tmp_path: Path):
    call = ToolCall(tool_name="file.read", args={"path": "README.md"})
    decision = PolicyEngine().decide(call, PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))
    assert decision.decision == PolicyAction.ALLOW


def test_file_edit_requires_approval(tmp_path: Path):
    call = ToolCall(tool_name="file.edit", args={"path": "app.py", "old": "a", "new": "b"})
    decision = PolicyEngine().decide(call, PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))
    assert decision.decision == PolicyAction.ASK
    assert decision.requires_user is True


def test_image_inspection_requires_approval(tmp_path: Path):
    call = ToolCall(tool_name="image.inspect", args={"path": "photo.jpg"})
    decision = PolicyEngine().decide(call, PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=False))
    assert decision.decision == PolicyAction.ASK


def test_path_escape_is_rejected(tmp_path: Path):
    with pytest.raises(WorkspaceViolation):
        resolve_workspace_path("../secret.txt", PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))


def test_dangerous_shell_is_denied(tmp_path: Path):
    call = ToolCall(tool_name="shell.run", args={"command": "rm -rf /"})
    decision = PolicyEngine().decide(call, PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))
    assert decision.decision == PolicyAction.DENY
    assert decision.rule_id == "shell.dangerous_command"


def test_env_file_read_is_denied(tmp_path: Path):
    call = ToolCall(tool_name="file.read", args={"path": ".env"})
    decision = PolicyEngine().decide(call, PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))
    assert decision.decision == PolicyAction.DENY


def test_shell_reading_credential_file_is_denied(tmp_path: Path):
    call = ToolCall(tool_name="shell.run", args={"command": "cat .env"})
    decision = PolicyEngine().decide(call, PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))
    assert decision.decision == PolicyAction.DENY
    assert decision.rule_id == "credential.shell_read_blocked"


def test_shell_reading_private_key_is_denied(tmp_path: Path):
    call = ToolCall(tool_name="shell.run", args={"command": "type id_rsa"})
    decision = PolicyEngine().decide(call, PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))
    assert decision.decision == PolicyAction.DENY
    assert decision.rule_id == "credential.shell_read_blocked"


@pytest.mark.parametrize(
    "command",
    [
        "rmdir /s /q C:\\Windows",
        "git push --force origin main",
        "sudo rm -rf /var",
        "dd if=/dev/zero of=/dev/sda",
        "shutdown -h now",
        ":(){ :|:& };:",
    ],
)
def test_additional_dangerous_commands_are_denied(tmp_path: Path, command: str):
    call = ToolCall(tool_name="shell.run", args={"command": command})
    decision = PolicyEngine().decide(call, PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))
    assert decision.decision == PolicyAction.DENY
    assert decision.rule_id == "shell.dangerous_command"


def test_ordinary_shell_command_still_asks(tmp_path: Path):
    call = ToolCall(tool_name="shell.run", args={"command": "python --version"})
    decision = PolicyEngine().decide(call, PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))
    assert decision.decision == PolicyAction.ASK


@pytest.mark.parametrize("command", ["rm -fr /", "rm -rf ~", "rm -rf .", "rm -rf /var/log"])
def test_rm_variants_are_denied(tmp_path: Path, command: str):
    call = ToolCall(tool_name="shell.run", args={"command": command})
    decision = PolicyEngine().decide(call, PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))
    assert decision.decision == PolicyAction.DENY
    assert decision.rule_id == "shell.dangerous_command"


@pytest.mark.parametrize(
    "command",
    [
        "grep -r shutdown .",
        "echo graceful shutdown done",
        "jq '.key' data.json",
        "rm -rf build",
        "python -c \"print(obj.key)\"",
    ],
)
def test_common_commands_are_not_false_positives(tmp_path: Path, command: str):
    call = ToolCall(tool_name="shell.run", args={"command": command})
    decision = PolicyEngine().decide(call, PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))
    assert decision.decision == PolicyAction.ASK


def test_reading_pem_with_read_command_is_denied(tmp_path: Path):
    call = ToolCall(tool_name="shell.run", args={"command": "cat server.pem"})
    decision = PolicyEngine().decide(call, PolicyContext(workspace_root=tmp_path, allowlist=[], interactive=True))
    assert decision.decision == PolicyAction.DENY
    assert decision.rule_id == "credential.shell_read_blocked"
