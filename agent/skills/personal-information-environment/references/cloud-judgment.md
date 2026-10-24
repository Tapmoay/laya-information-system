# 云端判断契约与换模型协议

> 这份文件回答四个问题：云端判断**测什么**、**怎么失败**、**多少钱**、**换模型之前必须做什么**。
> 它对应代码 `judgment/cloud_judge.py`，决策记录是 `docs/05` 的 ADR-0032。

## 1. 它测什么（而且不测什么）

一次调用 = 一条内容 × 13 个问句：

| 组 | 问句 | 输出 |
|---|---|---|
| 方向槽（4） | `primary` `secondary` `exploration` `task` | 每个 0–1 |
| 政策价值（5） | `value_task` `value_capability` `value_decision` `value_exploration` `value_environment` | 每个 0–1 |
| 质量（2） | `quality_testable` `quality_increment` | 每个 0–1 |
| 形态（1） | `form`：TECHNICAL / PRODUCT / EVENT / RECRUIT / MARKETING | 五个权重，归一化到 1 |
| 招募（1） | `recruit` | 0–1 |

**不测**：总分、排序、推荐、通道归属、值不值得看。这些由
`judgment/scoring.py` + Agent 终选决定。模型越界给总分，解析器不会采用（没有这个键）。

提示词里写死了三件事，改之前先想清楚：

1. 0 / 0.5 / 1 的锚点，避免所有题都给 0.5；
2. "只依据原文，不用外部知识补事实"；
3. "不评价值不值得推荐"。

## 2. 请求与响应

```json
{
  "model": "deepseek-flash",
  "messages": [{"role": "system", ...}, {"role": "user", ...}],
  "temperature": 0,
  "max_tokens": 900,
  "response_format": {"type": "json_object"},
  "thinking": {"type": "disabled"}
}
```

要求模型只输出：

```json
{
  "scores": {"primary": 0.12, "secondary": 0.05, "...": 0.0},
  "form": {"TECHNICAL": 0.85, "PRODUCT": 0.10, "EVENT": 0.02, "RECRUIT": 0.01, "MARKETING": 0.02},
  "note": "可选：一句话说明哪里证据不足"
}
```

落盘的行与本地 provider 完全同形：

```json
{
  "item_id": "item-xxx",
  "profile_id": "daily",
  "scores": {"primary": {"noul": 0.12}, "...": {}, "form": {"probabilities": {"TECHNICAL": 0.85}}},
  "judge": {"provider": "cloud", "model": "deepseek-flash", "attempts": 1, "ms": 1830,
            "usage": {"prompt_tokens": 1840, "completion_tokens": 208}, "note": ""},
  "provenance": {"source_id": "...", "prepared_chars": 2410, "...": "..."}
}
```

`judge` 是新增的审计字段，不影响下游；`profile_id` 仍是命令行给的画像名。

## 3. 解析规则（严格，宁可失败也不猜）

- 允许外层 `scores` / `questions` / `answers`，也允许平铺；键名必须齐全。
- 数值接受数字或数字字符串；`bool`、`null`、非有限值一律拒绝。
- 必须在 `[0, 1]`；**不做单位猜测**（返回 42 不会自动当 0.42）。
- 形态五个标签缺一不可；权重和 ≤0 拒绝；和不为 1 时**归一化**（模型经常给出 2/1/0/0/0）。
- 允许 ```json 代码块包裹；拒绝数组、纯文本、空响应。
- 坏输出会带着上一次的坏 JSON 再问一次（repair）；再失败才记 failure。

## 4. 失败码

| code | 含义 | 会不会重试 |
|---|---|---|
| `MISSING_CREDENTIAL` | 找不到 key | 不重试，直接停 |
| `HTTP_401/403` | key 无效/无权限 | 不重试 |
| `HTTP_400` | 请求非法（模型名、参数） | 不重试 |
| `HTTP_429` | 限流 | 重试，指数退避 |
| `HTTP_5xx` | 服务端错误 | 重试 |
| `NETWORK` | 连接/超时 | 重试 |
| `BAD_JSON` / `MISSING_KEYS` / `OUT_OF_RANGE` / `BAD_FORM` | 模型输出不合约 | 进入 repair，再失败才记 |
| `ADAPTER_ERROR` | 意外异常 | 记失败，不吞 |

失败写 `failures.jsonl`，退出码 1（默认 `--max-fail-rate 0`）。
**不要用 0 分或均值填补失败**——那会把"没判断"伪装成"不值得"。

## 5. 成本与时间（2026-10-24 实测）

| 项 | 数值 |
|---|---|
| 30 条 | 59,918 tokens，8.5 秒（6 并发） |
| 223 条 | 461,179 tokens，41.3 秒（8 并发） |
| 单条平均 | 约 2,070 tokens（prompt 1,864 / completion 204） |
| 本地对照 | 同批 223 条约 5 分钟，占 6GB 显存 |

按 DeepSeek 的公开价自己乘一下即可。这个量级远低于人工筛选的时间成本，
但**金额不是决策依据**：换 provider 是否值得，看的是排序质量与覆盖，不是省了几毛钱。

## 6. 换模型/换 provider 之前必须做的三件事

### 6.1 先对照，不先上线

```powershell
# A = 现在在用的；B = 候选
python tools/compare_judges.py --scores-a runs/live-<今天>/scores.jsonl ^
    --scores-b runs/cloud-full/scores.jsonl --items runs/live-<今天>/items.json ^
    --out runs/compare/compare.md
```

报告给的是**描述性统计**，不是"谁更准"：

- 逐问句均值差、平均绝对差、Spearman；
- 合成分的 Spearman、top-5/10 重合、形态一致率；
- 各阈值下的过线率，以及"保持入选数量不变"时新 provider 的阈值。

### 6.2 读懂"不同"意味着什么

2026-10-24 的第一次对照（223 条，本地 vs 云端）：

| 指标 | 结果 |
|---|---|
| 合成分 Spearman | 0.698 |
| 候选池重合（30 条） | 18/30 |
| 形态一致率 | 75.8% |
| 云端更低 | `value_task` −0.327、`quality_increment` −0.235、`value_exploration` −0.212 |
| 云端更高 | `exploration` +0.209 |
| 当天 5 条核心终选 | 云端排名 2 / 8 / 11 / 12 / 15，全部保留 |
| 当天 2 条探索终选 | 云端排名 66 / 91，其中 1 条不在候选池 |

结论：**核心位大体稳定，探索位与候选池尾部明显变化。**
这不是"云端更差"，而是"云端在按另一套尺度回答价值问题"。
没有人工标注时，只能承认不同，不能宣称更好。

### 6.3 再决定阈值与上线方式

三个可接受的选项，按风险从低到高：

1. **影子运行**（推荐）：云端分数照常落盘，但当天终选仍用本地分数；连续 2–3 天读
   `compare_judges` 的候选池重合与核心位保留情况，再切。
2. **切换但保留阈值**：用云端分数、阈值仍 0.45；接受候选池缩小与排序变化，
   两周复盘时看漏检与误选。
3. **切换并重标阈值**：只有在有 30 条以上人工标签、或至少连续 7 天真实取舍记录时才做；
   用 `tools/learn.py` 走参数版本，不手改。

**任何选项都要留下一条记录**：改了什么、依据哪份对照、什么时候回看。

## 7. 改提示词的规矩

- 只改"问句的措辞/评分锚点/输出示例"，不改问句数量与键名；键名变了下游会全线失败。
- 改完先跑 `python -m pytest tests/test_cloud_judge.py -q`，再用 30 条做 A/B：
  新旧提示词各跑一遍，比较逐问句均值与 top-10 重合。
- 不要为了"让分数接近本地"而调提示词。目标是让分数**对问句诚实**，
  不是让两个模型长得一样。对齐分布是阈值的事，不是提示词的事。
- 一次只改一处；同时改提示词和阈值，之后分不清差异是谁造成的。

## 8. 已知缺口

1. **自报分数没有校准**：没有概率头，`form` 的权重也不是 softmax。别把 0.7 当 70% 把握。
2. **没有人工标注集**：对照只能证明"不同"，不能证明"更准"。
3. **单次调用不稳定**：`temperature=0` 也不保证逐位相同。实测 5 条连跑两次：
   60 个问句平均绝对差 0.026、最大 0.75；形态首选项 4/5 一致。
   最大翻转发生在"建筑奖项页"的 `MARKETING ↔ RECRUIT`——一个只是降权、一个是硬排除。
   所以边界条目要回原文看，不要只看信号行。
4. **长文截断**：仍沿用 `judgment/text.py` 的 3500 字；截断位置会改变分数（实测过）。
5. **未接入正式适配器边界**：每日测量直接调用 provider，没走 `invoke_decision` 与
   `models/registry.yaml`。这是有意的取舍（13 问不是单标签 relevance），
   但换模型时不能依赖注册表的 ACTIVE/语言校验，只能靠这里的对照协议。
