import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
text = Path("runs/agent/2026-10-19/packet.md").read_text(encoding="utf-8")
for block in re.split(r"(?m)^### ", text)[1:]:
    lines = [l for l in block.splitlines() if l.strip()]
    head = lines[0]
    title = next((l for l in lines if l.startswith("**")), "").strip("*")
    summary = next((l for l in lines if l.startswith("> ")), "")[2:]
    signal = next((l for l in lines if l.startswith("信号")), "")
    signal = signal.replace("信号：方向 ", "").replace(" ｜ 最高价值 ", " | v:").replace(" ｜ 质量 ", " | q:").replace(" ｜ 形态 ", " | ").replace(" ｜ 招聘 ", " | recur:")
    print(head.replace("### ", "#"))
    print("  " + title[:52])
    print("  " + summary[:190])
    print("  " + signal)
