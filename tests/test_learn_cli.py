"""调参 CLI：复核表 → 反馈事件 → 版本 → 回滚。"""

from __future__ import annotations

import csv
import json

import tools.learn as learn


def _scores(overrides: dict[str, float]) -> dict:
    base = {
        "primary": 0.4, "secondary": 0.3, "exploration": 0.2, "task": 0.2,
        "value_task": 0.5, "value_capability": 0.5, "value_decision": 0.5,
        "value_exploration": 0.5, "value_environment": 0.5,
        "quality_testable": 0.6, "quality_increment": 0.6, "recruit": 0.1,
    }
    merged = {**base, **overrides}
    payload = {name: {"noul": value} for name, value in merged.items()}
    payload["form"] = {"probabilities": {"TECHNICAL": 0.9, "PRODUCT": 0.05, "EVENT": 0.02,
                                          "RECRUIT": 0.02, "MARKETING": 0.01}}
    return payload


def _write_fixture(tmp_path, labels: list[str], selected: list[str]):
    rows = []
    for index, (label, was_selected) in enumerate(zip(labels, selected), 1):
        rows.append(
            {
                "item_id": f"item-{index}",
                "profile_id": "ai",
                "scores": _scores({"primary": 0.5 + index * 0.01}),
                "provenance": {"title": f"标题 {index}", "domain": "design"},
            }
        )
    scores_path = tmp_path / "scores.jsonl"
    scores_path.write_text("".join(json.dumps(row, ensure_ascii=False) + chr(10) for row in rows), encoding="utf-8")

    csv_path = tmp_path / "review.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["item_id", "domain", "score", "selected", "label", "title"])
        writer.writeheader()
        for index, (label, was_selected) in enumerate(zip(labels, selected), 1):
            writer.writerow(
                {"item_id": f"item-{index}", "domain": "design", "score": "0.5",
                 "selected": was_selected, "label": label, "title": f"标题 {index}"}
            )
    return scores_path, csv_path


def test_from_csv_produces_events_and_commits(tmp_path):
    scores_path, csv_path = _write_fixture(tmp_path, ["0", "0", "0", "1"], ["1", "1", "1", "0"])
    code = learn.main([
        "--user", "u1", "--base", str(tmp_path / "params"),
        "--scores", str(scores_path), "--from-csv", str(csv_path), "--apply",
    ])
    assert code == 0
    current = json.loads((tmp_path / "params" / "u1" / "current.json").read_text(encoding="utf-8"))
    assert current["version"] == 1
    assert current["source"] == "ADAPTED"
    total = sum(current["parameters"][name] for name in ("direction", "value", "quality", "exploration"))
    assert abs(total - 1.0) < 1e-5


def test_agreement_alone_does_not_change_parameters(tmp_path):
    scores_path, csv_path = _write_fixture(tmp_path, ["1", "1", "1", "1"], ["1", "1", "1", "1"])
    learn.main([
        "--user", "u2", "--base", str(tmp_path / "params"),
        "--scores", str(scores_path), "--from-csv", str(csv_path), "--apply",
    ])
    assert not (tmp_path / "params" / "u2" / "current.json").exists()


def test_rollback_restores_prior(tmp_path):
    scores_path, csv_path = _write_fixture(tmp_path, ["0", "0", "0", "1"], ["1", "1", "1", "0"])
    base_args = ["--user", "u3", "--base", str(tmp_path / "params"), "--scores", str(scores_path)]
    learn.main([*base_args, "--from-csv", str(csv_path), "--apply"])
    before = json.loads((tmp_path / "params" / "u3" / "current.json").read_text(encoding="utf-8"))
    assert before["version"] == 1
    learn.main(["--user", "u3", "--base", str(tmp_path / "params"), "--rollback", "0"])
    after = json.loads((tmp_path / "params" / "u3" / "current.json").read_text(encoding="utf-8"))
    assert after["version"] == 2
    assert after["source"] == "ROLLBACK"
    from judgment.params import PRIOR

    assert after["parameters"]["threshold"] == PRIOR["threshold"]


def test_unclassified_statement_never_changes_parameters(tmp_path):
    code = learn.main([
        "--user", "u4", "--base", str(tmp_path / "params"),
        "--say", "我说不清，就是感觉不太对",
    ])
    assert code == 0
    assert not (tmp_path / "params" / "u4" / "current.json").exists()


def test_agent_overrides_become_feedback(tmp_path):
    scores_path, _ = _write_fixture(tmp_path, ["1", "1", "1", "1"], ["1", "1", "1", "0"])
    overrides = tmp_path / "overrides.json"
    overrides.write_text(
        json.dumps(
            [
                {"item_id": "item-1", "include": False, "was_selected": True, "reason": "广告"},
                {"item_id": "item-2", "include": False, "was_selected": True, "reason": "广告"},
                {"item_id": "item-4", "include": True, "was_selected": False, "reason": "有用"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    learn.main([
        "--user", "u5", "--base", str(tmp_path / "params"),
        "--scores", str(scores_path), "--from-overrides", str(overrides), "--apply",
    ])
    current = json.loads((tmp_path / "params" / "u5" / "current.json").read_text(encoding="utf-8"))
    assert current["version"] == 1
    assert current["note"].startswith("根据 3 条反馈")


def test_actions_map_to_labels_and_ignore_unknown(tmp_path):
    scores_path, _ = _write_fixture(tmp_path, ["1", "1", "1", "1"], ["1", "1", "1", "0"])
    actions = tmp_path / "actions.jsonl"
    rows = [
        {"item_id": "item-1", "action": "SKIPPED", "delivered": True},
        {"item_id": "item-2", "action": "DISMISSED", "delivered": True},
        {"item_id": "item-4", "action": "SAVED", "delivered": False},
        {"item_id": "item-3", "action": "READ_TIME", "delivered": True},
    ]
    actions.write_text("".join(json.dumps(row) + chr(10) for row in rows), encoding="utf-8")
    learn.main([
        "--user", "u6", "--base", str(tmp_path / "params"),
        "--scores", str(scores_path), "--from-actions", str(actions), "--apply",
    ])
    current = json.loads((tmp_path / "params" / "u6" / "current.json").read_text(encoding="utf-8"))
    assert current["version"] == 1
    assert current["note"].startswith("根据 3 条反馈")


def test_csv_alone_exports_labels_without_commit(tmp_path):
    scores_path, csv_path = _write_fixture(tmp_path, ["0", "1", "1", "0"], ["1", "0", "1", "0"])
    export = tmp_path / "user-labels.jsonl"
    learn.main([
        "--user", "u7", "--base", str(tmp_path / "params"),
        "--scores", str(scores_path), "--from-csv", str(csv_path),
        "--export-labels", str(export),
    ])
    rows = [json.loads(line) for line in export.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 4
    assert {row["labeler"] for row in rows} == {"USER"}
    assert {row["profile_id"] for row in rows} == {"ai"}
    # 只导出标签、不加 --apply：参数不能被动
    assert not (tmp_path / "params" / "u7" / "current.json").exists()
