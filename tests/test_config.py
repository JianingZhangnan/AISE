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


def test_prbench_provider_config_reads_only_explicit_environment() -> None:
    from phycode.config import load_prbench_provider_config

    config = load_prbench_provider_config(
        {
            "PHYCODE_API_KEY": "test-provider-secret",
            "PHYCODE_BASE_URL": "https://provider.example/v1",
            "PHYCODE_MODEL": "test-model",
            "OPENAI_API_KEY": "must-not-be-used",
        }
    )

    assert config.api_key.get_secret_value() == "test-provider-secret"
    assert config.base_url == "https://provider.example/v1"
    assert config.model == "test-model"
    assert "test-provider-secret" not in repr(config)


@pytest.mark.parametrize(
    "missing",
    ["PHYCODE_API_KEY", "PHYCODE_BASE_URL", "PHYCODE_MODEL"],
)
def test_prbench_provider_config_rejects_missing_or_blank_values(missing: str) -> None:
    from phycode.config import PRBenchProviderConfigError, load_prbench_provider_config

    environment = {
        "PHYCODE_API_KEY": "test-provider-secret",
        "PHYCODE_BASE_URL": "https://provider.example/v1",
        "PHYCODE_MODEL": "test-model",
    }
    environment[missing] = "  "

    with pytest.raises(PRBenchProviderConfigError, match="incomplete"):
        load_prbench_provider_config(environment)


@pytest.mark.parametrize("unsafe_model", ["https://provider.example/v1", "safe\ninjected"])
def test_prbench_provider_config_rejects_unsafe_model_for_summary_output(
    unsafe_model: str,
) -> None:
    from phycode.config import PRBenchProviderConfigError, load_prbench_provider_config

    with pytest.raises(PRBenchProviderConfigError, match="model"):
        load_prbench_provider_config(
            {
                "PHYCODE_API_KEY": "test-provider-secret",
                "PHYCODE_BASE_URL": "https://provider.example/v1",
                "PHYCODE_MODEL": unsafe_model,
            }
        )


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
