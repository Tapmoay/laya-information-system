"""个人配置：默认值、合法性、provider 选择、需求快照解析。"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tools import user_config


def test_defaults_are_valid() -> None:
    assert user_config.validate(copy.deepcopy(user_config.DEFAULTS)) == []


def test_secrets_must_not_be_written_into_config() -> None:
    data = copy.deepcopy(user_config.DEFAULTS)
    data["delivery"]["email"]["password"] = "hunter2"
    problems = user_config.validate(data)
    assert any("凭据" in problem for problem in problems)


def test_unknown_key_is_rejected_instead_of_silently_added() -> None:
    data = copy.deepcopy(user_config.DEFAULTS)
    with pytest.raises(KeyError):
        user_config.set_value(data, "judge.providerr", "cloud")


def test_provider_from_config_and_cli_override() -> None:
    data = copy.deepcopy(user_config.DEFAULTS)
    data["judge"]["provider"] = "local"
    assert user_config.resolve_provider(data)[0] == "local"
    assert user_config.resolve_provider(data, "cloud")[0] == "cloud"


def test_auto_falls_back_to_cloud_without_local_weights(monkeypatch) -> None:
    data = copy.deepcopy(user_config.DEFAULTS)
    data["judge"]["provider"] = "auto"
    monkeypatch.setattr(user_config, "local_model_exists", lambda _data: False)
    monkeypatch.setattr(user_config, "has_api_key", lambda: True)
    assert user_config.resolve_provider(data)[0] == "cloud"


def test_auto_without_any_judge_is_a_clear_error(monkeypatch) -> None:
    data = copy.deepcopy(user_config.DEFAULTS)
    data["judge"]["provider"] = "auto"
    monkeypatch.setattr(user_config, "local_model_exists", lambda _data: False)
    monkeypatch.setattr(user_config, "has_api_key", lambda: False)
    with pytest.raises(SystemExit) as caught:
        user_config.resolve_provider(data)
    assert "provider" in str(caught.value)


def test_explicit_demand_path_is_read(tmp_path: Path) -> None:
    path = tmp_path / "demand-test.json"
    path.write_text("{}", encoding="utf-8")
    assert user_config.resolve_demand(user_config.DEFAULTS, str(path)) == path


def test_missing_demand_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        user_config.resolve_demand(user_config.DEFAULTS, str(tmp_path / "nope.json"))
