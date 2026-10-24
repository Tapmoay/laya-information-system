# 首次使用：把"你的信息环境"配置出来

> 这份文件回答一个问题：**另一个人拿到这套系统，第一步做什么？**
> 答案不是 `crawl.py`，是先回答六个问题，让系统知道"对你什么算有用、结果送到哪"。
> 配置完之前不要跑抓取——用别人的需求抓一遍，产出的只是别人的读物。

## 0. 先分清两样东西

| 东西 | 是什么 | 能不能单独拿走 |
|---|---|---|
| **技能**（本目录） | 操作手册：流程、质量门、判断协议、失败处理 | 能；但只有方法，没有数据和工具 |
| **系统**（仓库） | 37 个来源、抓取/测量/打分/终选/交付的代码、政策与需求 | 能；需要 Python 与依赖 |

把技能发给另一个人时，**系统也要一起给**（仓库或压缩包）。只有技能没有仓库，
等于给了一本菜谱但没有厨房。技能会告诉他怎么装厨房，但不会替他把厨房变出来。

## 1. 先问六个问题（不要替用户编）

| # | 问题 | 为什么必须有 | 没填会怎样 |
|---|---|---|---|
| 1 | 你长期在做什么？（1–3 条） | 13 问的前四问靠它定位方向 | 所有内容都"不相关" |
| 2 | 当前在推进什么？（0–2 条） | "当前任务 > 长期目标"是终选规则 | A 通道退化成泛泛而谈 |
| 3 | 想被什么陌生领域撞一下？（可空） | C 通道的探索位靠它 | 探索位只能靠通用好奇 |
| 4 | 判断放本地还是云端？ | 决定速度、成本、是否需要 GPU/API key | 跑不起来或费用失控 |
| 5 | 结果送到哪？邮箱 / 文件 / 不推 / 其它 | 决定凭据与失败处理 | 生成完没人看得到 |
| 6 | 每天愿意花多少分钟？ | 决定推送上限与摘要长度 | 读物变成新的负担 |

把答案写进 `config/USER.md`（人看）和 `config/user.yaml`（机器读），
需求写进 `demand/demand-<日期>.json`。三份文件都由 `tools/setup.py` 生成。

## 2. 装系统（一次性）

```powershell
python -m pip install -e .                   # 基础依赖：pydantic / PyYAML / jsonschema
python -m pip install torch transformers     # 只有本地判断才需要
python tools/setup.py                         # 交互式问上面六个问题
python tools/doctor.py                        # 按你的配置检查缺什么
```

`setup.py` 会打印将要写入的三份文件并要求确认。**先读一遍再按 y**。

如果这台机器没有交互终端（无人值守/容器），用非交互模式：

```powershell
python tools/setup.py --non-interactive ^
  --goals "长期方向一;长期方向二" ^
  --tasks "当前任务" ^
  --explore "结构、张力在别的领域怎么体现" ^
  --provider local --channel file --minutes 30
```

邮箱渠道还要给连接信息（授权码仍然只从环境变量读）：

```powershell
python tools/setup.py --non-interactive --goals "..." --provider cloud --channel email ^
  --email-host smtp.example.com --email-from me@example.com ^
  --email-to me@example.com --email-password-env MY_EMAIL_PASSWORD
```

## 3. 判断方式怎么选（第 4 问）

| | 本地 `local` | 云端 `cloud` | 自动 `auto` |
|---|---|---|---|
| 需要 | GPU + 本地权重 | `DEEPSEEK_API_KEY` | 二者之一 |
| 200 条耗时 | 约 5 分钟 | 约 41 秒（8 并发） | 按选中的 |
| 费用 | 0 | 按 token 计费 | 按选中的 |
| 隐私 | 内容不出本机 | 内容发给 API | 按选中的 |
| 排序 | 与云端**不同**（见 docs/39） | 与本地不同 | — |

`auto` 只看"有没有本地权重"，不猜速度。没有 GPU 但有 key → cloud；
有权重但没 key → local；两者都没有 → 拒绝启动，并告诉你去建哪个。

**换 provider 不是换一个开关。** 先跑：

```powershell
python tools/compare_judges.py --scores-a <旧>.jsonl --scores-b <新>.jsonl `
  --items runs/live-<日期>/items.json --out runs/compare/compare.md
```

看候选池重合、top-K 重合、合成分相关；再决定阈值。阈值不在这份配置里，
走 `tools/learn.py` 与 `docs/38` 的变更协议。

## 4. 推送方式怎么选（第 5 问）

| 渠道 | 需要 | 失败时 | 适合 |
|---|---|---|---|
| `email` | SMTP 主机、账号、授权码环境变量 | 整封失败，不记账，下次补发 | 换人/换公司网络最通用 |
| `file` | 无 | 不发送，不记账 | 第一次试跑、审查产物 |
| `none` | 无 | 不发送 | 只生成不打扰 |

**凭据不进配置、不进仓库。** 只写"从哪里读"：
邮箱用 `delivery.email.password_env` 指向的环境变量（或 `~/.dsh/.credentials.yaml` 的 `refs`）。

## 5. 能力矩阵：缺什么会怎样

| 缺什么 | 仍可用 | 降级/缺失 | 怎么补 |
|---|---|---|---|
| GPU | 抓取、打分、终选、推送 | 本地判断变慢 | 换 `cloud` |
| 本地权重 | 云端判断、其余全部 | 不能 `local` | 下载权重或换 `cloud` |
| API key | 本地判断、其余全部 | 不能 `cloud`；翻译失败保留原文 | 设置 `DEEPSEEK_API_KEY` |
| 代理 | 直连源（34 个） | 3 个源记 `NEEDS_PROXY` | 配 `crawl.proxy` / `proxy` |
| 聊天渠道 | 邮件/文件仍可用 | 少了即时推送 | 配 `email` 或 `file` |
| SMTP | 其他渠道全部 | 不能邮件 | 换 `file` |
| 向量模型 | 同语言去重与测量 | 跨语言判同关闭 | 下模型或关 `crawl.embed` |

**"全部功能"不是"所有依赖都必须有"**：是每条路都有明确的降级与报错，不会静默少一条。

## 6. 配置改动的规矩（必须让 agent 执行）

改任何一项之前，先向用户解释三件事：

1. **改什么**：哪个键、旧值、新值；
2. **为什么**：解决什么问题（不是"看起来更好"）；
3. **影响**：会改哪些结果、哪些历史数据不可比、失败会怎样。

然后才执行：

```powershell
python tools/setup.py --set judge.provider=cloud --why "本机没有 GPU，云端更快" --yes
```

- 不带 `--why`：工具拒绝；
- 不带 `--yes`：只打印影响，不落盘；
- 每次改动追加到 `config/USER.md` 的变更记录。

**不要**用编辑器直接改 `config/user.yaml` 再假装解释过；变更记录里没有这一行，
三个月后就没人知道为什么换成云端。

## 7. 验收：配置完能不能跑

```powershell
python tools/doctor.py                  # 按配置检查缺什么
python tools/setup.py --show            # 看配置 + 需求摘要
python tools/score_items.py --items <items.json> --profile-id smoke --out runs/smoke/scores.jsonl --limit 2
python tools/push.py --dry-run          # 看会发什么，不真发
```

四条都过，才算"这个人的环境配好了"。任何一条失败，先修配置，不要往下跑。

## 8. 把技能交给别人时，一起交代什么

1. **仓库**（或压缩包）与依赖安装方式；
2. **不要带**：`config/user.yaml`、`config/USER.md`、`store/*.jsonl`、任何 key；
3. 让对方跑 `python tools/setup.py` 生成自己的三份文件；
4. 让对方先 `--channel file --provider auto` 试跑一天，再决定邮箱/其他/云端；
5. 告诉他：本技能里的链接是仓库内相对路径，换目录后以 `MISSION.md` 所在层为仓库根。

## 9. 已知边界

- 需求快照不会自动随对话更新；目标变了要重跑 `setup.py` 或手改 demand（并留记录）。
- 推送渠道只有 email/file/none 三种；要加新渠道，按 `tools/push.py` 的接口加一个分支。
- 本地/云端的分数不可直接比较；换 provider 前必须跑对照。
- `config/user.yaml` 只放环境与个人选择，不放阈值/权重；那些在 `state/params/` 里版本化。
