from pathlib import Path

import pytest

from phycode.config import load_project_config, write_config_value


def test_missing_project_config_uses_workspace_root(tmp_path: Path):
    config = load_project_config(tmp_path)
    assert config.workspace.root == tmp_path
    assert config.agent.max_steps == 50


def test_project_config_reads_test_command(tmp_path: Path):
    (tmp_path / "phycode.toml").write_text('[test]\ncommand = "uv run pytest"\n', encoding="utf-8")
    config = load_project_config(tmp_path)
    assert config.test.command == "uv run pytest"


def test_write_config_value_sets_llm_url_and_model(tmp_path: Path):
    write_config_value(tmp_path, "llm", "base_url", "https://real.example/v1")
    write_config_value(tmp_path, "llm", "model", "real-model")
    config = load_project_config(tmp_path)
    assert config.llm.base_url == "https://real.example/v1"
    assert config.llm.model == "real-model"


def test_write_config_value_preserves_other_sections(tmp_path: Path):
    (tmp_path / "phycode.toml").write_text('[test]\ncommand = "pytest"\n', encoding="utf-8")
    write_config_value(tmp_path, "llm", "base_url", "https://x/v1")
    config = load_project_config(tmp_path)
    assert config.test.command == "pytest"
    assert config.llm.base_url == "https://x/v1"


def test_write_config_value_coerces_max_steps_to_int(tmp_path: Path):
    write_config_value(tmp_path, "agent", "max_steps", "12")
    assert load_project_config(tmp_path).agent.max_steps == 12


def test_write_config_value_rejects_unknown_key(tmp_path: Path):
    with pytest.raises(ValueError):
        write_config_value(tmp_path, "secrets", "api_key", "x")


def test_write_config_value_rejects_control_characters(tmp_path: Path):
    with pytest.raises(ValueError):
        write_config_value(tmp_path, "test", "command", "a\nb")
