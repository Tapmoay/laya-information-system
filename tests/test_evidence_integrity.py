"""证据完整性：失败与推送失败都必须能在产物里查到来龙去脉。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from runtime.offline_run import run_case

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "fixtures" / "m1" / "basic"
NOW = datetime.fromisoformat("2026-10-18T05:50:00+08:00")


def test_push_failure_is_recorded_in_errors(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    outcome = run_case(
        CASE,
        out_dir=tmp_path / "run",
        run_id="run-err-01",
        now=NOW,
        push_config={"delivery": {"channel": "file", "target": str(blocker / "sub")}},
    )

    assert outcome["manifest"]["delivery_status"] == "FAILED"
    assert outcome["manifest"]["run_status"] == "PARTIAL"
    errors = json.loads((tmp_path / "run" / "errors.json").read_text(encoding="utf-8"))
    assert "PUSH" in errors
    assert "errors.json" in outcome["manifest"]["artifacts"]


def test_successful_run_has_no_errors_artifact(tmp_path: Path) -> None:
    outcome = run_case(CASE, out_dir=tmp_path / "run", run_id="run-err-02", now=NOW)
    assert not (tmp_path / "run" / "errors.json").exists()
    assert "errors.json" not in outcome["manifest"]["artifacts"]
