# 错误与未知语义（Error & Unknown Semantics）

> **交付物对应**：v0.3 §3.2、§10 `WP-R1`（`error-and-unknown-semantics.md`）、§4.1。
> **本文地位**：状态语义是契约的一部分。任何实现如果与本文冲突，以本文为准；
> 需要改语义时先改本文并留版本记录，不在代码里临时发明新状态。

---

## 1. 为什么必须单独成文

v0.3 反复出现同一类失败模式：**把"不知道"伪装成"知道"**。

- 抓取超时被记成"没有相关内容"；
- 模型置信度低但仍然给出标签；
- 翻译没做，就直接当原文用；
- 推送失败，但运行结束写 `success`。

这些都不是代码 bug，而是**语义 bug**：状态域不够用时，实现会用空字符串、`null`、`0`、`false`
去顶替，之后没有任何下游能区分"确实没有"和"没取到"。本文把状态域固定下来，
并规定它们如何传播。

---

## 2. 状态域

状态域 = `{OK}` ∪ 七个非 OK 状态。`OK` 是成功值，其余七个是**确定的非成功状态**（ADR-0006）。

| 状态 | 一句话判据 | 典型场景 | 不适用于 |
|---|---|---|---|
| `OK` | 能力完成，输出满足契约 | 抓取成功且字段齐全 | — |
| `MISSING` | **上游没有提供**这个输入 | 本轮没有提供 `FeedbackRecord` | "提供了但读不出来" |
| `UNKNOWN` | 尝试过，但**证据不足**或来源不可得 | 原文打不开；证据互相冲突；语种无法确定 | 输入本身就缺失 |
| `NOT_APPLICABLE` | **定义上不适用** | 政策 PDF 没有 `published_at`；纯代码仓库没有"语种" | "暂时判断不了" |
| `INVALID` | 提供了值，但**违反契约** | URL 不合法；枚举越界；时间戳格式错 | 网络失败、模型异常 |
| `ABSTAIN` | 能力正常，**主动选择不下结论** | 超出已验证语言范围；风险过高；证据不足且策略要求保守 | 程序崩溃 |
| `ERROR` | **执行失败** | 超时、显存不足、适配器不兼容、HTTP 500 | 执行成功但没把握 |
| `PARTIAL` | 完成了**可分离的一部分** | 只取到前 2000 字；只翻译了前 3 段 | 全部失败 |

### 2.1 两条硬规则

1. `null`、空字符串、`0`、`false` **不得**用来表示任何状态。字段缺失时用 `MISSING`，
   判断不出来时用 `UNKNOWN`。
2. `ERROR` 与 `UNKNOWN` **不得互相冒充**。执行失败不是"信息不足"，信息不足也不是"系统坏了"。

---

## 3. 四个最容易混淆的边界

### 3.1 `MISSING` vs `UNKNOWN`

- `MISSING`：上游**该给没给**（产出方违约，或可选输入未选）。责任在编排。
- `UNKNOWN`：上游给了，也尝试判断了，但**结果不可得**。责任在证据。

> 例：本轮没有配置视觉通道 → `aesthetic_match` 是 `MISSING`（我们没要求它）。
> 配置了视觉通道，但作品页 403 → `UNKNOWN`（我们要求了，取不到）。

### 3.2 `UNKNOWN` vs `ABSTAIN`

这是最关键的一对。判据不是"有没有把握"，而是**不确定性来自哪里**：

- 不确定性来自**外部证据**（原文缺失、来源不可达、证据冲突）→ `UNKNOWN`。
- 输入完整、但结论会**超出该能力已验证的适用范围**（未验证的语种、未验证的领域、
  超出上下文长度），或风险高到不宜给结论 → `ABSTAIN`。

> 例：一篇文章是土耳其语，而该测量能力只验证过英语和简体中文 → `ABSTAIN`，
> 理由码 `OUT_OF_VALIDATED_LANGUAGE_SCOPE`，**不是** `UNKNOWN`。文章的文本是完整的，
> 未知的是我们自己的能力边界。

### 3.3 `ERROR` vs `INVALID`

- `INVALID`：输入在进入能力**之前**就不合契约（可在调用前检出，代价低）。
- `ERROR`：输入合法，执行过程中失败（超时、崩溃、依赖缺失）。

> 例：`published_at: "上周二"` → `INVALID`。
> `published_at` 格式正确但时区数据库版本不认这个时区 → `ERROR`。

### 3.4 `PARTIAL` vs `UNKNOWN`

`PARTIAL` 必须能**指出完成了哪部分、缺哪部分**，否则等价于 `UNKNOWN`。
"抓了一半但不知道抓了哪一半"是 `ERROR`，不是 `PARTIAL`。

---

## 4. 传播规则

### 4.1 层内

1. 能力失败时**原样保留**状态，不得在返回前改写成更"好看"的状态。
2. 一个候选的某个测量失败，不影响同一候选的其他测量继续执行。

### 4.2 跨层

3. 下游可以把 `ERROR` 降级为 `UNKNOWN`，但**必须**写 `degraded_from: "ERROR"`
   与 `degrade_reason`。没有这条记录的降级视为数据损坏。
4. 下游不得把 `UNKNOWN` 升级为 `OK`。任何"补全"都必须是新增衍生对象（如翻译），
   并带 `derived_from_refs`。

### 4.3 阶段与运行

5. 单个候选 `ERROR` → 该候选本轮不可交付，记录在运行清单；**不**使整个 run 失败。
6. 来源级系统性失败（同一个 connector 全挂）→ 该阶段 `PARTIAL`，run 仍可继续。
7. 契约错误、政策版本不匹配、锁冲突 → 阶段 `FAILED`，run 终止。
8. `run_status` 与 `delivery_status` **必须分开记录**（v0.3 §2.3）。推送失败不改写 run 成功。

---

## 5. 与选择（Selector）的关系

选择器是唯一有权把测量变成决定的地方，因此状态在这里最容易出事故。规则：

1. **`UNKNOWN` 与 `ABSTAIN` 不是负面证据。** 不能因为测量缺失就把候选判为 `UNRELATED`。
2. 候选的**全部关键测量**都是 `ABSTAIN`/`UNKNOWN` 时，准入结论为 `ABSTAIN`：
   不进入主交付通道；是否进入 `MONITOR`（仅提示"存在但未核实"）由策略配置决定。
3. 候选存在 `ERROR` → 本轮**不可交付**，但必须在报告里出现，不能表现为"这个世界没有这条信息"。
4. **空结果是合法结果。** 没有合格候选时输出空 `DeliveryObject`，不补位、不降标准。
5. 选择器**不得**因为"凑不满容量"而把 `ABSTAIN` 当成通过。

---

## 6. 序列化要求

每个带状态的字段必须是一个对象，而不是裸字符串：

```json
{
  "status": "UNKNOWN",
  "reason_code": "SOURCE_UNREACHABLE",
  "detail_ref": "trace/2026-10-18/run-abc/fetch-7",
  "degraded_from": null,
  "degrade_reason": null
}
```

- `status`：本文 §2 的枚举。
- `reason_code`：**封闭枚举**，不是自由文本。当前 15 个取值（与
  `contracts/schemas/common-1.0.schema.json` 的 `reason_code` 枚举逐一对应）：
  `SOURCE_UNREACHABLE / SOURCE_GONE / SOURCE_PAYWALLED / EVIDENCE_CONFLICT / EVIDENCE_INSUFFICIENT /
  OUT_OF_VALIDATED_LANGUAGE_SCOPE / OUT_OF_VALIDATED_DOMAIN / CONTEXT_TRUNCATED /
  CONTRACT_VIOLATION / TIMEOUT / DEPENDENCY_MISSING / MODEL_LOAD_FAILED / PUSH_FAILED /
  NOT_CONFIGURED / NOT_APPLICABLE_FIELD / VERSION_CONFLICT / ADAPTER_ERROR / ACCESS_DISALLOWED`。
- **状态与原因码必须相容。** 例如 `TIMEOUT` 只能配 `ERROR`，`OUT_OF_VALIDATED_LANGUAGE_SCOPE`
  只能配 `ABSTAIN`。相容表在 `tools/validate_objects.py` 的 `REASON_ALLOWED`，
  由 `test_every_reason_code_is_mapped_to_a_status` 保证"schema 里的码都有人管"。
- `detail_ref`：指向可复核的证据或 trace，**不得**把长篇解释塞进状态字段。
- 自由文本只能出现在 `detail_ref` 指向的对象里。

---

## 7. 测试向量

| # | 场景 | 期望状态 | 明令禁止的错误做法 |
|---|---|---|---|
| T-01 | 来源 404 | `source_status=GONE`，测量 `ABSTAIN` | 记成"没有相关内容" |
| T-02 | HTTP 超时 3 次 | `ERROR` + `TIMEOUT` | 记成 `UNKNOWN` |
| T-03 | 原文只能取到摘要 | `PARTIAL` + 缺失段落引用 | 当成完整原文测量 |
| T-04 | 发布日期无法解析 | `UNKNOWN`（时间字段） | 填当前时间 |
| T-05 | 纯代码仓库无发布时间 | `NOT_APPLICABLE` | 记 `UNKNOWN`（不是"不知道"，是"不适用"） |
| T-06 | 土耳其语文章，能力只验证过 en/zh | `ABSTAIN` + `OUT_OF_VALIDATED_LANGUAGE_SCOPE` | 硬判成 `UNRELATED` |
| T-07 | 证据互相矛盾 | `UNKNOWN` + `EVIDENCE_CONFLICT` | 选信噪比高的那个 |
| T-08 | 模型返回非法枚举 | `INVALID` + `CONTRACT_VIOLATION` | 就近映射到最近标签 |
| T-09 | 模型文件加载失败 | `ERROR` + `MODEL_LOAD_FAILED` | 用规则兜底但不记录 |
| T-10 | 本轮未配置视觉通道 | `MISSING` | 记成"没有好作品" |
| T-11 | 抓取成功但推送失败 | `run_status=SUCCESS`，`delivery_status=ERROR` | 整体记成功 |
| T-12 | 所有候选测量都 `ABSTAIN` | 空 `DeliveryObject` + 说明 | 降低标准凑一条 |

---

## 8. 与注册表的对应

每个能力在 `capabilities/registry.yaml` 中用 `unknown_behavior` 声明**证据不足时的默认行为**，
校验器要求该值必须属于本文件 §2 的七个非 OK 状态之一（schema 枚举强制）。
这让"这个能力拿不准时会怎样"成为可检查的字段，而不是散落在实现里的默认分支。
