# 第一次配置（FIRST-SETUP）

> **这是一份一次性文档。** 按它把系统配好之后，最后一步是**删除本文件**。
> 日常流程看 `SKILL.md`；本文件只负责"从零到能跑"。

## 0. 先确认你在哪

- 当前目录是**仓库根**（含 `MISSION.md`、`tools/`、`judgment/`）？
  - 是：继续。
  - 不是：问用户仓库在哪，`cd` 过去。**不要**在没有仓库的目录里假装能跑。
- 用户拿到的是"技能 + 仓库"还是只有这份技能？
  - 只有技能：你拿到的是方法与契约，先向用户要仓库；不要凭空实现 37 个源。
- **不要直接抓取。** 没有需求快照就抓，产出的是别人的读物。

## 1. 问六个问题（不要替用户编）

| # | 问题 | 为什么 |
|---|---|---|
| 1 | 长期在做什么？（1–3 条） | 13 问的前四问靠它定位方向 |
| 2 | 当前在推进什么？（0–2 条） | "当前任务 > 长期目标"是终选规则 |
| 3 | 想被什么陌生领域撞一下？（可空） | C 通道探索位靠它 |
| 4 | 判断放本地还是云端？ | 决定速度、成本、依赖 |
| 5 | 结果送到哪？邮箱 / 文件 / 其它？ | 由用户决定，不要默认某个渠道 |
| 6 | 每天愿意花多少分钟？ | 决定推送上限与摘要长度 |

详细选项与能力矩阵见 `references/onboarding.md`。用户说不清时，给出默认值并解释后果，
但**不要替他决定目标和渠道**。

## 2. 写三份文件

```powershell
python tools/setup.py
```

交互式会写：

- `config/user.yaml`：provider、渠道、抓取预算、代理；
- `config/USER.md`：给人看的初始文档；
- `demand/demand-<日期>.json`：需求快照。

非交互环境：

```powershell
python tools/setup.py --non-interactive ^
  --goals "目标一;目标二" --tasks "当前任务" --explore "跨领域线索" ^
  --provider local --channel file --minutes 30
```

**第一次先选 `file` 或 `none`**，确认产物后再接邮箱/其它渠道。

## 3. 把初始文档读回给用户

打开 `config/USER.md`，逐项确认：目标、任务、探索、判断方式、推送方式。
有错就重跑 `setup.py`，或用：

```powershell
python tools/setup.py --set judge.provider=cloud --why "本机没有 GPU" --yes
```

**改任何一项之前先解释**：改什么、为什么、会影响什么。工具的 `--why/--yes` 是这条规则的执行层。

## 4. 检查环境

```powershell
python tools/doctor.py
```

只检查用户选的那条路：

- 本地判断缺权重/GPU → 建议换云端，或下载权重；
- 云端缺 key → 建议换本地，或申请 key；
- 邮箱缺凭据 → 建议先用 `file`，不要让整个流程跑不起来。

缺什么就修什么，不要跳过。

## 5. 第一次试跑（不要全量）

```powershell
python tools/score_items.py --items <一份 small 的 items.json> --profile-id smoke --out runs/smoke/scores.jsonl --limit 2
python tools/push.py --dry-run
```

通过标准：

- 测量有分数，失败/不可判是**显式**的；
- 终选能生成 `packet.md`；
- 推送 dry-run 能打印内容；
- 台账只在真发成功后增加。

## 6. 删除本文件（配置完成的标志）

如果读的是**安装副本**（`~/.agents/skills/personal-information-environment/`）：

```powershell
python tools/setup.py --finish
```

它会确认配置、需求与初始文档存在，然后删除安装副本里的 `FIRST-SETUP.md`。

如果读的是**仓库源**：不要删仓库里的模板（下次打包还要用）；
安装一份副本，对副本执行 `--finish`。没有仓库时，手动删掉你手里这份
`FIRST-SETUP.md` 即可。

删除后，下一次会话只读 `SKILL.md` 与日常参考，不再走一次性配置流程。

## 7. 交给日常流程

现在可以按 `SKILL.md` 的九阶段跑每日链路了：
抓取 → 测量 → 候选池 → 终选 → 回灌 → 翻译摘要 → 推送 → 收尾。

## 完成清单

- [ ] 六个问题都来自用户，不是我编的
- [ ] 三份文件存在，`config/USER.md` 已读回确认
- [ ] `doctor.py` 按用户选的 provider/channel 通过（或已知缺口有解释）
- [ ] 第一次用 `file`/`none` 跑通，用户看过产物
- [ ] 失败路径测过：拿掉 key/断网/空正文，系统说"失败"而不是"0 分"
- [ ] `FIRST-SETUP.md` 已从安装副本删除
- [ ] 告诉用户：改配置要先解释、`--why/--yes`、变更记录在哪
