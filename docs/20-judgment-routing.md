# 判断模型与"分歧升级"路由

> 你要的结构：**判断模型输出概率**；代码看有没有分歧——没分歧就按规则直接执行，有分歧就交给 AI（大模型）处理。
> 本文记录这套结构的实现、阈值与当前落地状态。

---

## 1. 数据流

```text
候选文本
  │
  ▼
判断模型（typed decision：noul / choice / score）
  │  输出概率分布 {DIRECT: 0.86, ADJACENT: 0.09, ...}
  ▼
judgment/router.py（确定性代码）
  ├─ 置信度够 & 领先够 → AUTO：直接用标签，写 MeasurementRecord(status=OK)
  └─ 否则           → ESCALATE：MeasurementRecord(status=ABSTAIN, reason=ESCALATED_TO_AGENT)
                       并把候选、问题、概率放进 escalations 队列
  ▼
Agent（大模型/人）只处理 escalations 队列
```

关键点：**分歧判定不经过模型**。模型只给概率，路由是纯函数。

---

## 2. 路由规则（`judgment/router.py`）

升级条件（任一）：

| 条件 | reason |
|---|---|
| 没有概率 | `NO_PROBABILITIES` |
| 概率非法（负数、全零、非数字） | `INVALID_PROBABILITIES` |
| 最高概率 < accept_threshold（默认 0.75） | `LOW_CONFIDENCE` |
| 前两名差距 < margin_threshold（默认 0.25） | `CLOSE_CALL` |

一个数学事实：默认阈值下 `CLOSE_CALL` 几乎不可达——top ≥ 0.75 且 second ≥ 0.5 会让总和超过 1。
它只在放宽 accept_threshold（例如 0.6）时才出现。这不是 bug，是阈值本身的性质；
测试里用放宽的阈值专门覆盖了这条分支。

概率会先归一化（和不为 1 时按比例缩放），同样的分布永远得到同样的结论。

---

## 3. 当前落地状态（2026-10-18）

| 项 | 状态 |
|---|---|
| 路由与升级逻辑 | ✅ 已实现（tests/test_router.py 覆盖九个场景） |
| 接入在线链路 | ✅ 当适配器给出 `probabilities` 时自动生效 |
| Strands Decider 2B 权重 | ✅ 已下载（LoRA + pointer head + 评测产物） |
| 基座 Qwen3.5-2B-Base | ✅ 已从魔塔社区下载（4.24GB） |
| `strands-decider` 运行时 | ⚠️ 已装（`--no-deps`），其依赖要求 `transformers>=5.15` |
| transformers 版本 | ⏳ 当前 4.57.1 不认识 `qwen3_5` 架构，升级进行中 |

踩过的坑：

1. **依赖会把你拖进 torch 2.7**：`pip install strands-decider` 默认要拉 torch≥2.7、transformers≥5.15，
   体积和风险都很大。改成 `--no-deps` 只装 wheel。
2. **基座路径要改写**：checkpoint 的 `strands_decider_config.json` 里写的是 HF 仓库名，
   离线加载会去 huggingface.co。做法是复制一份 checkpoint，把 `base_model` 改成本地绝对路径。
3. **架构不认**：transformers 4.57 报 `model type qwen3_5 ... does not recognize`。

---

## 4. 下一步

1. 等 transformers 5.x 装好，用 `load_engine` 加载本地 checkpoint，跑一条 `choice` 问题，确认能拿到**每个选项的概率**。
2. 把 decider 包成 `StrandsDeciderAdapter`（实现 `evaluate`，返回 `AdapterOutput(probabilities=...)`），
   注册为 `MODEL-PROFILE-STRANDS-DECIDER-2B`。
3. 用同一套 20 条留出集，对比"通用模型 + 提示词"与"typed decision + 概率路由"的准确率、拒绝率与升级率。
4. 只有对比通过，才把相关性测量（CAP-MEA-02）的判断依据换成 decider；通用模型退回"只做摘要与升级处理"。

---

## 5. 实测：判断模型接进链路之后（2026-10-18）

### 5.1 跑通的证据

```text
LOADED SystemOneEngine（Qwen3.5-2B-Base + LoRA + pointer head，本机 CUDA）
status: OK | label: ADJACENT
probabilities: {DIRECT: 0.231, ADJACENT: 0.439, EXPLORATORY: 0.201, UNRELATED: 0.004, INSUFFICIENT_EVIDENCE: 0.126}
route: ESCALATE | reason: LOW_CONFIDENCE | conf: 0.439 | margin: 0.208
after routing: ABSTAIN / ESCALATED_TO_AGENT
```

判断模型经 `invoke_decision` 边界调用，契约校验通过，概率进入路由层。

### 5.2 与通用模型的对比（同一留出集 20 条）

| 模型 | 总体 | 中文 | 英文 |
|---|---|---|---|
| Qwen2.5-3B-Instruct（提示词） | 65% | 80% | 50% |
| **Strands Decider 2B（typed decision）** | **60%** | **70%** | **50%** |

### 5.3 关键发现：默认阈值会让**所有**条目升级

20 条里最高概率只有 0.63，而默认 accept_threshold=0.75 → **自动 0 条，升级 20 条**。
也就是说：判断模型的概率是"保守"的，阈值必须按模型校准，不能沿用拍脑袋的 0.75。

用同一批概率做阈值扫描（自动接受部分的准确率 / 覆盖率）：

| 阈值 | 自动接受 | 覆盖率 | 自动接受准确率 |
|---|---|---|---|
| 0.60 | 2 条 | 10% | 100% |
| 0.55 | 8 条 | 40% | 87.5% |
| 0.50 | 11 条 | 55% | 82% |
| 0.40 | 17 条 | 85% | 65% |
| 0.30 | 20 条 | 100% | 60% |

结论：**阈值 0.55 左右是有意义的起点**（40% 自动、87.5% 准确，其余交给 Agent）。
但这批数据同时被用来选阈值，所以它已经不算"留出集"——下一步要新写一批数据重新验证。

### 5.4 现在的定位

- 判断模型：**已跑通**，适合当"筛子"，但当前准确率与置信度还不足以让它单独决定；
- 通用模型：继续做摘要、可信度、推荐理由（本来就是 Agent 的活）；
- 路由：默认阈值需要按模型校准后再启用，校准前的行为是"全部升级"，不会误判。

---

## 6. 动态问句与稳定性（2026-10-18 第二轮）

### 6.1 问句由需求生成

`judgment/questions.py`：目标、任务、探索方向的问句全部从 `DemandSnapshot` 现取现用，代码里没有目标文本。
政策 §2 的五项价值与 §2.1 的两条质量特征是需求无关的常量，永远问。

### 6.2 稳定性实测

| 实验 | 结果 |
|---|---|
| 同一问句集连跑两次 | **完全一致**（读出头是确定性的）|
| 13 问一次问 vs 分 3 批问 | 差值 ≤ 0.008 |
| **正文截断位置** | **差异巨大**：同一篇长文，2200 字处质量 0.53，3000 字处 0.80 |

结论：不稳定的来源不是随机性，也不是问题分批，而是**正文截断**。
修法：`judgment/text.py` 固定 3500 字上限、按句截断、先去站点导航。

### 6.3 规范化后的排序（14 条真实文章）

| # | 分数 | 价值 | 质量 | 形态 | 条目 |
|---|---|---|---|---|---|
| 1 | 0.834 | 0.82 | 0.84 | PRODUCT | 智谱 GLM-Legal |
| 2 | 0.833 | 0.89 | 0.78 | PRODUCT | 清华 PilotDeck |
| 3 | 0.802 | 0.78 | 0.82 | TECHNICAL | Akka 规格驱动 |
| 4 | 0.780 | 0.68 | 0.86 | TECHNICAL | 优必选 Thinker-WM |
| 5 | 0.772 | 0.78 | 0.77 | PRODUCT | DeepSeek 新模型 |
| 6 | 0.735 | 0.69 | 0.77 | TECHNICAL | Claude 密码学（探索位）|
| 7 | 0.635 | 0.75 | 0.54 | EVENT | QCon 持续学习（探索位）|

噪音全部沉底：GeoPT 0.586（MARKETING）、迪拜 0.475（MARKETING）、招聘 0.456（招募信号触发降权）。

与人工选择的 7 条重合 **6/7**（差一条：Aspire 13.6 被正确判定为弱相关，价值仅 0.42）。

### 6.4 还没做完

1. **权重标定**：0.45/0.55/0.15 仍是手拍，需要一份 30 条以上的人工标注集才能标定；
2. **方向迁移测试**：用另一份需求（例如独立游戏设计）重跑同一批文章，看排序是否跟着变；
3. 标定集与验证集必须分开，否则又是在噪声上拟合。

---

## 7. 云端判断（2026-10-24，ADR-0032）

每日 13 问的测量 provider 由 `config/user.yaml` 决定（本机默认本地，云端可切换；
见 ADR-0032/ADR-0033），与上面的概率路由不是同一条路径：

| | 概率路由（§1–§6） | 云端 13 问（§7） |
|---|---|---|
| 输出 | 单个 choice 的概率分布 | 12 个 0–1 分 + 形态权重 |
| 升级机制 | 阈值/margin 不够 → `ABSTAIN` 进 escalations | 调用失败 → failure；正文为空 → unscorable |
| 谁在用 | `invoke_decision` / CAP-MEA-02 单标签场景 | `tools/score_items.py` 每日链路 |

**为什么不用路由器**：云端给的是自报分数，没有 logit，`probabilities` 不是校准过的概率；
把自报 conf 当概率去算 margin，只是给一个未校准的数字套上确定性外壳。
所以云端路径把"不确定"表达在两处：失败（`failures.jsonl`）与不可判（`unscorable.json`），
而不是伪装成一个低概率。

正式适配器边界（`invoke_decision` + 注册表 + 语言校验）仍然保留，供单标签 relevance 场景使用。
每日 13 问是另一套契约，二者的对照与迁移协议见 `docs/39-judge-comparison.md`。
