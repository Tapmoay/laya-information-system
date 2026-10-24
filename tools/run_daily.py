"""每天 06:00 的无人值守入口：拉起 dsh headless，跑完就退出。

为什么不是"在桌面版里挂个提醒"：桌面版是 GUI，无人值守时它起不来、会话也不一定在。
CLI 的 headless 模式回答一次任务就退出，没有窗口、没有会话恢复问题，
但仍然是完整 agent（能读文件、能跑命令、能加载 skills）。

三件事必须显式处理，否则会静默失败：

1. **API key**：headless 不读桌面版的凭据服务，只认环境变量，
   所以这里从 ~/.dsh/.credentials.yaml 的 refs.DEEPSEEK_API_KEY 取出来注入子进程。
2. **日志**：无人看着跑，失败必须留痕。每次运行写 runs/daily-<日期>/headless.log。
3. **失败留痕**：跑挂了要把退出码和日志尾部写进 stderr / runner.log，而不是安静退出。

用法：
    python tools/run_daily.py                 # 跑今天的例行
    python tools/run_daily.py --task "..."    # 换成一次性任务（冒烟测试用）
    python tools/run_daily.py --dry-run       # 只打印将要执行的命令与任务
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import user_config  # noqa: E402

RUNBOOK = ROOT / "config" / "daily-runbook.md"
LOGS = ROOT / "runs"
DEFAULT_PROXY_URL = ""
DEFAULT_PROXY_PORT = 7897
DEFAULT_AUTOSTART = ""


def _api_key() -> str:
    """从 DSH 的凭据文件里取 DeepSeek key；没有就退回环境变量。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    path = Path(os.path.expanduser("~")) / ".dsh" / ".credentials.yaml"
    if not path.exists():
        raise SystemExit("找不到凭据文件：" + str(path))
    try:
        import yaml
    except ImportError:
        raise SystemExit("需要 pyyaml 才能读凭据文件；或直接设置 DEEPSEEK_API_KEY")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    key = str(((data.get("refs") or {}).get("DEEPSEEK_API_KEY") or "")).strip()
    if not key:
        raise SystemExit("凭据文件里没有 refs.DEEPSEEK_API_KEY")
    return key


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_proxy(wait_seconds: int = 60, *, proxy_url: str | None = None,
                 proxy_port: int | None = None, autostart_exe: str | None = None) -> str | None:
    """代理没开就自己开。

    为什么要这一步：17 个外网源在代理关闭时全部 TLS 超时，一次运行会白烧
    十几分钟预算，还会把排在后面的设计源挤掉。开代理是三步里最便宜的。

    换机器/换人时：config/user.yaml 里 proxy.url 留空就完全不碰代理；
    填了 url 但 autostart_exe 留空，只检查不启动。没有代理客户端的机器不会因此失败。
    """
    url = (proxy_url if proxy_url is not None else DEFAULT_PROXY_URL).strip()
    if not url:
        return None
    from urllib.parse import urlparse

    parsed = urlparse(url)
    port = int(proxy_port or parsed.port or DEFAULT_PROXY_PORT)
    host = parsed.hostname or "127.0.0.1"
    if _port_open(port, host):
        return url
    exe = Path(DEFAULT_AUTOSTART if autostart_exe is None else autostart_exe)
    if not str(exe).strip() or str(exe) == ".":
        print("代理未开启，且配置未允许自动启动（proxy.autostart_exe 为空）", file=sys.stderr)
        return None
    if not exe.exists():
        print("代理未开启，且找不到 {}".format(exe), file=sys.stderr)
        return None
    print("代理未开启，正在启动 {} …".format(exe))
    try:
        subprocess.Popen(
            [str(exe)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as exc:
        print("启动代理失败：{}".format(exc), file=sys.stderr)
        return None
    for _ in range(wait_seconds):
        time.sleep(1)
        if _port_open(port, host):
            print("代理已就绪：{}".format(url))
            return url
    print("代理在 {} 秒内没有起来".format(wait_seconds), file=sys.stderr)
    return None


def _notify_failure(log_path: Path, code: int, tail: str) -> None:
    """失败要留痕；具体通知渠道由部署者自己接（邮件/webhook/...）。"""
    print("每日例行失败：退出码 {}；日志 {}；尾部：{}".format(code, log_path, tail[-400:]), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="无人值守：拉起 dsh headless 跑每日例行")
    parser.add_argument("--task", default=None, help="覆盖 runbook 的一次性任务")
    parser.add_argument("--config", default=None, help="默认 config/user.yaml")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=5400, help="秒；超时视为失败")
    args = parser.parse_args(argv)

    config = user_config.load(args.config)
    proxy_url = str(user_config.get(config, "proxy.url") or "").strip()
    proxy_port = int(user_config.get(config, "proxy.port") or DEFAULT_PROXY_PORT)
    autostart = user_config.get(config, "proxy.autostart_exe")
    autostart = "" if autostart is None else str(autostart)

    if args.task:
        task = args.task
    else:
        if not RUNBOOK.exists():
            print("错误：找不到 runbook " + str(RUNBOOK), file=sys.stderr)
            return 2
        task = RUNBOOK.read_text(encoding="utf-8")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = LOGS / ("daily-" + datetime.now().strftime("%Y-%m-%d"))
    log_path = out_dir / "headless.log"

    # Windows 上 npx 是 .cmd 包装脚本，CreateProcess 不能直接执行它；
    # 交给 cmd.exe 解析。任务正文走 stdin，所以命令行里没有需要转义的内容。
    command = "npx -y @deepseek-ai/dsh headless -"
    print("任务长度：{} 字".format(len(task)))
    print("日志：{}".format(log_path))
    if args.dry_run:
        print("命令：{}".format(" ".join(command)))
        print("---- 任务 ----")
        print(task[:800])
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    # 计划任务没有控制台：stdout 会丢，事后就查不到"代理到底起没起来"。
    # 把关键行另写一份，和 headless.log 放在一起。
    runner_log = out_dir / "runner.log"

    def note(message: str) -> None:
        print(message)
        try:
            with runner_log.open("a", encoding="utf-8") as handle:
                handle.write("[{}] {}{}".format(datetime.now().strftime("%H:%M:%S"), message, chr(10)))
        except OSError:
            pass

    env = dict(os.environ)
    env["DEEPSEEK_API_KEY"] = _api_key()
    if args.config:
        env["LAYA_CONFIG"] = str(user_config.config_path(args.config))
    proxy = ensure_proxy(proxy_url=proxy_url, proxy_port=proxy_port, autostart_exe=autostart)
    note("代理：" + (proxy or "未启用"))
    if proxy:
        # crawl 直接读这个环境变量；agent 在 runbook 里不必再关心代理
        env["LAYA_PROXY"] = proxy
    env["PYTHONIOENCODING"] = "utf-8"
    # 无人值守时没有审批通道，而沙箱在这个工作区里连 store/ runs/ 的子目录都写不进去，
    # 每一次写都会被判成"需要审批"然后 fail closed。这里显式解除限制——
    # 代价是这个 agent 不受沙箱保护，所以任务范围必须由 config/daily-runbook.md 约束。
    env["DSH_PERMISSION_MODE"] = "danger-full-access"

    started = time.time()
    with log_path.open("w", encoding="utf-8") as handle:
        handle.write("# {}\n# task chars: {}\n\n".format(stamp, len(task)))
        handle.flush()
        try:
            process = subprocess.run(
                command,
                shell=True,
                input=task,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(ROOT),
                env=env,
                timeout=args.timeout,
            )
            code = process.returncode
        except subprocess.TimeoutExpired:
            handle.write("\n[timeout] 超过 {} 秒\n".format(args.timeout))
            code = 124

    elapsed = time.time() - started
    note("退出码 {}，耗时 {:.1f} 分钟".format(code, elapsed / 60))
    tail = ""
    try:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-600:]
    except OSError:
        pass
    print(tail[-800:])

    if code != 0:
        _notify_failure(log_path, code, tail)
    return code


if __name__ == "__main__":
    raise SystemExit(main())


