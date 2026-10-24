# 38 · 每日信息工作流规范

> 这份规范定义"一次完整的每日运行"由哪些阶段组成、每个阶段交付什么、
> 什么条件下算通过、失败时谁负责。它不是操作手册（命令在
> [`agent/skills/personal-information-environment/SKILL.md`](../agent/skills/personal-information-environment/SKILL.md)），
> 也不是架构说明（在 [docs/17](17-architecture.md)）。
>
> **为什么需要它**：2026-10-24 之前，流程只存在于 runbook 的编号列表里，
> 出问题时只能靠读当天的 `headless.log` 猜哪一步没做。规范把"通过条件"提前写死，
> 让无人值守的失败能被定位，而不是被解释。

---

## 1. 目标与非目标

**目标**

1. 每天产出一份可解释的读物：5 条以内核心 + 2 条以内探索，每条带理由与逐字证据。
2. 每个阶段都有可检查的产物；失败可定位到阶段，不靠记忆。
3. 判断模型可替换（本地 / 云端），但**测量与决策的边界不变**。
4. 成本、时间、注意力都有上限，并且写下来。

**非目标**

- 不追求零遗漏（MISSION 第 5 条）。
- 不追求每天凑满 7 条。
- 不让模型决定推荐什么。

---

## 2. 角色与权责

| 角色 | 谁 | 能做 | 不能做 |
|---|---|---|---|
| 触发者 | Windows 任务计划 / 人 | 06:00 启动；或手动发起 | 改流程 |
| Runner | `tools/run_daily.py` | 注入凭据、开代理、拉起 headless、写日志、失败通知 | 解释内容 |
| Agent | `dsh headless` 或交互式会话 | 按阶段执行命令、读终选包、写 `decision.json`、写 SUMMARY | 改问句、权重、阈值；绕过校验 |
| 确定性代码 | `judgment/`、`selection/`、`delivery/` | 测量映射、打分、候选池、交付、台账 | 调用模型做选择 |
| 人 | 用户 | 改政策/需求、复盘、批准规则变更 | 手改 selection.json |

**唯一的判断权在 Agent 终选**（S4）。其他阶段的"判断"都是代码里的确定性规则。

---

## 3. 阶段契约

所有阶段都在仓库根执行；`<今天>` = `yyyy-MM-dd`。

### S-1 初始设置（只在第一次 / 换人 / 换机器时做）

| 项 | 内容 |
|---|---|
| 输入 | 用户的六个答案：长期目标、当前任务、探索方向、判断方式、推送方式、每天时间 |
| 动作 | `python tools/setup.py`（交互式或 `--non-interactive`） |
| 产物 | `config/user.yaml`、`config/USER.md`、`demand/demand-<日期>.json` |
| 通过 | 三份文件存在；`config/USER.md` 被用户读回确认；`doctor.py` 按配置通过 |
| 失败 | 不进入 S0；用别人的需求抓取只会产出别人的读物 |
| 幂等 | 重跑会生成新的需求快照；改配置用 `setup.py --set`，不要手改 |

### S0 预检

| 项 | 内容 |
|---|---|
| 输入 | 仓库、凭据、需求快照 |
| 动作 | `python tools/doctor.py`；确认最新 `demand/demand-*.json` |
| 产物 | 屏幕输出（无文件） |
| 通过 | 凭据可用；需求 `state.status == OK`；来源库可读 |
| 失败 | 缺凭据/需求 → **停止**，不进入 S1 |
| 幂等 | 是 |

### S1 抓取

| 项 | 内容 |
|---|---|
| 输入 | `config/sources-index.yaml`（37 个源）、代理 |
| 动作 | `python tools/crawl.py --max-total 300 --embed --max-seconds 900 --out runs/live-<今天>` |
| 产物 | `items.json`、`provenance.jsonl`、`raw/`、`crawl-manifest.json` |
| 通过 | 抓到 > 0 条；每个失败源有原因码（`NEEDS_PROXY` 不算失败） |
| 失败 | 0 条 → 停止；部分源失败 → 继续，但 SUMMARY 写明 |
| 幂等 | 重跑会覆盖同日目录；已有 `raw/` 不删 |

### S2 测量

| 项 | 内容 |
|---|---|
| 输入 | `items.json`、需求快照、`config/user.yaml` 的 provider |
| 动作 | `python tools/score_items.py --items ... --profile-id daily --out runs/live-<今天>/scores.jsonl` |
| 产物 | `scores.jsonl`、`judge.log.jsonl`、`unscorable.json`；失败时 `failures.jsonl` |
| 通过 | 覆盖 100%（不可判除外）；**没有 `failures.jsonl`**；退出码 0 |
| 失败 | 有失败 → 停止；不可判 → 继续，但计入 SUMMARY |
| 幂等 | 是（重跑覆盖同日 scores） |
| 契约 | `{"scores": {12×noul + form}, "judge": {...}, "provenance": {...}}`，见 [云端判断契约](../agent/skills/personal-information-environment/references/cloud-judgment.md) |

**两个语义必须分开**

- `unscorable`：正文为空/过短 → **不可判**，不送测量、不算 0 分；
- `failure`：调用失败/输出不合约 → **未知**，不写 0 分，覆盖不达标。

把两者混成一个"0 分"是本系统最严重的错误类别。

### S3 候选池

| 项 | 内容 |
|---|---|
| 输入 | `scores.jsonl`、`items.json`、需求、参数版本 |
| 动作 | `python tools/agent_select.py --build ... --limit 30` |
| 产物 | `packet.md`、`decision.json`（模板）、`candidates.json`、`params.json`、`event-log.jsonl` |
| 通过 | 候选 1–30 条；`params.json` 记录版本/阈值/权重；每源 ≤2 席 |
| 失败 | 0 条 → 不进入 S4；先查阈值、需求、抓取量 |
| 幂等 | 是（同参数同输入同结果） |

### S4 终选（唯一判断权）

| 项 | 内容 |
|---|---|
| 输入 | `packet.md`（需求原文 + 候选摘要 + 信号行） |
| 动作 | 按 `agent/AGENT.md` 与 `final-selection` 技能，填 `decision.json`；`--apply` |
| 产物 | `selection.json`、`review.md`、`review.html`、事件日志 |
| 通过 | 校验器通过；每条 pick/exclude 有理由 + 逐字证据；核心 ≤5、探索 ≤2 |
| 失败 | 校验不过 → 逐条修；不许绕过校验手写 selection.json |
| 幂等 | 同一天重复 `--apply` 以最后一次为准（事件日志同日覆盖） |

**顺序约束**：先读需求，再看候选。先看候选再想需求，会被内容牵着走。

### S5 回灌参数

| 项 | 内容 |
|---|---|
| 输入 | `scores.jsonl` + `selection.json` 的 `override_events` |
| 动作 | `python tools/learn.py --user me --scores ... --from-overrides ... --apply` |
| 产物 | `state/params/me/` 新版本或 `pending.jsonl` |
| 通过 | 提交新版本，或明确记录"未提交及原因" |
| 失败 | 回放护栏/步长上限拦截 → 不阻塞，但必须写进 SUMMARY |
| 幂等 | 是（同版本不会重复提交） |

### S6 翻译与摘要

| 项 | 内容 |
|---|---|
| 输入 | `items.json` + `selection.json` |
| 动作 | `translate_items.py` → `final_digest.py` |
| 产物 | `translations.json`、`translate.log.jsonl`、`final.md`、`ledger-pending.jsonl` |
| 通过 | `final.md` 条数 = 选中条数（除非翻译失败降级）；摘要不添加原文没有的判断 |
| 失败 | 翻译失败不阻塞（保留原文标题）；`final_digest` 失败 → 停止 |
| 幂等 | 是（`final_digest` 只写 pending，不记账） |

### S7 推送

| 项 | 内容 |
|---|---|
| 输入 | `final.md`、`config/user.yaml` 的 delivery.channel |
| 动作 | 先 `--dry-run`，再 `python tools/push.py --from-final ...` |
| 产物 | 渠道消息（邮件/文件）、`store/push-ledger.jsonl`、`store/delivered.jsonl` |
| 通过 | 实际发出条数 = `final.md` 条数 = 台账新增行数（file 渠道不记账） |
| 失败 | 部分失败 → 只记成功的；失败的下次补发（不占 7 天窗口） |
| 幂等 | 是（按 item_id 记账 + 幂等键） |

### S8 收尾与巡检

| 项 | 内容 |
|---|---|
| 输入 | 当天全部产物 |
| 动作 | `backlog.py --carry`；06:20 巡检日志/条数/台账 |
| 产物 | `store/backlog.jsonl`（≤80 条，7 天过期）、SUMMARY |
| 通过 | 三处数字对得上；SUMMARY 是最后一行 |
| 失败 | 对不上 → 查 S7 台账；不要把"没发"说成"今天没有值得推的" |

---

## 4. 产物与依赖图

```text
crawl ──▶ items.json ──┬─▶ score_items ──▶ scores.jsonl ──┬─▶ agent_select --build ──▶ packet.md
                       │                                  │
                       │                                  └─▶ learn.py
                       │
                       └─▶ translate_items ──▶ translations.json
                                                      │
agent_select --apply ──▶ selection.json ──────────────┼─▶ final_digest ──▶ final.md
                                                      │                        │
                                                      └─▶ backlog              └─▶ push ──▶ ledgers
```

依赖是单向的：后面的阶段可以读前面的产物，不能反过来改。

---

## 5. 不变量

| # | 不变量 | 落在哪 |
|---|---|---|
| 1 | 测量不等于决策：模型只产出测量，选择由规则/Agent 做 | `judgment/cloud_judge.py`、`judgment/scoring.py`、`INV-02` |
| 2 | 未知必须可表达：`UNKNOWN`、`ERROR`、`unscorable` 不得互相冒充 | `scores.jsonl` / `failures.jsonl` / `unscorable.json` 三文件 |
| 3 | 理由必须指向"它能帮我做什么"，且证据必须逐字来自摘要 | `judgment/agent_select.py` 校验器 |
| 4 | 推送幂等：同一条不因重跑而重复推 | `delivery/push.py`、`store/*.jsonl` |
| 5 | 换 provider 必须留下对照与决策记录 | `tools/compare_judges.py`、`docs/39`、ADR |

违反任何一条，当天结果不算完成，即使消息已经发出去了。

---

## 6. 失败矩阵（阶段级）

| 阶段 | 失败形态 | 是否阻塞 | 动作 |
|---|---|---|---|
| S0 | 凭据缺失 | 是 | 补 key；不要重试烧预算 |
| S1 | 全部源 0 条 | 是 | 查网络/代理/源库，不进入测量 |
| S1 | 个别源失败 | 否 | 记原因码；`NEEDS_PROXY` 正常 |
| S2 | 单条失败 | 是（默认） | 重跑该批；连续失败查 `judge.log.jsonl` |
| S2 | 全部不可判 | 是 | 回 S1 查空壳 |
| S3 | 候选 0 条 | 是 | 查阈值/需求/抓取量；不许手动凑 |
| S4 | 校验不过 | 是 | 逐条改；不许绕过 |
| S5 | 护栏拦截 | 否 | SUMMARY 写明未提交 |
| S6 | 翻译失败 | 否 | 原文标题降级；日志可查 |
| S7 | 部分发送失败 | 否 | 只记成功；补发 |
| S8 | 数字对不上 | 是（复盘） | 查台账；当天标记异常 |

---

## 7. 预算

| 资源 | 上限 | 2026-10-24 实测 |
|---|---|---|
| 抓取时间 | 900 秒硬预算 | 约 5 分钟 |
| 测量 | 按 `config/user.yaml` 的 provider | 223 条 41 秒 / 461k tokens（云端）；约 5 分钟（本地） |
| 终选 | 候选 ≤30 条；核心 ≤5、探索 ≤2 | 30 条候选 |
| 翻译 | 只翻选中的非中文条目 | 9 条、22 次调用 |
| 推送 | 每人每天 1 次；条数 = 选中条数 | 7 条 |
| 注意力 | 推送 ≤7 条 + 暂存只留链接 | 7 条 |

超过预算不是"更好"，是异常，要写进 SUMMARY。

---

## 8. 观测与验收

一次运行算通过，需要同时满足：

1. `runs/daily-<日期>/headless.log` 尾部有 `SUMMARY:`；
2. `runs/final/<日期>/final.md` 条数 = 实际推送条数；
3. `store/push-ledger.jsonl` 当天新增行数 = 实际推送条数；
4. 没有 `failures.jsonl`；`unscorable.json` 若存在，条数与原因已写进 SUMMARY；
5. `runs/agent/<日期>/params.json` 存在，SUMMARY 里有版本与阈值。

四条里任何一条不满足，就在 SUMMARY 里写成异常——**不许静默通过**。

---

## 9. 变更协议

| 改什么 | 必须先有什么 | 改完要做什么 |
|---|---|---|
| 初始需求/目标 | 用户确认六个答案 | 跑 `tools/setup.py`；旧 demand 保留 |
| 判断 provider/渠道 | 对照报告（provider）或凭据检查（渠道） | `setup.py --set ... --why ... --yes`；变更写入 `config/USER.md` |
| 提示词/评分锚点 | 新旧提示词 30 条 A/B | 更新 [云端判断契约](../agent/skills/personal-information-environment/references/cloud-judgment.md)；记录差异 |
| 阈值/权重 | 连续 7 天真实取舍 或 30 条人工标签 | 走 `learn.py`；写版本；回看 |
| 加/停来源 | 来源审计（docs/27） | 更新 source index；渲染索引 |
| 改阶段/通过条件 | 先改本规范 | 同一天改 SKILL.md 与 runbook |
| 改推送渠道 | 幂等与台账先就位 | 补失败通知与巡检 |

**原则**：先有证据，再改规则；改规则和改观测同一天完成，否则下次无法判断改动有没有用。
改配置还要多一条：**先向用户解释改什么、为什么、影响什么**；
`tools/setup.py --set` 用 `--why` 与 `--yes` 把这条规则变成可执行的动作。

---

## 10. 开放问题

1. **没有人工标注集**：换 provider 只能证明"不同"，不能证明"更准"。
2. **探索位与候选池尾部对 provider 敏感**：云端把探索分抬高 0.209，探索位排序会变。
3. **自报分数没有校准**：`form` 权重不是 softmax，0.7 不是 70% 把握。
4. **未接正式适配器边界**：13 问直连 provider，不经过 `invoke_decision` 的语言/状态校验。
5. **连续失败告警缺失**：单次失败有通知，连续三天失败没有聚合告警。
6. **桌面版提醒与 headless 的重复风险**：目前靠"删掉桌面提醒"保证，没有自动检测。
7. **配置漂移**：`config/user.yaml` 没有 schema 迁移；加字段时要手写迁移。
8. **需求更新靠人**：目标变了不重跑 `setup.py`，13 问会继续用旧方向。
