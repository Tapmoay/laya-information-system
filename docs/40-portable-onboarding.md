# 40 · 可移植的初始设置：让另一个人也能用

> 2026-10-24 之前，这套系统是"一个人的脚本"：目标、模型、渠道、代理都写死在
> runbook 与代码里。换一个人用，第一件事不是跑，而是改代码。
>
> 这一轮把"个人环境"从代码里拆出来，做成三份文件 + 一个设置工具：
> `config/user.yaml`、`config/USER.md`、`demand/demand-<日期>.json`，
> 入口是 `python tools/setup.py`。操作手册见
> [`agent/skills/personal-information-environment/references/onboarding.md`](../agent/skills/personal-information-environment/references/onboarding.md)。

---

## 1. 为什么"初始文档"是必需件

13 问里有四个方向槽（主方向 / 第二方向 / 探索 / 当前任务），它们的文字直接来自需求快照。
没有需求快照，四个槽是空的，测量结果只能回答"这篇内容本身如何"，
无法回答"对你有没有用"。

所以第一件事不是抓取，而是回答六个问题：

1. 长期目标（1–3 条）；
2. 当前任务（0–2 条）；
3. 跨领域探索方向（可空）；
4. 判断方式（本地 / 云端 / 自动）；
5. 推送方式（邮箱 / 文件 / 不推）；
6. 每天愿意花多少分钟。

答案落成三份文件：

| 文件 | 性质 | 谁读 | 是否进 git |
|---|---|---|---|
| `config/user.yaml` | 机器配置 | 代码 | **否**（.gitignore） |
| `config/USER.md` | 初始文档 + 变更记录 | 人 + agent | **否** |
| `demand/demand-<日期>.json` | 需求快照 | 测量与终选 | 是（不含凭据） |

`config/user.example.yaml` 是模板，进 git；`config/user.yaml` 是本机实际配置，不进。

---

## 2. 三层配置：什么放哪

| 层 | 放什么 | 不放什么 |
|---|---|---|
| 个人配置 `config/user.yaml` | provider、模型、渠道、抓取预算、代理 | 阈值、权重、凭据 |
| 需求快照 `demand/*.json` | 目标、任务、探索、每天时间 | 判断规则 |
| 判断参数 `state/params/` | 阈值、权重、版本、历史 | 手工偏好 |

**为什么阈值不放进用户配置**：阈值是"学习结果"，有版本、有步长上限、有回放护栏。
放进用户配置等于给每个人一把没有刻度的旋钮：改了以后，分不清是阈值变了还是内容变了。
要改阈值，走 `tools/learn.py`，见 [docs/38](38-daily-workflow.md) §9。

**为什么凭据不放进配置**：配置会被复制、发给人、贴进 issue。
凭据只允许"指向哪里"：环境变量、`~/.dsh/.credentials.yaml`。

---

## 3. 判断方式：本地 / 云端 / 自动

| | local | cloud | auto |
|---|---|---|---|
| 需要 | GPU + 本地权重 | `DEEPSEEK_API_KEY` | 二者之一 |
| 223 条实测 | 约 5 分钟 | 41.3 秒 / 461,179 tokens | 按选中的 |
| 费用 | 0 | 按 token 计费 | 按选中的 |
| 隐私 | 不出本机 | 内容发给 API | 按选中的 |

- **本机默认 local**（2026-10-24 用户决定）；云端保留为可切换能力。
- `auto` 只按"有没有本地权重"决定，不猜速度；两者都没有时拒绝启动，并给出下一步。
- 换 provider 会改变排序与候选池：先跑 `tools/compare_judges.py`，
  读 [docs/39](39-judge-comparison.md)，再决定阈值。**阈值不会自动跟着换。**

---

## 4. 推送渠道：不是固定的

原流程第 6 步写死一个渠道。换一个人，他可能只有邮箱，
或者只想先看文件。所以推送做成渠道抽象，入口统一为 `tools/push.py`：

| 渠道 | 发送方式 | 记账 | 失败时 |
|---|---|---|---|
| `email` | 一封 HTML 邮件 | 按 item_id，整封 | 整封失败，不记账，下次补发 |
| `file` | 复制到 `runs/outbox` | **不记账** | 不发送；产物仍在 |
| `none` | 不发送 | 不记账 | — |

**为什么 file 不记账**：记账 = "用户已经看过了，7 天内别再推"。
文件渠道只是生成产物，不能替用户确认他读过。宁可下次再推，也不替他假设。

**为什么按 item_id 记账**：`final.md` 按通道 A/B/C 分组，`ledger-pending.jsonl`
按核心/探索排列；两者顺序不保证一致，按行号会把 A 的账记到 B 上。

---

## 5. 改配置的协议：先解释，再落盘

用户的原话：**"有些参数有些东西也可以改的，但是改之前首先要向用户解释。"**

协议写成三件事：

1. **改什么**：哪个键、旧值、新值；
2. **为什么**：解决什么问题（不是"看起来更好"）；
3. **影响**：哪些结果会变、哪些历史数据不可比、失败会怎样。

工具层把它变成可执行的动作：

```powershell
python tools/setup.py --set judge.provider=cloud --why "本机没有 GPU，云端更快" --yes
```

- 不带 `--why` → 拒绝；
- 不带 `--yes` → 只打印影响，不落盘；
- 每次改动追加到 `config/USER.md` 的变更记录。

**为什么要有工具层**：只写"请解释"的规则，agent 可能跳过；
把它变成"没有 `--why` 就拒绝"，才和抓取失败留痕一样，是可检查的。

---

## 6. 能力矩阵：缺什么会怎样

| 缺什么 | 仍可用 | 降级/缺失 | 怎么补 |
|---|---|---|---|
| GPU | 抓取、打分、终选、推送 | 本地判断变慢 | 换 `cloud` |
| 本地权重 | 云端判断、其余全部 | 不能 `local` | 下载或换 `cloud` |
| API key | 本地判断、其余全部 | 不能 `cloud`；翻译失败保留原文 | 设置 key |
| 代理 | 34 个直连源 | 3 个源记 `NEEDS_PROXY` | 配 `crawl.proxy` |
| 聊天渠道 | 邮件/文件仍可用 | 少了即时推送 | 配 `email` 或 `file` |
| SMTP | 其他渠道全部 | 不能邮件 | 换 `file` |
| 向量模型 | 同语言去重与测量 | 跨语言判同关闭 | 下模型或关 `crawl.embed` |

"全部功能"不是"所有依赖都必须有"：是每条路都有明确的降级与报错，不会静默少一条。
`tools/doctor.py` 只检查**你配置的那条路**，缺别的渠道只提示、不算失败。

---

## 7. 把技能交给别人

分发单位是 **技能 + 仓库**：

- 技能（`agent/skills/personal-information-environment/`）是操作手册；
- 仓库是数据与工具（37 个来源、抓取/测量/打分/终选/交付代码）。

**不要带**：`config/user.yaml`、`config/USER.md`、`store/*.jsonl`、
任何 key。让对方跑 `python tools/setup.py` 生成自己的三份文件。

先 `--channel file --provider auto` 试跑一天，再决定邮箱/其他/云端。
这样第一次失败最多是"文件没生成"，而不是"消息发错人"。

---

## 8. 设计取舍

1. **配置用 YAML，不用数据库**：人要能读、能 diff、能随仓库发给别人；
   密钥与状态才放 `store/`。
2. **需求快照是文件，不是对话**：对话会消失，文件能进 `runs/` 与 `params.json`，事后可复算。
3. **`auto` 只看权重存在性**：不猜 GPU 型号与速度；猜错比不猜更糟。
4. **不改判断参数的位置**：配置文件只解决"环境与个人选择"，判断规则的变更仍走参数版本。
5. **推送失败不阻塞其余阶段**：产物先生成，推送是最后一步；失败只影响"送达"，不影响"判断"。

---

## 9. 已知缺口

1. **需求不会自动更新**：目标变了要重跑 `setup.py` 或手改 demand，并留下记录。
2. **渠道只有四种**：加新渠道要在 `tools/push.py` 里加一个分支，没有插件机制。
3. **配置没有 schema 迁移**：`schema_version` 目前只有 1；将来加字段要写迁移。
4. **没有交互式校验回显**：`setup.py` 打印摘要后确认，但没有逐项回显"你选这个会缺什么"。
5. **`auto` 不测 GPU 可用性**：有 CUDA 标记但驱动坏了时，会先选 local 再失败；
   `doctor.py` 会提前发现，但 `score_items` 不会自动回退。
6. **邮件只支持单封**：条目多时邮件很长；没有按通道拆多封。

---

## 10. 验证

```powershell
python tools/setup.py --non-interactive --config <临时> --goals "A;B" --provider local --channel file
python tools/setup.py --config <临时> --set judge.provider=cloud --why "测试" --yes
python tools/doctor.py
python -m pytest tests/test_user_config.py tests/test_setup.py tests/test_push.py -q
python tools/check.py
```

对应测试：

- `tests/test_user_config.py`：默认值、拒绝凭据、provider 选择、需求解析；
- `tests/test_setup.py`：写三份文件、无 `--why` 拒绝、无 `--yes` 预览、变更记录、`--finish`；
- `tests/test_push.py`：邮件发送（假 SMTP）、dry-run、按 item_id 记账、file 不记账；
- `tests/test_package_skill.py`：分发包内容、manifest、安装不带个人配置。

---

## 11. 打包与"一次性配置文档"

`python tools/package_skill.py` 生成 `dist/personal-information-environment-<日期>.skill`：

- 包内是**手册**：`SKILL.md`、`FIRST-SETUP.md`、`PACKAGE.md`、`references/`、`docs/38–40`、`config/user.example.yaml`；
- 包内没有 `tools/`、模型权重、`config/user.yaml`、`store/`、任何凭据；
- 带 `manifest.json`（文件清单 + sha256）。

`--install` 把同一份内容安装到 `~/.agents/skills/personal-information-environment/`。
安装副本与仓库分离：仓库是源，安装副本是"这个人正在用的那一份"。

**`FIRST-SETUP.md` 是一次性的**：它教 agent 怎么问六个问题、怎么配置、怎么第一次试跑；
配置完成后必须删除。删除只删**安装副本**，不删仓库模板（下次打包还要用）：

```powershell
python tools/setup.py --finish
```

`--finish` 会先确认配置、需求与 `config/USER.md` 都存在，再删安装副本里的
`FIRST-SETUP.md`；如果目标指向仓库（junction），它会跳过并提示安装副本。

这样下一次会话只读 `SKILL.md`，不会再走"第一次配置"的流程；而包本身仍然完整，
可以发给下一个人。
