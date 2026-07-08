from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


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


class ProjectConfig(BaseModel):
    workspace: WorkspaceConfig
    agent: AgentConfig = Field(default_factory=AgentConfig)
    test: TestConfig = Field(default_factory=TestConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


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
