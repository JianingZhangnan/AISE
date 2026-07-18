from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from phycode.redaction import redact_text


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
