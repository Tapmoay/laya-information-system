---
name: personal-information-environment
description: 运营 LaYA / 个人信息环境系统——首次设置（需求、判断方式、推送渠道）、抓取 37 个来源、13 问测量、确定性打分、Agent 终选 5+2、中文摘要、邮箱/文件推送，以及由你自己的 agent 适配的其它渠道，以及换判断模型（本地/云端）、调阈值、复盘。当用户说"跑今天的信息""抓取打分""终选""推送读物""判断模型上云""换一台机器/换个人用""LaYA""每日例行"，或要改这个仓库的抓取/测量/选择/交付规则时使用。即使用户没点名流程，只要任务是处理这个仓库的信息链路，也先读本技能。
---

# 个人信息环境（LaYA）的运营技能

> 这个系统不替用户理解，只负责把值得看的东西放到位——**一个指针，不是一份饲料**。
> 每次运行都必须能回答三件事：这条属于哪条通道、为什么选它、它可能改变什么判断。
> 说不出来，就允许少于上限。
>
> **仓库根**：包含 `MISSION.md`、`tools/`、`judgment/` 的那一层。
> 先 `Get-Location` 确认；不在根就先 `cd` 过去。下文的相对路径都以它为基准。
> 如果当前目录不是仓库根：问用户仓库在哪，或从本技能文件向上找到 `MISSION.md` 的那一层
> （通过 junction 安装时先解析真实路径）。所有命令都在那一层执行。
>
> **第一次使用？** 先读 `FIRST-SETUP.md`——那是一次性配置清单，配置完成后删除它。
> 日常流程只读本文件与 `references/`。

## 0. 先读什么

1. 本技能（流程、质量门、配置规矩）。
2. `MISSION.md`（为什么存在，五条信念）。
3. `information-policy.md`（用户原话；与主旨冲突时以它为准）。
4. `docs/35-principles.md`（每条原则落在哪一行代码）。

无人值守时喂给 `dsh headless` 的是 `config/daily-runbook.md`。
**两者是同一套流程的两种写法**：改流程必须同时改 runbook，否则第二天 06:00 跑的还是旧流程。

## 1. 第一次使用 / 换人 / 换机器：先做初始设置

**如果目录里有 `FIRST-SETUP.md`，先按它做。** 它是一次性清单：问六个问题、写三份文件、
检查环境、第一次试跑，最后删除自己。不要跳过它直接抓取。

如果已经配置过（没有 `FIRST-SETUP.md`），直接进入下面的日常流程；
配置写在 `config/user.yaml`，人的说明在 `config/USER.md`，需求在 `demand/`。

环境完全不同（没有仓库、没有聊天渠道、方向不一样）时，先读
[`references/adaptation.md`](references/adaptation.md)：
不变量照抄，实现随便换，判断结果不要照抄。
**如果只有技能包、要从零构建整套系统**，读
[`references/build-from-scratch.md`](references/build-from-scratch.md)：
它给出七阶段、每阶段验收、六个数据契约与完成定义。

配置写在三份文件里：

| 文件 | 内容 | 谁读 |
|---|---|---|
| `config/USER.md` | 初始文档：目标、任务、探索、判断方式、推送方式、能力降级 | 人 + agent |
| `config/user.yaml` | 机器配置：provider、渠道、抓取预算、代理 | 代码 |
| `demand/demand-<日期>.json` | 需求快照：13 问的前四问从这里取 | 测量与终选 |

六个问题（详细问法与选项见 [`references/onboarding.md`](references/onboarding.md)）：

1. 长期目标（1–3 条）；
2. 当前任务（0–2 条）；
3. 跨领域探索方向（可空）；
4. 判断方式：`local` / `cloud` / `auto`；
5. 推送方式：`email` / `file` / `none`（其他渠道自己加）；
6. 每天愿意花多少分钟。

```powershell
python -m pip install -e .        # 基础依赖
python tools/setup.py             # 交互式；写上面三份文件，先打印再确认
python tools/doctor.py            # 按你选的 provider/渠道检查缺什么
```

非交互环境：

```powershell
python tools/setup.py --non-interactive ^
  --goals "长期方向一;长期方向二" --tasks "当前任务" ^
  --explore "结构、张力在别的领域怎么体现" ^
  --provider local --channel file --minutes 30
```

**配置完成前不跑抓取**：用别人的需求抓一遍，产出的只是别人的读物。
设置完成后，把 `config/USER.md` 读回给用户确认，再进入日常流程。

## 2. 配置从哪来（以及什么不在这里）

| 放哪 | 放什么 | 不放什么 |
|---|---|---|
| `config/user.yaml` | provider、模型、渠道、抓取预算、代理 | 阈值、权重、凭据 |
| `config/USER.md` | 人的说明 + 变更记录 | 密码、token |
| `demand/*.json` | 目标、任务、探索、每天时间 | 判断规则 |
| `state/params/` | 阈值、权重、版本 | 手工偏好 |
| `store/` | 台账、候补、凭据文件 | 应进配置的东西 |

- 阈值与权重**不在**用户配置里。它们由 `tools/learn.py` 版本化更新，
  带步长上限、回放护栏与回滚；变更协议见 `docs/38-daily-workflow.md`。
- 凭据**不写进配置**：邮箱授权码用 `delivery.email.password_env` 指向的环境变量。
- 需求快照默认取 `demand/demand-2*.json` 里最新一份；命令行可显式传路径。

## 3. 每天的工作流：九个阶段

完整规范（输入/产物/通过条件/失败动作/幂等性）在 `docs/38-daily-workflow.md`。

| 阶段 | 命令 | 产物 | 通过条件 |
|---|---|---|---|
| S0 预检 | `python tools/doctor.py` | 屏幕输出 | 按配置不缺关键项 |
| S1 抓取 | `python tools/crawl.py --max-total 300 --embed --max-seconds 900 --out runs/live-<今天>` | `items.json`、`provenance.jsonl`、`raw/` | 抓到 >0 条；失败源有原因码 |
| S2 测量 | `python tools/score_items.py --items runs/live-<今天>/items.json --profile-id daily --out runs/live-<今天>/scores.jsonl` | `scores.jsonl`、`judge.log.jsonl`、`unscorable.json` | 覆盖 100%（不可判除外）；**有 `failures.jsonl` 就停** |
| S3 候选池 | `python tools/agent_select.py --build --scores runs/live-<今天>/scores.jsonl --items runs/live-<今天>/items.json --out runs/agent/<今天> --limit 30` | `packet.md`、`candidates.json`、`params.json` | 候选 1–30 条；参数版本已留痕 |
| S4 终选 | 读 `packet.md`，填 `decision.json`，`--apply` | `selection.json`、`review.md`、`review.html` | 校验通过；每条有理由 + 逐字证据；核心 ≤5、探索 ≤2 |
| S5 回灌 | `python tools/learn.py --user me --scores runs/live-<今天>/scores.jsonl --from-overrides runs/agent/<今天>/selection.json --apply` | `state/params/me/` 新版本或 pending | 失败不阻塞，但必须在 SUMMARY 说明 |
| S6 翻译摘要 | `python tools/translate_items.py ...`；`python tools/final_digest.py ...` | `translations.json`、`final.md` | 翻译失败可降级为原文标题；摘要不许编造 |
| S7 推送 | `python tools/push.py --from-final runs/final/<今天>/final.md` | 渠道消息、`store/push-ledger.jsonl` | 先 `--dry-run`；发几条记几条账 |
| S8 收尾 | `python tools/backlog.py --carry --items ... --scores ... --selection ...` | `store/backlog.jsonl` | 候补 ≤80 条、7 天过期 |

**顺序不可换**：测量在打分之前，打分在终选之前，终选在翻译之前（只翻选中的），
推送在记账之前（发成功才占 7 天窗口）。任何一步产出为空，视为失败并停止。

## 4. 判断 provider：由配置决定

```powershell
# 默认读 config/user.yaml；命令行只用于覆盖
python tools/score_items.py --provider cloud ...   # 临时用云端
```

| provider | 行为 | 需要 |
|---|---|---|
| `local` | 本机 Strands Decider，逐条问 | GPU + `judge.local_model` 权重 |
| `cloud` | `deepseek-flash`，JSON、关思考、并发 | `DEEPSEEK_API_KEY` |
| `auto` | 有本地权重用 local，否则有 key 用 cloud | 二者之一 |

- 本机默认是 `local`（2026-10-24 决定）；云端保留为可选项。
- 云端实测：223 条 = 461,179 tokens、41.3 秒、8 并发、0 失败；本地同批约 5 分钟。
- 失败：`failures.jsonl` + 退出码 1。**绝不允许把失败写成 0 分。**
- 不可判：正文为空/过短写 `unscorable.json`，不送测量、不计入分母、不算 0 分。
- **换 provider 不是换一个开关**：云端是自报分数，本地是 logit 头概率；
  同一批内容合成分 Spearman 0.698、候选池重合 18/30（2026-10-24 实测，
  `docs/39-judge-comparison.md`）。换之前先跑对照、读完报告、再决定阈值。

```powershell
python tools/compare_judges.py --scores-a <旧>.jsonl --scores-b <新>.jsonl ^
    --items runs/live-<今天>/items.json --out runs/compare/compare.md
```

细节与失败码：[`references/cloud-judgment.md`](references/cloud-judgment.md)。

## 5. 打分与阈值：确定性代码

- 打分在 `judgment/scoring.py`：价值取最高项、质量取平均、形态只降权、过时只减分。
- 阈值与权重来自 `state/params/me/` 的当前版本，`agent_select.py --build` 会打印并写进
  `runs/agent/<今天>/params.json`。**不要手改 selection.json，也不要手改参数文件**——
  要改走 `tools/learn.py`，它带步长上限、回放护栏和版本。
- 未知时间不降权；`UNKNOWN` 与 `ERROR` 不互相冒充（`docs/03`）。

## 6. 终选：唯一有判断权的一步

**先读需求，再看候选**，顺序反了就会被内容牵着走。具体规则见
`agent/skills/final-selection/SKILL.md` 与 `agent/AGENT.md`：

- 核心 ≤5、探索 ≤2；**宁可少于 7 条，空位不是失败**；
- 每条 pick / exclude 都要有理由 + 从摘要里逐字摘的 `evidence`；
- 排除理由必须指到政策条款（§2.3-1/4/5/6），指不到就不该排；
- 重大事件加 `major_event: true` + `event_reason`（改变了什么判断）；
- 分数只用来排候选，**不能用"取前 5"代替终选**。

## 7. 推送：渠道由用户决定

**不要默认某个渠道。** 先问用户平时在哪里看；内置三个渠道只是参考实现：

| 渠道 | 命令 | 行为 |
|---|---|---|
| `email` | 同上 | 一封 HTML 邮件；失败不记账 |
| `file` | 同上 | 复制到 `runs/outbox`；不发送、不记账 |
| `none` | 同上 | 只保留 final.md |

其他渠道（企业微信、Telegram、Slack…）按
[`references/adaptation.md`](references/adaptation.md) 的适配器契约自己加：
dry-run、item_id 记账、失败不记账、凭据不进配置。**不要改判断层来迁就渠道。**

先 `--dry-run` 看内容，再真发。邮箱授权码的放法与失败处理见
[`references/operations.md`](references/operations.md)。

## 8. 无人值守（06:00）

- 入口：`python tools/run_daily.py`（Windows 任务计划 `LaYA-Daily-Information`）。
- 它做三件事：注入 `DEEPSEEK_API_KEY`、按 `proxy` 配置准备代理、把 runbook 喂给 `dsh headless`。
- 日志：`runs/daily-<日期>/headless.log`、`runner.log`；退出码非 0 会发失败通知。
- 06:20 巡检：日志尾有 `SUMMARY`、`final.md` 条数、`push-ledger` 行数三处能对上。

## 9. 质量门（结束前逐条勾）

- [ ] 测量覆盖 100%（不可判条目单独列在 `unscorable.json`，没有 `failures.jsonl`）
- [ ] 候选池参数版本写进 `params.json`，SUMMARY 里有 `vNNNN / 阈值`
- [ ] 每条 pick / exclude 都有非空理由 + 逐字证据（`--apply` 已强制）
- [ ] 核心 ≤5、探索 ≤2；没有为凑数加条目
- [ ] 排除理由能指到政策条款；没有"没讲方法""只是展示"这类无效理由
- [ ] 推送条数与 `final.md`、台账三处对得上；失败的不占 7 天窗口
- [ ] `store/` 以外的状态文件没被改；`runs/` 下已有目录没被删
- [ ] 最后一行是 `SUMMARY: <一句话>`

## 10. 反模式

| 不要 | 因为 |
|---|---|
| 没做初始设置就直接抓取 | 没有需求快照，13 问的前四问是空的 |
| 用分数排序直接取前 5 | 等于删掉终选这一层，理由与证据都消失了 |
| 把失败/拿不到正文记成 0 分 | 把"没判断"伪装成"不值得" |
| 抽样测量 | 实测抽样 48 条漏掉全局前 15 里的 8 条 |
| 换 provider 后不对比就上线 | 排序会变；阈值对应的是另一套分布 |
| 为了今天好看凑满 7 条 | 空位是设计，不是失败 |
| 手改 `selection.json` / 参数文件 | 绕过校验与版本，事后无法解释 |
| 把凭据写进 `config/user.yaml` | 会被同步到别人的副本里 |
| 直接改配置不解释 | 三个月后没人知道为什么改 |

## 11. 失败矩阵

| 现象 | 先看 | 处理 |
|---|---|---|
| `MISSING_CREDENTIAL` | 环境变量 / `~/.dsh/.credentials.yaml` | 补 key；不要重试到烧完预算 |
| 本地权重缺失 | `doctor.py` | 下载权重，或 `setup.py --set judge.provider=cloud` |
| 个别条目 `OUT_OF_RANGE` / `MISSING_KEYS` | `judge.log.jsonl` 的 attempts 与 note | 已自动修一次；仍失败就按覆盖门停下 |
| `failures.jsonl` 非空 | 错误码（`NETWORK` / `HTTP_5xx` / `BAD_JSON`） | 重跑该批；连续失败看网络与模型状态 |
| 全部条目 `unscorable` | 抓取产物是否空壳 | 回 S1 查抓取；不要拿空壳打分 |
| 候选池 0 条 | 阈值、需求、抓取量 | 看 `params.json` 与分数分布；不要手动降阈值凑数 |
| 翻译大量失败 | `translate.log.jsonl` | 不阻塞；英文条目保留原文标题 |
| 推送条数与 final.md 不符 | `store/push-ledger.jsonl`、`ledger-pending.jsonl` | 只对发成功的记账；失败的补发 |
| `learn.py` 被回放护栏拦截 | 参数版本 `--history` | 正常保护；在 SUMMARY 说明未提交 |

## 12. 输出格式（每日 SUMMARY）

最后一行必须是一句话，包含：抓取条数、测量覆盖、终选数量与通道分布、
参数版本与阈值、异常。例：

```text
SUMMARY: 抓取 225 条、测量 223/225（2 条不可判）、终选 5 核心 + 2 探索并实推 7 条（A5/C2/B0），参数 v1/0.45，异常是 3 条首版已推项被 7 天台账压住后替换。
```

## 13. 改配置 / 改参数的规矩（硬性）

**改任何一项之前，先向用户解释三件事**：改什么、为什么改、会影响什么。
解释完才执行，并把"为什么"留痕。

```powershell
# 配置：工具强制要求 --why，且 --yes 才落盘
python tools/setup.py --set judge.provider=cloud --why "本机没有 GPU，云端更快" --yes

# 阈值/权重：走参数版本，不手改文件
python tools/learn.py --user me --scores ... --from-overrides ... --apply
```

- 不带 `--why`：`setup.py` 拒绝；不带 `--yes`：只打印影响，不落盘；
- 每次配置改动追加到 `config/USER.md` 的变更记录；
- 参数改动由 `learn.py` 的护栏与版本负责；**不要绕过它直接改 `state/params/`**；
- 换 provider / 改提示词 / 改阈值之前，先有对照或 7 天取舍记录（`docs/38` §9）。

## 14. 参考

| 文件 | 什么时候读 |
|---|---|
| `FIRST-SETUP.md` | 第一次配置；配完删除 |
| [`references/onboarding.md`](references/onboarding.md) | 六个问题、判断/推送选项、能力矩阵 |
| [`references/adaptation.md`](references/adaptation.md) | 换环境、换用户、没有仓库、加新推送渠道 |
| [`references/build-from-scratch.md`](references/build-from-scratch.md) | 只有技能包时，从零构建整套系统的七阶段与验收 |
| [`references/cloud-judgment.md`](references/cloud-judgment.md) | 改提示词、换模型、查云端契约与失败码 |
| [`references/operations.md`](references/operations.md) | 定时任务、凭据、代理、日志、巡检、回滚 |
| `PACKAGE.md` | 这个包有什么、没有什么、怎么安装/分发 |
| `docs/38-daily-workflow.md` | 要改流程、加阶段、定通过条件 |
| `docs/39-judge-comparison.md` | 换 provider 前的对照方法与实测数字 |
| `docs/37-daily-run.md` | 无人值守的历史与边界 |
| `agent/skills/final-selection/SKILL.md` | 终选的具体判据与反模式 |
| `agent/skills/crawl-network/SKILL.md` | 抓取失败分类与代理 |

## 15. 安装 / 打包 / 交给别人

```powershell
python tools/package_skill.py            # 生成 dist/personal-information-environment-<日期>.skill
python tools/package_skill.py --install  # 安装到 ~/.agents/skills/personal-information-environment
```

包只含手册、方法与模板，**不含** tools、模型权重、个人配置与凭据。
交给别人时：**包 + 仓库一起给**；对方按 `FIRST-SETUP.md` 配置，
配完用 `python tools/setup.py --finish` 删掉安装副本里的 `FIRST-SETUP.md`。

已经用 junction 开发时，`--finish` 会跳过源仓库模板（打包还要用），只删安装副本。
不要带 `config/user.yaml`、`config/USER.md`、`store/*.jsonl`、或任何 key。
