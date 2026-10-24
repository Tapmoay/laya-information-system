"""Agent 层的文档必须和代码对得上，否则下一个会话会照着过期的说明做。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent" / "AGENT.md"
SKILL = ROOT / "agent" / "skills" / "final-selection" / "SKILL.md"
DAILY_SKILL = ROOT / "agent" / "skills" / "personal-information-environment" / "SKILL.md"
CLOUD_REF = DAILY_SKILL.parent / "references" / "cloud-judgment.md"
OPS_REF = DAILY_SKILL.parent / "references" / "operations.md"
ONBOARDING_REF = DAILY_SKILL.parent / "references" / "onboarding.md"
ADAPTATION_REF = DAILY_SKILL.parent / "references" / "adaptation.md"
FIRST_SETUP = DAILY_SKILL.parent / "FIRST-SETUP.md"
PACKAGE_DOC = DAILY_SKILL.parent / "PACKAGE.md"


def test_agent_role_doc_exists_and_states_hard_rules():
    text = AGENT.read_text(encoding="utf-8")
    assert "角色边界" in text
    assert "必须有非空理由" in text or "都必须有非空理由" in text
    assert "≤5" in text and "≤2" in text
    assert "tools/agent_select.py" in text


def test_skill_doc_points_at_real_tools():
    text = SKILL.read_text(encoding="utf-8")
    assert "tools/agent_select.py --build" in text
    assert "tools/agent_select.py --apply" in text
    assert (ROOT / "tools" / "agent_select.py").exists()
    assert (ROOT / "tools" / "learn.py").exists()


def test_skill_has_checklist_and_antipatterns():
    text = SKILL.read_text(encoding="utf-8")
    assert "检查清单" in text
    assert "反模式" in text


def test_daily_skill_has_frontmatter_and_points_at_real_commands():
    """技能要能被发现、被加载，并且指向真实存在的命令与产物。"""
    text = DAILY_SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\nname: personal-information-environment\n")
    frontmatter = text.split("---", 2)[1]
    assert "description:" in frontmatter
    for token in (
        "tools/score_items.py",
        "--provider cloud",
        "failures.jsonl",
        "unscorable.json",
        "tools/compare_judges.py",
        "SUMMARY:",
        "FIRST-SETUP.md",
        "adaptation.md",
    ):
        assert token in text
    assert CLOUD_REF.exists()
    assert OPS_REF.exists()
    assert ONBOARDING_REF.exists()
    assert ADAPTATION_REF.exists()
    assert FIRST_SETUP.exists()
    assert PACKAGE_DOC.exists()
    assert (ROOT / "config" / "user.example.yaml").exists()
    assert (ROOT / "tools" / "setup.py").exists()
    assert (ROOT / "tools" / "push.py").exists()
    assert (ROOT / "tools" / "package_skill.py").exists()
    assert (ROOT / "docs" / "38-daily-workflow.md").exists()
    assert (ROOT / "docs" / "39-judge-comparison.md").exists()


def test_daily_skill_is_portable_without_machine_absolute_paths():
    """技能要能发给别人：不能写死某台机器的仓库绝对路径。"""
    text = DAILY_SKILL.read_text(encoding="utf-8")
    assert "D:\\AProjects" not in text
    assert "C:\\Users" not in text
    assert "仓库根" in text
    assert "tools/setup.py" in text
    assert "config/user.yaml" in text


def test_daily_skill_separates_unknown_from_error_and_never_fakes_zero():
    text = DAILY_SKILL.read_text(encoding="utf-8")
    assert "不可判" in text
    assert "0 分" in text
    assert "测量" in text and "终选" in text
    # 技能必须写明"换 provider 先对照"，否则下一次会直接切
    assert "compare_judges" in text
    assert "阈值" in text
