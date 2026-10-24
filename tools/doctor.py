"""环境自检：这台机器上，**按你的配置**能不能把这套系统跑起来。

    python tools/doctor.py
    python tools/doctor.py --config config/user.yaml

它只回答"缺什么、缺了会怎样"，不改任何东西。
换机器、换人、或者隔了几个月回来时先跑这个。

和旧版的差别：不再要求"本地模型 + 云端 key + 某个渠道"同时存在，
而是只看你选的 provider 与 delivery 需要什么。缺的是别的渠道，只提示，不算失败。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from tools import user_config  # noqa: E402

OK, WARN, FAIL = "通过", "注意", "缺失"


def _has_secret(name: str) -> bool:
    if os.environ.get(name, "").strip():
        return True
    path = Path(os.path.expanduser("~")) / ".dsh" / ".credentials.yaml"
    if not path.exists():
        return False
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    return bool(str(((data.get("refs") or {}).get(name) or "")).strip())


def _line(name: str, state: str, detail: str = "") -> None:
    print("[{}] {}{}".format(state, name, ("　" + detail) if detail else ""))


def _module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _check_config(path: str | None) -> tuple[dict, int]:
    config = user_config.load(path)
    print("配置：", user_config.config_path(path))
    problems = user_config.validate(config)
    if problems:
        print("配置本身不合法：")
        for problem in problems:
            print("  - " + problem)
        return config, 1
    _line("配置", OK, f"provider={user_config.get(config, 'judge.provider')} "
                      f"channel={user_config.get(config, 'delivery.channel')}")
    return config, 0


def _check_provider(config: dict) -> int:
    provider = user_config.resolve_provider(config)[0] if user_config.get(
        config, "judge.provider"
    ) != "auto" else None
    problems = 0
    if provider is None:
        # auto：至少要有其中一条路
        has_local = user_config.local_model_exists(config)
        has_cloud = user_config.has_api_key()
        if not has_local and not has_cloud:
            _line("判断 provider", FAIL, "auto 但既没有本地权重，也没有 DEEPSEEK_API_KEY")
            return 1
        _line("判断 provider", OK, "auto → " + ("local" if has_local else "cloud"))
        provider = "local" if has_local else "cloud"

    if provider == "local":
        model = str(user_config.get(config, "judge.local_model"))
        if user_config.local_model_exists(config):
            _line("本地判断模型", OK, model)
        else:
            _line("本地判断模型", FAIL, "缺 " + model + "；可改用 cloud")
            problems += 1
        for module in ("torch", "transformers"):
            if _module(module):
                _line("依赖 " + module, OK)
            else:
                _line("依赖 " + module, FAIL, "pip install " + module)
                problems += 1
        try:
            import torch

            if torch.cuda.is_available():
                _line("CUDA", OK, torch.cuda.get_device_name(0)[:40])
            else:
                _line("CUDA", WARN, "不可用，本地判断会退回 CPU（很慢）")
        except Exception:  # noqa: BLE001
            _line("CUDA", WARN, "无法检测")
    else:
        if user_config.has_api_key():
            _line("云端判断凭据", OK, "DEEPSEEK_API_KEY 可用")
        else:
            _line("云端判断凭据", FAIL, "缺 DEEPSEEK_API_KEY；可改用 local")
            problems += 1
    return problems


def _check_delivery(config: dict) -> int:
    channel = str(user_config.get(config, "delivery.channel"))
    if channel in {"file", "none"}:
        _line("推送渠道", OK, channel + "（不需要凭据）")
        return 0
    # email
    problems = 0
    host = str(user_config.get(config, "delivery.email.host") or "")
    sender = str(user_config.get(config, "delivery.email.from") or "")
    to = user_config.get(config, "delivery.email.to") or []
    if host and sender and to:
        _line("邮箱配置", OK, f"{host} → {to}")
    else:
        _line("邮箱配置", FAIL, "缺 delivery.email.host / from / to")
        problems += 1
    name = str(user_config.get(config, "delivery.email.password_env") or "LAYA_EMAIL_PASSWORD")
    if _has_secret(name):
        _line("邮箱授权码", OK, name + " 可用")
    else:
        _line("邮箱授权码", WARN, "环境变量 " + name + " 未设置（可用 ~/.dsh/.credentials.yaml 的 refs）")
    return problems


def _check_demand(config: dict) -> int:
    try:
        path = user_config.resolve_demand(config)
    except FileNotFoundError as exc:
        _line("需求快照", FAIL, str(exc))
        return 1
    try:
        demand = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        _line("需求快照", FAIL, f"{path}：{exc}")
        return 1
    status = (demand.get("state") or {}).get("status")
    goals = len(demand.get("active_goals") or [])
    tasks = len([item for item in (demand.get("active_tasks") or []) if item.get("status") == "ACTIVE"])
    if status != "OK":
        _line("需求快照", FAIL, f"{path.name} status={status}")
        return 1
    _line("需求快照", OK, f"{path.name}（目标 {goals} / 任务 {tasks}）")
    return 0


def _check_other(config: dict) -> int:
    problems = 0
    for module in ("yaml", "numpy"):
        if _module(module):
            _line("依赖 " + module, OK)
        else:
            _line("依赖 " + module, FAIL, "pip install " + module)
            problems += 1

    if bool(user_config.get(config, "crawl.embed")):
        model_dir = ROOT / "models" / "paraphrase-multilingual-MiniLM-L12-v2"
        if model_dir.exists() and _module("transformers"):
            _line("向量模型", OK, "跨语言判同可用")
        else:
            _line("向量模型", WARN, "缺模型或 transformers；--embed 会降级，可关掉 crawl.embed")

    try:
        from ingest.source_index import load

        data = load(ROOT / str(user_config.get(config, "sources.index")))
        sources = data["sources"]
        need_proxy = sum(1 for source in sources if source.network == "proxy")
        _line("信息源库", OK, "{} 个来源，其中 {} 个需要代理".format(len(sources), need_proxy))
    except Exception as exc:  # noqa: BLE001
        _line("信息源库", FAIL, str(exc)[:80])
        problems += 1

    for name, path in (("已推送台账", "store/delivered.jsonl"), ("候补池", "store/backlog.jsonl")):
        target = ROOT / path
        if not target.exists():
            _line(name, WARN, "还没有文件（第一次运行会创建）")
            continue
        rows = 0
        broken = 0
        for line in target.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                json.loads(line)
                rows += 1
            except json.JSONDecodeError:
                broken += 1
        _line(name, OK if not broken else WARN,
              "{} 条{}".format(rows, "，坏行 {}".format(broken) if broken else ""))

    proxy = str(
        user_config.get(config, "crawl.proxy")
        or user_config.get(config, "proxy.url")
        or ""
    ).strip()
    if proxy:
        import socket
        from urllib.parse import urlparse

        parsed = urlparse(proxy)
        port = parsed.port or 80
        try:
            with socket.create_connection((parsed.hostname or "127.0.0.1", port), timeout=0.8):
                _line("代理", OK, proxy)
        except OSError:
            _line("代理", WARN, proxy + " 连不上；需要代理的源会记 NEEDS_PROXY")
    else:
        _line("代理", OK, "未配置（直连）")
    return problems


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import argparse

    parser = argparse.ArgumentParser(description="按配置检查这台机器缺什么")
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)

    version = sys.version_info
    _line("Python", OK if version >= (3, 10) else FAIL, "{}.{}".format(version.major, version.minor))
    problems = 0 if version >= (3, 10) else 1

    config, config_problems = _check_config(args.config)
    problems += config_problems
    if not config_problems:
        problems += _check_provider(config)
        problems += _check_delivery(config)
        problems += _check_demand(config)
        problems += _check_other(config)

    print()
    if problems:
        print("有 {} 项缺失，先补齐再跑。".format(problems))
        return 1
    print("按当前配置，环境齐了。下一步：python tools/check.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

