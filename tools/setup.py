"""首次使用 / 换一个人 / 换一台机器时的初始设置。

    python tools/setup.py                 # 交互式：问需求、判断方式、推送方式
    python tools/setup.py --show          # 看当前配置与需求快照
    python tools/setup.py --check         # 配置 + 环境能不能跑
    python tools/setup.py --set judge.provider=cloud --why "本机没有 GPU" --yes

它做三件事，缺一不可：

1. **写需求快照** `demand/demand-<日期>.json`：长期目标、当前任务、跨领域探索。
   13 问的前四问直接从这份快照取，没有它系统不知道"对你什么算有用"。
2. **写个人配置** `config/user.yaml`：判断 provider、推送渠道、抓取预算、代理。
3. **写初始文档** `config/USER.md`：给人看的一份说明——选了什么、为什么、
   换之前要注意什么。别人拿到这套系统时，先读这份，而不是先读代码。

一条硬规则：**改配置之前必须先解释。** `--set` 不带 `--why` 会直接拒绝；
带 `--yes` 才会落盘，并把"改了什么、为什么"写进 `config/USER.md` 的变更记录。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from tools import user_config  # noqa: E402

DEFAULT_MINUTES = 30
INSTALLED_SKILL = Path.home() / ".agents" / "skills" / "personal-information-environment"


def _remove_first_setup(skill_dir: Path) -> str:
    """从安装副本删除一次性文档；源仓库的模板不删。"""
    target = skill_dir / "FIRST-SETUP.md"
    if not target.exists():
        return "missing"
    # junction 会 resolve 到仓库，按"源"处理；真正的安装副本 resolve 在仓库之外。
    try:
        target.resolve().relative_to(ROOT.resolve())
    except ValueError:
        target.unlink()
        return "deleted"
    return "source"


def finish_setup(args: argparse.Namespace) -> int:
    config_file = user_config.config_path(args.config)
    if not config_file.exists():
        print("还没有配置：先跑 python tools/setup.py", file=sys.stderr)
        return 2
    data = user_config.load(args.config)
    try:
        demand_path = user_config.resolve_demand(data)
    except FileNotFoundError as exc:
        print("需求快照缺失：{}".format(exc), file=sys.stderr)
        return 2
    doc_path = _user_doc(args.config)
    if not doc_path.exists():
        print("初始文档缺失：{}；先跑 python tools/setup.py".format(doc_path), file=sys.stderr)
        return 2

    candidates: list[Path] = []
    if args.skill_dir:
        candidates.append(Path(args.skill_dir))
    candidates += [INSTALLED_SKILL, ROOT / "agent" / "skills" / "personal-information-environment"]
    seen: set[Path] = set()
    for candidate in candidates:
        key = candidate.resolve()
        if key in seen:
            continue
        seen.add(key)
        result = _remove_first_setup(candidate)
        if result == "deleted":
            print("已删除一次性文档：{}".format(candidate / "FIRST-SETUP.md"))
            print("配置完成。后续只读 SKILL.md 与 references/。")
            return 0
        if result == "source":
            print("跳过源仓库模板：{}（打包还要用）".format(candidate / "FIRST-SETUP.md"))
    print(
        "没有找到可删除的 FIRST-SETUP.md。\n"
        "如果读的是安装副本，用 --skill-dir 指向它；如果已经删过，可以忽略。"
    )
    return 0


def _user_doc(config_file: str | Path | None) -> Path:
    """初始文档放在配置文件旁边：默认 config/user.yaml → config/USER.md。"""
    return user_config.config_path(config_file).parent / "USER.md"


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _split(text: str) -> list[str]:
    parts = []
    for chunk in (text or "").replace("；", ";").replace("\n", ";").split(";"):
        item = chunk.strip()
        if item:
            parts.append(item)
    return parts


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return answer or default


def _ask_choice(prompt: str, choices: list[str], default: str) -> str:
    while True:
        answer = _ask(prompt + "（" + "/".join(choices) + "）", default).strip().lower()
        if answer in choices:
            return answer
        print("  只能是：" + " / ".join(choices))


def _demand_path(explicit: str | None, *, force: bool) -> Path:
    if explicit:
        path = Path(explicit)
        return path if path.is_absolute() else ROOT / path
    stamp = datetime.now().strftime("%Y-%m-%d")
    path = ROOT / "demand" / f"demand-{stamp}.json"
    if path.exists() and not force:
        path = ROOT / "demand" / f"demand-{stamp}-{datetime.now().strftime('%H%M%S')}.json"
    return path


def build_demand(
    goals: list[str],
    tasks: list[str],
    explore: str,
    *,
    minutes: int,
    provider: str,
    channel: str,
) -> dict[str, Any]:
    if not goals:
        raise SystemExit("至少需要一个长期目标：13 问的前四问靠它定位方向")
    now = datetime.now().astimezone()
    return {
        "demand_snapshot_id": "demand-" + now.strftime("%Y-%m-%d"),
        "created_at": now.isoformat(timespec="seconds"),
        "policy_bundle_ref": "information-policy.md",
        "active_goals": [
            {
                "goal_id": f"goal-{index}",
                "statement": statement,
                "state": "ACTIVE_EXPLICIT",
                "source_ref": "config/USER.md",
            }
            for index, statement in enumerate(goals, 1)
        ],
        "inferred_goals": [],
        "active_tasks": [
            {
                "task_id": f"task-{index}",
                "statement": statement,
                "status": "ACTIVE",
                "source_ref": "config/USER.md",
            }
            for index, statement in enumerate(tasks, 1)
        ],
        "capability_gaps": [],
        "constraints": {
            "daily_information_budget_minutes": minutes,
            "energy_state": "NORMAL",
        },
        "exploration_intent": {
            "enabled": bool(explore.strip()),
            "max_items": 2,
            "domains_hint": [explore.strip()] if explore.strip() else [],
            "notes": "跨领域不是固定学科清单，而是同一抽象概念在别处的化身；关联度越高越优先",
        },
        "assumptions": [
            "由 tools/setup.py 生成",
            f"judge.provider={provider}",
            f"delivery.channel={channel}",
        ],
        "unknowns": [],
        "state": {"status": "OK"},
        "contract_version": "1.0",
    }


def write_demand(path: Path, demand: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(demand, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _channel_help(channel: str, data: dict[str, Any]) -> str:
    if channel == "email":
        email = user_config.get(data, "delivery.email") or {}
        host = email.get("host") or "（未配置）"
        to = email.get("to") or "（未配置）"
        return f"SMTP {host} → {to}；授权码只从环境变量 {email.get('password_env')} 读。"
    if channel == "file":
        return "只把 final.md 落到 runs/final/<日期>/，不发送，也不写已推送台账。"
    return "不推送；产物仍会生成。"


def _provider_help(provider: str) -> str:
    if provider == "local":
        return "本机 GPU + Strands Decider：免费、离线、约 5 分钟/200 条；需要本地权重。"
    if provider == "cloud":
        return "DeepSeek API：约 41 秒/200 条、按 token 计费；需要 DEEPSEEK_API_KEY。"
    return "auto：有本地权重用 local，没有权重但有 key 用 cloud；两者都没有会拒绝启动。"


def _ask_email(data: dict[str, Any]) -> dict[str, Any]:
    email = user_config.get(data, "delivery.email") or {}
    host = _ask("SMTP 主机（例如 smtp.example.com）", str(email.get("host") or ""))
    port_raw = _ask("SMTP 端口", str(email.get("port") or 587))
    try:
        port = int(port_raw)
    except ValueError:
        port = 587
    username = _ask("SMTP 用户名（可空）", str(email.get("username") or ""))
    sender = _ask("发件人地址", str(email.get("from") or username))
    raw_to = _ask("收件人（逗号或分号分隔）", ",".join(email.get("to") or []))
    recipients = [item.strip() for item in raw_to.replace("；", ",").replace(";", ",").split(",") if item.strip()]
    password_env = _ask("授权码环境变量名", str(email.get("password_env") or "LAYA_EMAIL_PASSWORD"))
    return {
        "host": host,
        "port": port,
        "username": username,
        "from": sender,
        "to": recipients,
        "password_env": password_env,
    }


def render_user_doc(
    data: dict[str, Any],
    demand: dict[str, Any],
    demand_path: Path,
    *,
    changes: list[dict[str, str]] | None = None,
) -> str:
    goals = [item["statement"] for item in demand.get("active_goals") or []]
    tasks = [
        item["statement"]
        for item in (demand.get("active_tasks") or [])
        if item.get("status") == "ACTIVE"
    ]
    explore = (demand.get("exploration_intent") or {}).get("domains_hint") or []
    provider = str(user_config.get(data, "judge.provider"))
    channel = str(user_config.get(data, "delivery.channel"))
    minutes = (demand.get("constraints") or {}).get(
        "daily_information_budget_minutes", DEFAULT_MINUTES
    )
    lines = [
        "# 我的信息环境设置",
        "",
        f"> 生成于 {datetime.now().astimezone().isoformat(timespec='seconds')}。",
        "> 这是给人看的一份初始文档：选了什么、为什么、改之前要注意什么。",
        "> 机器可读的配置是 `config/user.yaml`；需求快照是 `" + _rel(demand_path) + "`。",
        "",
        "## 1. 我要什么",
        "",
        "**长期目标**",
        "",
    ]
    lines += [f"- {item}" for item in goals] or ["- （未填写）"]
    lines += ["", "**当前任务**", ""]
    lines += [f"- {item}" for item in tasks] or ["- （当前没有明确任务）"]
    lines += ["", "**跨领域探索**", ""]
    lines += [f"- {item}" for item in explore] or ["- （不开探索位；C 通道会少一条来源）"]
    lines += [
        "",
        f"**每天愿意花的时间**：{minutes} 分钟；推送上限仍由政策决定（核心 ≤5 + 探索 ≤2）。",
        "",
        "## 2. 判断方式",
        "",
        f"- provider：`{provider}`",
        f"- 说明：{_provider_help(provider)}",
        "- 换 provider **不是换一个开关**：排序与候选池会变。先跑：",
        "  `python tools/compare_judges.py --scores-a <旧> --scores-b <新> --items <items.json>`",
        "- 阈值/权重不在这份配置里；它们在 `state/params/` 里版本化，改要走 `tools/learn.py`。",
        "",
        "## 3. 推送方式",
        "",
        f"- channel：`{channel}`",
        f"- 说明：{_channel_help(channel, data)}",
        "- 推送失败只补发，不重复占 7 天窗口；台账在 `store/delivered.jsonl`。",
        "",
        "## 4. 改配置的规矩",
        "",
        "改任何一项之前，先解释三件事：**改什么、为什么改、会影响什么**。",
        "",
        "```powershell",
        'python tools/setup.py --set judge.provider=cloud --why "本机没有 GPU，云端更快" --yes',
        "```",
        "",
        "- 不带 `--why` 会被拒绝；",
        "- 不带 `--yes` 只打印影响，不落盘；",
        "- 每次改动都会追加到本文档的变更记录。",
        "",
        "## 5. 能力与降级",
        "",
        "| 缺什么 | 还能做什么 | 会缺什么 |",
        "|---|---|---|",
        "| 没有 GPU / 本地权重 | 用 `judge.provider=cloud` | 按 token 计费；需要网络与 key |",
        "| 没有 API key | 用 `judge.provider=local` | 首次加载慢；占显存 |",
        "| 没有代理 | 直连源照常抓 | 三个源记 `NEEDS_PROXY`，不是失败 |",
        "| 没有聊天渠道 | 用 `delivery.channel=email` 或 `file` | 少了即时推送；产物仍在 |",
        "| 没有 SMTP | 用 `delivery.channel=file` | 不发送；需要自己看 final.md |",
        "",
        "## 6. 变更记录",
        "",
        "| 日期 | 项 | 旧 → 新 | 为什么 |",
        "|---|---|---|---|",
    ]
    for row in changes or []:
        lines.append(
            "| {at} | {key} | {old} → {new} | {why} |".format(
                at=row.get("at", ""),
                key=row.get("key", ""),
                old=row.get("old", ""),
                new=row.get("new", ""),
                why=row.get("why", ""),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _load_changes(path: Path) -> list[dict[str, str]]:
    """从 USER.md 的变更记录表里恢复历史，避免每次 --set 覆盖掉旧记录。"""
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    in_table = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("| 日期 | 项 | 旧 → 新 | 为什么 |"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) == 4 and cells[0] not in {"---", ""}:
                old, _, new = cells[2].partition(" → ")
                rows.append(
                    {"at": cells[0], "key": cells[1], "old": old, "new": new, "why": cells[3]}
                )
    return rows


def interactive(data: dict[str, Any], demand: dict[str, Any] | None) -> dict[str, Any]:
    print("== 个人信息环境 · 初始设置 ==")
    print("这一步只做三件事：写需求快照、选判断方式、选推送方式。")
    print("判断阈值与权重不在这里改——它们有版本和护栏。\n")

    current_goals = "; ".join(
        item["statement"] for item in (demand or {}).get("active_goals") or []
    )
    current_tasks = "; ".join(
        item["statement"]
        for item in ((demand or {}).get("active_tasks") or [])
        if item.get("status") == "ACTIVE"
    )
    current_explore = "; ".join(
        ((demand or {}).get("exploration_intent") or {}).get("domains_hint") or []
    )
    current_minutes = str(
        ((demand or {}).get("constraints") or {}).get(
            "daily_information_budget_minutes", DEFAULT_MINUTES
        )
    )

    goals = _split(_ask("1/5 长期目标（1–3 条，用分号分隔）", current_goals))
    tasks = _split(_ask("2/5 当前任务（可空，用分号分隔）", current_tasks))
    explore = _ask("3/5 跨领域探索方向（可空）", current_explore).strip()
    provider = _ask_choice(
        "4/5 判断方式",
        ["local", "cloud", "auto"],
        str(user_config.get(data, "judge.provider", "local")),
    )
    print("    " + _provider_help(provider))
    channel = _ask_choice(
        "5/5 推送方式",
        ["email", "file", "none"],
        str(user_config.get(data, "delivery.channel", "file")),
    )
    print("    " + _channel_help(channel, data))
    email_values: dict[str, Any] | None = None
    if channel == "email":
        email_values = _ask_email(data)
    minutes_raw = _ask("每天愿意花多少分钟", current_minutes)
    try:
        minutes = int(minutes_raw)
    except ValueError:
        minutes = DEFAULT_MINUTES

    data = dict(data)
    user_config.set_value(data, "judge.provider", provider)
    user_config.set_value(data, "delivery.channel", channel)
    if email_values:
        for key, value in email_values.items():
            user_config.set_value(data, f"delivery.email.{key}", value)
    demand_data = build_demand(
        goals, tasks, explore, minutes=minutes, provider=provider, channel=channel
    )

    print("\n将写入：")
    print(f"  config/user.yaml   provider={provider} channel={channel}")
    print("  config/USER.md     初始文档（含能力与降级说明）")
    print(
        f"  demand/            {len(goals)} 个目标、{len(tasks)} 个任务、"
        f"探索位 {'开' if explore else '关'}"
    )
    confirm = _ask("确认写入？(y/N)", "n").lower()
    if confirm not in {"y", "yes"}:
        print("未写入。")
        raise SystemExit(2)
    return {"config": data, "demand": demand_data}


def _impact_of(key: str, old: Any, new: Any, data: dict[str, Any]) -> str:
    if key == "judge.provider":
        return (
            f"判断 provider 从 {old} 变成 {new}。排序与候选池会变：先跑 "
            "tools/compare_judges.py，阈值不会自动跟着改；" + _provider_help(str(new))
        )
    if key == "delivery.channel":
        return f"推送方式从 {old} 变成 {new}。" + _channel_help(str(new), data)
    if key == "demand.path":
        return "需求快照换了一份：13 问里的四个方向槽会跟着换，旧分数不可直接比较。"
    if key in {"judge.local_model", "judge.device"}:
        return "本地模型/设备变了：旧的测量结果不能直接与新结果比较；本机需要相应权重。"
    if key.startswith("delivery.email."):
        return "邮箱配置变了；授权码只从环境变量读，不要写进配置。"
    if key.startswith("crawl."):
        return "抓取预算/代理变了；预算变小可能漏掉排在后面的来源，变大则更慢。"
    return "这是环境配置，不改变判断规则；下一次运行生效。"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="首次设置 / 查看 / 修改个人配置")
    parser.add_argument("--config", default=None, help="默认 config/user.yaml")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--render-doc", action="store_true",
                        help="只按当前配置+需求重写 config/USER.md，不动需求快照")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--finish", action="store_true",
                        help="配置完成后删除安装副本里的 FIRST-SETUP.md")
    parser.add_argument("--skill-dir", default=None, help="配合 --finish：指定安装副本目录")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--goals", default=None, help="长期目标，分号分隔")
    parser.add_argument("--tasks", default=None, help="当前任务，分号分隔")
    parser.add_argument("--explore", default=None, help="跨领域探索方向")
    parser.add_argument("--provider", choices=["local", "cloud", "auto"], default=None)
    parser.add_argument("--channel", choices=["email", "file", "none"], default=None)
    parser.add_argument("--email-host", default=None)
    parser.add_argument("--email-port", type=int, default=None)
    parser.add_argument("--email-user", default=None)
    parser.add_argument("--email-from", default=None)
    parser.add_argument("--email-to", default=None, help="逗号或分号分隔")
    parser.add_argument("--email-password-env", default=None)
    parser.add_argument("--minutes", type=int, default=DEFAULT_MINUTES)
    parser.add_argument("--demand-out", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--set", dest="set_item", default=None, help="key=value")
    parser.add_argument("--why", default="", help="为什么改；--set 必填")
    parser.add_argument("--yes", action="store_true", help="确认写入")
    args = parser.parse_args(argv)

    data = user_config.load(args.config)
    doc_path = _user_doc(args.config)
    problems = user_config.validate(data)

    if args.show:
        print(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        try:
            path = user_config.resolve_demand(data)
            demand = json.loads(path.read_text(encoding="utf-8-sig"))
            print("== 需求快照 ==")
            print(f"  {_rel(path)}")
            print(
                f"  目标 {len(demand.get('active_goals') or [])} / "
                f"任务 {len(demand.get('active_tasks') or [])}"
            )
            print(f"  探索位 {(demand.get('exploration_intent') or {}).get('enabled')}")
        except FileNotFoundError as exc:
            print("== 需求快照 ==")
            print("  " + str(exc))
        if doc_path.exists():
            print("初始文档：", _rel(doc_path))
        if problems:
            print("配置问题：")
            for problem in problems:
                print("  - " + problem)
        return 0

    if args.render_doc:
        try:
            demand_path = user_config.resolve_demand(data)
            demand = json.loads(demand_path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        changes = _load_changes(doc_path)
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(
            render_user_doc(data, demand, demand_path, changes=changes), encoding="utf-8"
        )
        print("已重写", _rel(doc_path))
        return 0

    if args.check:
        from tools import doctor

        if problems:
            print("配置问题：")
            for problem in problems:
                print("  - " + problem)
            return 1
        return doctor.main(["--config", str(user_config.config_path(args.config))])

    if args.finish:
        return finish_setup(args)

    if args.set_item:
        if "=" not in args.set_item:
            print("--set 的格式是 key=value，例如 judge.provider=cloud", file=sys.stderr)
            return 2
        key, raw = args.set_item.split("=", 1)
        key = key.strip()
        try:
            value = yaml.safe_load(raw.strip())
        except yaml.YAMLError:
            value = raw.strip()
        if not args.why.strip():
            print('拒绝修改：改配置之前必须先说明为什么。加 --why "..."。', file=sys.stderr)
            return 2
        try:
            old, new = user_config.set_value(data, key, value)
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"将修改：{key}")
        print(f"  旧：{old}")
        print(f"  新：{new}")
        print("影响：" + _impact_of(key, old, new, data))
        if not args.yes:
            print("\n未写入。确认后加 --yes；--why 会记进 config/USER.md。")
            return 2
        problems = user_config.validate(data)
        if problems:
            print("配置不合法，未写入：")
            for problem in problems:
                print("  - " + problem)
            return 1
        user_config.save(data, args.config)
        try:
            demand_path = user_config.resolve_demand(data)
            demand = json.loads(demand_path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            demand_path = ROOT / "demand" / "demand-unknown.json"
            demand = {
                "active_goals": [],
                "active_tasks": [],
                "exploration_intent": {},
                "constraints": {},
            }
        changes = _load_changes(doc_path)
        changes.append(
            {
                "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "key": key,
                "old": str(old),
                "new": str(new),
                "why": args.why.strip()[:120],
            }
        )
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(
            render_user_doc(data, demand, demand_path, changes=changes), encoding="utf-8"
        )
        print(f"已写入 {user_config.config_path(args.config)}，变更记入 {_rel(doc_path)}")
        return 0

    existing_demand = None
    try:
        existing_path = user_config.resolve_demand(data)
        existing_demand = json.loads(existing_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        pass

    if args.non_interactive:
        if not args.goals:
            print('--non-interactive 至少需要 --goals "目标1;目标2"', file=sys.stderr)
            return 2
        provider = args.provider or str(user_config.get(data, "judge.provider", "local"))
        channel = args.channel or str(user_config.get(data, "delivery.channel", "file"))
        user_config.set_value(data, "judge.provider", provider)
        user_config.set_value(data, "delivery.channel", channel)
        if channel == "email":
            email_updates = {
                "host": args.email_host,
                "port": args.email_port,
                "username": args.email_user,
                "from": args.email_from,
                "password_env": args.email_password_env,
            }
            for key, value in email_updates.items():
                if value is not None:
                    user_config.set_value(data, f"delivery.email.{key}", value)
            if args.email_to is not None:
                recipients = [
                    item.strip()
                    for item in args.email_to.replace("；", ",").replace(";", ",").split(",")
                    if item.strip()
                ]
                user_config.set_value(data, "delivery.email.to", recipients)
        demand = build_demand(
            _split(args.goals),
            _split(args.tasks or ""),
            args.explore or "",
            minutes=args.minutes,
            provider=provider,
            channel=channel,
        )
        result = {"config": data, "demand": demand}
    else:
        if not sys.stdin.isatty():
            print("没有交互终端。用 --non-interactive 传参，或先看 --show。", file=sys.stderr)
            return 2
        result = interactive(data, existing_demand)

    data = result["config"]
    demand = result["demand"]
    problems = user_config.validate(data)
    if problems:
        print("配置不合法，未写入：")
        for problem in problems:
            print("  - " + problem)
        return 1

    demand_path = _demand_path(args.demand_out, force=args.force)
    write_demand(demand_path, demand)
    user_config.set_value(data, "demand.path", _rel(demand_path))
    config_file = user_config.save(data, args.config)
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(render_user_doc(data, demand, demand_path), encoding="utf-8")

    print("\n已写入：")
    print("  " + _rel(config_file))
    print("  " + _rel(demand_path))
    print("  " + _rel(doc_path))
    print("\n下一步：")
    print("  python tools/doctor.py     # 看这台机器缺什么")
    print("  python tools/check.py      # 五项自检")
    print('  python tools/crawl.py ...  # 或直接说"跑今天的信息"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


