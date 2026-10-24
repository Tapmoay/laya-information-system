【每日例行 · 无人值守】现在跑信息系统的完整流程，并把结果推给用户。按顺序做，每步都要有结果；失败就停下来说明卡在哪，不要凑数。

1) 抓取：python tools/crawl.py --max-total 300 --embed --max-seconds 900 --out runs/live-<今天>
   （代理读 LAYA_PROXY 或 config/user.yaml；需要代理的源会记 NEEDS_PROXY，属正常）

2) 打分：全量，不抽样
   python tools/score_items.py --items runs/live-<今天>/items.json --profile-id daily --out runs/live-<今天>/scores.jsonl
   （provider 读 config/user.yaml；失败写 failures.jsonl 并以退出码 1 结束；不可判写 unscorable.json）

3) 终选：python tools/agent_select.py --build --scores runs/live-<今天>/scores.jsonl --items runs/live-<今天>/items.json --out runs/agent/<今天> --limit 30
   （候选池含最多 3 条事件候选；重大事件可占核心位，写 major_event 与 event_reason）
   → 读 packet，按 agent/AGENT.md 与 agent/skills/final-selection 选出 5+2 →
   每条写 reason 与 gist，写进 decision.json 后 --apply

4) 调参：python tools/learn.py --user me --scores runs/live-<今天>/scores.jsonl --from-overrides runs/agent/<今天>/selection.json --apply
   （失败不阻塞推送，但要在 summary 说明）

5) 翻译 + 摘要：python tools/translate_items.py --items runs/live-<今天>/items.json --selection runs/agent/<今天>/selection.json --out runs/label/<今天>
   python tools/final_digest.py --selection runs/agent/<今天>/selection.json --items runs/live-<今天>/items.json --translations runs/label/<今天>/translations.json --out runs/final/<今天>

6) 推送：python tools/push.py --from-final runs/final/<今天>/final.md
   （先 --dry-run；渠道读 config/user.yaml 的 delivery.channel）

7) 收尾：python tools/backlog.py --carry --items runs/live-<今天>/items.json --scores runs/live-<今天>/scores.jsonl --selection runs/agent/<今天>/selection.json
   最后用一句话总结：几条、哪些通道、参数版本、有没有异常。

约束：
- 不要修改 store/ 以外的状态文件；不要删 runs/ 下已有目录。
- 任何一步产出为空，视为失败并停止。
- 全部结束后，把 summary 打印成最后一行，格式：SUMMARY: <一句话>
