"""初始设置：写需求快照、写配置、写初始文档；改配置必须先解释。"""

from __future__ import annotations

import json
from pathlib import Path

from tools import setup, user_config


def _init(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "user.yaml"
    demand = tmp_path / "demand.json"
    code = setup.main([
        "--non-interactive", "--config", str(config), "--demand-out", str(demand),
        "--goals", "AI 系统;示例第二方向", "--tasks", "示例任务", "--explore", "结构与张力",
        "--provider", "local", "--channel", "file", "--minutes", "20",
    ])
    assert code == 0
    return config, demand


def test_setup_writes_config_demand_and_user_doc(tmp_path: Path) -> None:
    config, demand_path = _init(tmp_path)
    assert config.exists() and demand_path.exists()
    data = user_config.load(config)
    assert data["judge"]["provider"] == "local"
    assert data["delivery"]["channel"] == "file"
    demand = json.loads(demand_path.read_text(encoding="utf-8-sig"))
    assert demand["state"]["status"] == "OK"
    assert len(demand["active_goals"]) == 2
    assert demand["constraints"]["daily_information_budget_minutes"] == 20
    doc = (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert "AI 系统" in doc and "能力与降级" in doc


def test_set_refuses_without_explanation(tmp_path: Path) -> None:
    config, _ = _init(tmp_path)
    code = setup.main(["--config", str(config), "--set", "judge.provider=cloud"])
    assert code == 2
    assert user_config.load(config)["judge"]["provider"] == "local"


def test_set_without_yes_previews_only(tmp_path: Path) -> None:
    config, _ = _init(tmp_path)
    code = setup.main([
        "--config", str(config), "--set", "judge.provider=cloud", "--why", "测试：没有 GPU",
    ])
    assert code == 2
    assert user_config.load(config)["judge"]["provider"] == "local"


def test_set_with_why_and_yes_appends_change_log(tmp_path: Path) -> None:
    config, _ = _init(tmp_path)
    code = setup.main([
        "--config", str(config), "--set", "judge.provider=cloud",
        "--why", "本机没有 GPU", "--yes",
    ])
    assert code == 0
    assert user_config.load(config)["judge"]["provider"] == "cloud"
    doc = (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert "本机没有 GPU" in doc
    assert "judge.provider" in doc


def test_set_rejects_unknown_key(tmp_path: Path) -> None:
    config, _ = _init(tmp_path)
    code = setup.main([
        "--config", str(config), "--set", "judge.providerr=cloud",
        "--why", "拼错了", "--yes",
    ])
    assert code == 2


def test_finish_deletes_first_setup_from_installed_copy(tmp_path: Path) -> None:
    config, _ = _init(tmp_path)
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "FIRST-SETUP.md").write_text("一次性", encoding="utf-8")
    code = setup.main(["--config", str(config), "--finish", "--skill-dir", str(skill)])
    assert code == 0
    assert not (skill / "FIRST-SETUP.md").exists()


def test_finish_refuses_before_configuration(tmp_path: Path) -> None:
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "FIRST-SETUP.md").write_text("一次性", encoding="utf-8")
    code = setup.main([
        "--config", str(tmp_path / "missing.yaml"), "--finish", "--skill-dir", str(skill),
    ])
    assert code == 2
    assert (skill / "FIRST-SETUP.md").exists()


def test_setup_email_channel_accepts_connection_fields(tmp_path: Path) -> None:
    config = tmp_path / "user.yaml"
    demand = tmp_path / "demand.json"
    code = setup.main([
        "--non-interactive", "--config", str(config), "--demand-out", str(demand),
        "--goals", "AI 系统", "--provider", "cloud", "--channel", "email",
        "--email-host", "smtp.example.com", "--email-port", "587",
        "--email-from", "me@example.com", "--email-to", "me@example.com;other@example.com",
        "--email-password-env", "MY_EMAIL_PASSWORD",
    ])
    assert code == 0
    data = user_config.load(config)
    assert data["delivery"]["channel"] == "email"
    assert data["delivery"]["email"]["host"] == "smtp.example.com"
    assert data["delivery"]["email"]["to"] == ["me@example.com", "other@example.com"]
    assert data["delivery"]["email"]["password_env"] == "MY_EMAIL_PASSWORD"
