# 对象契约（Object Contracts）

> **交付物对应**：v0.3 §8（核心数据对象）、§10 `WP-R2`。
> **事实源**：`contracts/schemas/*.schema.json` + `contracts/label-sets.yaml`。
> 本文件解释"为什么这样设计"，字段以 schema 为准；两者冲突时以 schema 为准并修本文。

---

## 1. 约定

| 约定 | 说明 |
|---|---|
| 文件名 | `<object>-<major>.<minor>.schema.json`，与注册表 `schema_ref` 一一对应（`--backlog` 据此判断是否落地） |
| `$id` | `https://laya.local/contracts/<object>/<version>`，跨文件引用用绝对 `$id` |
| 公共定义 | `common-1.0.schema.json`：状态、时间观测、语言标签、证据引用、概率表 |
| 校验 | schema 管结构，`tools/validate_objects.py` 管 schema 表达不了的语义 |
| 版本 | 新增可选字段 → minor；改语义/必填/枚举 → major |

**为什么用 JSON Schema 而不是直接写 Pydantic**：契约要能被别的语言和工具读，也要能被
`--backlog` 机械检查。Pydantic 是实现手段，不是事实源（ADR-0003）。

---

## 2. 已完成（8 个）

### 2.1 `POLICY-BUNDLE/1.0`

本轮读到的政策与目标文件：路径、sha256、字节数、自述版本。

- **两个版本字段并存**：`declared_version`（文件顶部）与 `declared_version_in_changelog`（§10）。
  C-01 的冲突不再被"选一个"掩盖，而是作为 `version_conflicts` 数组落盘。
- **三分状态**：无冲突 → `OK`；有冲突且 `proceed_allowed=true` → `PARTIAL`；
  有冲突且未获准 → `INVALID`。三个分支由 schema 强制，代码不能偷偷继续跑。

### 2.2 `POLICY-CONFIG/1.0`

政策编译出的机器可读参数。

- **每个容量上限必须有条款来源**（`capacity_sources`，语义校验要求覆盖全部 5 个字段）。
  政策一改，来源行就过期，测试会逼着人回来看。
- **`run_limits` 允许 null，但不允许默认常量**。null = 尚未实测，代码必须显式处理，
  不能用"随便给个 30 分钟"顶替（v0.3 §2.2）。
- **冻结**：`translated_text_is_evidence` 恒为 `false`；`delivery_language_mode=ZH_TRANSLATION`
  时 `show_original_title` 必须为 `true`（ADR-0008）。

### 2.3 `DEMAND-SNAPSHOT/1.0`

一次运行的需求快照，不是画像。

- **用户确认的目标与 AI 推断的目标分两个数组**。`active_goals[].state` 只允许
  `ACTIVE_EXPLICIT / ACTIVE_CONTEXTUAL`；`inferred_goals[].state` 恒为 `CANDIDATE_INFERRED`
  且必须带证据。政策 §1.5"不得把推测静默改写成用户已确认目标"成了结构约束。
- **时间预算是上限不是目标**：`energy_state=LOW` 时预算上限被 schema 压到 240 分钟。

### 2.4 `CANDIDATE-RECORD/1.0`

- **发布者与上游分开**；`identity_status=CONFIRMED` 必须带名称和至少一条证据。
- **时间不是裸字符串**：`published_at` 是 `{value, state}`，非 OK 时 `value` 必须为 null。
- **取数动作与来源状态正交（C-10 定稿）**：`retrieval` 描述本轮抓取，
  `source_availability` 描述来源本身；可以"抓取成功但来源已重定向"。

### 2.5 `MEASUREMENT-RECORD/1.0`

- **`value` 与 `state` 互斥**：OK 必须有值和证据；非 OK 必须 null。
- **语言上下文四维分开**；`input_representation=TRANSLATION` 时 `translation_ref` 必填。
- **模型必须可追溯**：`model_revision` 不可缺省；有 `probabilities` 必须有 `calibration_profile_id`。

### 2.6 `SELECTION-DECISION/1.0`

- **分量与结论分离**：`priority_components` 每项必须挂 `source_measurement_refs`，
  没有来源的分量不允许出现；`value: null` 表示"算不出来"，不许用 0 顶替。
- **决定由准入推导**：`DELIVER` ⇒ `ELIGIBLE` 且通道不为 `NONE`；
  `INELIGIBLE` ⇒ 必须有 `blocked_reasons` 且决定只能是 `DISCARD/ABSTAIN`。
- **理由必填**：`reasons` 至少一条，来自封闭枚举。

### 2.7 `DELIVERY-OBJECT/1.0`

- **空结果要解释**：`items` 为空时 `empty_reason` 必填（`NO_ELIGIBLE_CANDIDATES` 等），
  非空时必须为 null。"今天没有值得推的"是一个合法且可审计的结论。
- **名次连续**：`priority_rank` 必须 1..n 连续（语义校验），避免呈现层自己插队。
- **原文标题不可被翻译覆盖**：`original_title` 必填，`rendered_title` 是可选的衍生字段。

### 2.8 `RUN-MANIFEST/1.0`

- **run 状态由阶段推导**：任一阶段 `FAILED` ⇒ `run_status=FAILED`；
  有 `PARTIAL` 且无 `FAILED` ⇒ `PARTIAL/ABORTED`；全 `OK/SKIPPED` ⇒ `SUCCESS`。
- **run 状态 ≠ 推送状态**：`delivery_status=SUCCESS` 必须存在 `PUSH=OK` 阶段；
  推送失败不改写 run 成功（v0.3 §2.3）。
- **模型 revision 不许浮动**：`latest / main / master` 被正则拒绝。

---

## 3. 标签集

`contracts/label-sets.yaml` 定义每种 `measurement_type` 的允许标签、`label_set` 版本、
以及**哪个能力有权产出它**。校验器检查三者一致。

| measurement_type | 标签 | 产出能力 |
|---|---|---|
| `RELEVANCE` | DIRECT / ADJACENT / EXPLORATORY / UNRELATED / INSUFFICIENT_EVIDENCE | `CAP-MEA-02` |
| `GROWTH_VALUE` | TRANSFERABLE_MECHANISM / PRACTICE_OPPORTUNITY / GAP_FILLING / INCREMENTAL / NONE / INSUFFICIENT_EVIDENCE | `CAP-MEA-03` |
| `RECENCY` | FRESH / RECENT / AGING / STALE | `CAP-MEA-04` |
| `DECISION_IMPACT` | CHANGES_DECISION / MAY_CHANGE_DECISION / CONTEXT_ONLY / NONE / INSUFFICIENT_EVIDENCE | `CAP-MEA-05` |
| `MATURITY` | ANNOUNCED / DEMONSTRATED / REPRODUCIBLE / DEPLOYABLE / ESTABLISHED / INSUFFICIENT_EVIDENCE | `CAP-MEA-06` |
| `CLAIM_EVIDENCE` | SUPPORTED / PARTIALLY_SUPPORTED / CONFLICTING / INSUFFICIENT_EVIDENCE | `CAP-MEA-07` |
| `EXPLORATION_VALUE` | HIGH / MODERATE / LOW / NONE / INSUFFICIENT_EVIDENCE | `CAP-MEA-08` |
| `SOURCE_CREDIBILITY` | PRIMARY_INDEPENDENT / PRIMARY_SINGLE / SECONDARY_INDEPENDENT / AGGREGATED / UNKNOWN | `CAP-MEA-09` |
| `IMPORTANCE` | S / A / B / INSUFFICIENT_EVIDENCE | `CAP-MEA-10` |

> 2026-10-18：新增 `IMPORTANCE/1.0`。`MEASUREMENT-RECORD` 的 `value.label` 正则放宽为
> `^[A-Z][A-Z0-9_]{0,40}$`，因为政策原文用的就是单字母 `S`。历史记录不受影响。

**状态：`PROVISIONAL`。** 除 `RELEVANCE`（v0.3 §5.3）与 `MATURITY`（政策 §3.5），
其余标签是本仓库初版定义，必须在 WP-R3 评测里校准。改标签 = 升 `label_set` 版本号。

---

## 4. 校验分工

| 层 | 管什么 | 例子 |
|---|---|---|
| JSON Schema | 结构、必填、类型、跨字段条件 | 空交付必须有 empty_reason；模型 revision 不许 main |
| 语义校验（Python） | 需要查表的规则 | 标签是否属于该测量类型；原因码与状态是否相容；名次是否连续 |
| 夹具回归 | 端到端不退化 | 43 个夹具：valid 必过、invalid 必拦且拦对原因 |

```powershell
python tools/validate_objects.py --all-fixtures          # 43 个夹具
python tools/validate_objects.py --type delivery-object <file>.json
python -m pytest -q                                      # 33 passed
```

---

## 5. 尚未落地（22 项）

| 分组 | schema_ref | 何时需要 |
|---|---|---|
| 模型边界 | `DECISION-REQUEST/1.1`、`DECISION-RESULT/1.1` | WP-R3 |
| 检索 | `QUERY-PLAN/1.0`、`FETCH-RESULT/1.0`、`LANGUAGE-OBSERVATION/1.0`、`SOURCE-IDENTITY/1.0` | WP-R6 |
| 文本 | `TEXT-REPRESENTATION/1.0` | WP-R6 |
| 监测 | `MONITORING-PROFILE/1.0` | WP-R6 |
| 运行 | `RUN-STATE/1.0`、`CHECKPOINT/1.0`、`TRIGGER-RECORD/1.0`、`RESOURCE-RELEASE/1.0` | WP-R7 |
| 交付 | `RENDERED-BRIEF/1.0`、`DELIVERY-STATUS/1.0` | WP-R8 |
| 反馈 | `FEEDBACK-RECORD/1.0`、`OUTCOME-RECORD/1.0`、`POLICY-PROPOSAL/1.0`、`POLICY-DIFF-REPORT/1.0`、`POLICY-ACTIVATION/1.0` | WP-R9 |
| 输入 | `POLICY-DOC/1.0`、`GOAL-DOC-SET/1.0`、`EVIDENCE-REF/1.0` | 按需 |

`python tools/validate_registry.py --backlog` 给出完整清单。

---

## 6. 已知取舍

1. **标签集不在 schema 里。** 写进 schema 需要 8 段几乎相同的 `oneOf`，而标签本来就会随评测改动。
   放在 YAML + Python 校验，改一处即可，并有测试兜底。代价：只用 jsonschema 而不跑本仓库校验器的人拿不到这层保护。
2. **`EVIDENCE-REF` 仍是字符串。** 等真正需要"证据对象"（来源、片段、可信度）时再升级，避免过早抽象。
3. **`score` 与 `probabilities` 并存但语义不同。** `score` 用于规则评分（如新鲜度），
   `probabilities` 只能来自校准过的模型输出。
4. **`RunManifest` 的阶段名与能力阶段是两套枚举。** 映射表见 `docs/01-open-conflicts.md` C-12；
   能力阶段是契约主键，运行阶段只出现在清单里。
