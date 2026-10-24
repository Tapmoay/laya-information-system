"""个人配置：让同一套系统在不同的人、不同的机器上跑起来。

为什么需要它：
系统原来把"我的目标、我的模型、我的渠道、我的代理"写死在 runbook 和代码里。
别人拿到这套系统后，第一件事不是跑，而是改代码——这就不是技能，是私有脚本。

这份配置只放**环境与个人选择**，不放判断参数：

- 判断 provider（本地 / 云端 / 自动）、模型、并发；
- 推送渠道（邮箱 / 文件）；
- 抓取预算、代理；
- 需求快照位置。

阈值、权重、通道判据不在这里。它们由 `state/params/` 版本化、
`tools/learn.py` 更新、`docs/38` 规定变更协议。把判断参数塞进用户配置，
等于让每个人都能悄悄改判据，事后无法解释。

配置文件默认 `config/user.yaml`（已在 .gitignore），
第一次使用跑 `python tools/setup.py`；示例见 `config/user.example.yaml`。
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "config" / "user.yaml"
EXAMPLE_PATH = ROOT / "config" / "user.example.yaml"
ENV_CONFIG = "LAYA_CONFIG"

SCHEMA_VERSION = 1

DEFAULTS: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "profile": {
        "name": "default",
        "language": "zh-Hans",
    },
    "demand": {
        # 空字符串 = 自动取 demand/ 下最新的日期快照
        "path": "",
    },
    "judge": {
        "provider": "local",  # local | cloud | auto
        "local_model": "models/strands-decider-2b-local",
        "device": "cuda",
        "cloud_model": "deepseek-flash",
        "workers": 8,
        "retries": 2,
        "timeout": 90,
        "thinking": "disabled",
    },
    "delivery": {
        "channel": "file",  # email | file | none
        "email": {
            "host": "",
            "port": 587,
            "username": "",
            "from": "",
            "to": [],
            "password_env": "LAYA_EMAIL_PASSWORD",
            "starttls": True,
            "subject_prefix": "每日读物",
        },
        "file": {
            "outbox": "runs/outbox",
        },
    },
    "crawl": {
        "max_total": 300,
        "max_seconds": 900,
        "embed": True,
        # null = 直连；字符串 = 代理地址
        "proxy": None,
    },
    "proxy": {
        # 需要代理时填，例如 http://127.0.0.1:7897；留空 = 不自动开代理
        "url": "",
        "port": 7897,
        # 无人值守时若端口没开，尝试启动这个程序；留空 = 不启动
        "autostart_exe": "",
    },
    "sources": {
        "index": "config/sources-index.yaml",
        "only": [],
    },
}

FORBIDDEN_KEYS = {"password", "secret", "token", "api_key", "apikey"}


def config_path(path: str | Path | None = None) -> Path:
    """优先级：显式参数 > LAYA_CONFIG 环境变量 > config/user.yaml。相对路径按仓库根解析。"""
    if path:
        target = Path(path)
    else:
        env = os.environ.get(ENV_CONFIG, "").strip()
        target = Path(env) if env else DEFAULT_PATH
    return target if target.is_absolute() else ROOT / target


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load(path: str | Path | None = None) -> dict[str, Any]:
    target = config_path(path)
    if not target.exists():
        return copy.deepcopy(DEFAULTS)
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{target} 不是 YAML 对象")
    return deep_merge(copy.deepcopy(DEFAULTS), data)


def save(data: dict[str, Any], path: str | Path | None = None) -> Path:
    target = config_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return target


def get(data: dict[str, Any], dotted: str, default: Any = None) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set_value(data: dict[str, Any], dotted: str, value: Any) -> tuple[Any, Any]:
    """返回 (旧值, 新值)。路径不存在时抛 KeyError，避免拼写错误悄悄生效。"""
    parts = dotted.split(".")
    node: Any = data
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"配置项不存在：{dotted}")
        node = node[part]
    if not isinstance(node, dict) or parts[-1] not in node:
        raise KeyError(f"配置项不存在：{dotted}")
    old = node[parts[-1]]
    node[parts[-1]] = value
    return old, value


def validate(data: dict[str, Any]) -> list[str]:
    """只报错配置本身；不检查外部资源（那是 doctor 的事）。"""
    problems: list[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version 必须是 {SCHEMA_VERSION}")

    provider = get(data, "judge.provider")
    if provider not in {"local", "cloud", "auto"}:
        problems.append("judge.provider 只能是 local / cloud / auto")
    workers = get(data, "judge.workers")
    if not isinstance(workers, int) or not 1 <= workers <= 32:
        problems.append("judge.workers 必须在 1..32")
    retries = get(data, "judge.retries")
    if not isinstance(retries, int) or not 0 <= retries <= 5:
        problems.append("judge.retries 必须在 0..5")
    timeout = get(data, "judge.timeout")
    if not isinstance(timeout, (int, float)) or not 5 <= float(timeout) <= 600:
        problems.append("judge.timeout 必须在 5..600 秒")
    if get(data, "judge.device") not in {"cuda", "cpu"}:
        problems.append("judge.device 只能是 cuda / cpu")

    channel = get(data, "delivery.channel")
    if channel not in {"email", "file", "none"}:
        problems.append("delivery.channel 只能是 email / file / none")
    if channel == "email":
        email = get(data, "delivery.email") or {}
        for key in ("host", "from"):
            if not str(email.get(key) or "").strip():
                problems.append(f"email 渠道缺少 delivery.email.{key}")
        to = email.get("to")
        if not to or (isinstance(to, list) and not any(str(item).strip() for item in to)):
            problems.append("email 渠道缺少收件人 delivery.email.to")
    if channel == "file" and not str(get(data, "delivery.file.outbox") or "").strip():
        problems.append("file 渠道缺少 delivery.file.outbox")

    max_total = get(data, "crawl.max_total")
    if not isinstance(max_total, int) or max_total < 0:
        problems.append("crawl.max_total 必须是非负整数")
    max_seconds = get(data, "crawl.max_seconds")
    if not isinstance(max_seconds, (int, float)) or max_seconds < 0:
        problems.append("crawl.max_seconds 必须是非负数")
    only = get(data, "sources.only")
    if not isinstance(only, list) or any(not isinstance(item, str) for item in only):
        problems.append("sources.only 必须是字符串列表")
    proxy_port = get(data, "proxy.port")
    if not isinstance(proxy_port, int) or not 1 <= proxy_port <= 65535:
        problems.append("proxy.port 必须在 1..65535")

    # 凭据只允许"指向哪里"，不允许"写在这里"
    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = str(key).lower()
                if lowered in FORBIDDEN_KEYS and value not in (None, ""):
                    problems.append(f"{path}.{key}：不要把凭据写进配置，用环境变量")
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(data, "config")
    return problems


def has_api_key() -> bool:
    if os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return True
    path = Path(os.path.expanduser("~")) / ".dsh" / ".credentials.yaml"
    if not path.exists():
        return False
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    return bool(str(((data.get("refs") or {}).get("DEEPSEEK_API_KEY") or "")).strip())


def local_model_exists(data: dict[str, Any]) -> bool:
    return (ROOT / str(get(data, "judge.local_model"))).exists()


def resolve_provider(data: dict[str, Any], cli: str | None = None) -> tuple[str, str]:
    """返回 (provider, 依据)。auto 只按"有没有本地权重"决定，不猜速度。"""
    if cli in {"local", "cloud"}:
        return cli, "命令行 --provider"
    configured = str(get(data, "judge.provider", "local"))
    if configured in {"local", "cloud"}:
        return configured, "config/user.yaml"
    if configured == "auto":
        if local_model_exists(data):
            return "local", "auto：本机有本地权重"
        if has_api_key():
            return "cloud", "auto：没有本地权重，回退云端"
        raise SystemExit(
            "auto 无法决定：既找不到 judge.local_model，也没有 DEEPSEEK_API_KEY。"
            "先跑 python tools/setup.py 选一个 provider。"
        )
    raise SystemExit(f"未知 judge.provider：{configured}")


def resolve_channel(data: dict[str, Any], cli: str | None = None) -> str:
    if cli:
        return cli
    channel = str(get(data, "delivery.channel", "file"))
    if channel not in {"email", "file", "none"}:
        raise SystemExit(f"未知 delivery.channel：{channel}")
    return channel


def resolve_demand(data: dict[str, Any], requested: str | None = None) -> Path:
    """requested 可以是具体路径或 latest；空则读配置，再空则取最新日期快照。"""
    candidate = requested or str(get(data, "demand.path") or "").strip() or "latest"
    if candidate != "latest":
        path = Path(candidate)
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            raise FileNotFoundError(f"需求快照不存在：{path}")
        return path
    files = sorted((ROOT / "demand").glob("demand-2*.json"))
    files = [path for path in files if path.is_file()]
    if not files:
        raise FileNotFoundError(
            "demand/ 下没有日期快照。先跑 python tools/setup.py 生成 demand-<日期>.json。"
        )
    # 文件名带 ISO 日期，最后修改时间处理同一天多次生成
    return max(files, key=lambda path: (path.name, path.stat().st_mtime))


def provider_summary(data: dict[str, Any], provider: str) -> str:
    if provider == "local":
        return "本地 decider（{}，{}）".format(
            get(data, "judge.local_model"), get(data, "judge.device")
        )
    return "云端（{}，{} 并发）".format(
        get(data, "judge.cloud_model"), get(data, "judge.workers")
    )

