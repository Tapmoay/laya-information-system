# 操作手册：定时、凭据、日志、巡检、回滚

> SKILL.md 讲"按什么顺序做什么"，这份文件讲"机器上具体怎么落地、坏了怎么看"。
> 路径都相对仓库根；Windows / PowerShell 为主。

## 1. 目录与状态速查

| 路径 | 是什么 | 谁写 |
|---|---|---|
| `runs/live-<日期>/` | 当天抓取 + 测量产物 | `tools/crawl.py`、`tools/score_items.py` |
| `runs/live-<日期>/unscorable.json` | 正文为空/过短，显式不可判 | `score_items.py`（两个 provider 都写） |
| `runs/live-<日期>/failures.jsonl` | 测量失败条目（有就说明覆盖不全） | 同上 |
| `runs/agent/<日期>/` | 终选包、决策、复核表、参数留痕 | `tools/agent_select.py` |
| `runs/label/<日期>/` | 译文、翻译日志 | `tools/translate_items.py` |
| `runs/final/<日期>/` | `final.md`、待记账清单 | `tools/final_digest.py` |
| `runs/compare/` | provider 对照报告 | `tools/compare_judges.py` |
| `store/delivered.jsonl` | 真正发出去的台账（7 天窗口） | `tools/push.py` |
| `store/push-ledger.jsonl` | 每次推送的幂等记录 | 同上 |
| `store/backlog.jsonl` | 过线但没选中的候补（7 天/80 条） | `tools/backlog.py` |
| `state/params/me/` | 参数版本、pending、历史 | `tools/learn.py` |

**只读**：`information-policy.md`、`demand/demand-*.json`（不是不能改，是改之前要确认）。
**不要删**：`runs/` 下已有目录——排障时它们是唯一的证据。

## 2. 手动跑一遍（交互式）

按 SKILL.md 的 S0–S8。最短路径：

```powershell
$today = Get-Date -Format 'yyyy-MM-dd'
python tools/crawl.py --max-total 300 --embed --max-seconds 900 --out runs/live-$today
python tools/score_items.py --items runs/live-$today/items.json `
  --demand demand/demand-2026-01-01.json --profile-id daily `
  --out runs/live-$today/scores.jsonl --workers 8
python tools/agent_select.py --build --scores runs/live-$today/scores.jsonl `
  --items runs/live-$today/items.json --demand demand/demand-2026-01-01.json `
  --out runs/agent/$today --limit 30
# 读 packet.md，填 decision.json，然后：
python tools/agent_select.py --apply --scores runs/live-$today/scores.jsonl `
  --items runs/live-$today/items.json --demand demand/demand-2026-01-01.json `
  --out runs/agent/$today
python tools/learn.py --user me --scores runs/live-$today/scores.jsonl `
  --from-overrides runs/agent/$today/selection.json --apply
python tools/translate_items.py --items runs/live-$today/items.json `
  --selection runs/agent/$today/selection.json --out runs/label/$today
python tools/final_digest.py --selection runs/agent/$today/selection.json `
  --items runs/live-$today/items.json --translations runs/label/$today/translations.json `
  --out runs/final/$today
python tools/push.py --from-final runs/final/$today/final.md --dry-run
python tools/push.py --from-final runs/final/$today/final.md
python tools/backlog.py --carry --items runs/live-$today/items.json `
  --scores runs/live-$today/scores.jsonl --selection runs/agent/$today/selection.json
```

## 3. 无人值守

### 3.1 注册/查看任务

```powershell
schtasks /Create /TN 'LaYA-Daily-Information' /SC DAILY /ST 06:00 ^
  /TR '"C:\python\python.exe" ".\tools\run_daily.py"' /F
schtasks /Query /TN 'LaYA-Daily-Information' /FO LIST
schtasks /Run   /TN 'LaYA-Daily-Information'
schtasks /Delete /TN 'LaYA-Daily-Information' /F
```

`run_daily.py` 会：

1. 从 `DEEPSEEK_API_KEY` 或 `~/.dsh/.credentials.yaml` 的 `refs.DEEPSEEK_API_KEY` 取 key；
2. 7897 没开就尝试拉起 Clash Verge（`D:/APPS/Clash Verge/clash-verge.exe`）；
3. 设 `LAYA_PROXY`、`PYTHONIOENCODING=utf-8`、`DSH_PERMISSION_MODE=danger-full-access`；
4. 把 `config/daily-runbook.md` 从 stdin 喂给 `npx -y @deepseek-ai/dsh headless -`；
5. 日志写 `runs/daily-<日期>/headless.log` 与 `runner.log`；失败写日志（需要通知就接自己的 webhook/邮件）。

**为什么必须解除沙箱**：无人值守没有审批通道，workspace-write 会 fail closed，
每一次写都失败。代价是 agent 不受沙箱保护，所以任务范围必须由 runbook 约束。

### 3.2 巡检（建议 06:20）

```powershell
Get-Content runs/daily-$(Get-Date -Format 'yyyy-MM-dd')/headless.log -Tail 40
(Select-String -Path runs/final/*/final.md -Pattern '^\| ' | Measure-Object).Count
(Get-Content store/push-ledger.jsonl | Measure-Object -Line).Count
```

三处数字对不上就往下查。**最坏的失败不是报错，是"看起来跑了，其实没发"。**

### 3.3 凭据

- 优先环境变量；没有才读 `~/.dsh/.credentials.yaml`。
- 不要把 key 写进 runbook、日志、技能、提交记录。
- `dsh headless` 不读桌面版凭据服务，只认环境变量——这就是 `run_daily.py` 注入它的原因。

## 4. 代理

- 三个源登记为 `network: proxy`：Project Zero、HF Papers、HF Blog。
- 其余源直连；直连失败才回退代理（按源分流）。
- 无代理时这三个源记 `NEEDS_PROXY`，属正常，不算失败，不要反复重试。

## 5. 回滚

| 要回滚什么 | 怎么做 |
|---|---|
| 判断 provider | `--provider local --device cuda`（需本地权重）；或反过来 `--provider cloud` |
| 阈值/权重 | `python tools/learn.py --user me --rollback <版本>`；不要手改文件 |
| runbook 流程 | `git log -p config/daily-runbook.md`，改回上一版 |
| 技能 | 删 `~/.agents/skills/personal-information-environment` junction（仓库内副本保留） |
| 定时任务 | `schtasks /Delete /TN 'LaYA-Daily-Information' /F` |

回滚之后**补一条记录**：为什么回滚、哪次运行的证据。否则下次还会踩。

## 6. 排障顺序

1. `python tools/doctor.py`——缺依赖/凭据/需求，先补；
2. 看当天的 `runner.log`（代理起没起、退出码、耗时）；
3. 看 `headless.log` 尾部有没有 `SUMMARY`、有没有异常栈；
4. 数字对不上：`final.md` 条数 → `push-ledger` → `delivered`，逐层核对；
5. 测量覆盖不全：`failures.jsonl` → `judge.log.jsonl`（attempts / error）；
6. 内容质量不对：先看 `runs/agent/<日期>/params.json` 的版本与阈值，再看分数分布；
7. 抓取少：`runs/live-<日期>/items.json` 的来源计数 + `crawl-manifest.json` 的失败原因码。

## 7. 改流程的规矩

- 交互式流程改 `SKILL.md`；无人值守改 `config/daily-runbook.md`；**两边一起改**。
- 流程规范（阶段、通过条件、失败动作）改 `docs/38-daily-workflow.md`；
  只改命令不改规范，下一个人会按旧规范验收。
- 改完跑 `python tools/check.py`（五项全过）；它包含测试数与文档数字的自洽检查。
