"""端到端验收脚本本身也要有回归测试，否则它会慢慢跑不通而没人知道。"""

from __future__ import annotations

from pathlib import Path

from tools.acceptance import run_all


def test_acceptance_passes_all_steps(tmp_path: Path) -> None:
    steps = run_all(tmp_path / "acceptance")
    names = [step.name for step in steps]
    assert len(steps) == 7, names
    failed = [step for step in steps if not step.ok]
    assert not failed, [(step.name, step.detail) for step in failed]
