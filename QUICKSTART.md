# 快速开始

> 从零到拿到今天这份读物：先做一次初始设置，然后六步。全部在仓库根目录执行。

## 0. 初始设置 + 看环境

```powershell
python -m pip install -e .
python tools/setup.py      # 目标、任务、探索、判断方式、推送方式、每天时间
python tools/doctor.py     # 按你的配置看缺什么
```

`setup.py` 写三份文件：`config/user.yaml`（机器配置）、`config/USER.md`（初始文档）、
`demand/demand-<日期>.json`（需求快照）。13 问的前四问直接来自这份需求。

**配置完成前不跑抓取**：用别人的需求抓一遍，产出的只是别人的读物。

## 1. 抓

```powershell
python tools/crawl.py --max-total 300 --embed --max-seconds 900
```

产物：`items.json`、`provenance.jsonl`、`raw/`。代理读 `LAYA_PROXY` 或配置；
需要代理的源会记 `NEEDS_PROXY`，不是失败。

## 2. 打分

```powershell
python tools/score_items.py --items runs/crawl-<时间戳>/items.json ^
    --profile-id daily --out runs/scores.jsonl
```

判断方式读 `config/user.yaml`（local / cloud / auto）。失败写 `failures.jsonl` 并以退出码 1 结束；
正文为空写 `unscorable.json`，不按 0 分处理。

## 3. 查重

```powershell
python tools/repeat_guard.py --items runs/crawl-<时间戳> --scores runs/scores.jsonl ^
    --facts-lib store/fact-library.jsonl --out runs/repeat.json
```

## 4. 终选

```powershell
python tools/agent_select.py --build --scores runs/scores.jsonl ^
    --items runs/crawl-<时间戳>/items.json --out runs/agent/<日期>
```

读 `runs/agent/<日期>/packet.md`，按 `decision.json` 的格式填，然后：

```powershell
python tools/agent_select.py --apply --scores runs/scores.jsonl ^
    --items runs/crawl-<时间戳>/items.json --out runs/agent/<日期>
```

## 5. 翻译 + 摘要

```powershell
python tools/translate_items.py --items runs/crawl-<时间戳>/items.json ^
    --selection runs/agent/<日期>/selection.json --out runs/label/<日期>
python tools/final_digest.py --selection runs/agent/<日期>/selection.json ^
    --items runs/crawl-<时间戳> --translations runs/label/<日期> ^
    --scores runs/scores.jsonl --out runs/final/<日期>
```

## 6. 推送

```powershell
python tools/push.py --from-final runs/final/<日期>/final.md --dry-run
python tools/push.py --from-final runs/final/<日期>/final.md
```

渠道读 `config/user.yaml`：`email` / `file` / `none`。
先 `--dry-run`；按 `item_id` 记账，发失败的不占 7 天窗口。

## 7. 收尾

```powershell
python tools/backlog.py --carry --items runs/crawl-<时间戳> --scores runs/scores.jsonl ^
    --selection runs/agent/<日期>/selection.json --ledger store/backlog.jsonl
python tools/check.py
```

## 出问题时

| 症状 | 先看 |
|---|---|
| 某些源一直 0 条 | `python tools/source_index.py --verify` |
| 本地判断起不来 | `python tools/doctor.py`；或改用 `cloud` |
| 推的东西太水 | `judgment/params.py` 的 `threshold` 与 `judgment/channels.py` 的 `THRESHOLDS` |
| 同一件事推了两次 | `store/delivered.jsonl` 与事实库 |
| 推送失败 | `python tools/push.py --dry-run`；渠道凭据见技能里的 operations |
| 有该看的东西没出现 | 候补池；或者它根本没被任何源抓到 |

改之前先读 `MISSION.md`、`information-policy.md` 与 `docs/38-daily-workflow.md`。
