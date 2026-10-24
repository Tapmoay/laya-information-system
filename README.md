# LaYA · 个人信息环境

> 一个**个人**的信息决策系统：每天抓一批内容，用固定的 13 个问句测量它，
> 按三条通道挑出该看的几条，并把**为什么选它**写清楚。
>
> 它不替你理解，只负责把值得看的东西放到位——**一个指针，不是一份饲料**。

## 它解决什么问题

推荐算法给的是"你会点开的东西"，不是"你该知道的东西"。
自建系统容易走另一个极端：为了不漏，什么都收，最后被自己的完备性压垮。

这个项目选的是第三条路：

- **承认会漏。** 候补池有上限与过期时间——那是设计，不是缺陷。
- **控制关系，不是控制信息。** 每条都必须说得清属于哪条通道、为什么选、可能改变什么判断。
- **规则必须能追溯到信念。** 新规则追不到 [MISSION.md](MISSION.md) 五条之一，就不加。

## 它怎么工作

```text
信息源（列表页 + 路径正则，每条内容可回溯到抓取那一刻）
        │
        ├─ 规范化 · 去导航 · 抽配图 · 抽发布时间
        │
        ├─ 去重：URL / 内容摘要 / 事件锚点 / 跨语言向量
        │      再叠加：已推送台账 + 候补池
        │
        ├─ 测量：固定 13 问（4 方向槽 + 5 价值 + 2 质量 + 形态 + 招募）
        │      判断模型只产出测量，不做决策
        │
        ├─ 打分：确定性代码。方向门槛 → 加权 → 降权 → 过时降权
        │
        ├─ 终选：Agent 从候选池挑 5+2，每条必须给理由 + 逐字证据
        │
        └─ 交付：通道 A/B/C 分组 · 中文标题 · AI 摘要 · 原文链接 · 暂存清单
```

### 三条通道

| 通道 | 要解决什么 | 判据偏重 |
|---|---|---|
| **A 目标驱动** | 把正在做的事推进下去 | 任务价值、主方向 |
| **B 变化监测** | 别漏掉会影响未来判断的外部变化 | 环境价值 |
| **C 受控探索** | 扩大"不知道自己需要什么"的接触面 | 探索价值、审美与表达 |

## 三条硬约束

1. **测量不等于决策。** 模型只产出测量记录，选择由确定性规则做，终选由 Agent 做。
2. **未知必须可表达。** `UNKNOWN` 与 `ERROR` 不得互相冒充；
   把"不知道"当成"很旧"或当成"很新"都是编造。
3. **理由必须指向"它能帮我做什么"。** 说不出"这条很好"以外的东西，就允许少于上限。

## 快速开始

```powershell
python -m pip install -e .
python tools/setup.py          # 问六个问题：目标/任务/探索/判断方式/推送方式/时间
python tools/doctor.py         # 按你的配置检查缺什么
python tools/check.py          # 五项自检
```

完整流程见 [QUICKSTART.md](QUICKSTART.md)：抓取 → 打分 → 查重 → 终选 → 摘要 → 推送。

## 判断方式：本地或云端

13 问的测量可以跑在本地，也可以走云端 API；由 `config/user.yaml` 决定：

| provider | 需要 | 223 条实测 |
|---|---|---|
| `local` | GPU + 本地权重 | 约 5 分钟 |
| `cloud` | `DEEPSEEK_API_KEY` | 约 41 秒 / 46 万 tokens / 8 并发 |
| `auto` | 二者之一 | 按选中的 |

换 provider 会改变排序与候选池，**阈值不会自动跟着换**：
先跑 `tools/compare_judges.py`，再决定是否改参数。见 [docs/39](docs/39-judge-comparison.md)。

## 推送：渠道由你决定

`tools/push.py` 内置 `email` / `file` / `none`；其他渠道（企业微信、Telegram、Slack…）
按 [agent/skills/personal-information-environment/references/adaptation.md](agent/skills/personal-information-environment/references/adaptation.md)
的适配器契约自己加：dry-run、按 item_id 记账、失败不记账、凭据不进配置。

## 目录

| 目录 | 内容 |
|---|---|
| `ingest/` | 抓取、编码嗅探、链接发现、内容规范化、去重 |
| `judgment/` | 问句、打分、参数、事件聚簇、时效、通道、云端判断 |
| `selection/` | 确定性选择器（规则表 + 引擎） |
| `delivery/` | 交付对象装配 |
| `tools/` | 命令行：抓取、打分、终选、交付、复盘、打包 |
| `store/` | 已推送台账、候补池、事实库（运行时数据不进 git） |
| `docs/` | 决策记录、架构、工作流与实测 |
| `agent/` | 终选角色边界 + 可分发技能 |
| `contracts/` | 对象契约 + 夹具 |

## 状态

- 40 个能力契约中 26 个 `IMPLEMENTED`；23 个对象契约、76 个契约夹具、593 个测试
- 本地与云端判断都有实测；云端对照见 [docs/39](docs/39-judge-comparison.md)
- 已知缺口写在每份 docs 的最后一节，不在 README 里吹完整

## 文档

| 文件 | 地位 |
|---|---|
| [MISSION.md](MISSION.md) | 主旨（为什么） |
| [information-policy.md](information-policy.md) | 政策模板（使用前改成你自己的） |
| [docs/17-architecture.md](docs/17-architecture.md) | 全貌地图 |
| [docs/38-daily-workflow.md](docs/38-daily-workflow.md) | 每日工作流规范 |
| [docs/40-portable-onboarding.md](docs/40-portable-onboarding.md) | 可移植性与初始设置 |
| [agent/skills/personal-information-environment/SKILL.md](agent/skills/personal-information-environment/SKILL.md) | 日常运营技能 |

## 许可

本仓库用于个人研究与工程实践。第三方内容、模型与 API 的使用请遵守各自许可与服务条款。

