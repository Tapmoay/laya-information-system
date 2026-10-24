"""M2 在线链路回归测试（抓取 → 规范化 → 测量 → 选择 → 交付 → 推送）。

全程跑在本机 HTTP 服务器上：真实 TCP、真实状态码、真实 robots。
没有公网访问，也不假装有。
"""

from __future__ import annotations

import http.server
import json
import threading
from datetime import datetime
from pathlib import Path

import pytest

from ingest.fetch import FetchPolicy
from runtime.live_run import LiveRunConfig, run_live
from tools.validate_objects import validate

ROOT = Path(__file__).resolve().parents[1]
HTML_DIR = ROOT / "fixtures" / "html"
NOW = datetime.fromisoformat("2026-10-18T04:30:00+08:00")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HTML_DIR), **kwargs)

    def log_message(self, *args):
        return


@pytest.fixture(scope="module")
def base_url():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def live_config(base: str, urls: list[str], *, profile: str = "MODEL-PROFILE-STUB-AGENT", push_dir: Path | None = None) -> LiveRunConfig:
    demand = json.loads((ROOT / "fixtures" / "m1" / "basic" / "demand-snapshot.json").read_text(encoding="utf-8"))
    policy_config = json.loads((ROOT / "fixtures" / "m1" / "basic" / "policy-config.json").read_text(encoding="utf-8"))
    push = (
        {"delivery": {"channel": "file", "target": str(push_dir)}}
        if push_dir is not None
        else None
    )
    from delivery.push import PushConfig

    return LiveRunConfig(
        urls=urls,
        demand=demand,
        policy_config=policy_config,
        fetch_policy=FetchPolicy(allowed_hosts=("127.0.0.1",), timeout_seconds=5, retry_backoff_seconds=0.01),
        push=PushConfig.from_mapping(push) if push else PushConfig(),
        model_profile_id=profile,
    )


def test_live_run_goes_end_to_end(base_url: str, tmp_path: Path) -> None:
    out = tmp_path / "run"
    result = run_live(
        live_config(
            base_url,
            [f"{base_url}/article.html", f"{base_url}/chinese.html", f"{base_url}/missing.html"],
            push_dir=tmp_path / "outbox",
        ),
        run_id="live-0001",
        out_dir=out,
        now=NOW,
    )

    manifest = result["manifest"]
    assert validate("run-manifest", manifest) == []
    assert manifest["counts"] == {"candidates": 2, "measurements": 2, "selected": 1, "delivered": 1}
    assert manifest["run_status"] == "PARTIAL", "一个来源 404 不应让整轮成功"
    assert manifest["delivery_status"] == "SUCCESS"
    assert manifest["model_revisions"] == [
        {"model_profile_id": "MODEL-PROFILE-STUB-AGENT", "model_revision": "stub-v1"}
    ]

    stages = {stage["stage"]: stage["status"] for stage in manifest["stages"]}
    assert stages["DISCOVERY"] == "PARTIAL"
    assert stages["NORMALIZATION"] == "OK"
    assert stages["PUSH"] == "OK"


def test_only_the_matching_candidate_is_delivered(base_url: str, tmp_path: Path) -> None:
    result = run_live(
        live_config(base_url, [f"{base_url}/article.html", f"{base_url}/chinese.html"]),
        run_id="live-0002",
        out_dir=tmp_path / "run",
        now=NOW,
    )
    delivery = result["delivery"]
    assert len(delivery["items"]) == 1
    assert "article" not in delivery["items"][0]["candidate_id"]
    assert delivery["items"][0]["source_url"].endswith("/article.html")

    decisions = {item["candidate_id"]: item for item in result["decisions"]}
    discarded = [item for item in decisions.values() if item["decision"] == "DISCARD"]
    assert discarded, "另一篇应被判为无关，而不是消失"


def test_swapping_the_adapter_swaps_the_delivery(base_url: str, tmp_path: Path) -> None:
    agent = run_live(
        live_config(base_url, [f"{base_url}/article.html", f"{base_url}/chinese.html"]),
        run_id="live-0003",
        out_dir=tmp_path / "a",
        now=NOW,
    )
    visual = run_live(
        live_config(
            base_url,
            [f"{base_url}/article.html", f"{base_url}/chinese.html"],
            profile="MODEL-PROFILE-STUB-VISUAL",
        ),
        run_id="live-0004",
        out_dir=tmp_path / "b",
        now=NOW,
    )

    agent_urls = [item["source_url"] for item in agent["delivery"]["items"]]
    visual_urls = [item["source_url"] for item in visual["delivery"]["items"]]
    assert agent_urls and visual_urls
    assert set(agent_urls) != set(visual_urls), "换适配器应换掉测量结论，从而换掉交付"


def test_all_sources_failing_still_produces_an_explained_run(base_url: str, tmp_path: Path) -> None:
    result = run_live(
        live_config(base_url, [f"{base_url}/nope-1.html", f"{base_url}/nope-2.html"]),
        run_id="live-0005",
        out_dir=tmp_path / "run",
        now=NOW,
    )
    manifest = result["manifest"]
    assert manifest["counts"]["candidates"] == 0
    assert manifest["counts"]["selected"] == 0
    assert manifest["run_status"] == "PARTIAL"
    assert result["delivery"]["items"] == []
    assert result["delivery"]["empty_reason"]["code"] == "NO_ELIGIBLE_CANDIDATES"


def test_artifacts_are_written(base_url: str, tmp_path: Path) -> None:
    out = tmp_path / "run"
    result = run_live(
        live_config(base_url, [f"{base_url}/article.html"]),
        run_id="live-0006",
        out_dir=out,
        now=NOW,
    )
    listed = set(result["manifest"]["artifacts"])
    for name in (
        "fetch-results.json",
        "candidates.json",
        "duplicates.json",
        "measurements.json",
        "selection-decisions.json",
        "delivery-object.json",
        "rendered-brief.json",
        "run-manifest.json",
    ):
        assert name in listed, name
        assert (out / name).exists(), name


def test_source_outside_allowlist_aborts_before_fetching(base_url: str, tmp_path: Path) -> None:
    config = live_config(base_url, ["https://example.com/post"])
    result = run_live(config, run_id="live-0007", out_dir=tmp_path / "run", now=NOW)
    assert result["failed_stage"] == "PRECHECK"
    assert result["manifest"]["run_status"] == "FAILED"


def test_measurements_carry_language_and_method(base_url: str, tmp_path: Path) -> None:
    result = run_live(
        live_config(base_url, [f"{base_url}/article.html"]),
        run_id="live-0008",
        out_dir=tmp_path / "run",
        now=NOW,
    )
    measurement = result["measurements"][0]
    assert validate("measurement-record", measurement) == []
    assert measurement["method"]["kind"] == "RULE"
    assert measurement["uncertainties"], "未验证的桩必须在测量里留痕"
    assert measurement["language_context"]["judgment_language"] == "en"
