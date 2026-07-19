from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from phycode.redaction import redact_text

# Only non-sensitive scalar configuration may be written back to phycode.toml.
ALLOWED_CONFIG_KEYS = {
    ("agent", "max_steps"),
    ("test", "command"),
    ("llm", "provider"),
    ("llm", "base_url"),
    ("llm", "model"),
}


class WorkspaceConfig(BaseModel):
    root: Path
    allowlist: list[Path] = Field(default_factory=list)


class AgentConfig(BaseModel):
    max_steps: int = 50


class TestConfig(BaseModel):
    command: str = "uv run pytest"


class LLMConfig(BaseModel):
    provider: str = "openai-compatible"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"
    vision_model: str | None = None
    timeout_seconds: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0)


class ProjectConfig(BaseModel):
    workspace: WorkspaceConfig
    agent: AgentConfig = Field(default_factory=AgentConfig)
    test: TestConfig = Field(default_factory=TestConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


class PRBenchProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: SecretStr
    base_url: str
    model: str


class PRBenchProviderConfigError(ValueError):
    pass


def validate_prbench_model_label(value: str) -> str:
    model = value.strip()
    if (
        not model
        or "://" in model
        or "\r" in model
        or "\n" in model
        or redact_text(model) != model
    ):
        raise PRBenchProviderConfigError("PRBench provider model is unsafe for summary output")
    return model


def load_prbench_provider_config(
    environment: Mapping[str, str] | None = None,
) -> PRBenchProviderConfig:
    source = os.environ if environment is None else environment
    names = ("PHYCODE_API_KEY", "PHYCODE_BASE_URL", "PHYCODE_MODEL")
    values = {name: source.get(name, "").strip() for name in names}
    if any(not value for value in values.values()):
        raise PRBenchProviderConfigError("PRBench provider environment is incomplete")
    model = validate_prbench_model_label(values["PHYCODE_MODEL"])
    return PRBenchProviderConfig(
        api_key=SecretStr(values["PHYCODE_API_KEY"]),
        base_url=values["PHYCODE_BASE_URL"],
        model=model,
    )


def load_project_config(workspace_root: Path) -> ProjectConfig:
    root = workspace_root.resolve()
    config_path = root / "phycode.toml"
    data: dict = {}
    if config_path.exists():
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    workspace_data = data.get("workspace", {})
    allowlist = [Path(item).expanduser().resolve() for item in workspace_data.get("allowlist", [])]
    return ProjectConfig(
        workspace=WorkspaceConfig(root=root, allowlist=allowlist),
        agent=AgentConfig(**data.get("agent", {})),
        test=TestConfig(**data.get("test", {})),
        llm=LLMConfig(**data.get("llm", {})),
    )


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if not isinstance(value, str):
        raise TypeError(f"unsupported TOML value type: {type(value).__name__}")
    if any(ord(ch) < 0x20 and ch != "\t" for ch in value):
        raise ValueError("string value contains control characters")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _dump_toml(data: dict[str, dict[str, Any]]) -> str:
    blocks: list[str] = []
    for section, values in data.items():
        lines = [f"[{section}]"]
        lines.extend(f"{key} = {_toml_value(val)}" for key, val in values.items())
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def write_config_value(workspace_root: Path, section: str, key: str, value: Any) -> None:
    """Write a whitelisted non-sensitive config value into phycode.toml.

    Fail-safe: renders the whole file to a string first and only writes on success,
    so a bad value or an un-serializable existing file never corrupts the config.
    Raises ValueError/TypeError on a disallowed key, bad value, or unwritable file.
    """
    if (section, key) not in ALLOWED_CONFIG_KEYS:
        allowed = ", ".join(f"{s}.{k}" for s, k in sorted(ALLOWED_CONFIG_KEYS))
        raise ValueError(f"config key {section}.{key} is not writable; allowed: {allowed}")
    if key == "max_steps":
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ValueError("max_steps must be an integer")
    root = workspace_root.resolve()
    config_path = root / "phycode.toml"
    existing = tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    data: dict[str, dict[str, Any]] = {}
    for existing_key, existing_value in existing.items():
        if not isinstance(existing_value, dict):
            raise ValueError(f"cannot rewrite phycode.toml: top-level key {existing_key!r} is not a table")
        data[existing_key] = dict(existing_value)
    data.setdefault(section, {})[key] = value
    rendered = _dump_toml(data)  # may raise on control chars / unsupported types
    config_path.write_text(rendered, encoding="utf-8")
