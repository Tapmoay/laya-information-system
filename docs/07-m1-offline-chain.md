# M1 离线链路（Offline Chain）

> **交付物对应**：v0.3 §9.1「第一条离线链路」、§10 WP-R4 / WP-R5、§13 DoD 第 3/5 条。
> **结论**：链路已跑通。固定需求 + 固定候选 + 固定测量 → 结构化 `DeliveryObject` + `RunManifest`，
> 全程不访问网络、不加载模型。

---

## 1. 这一批真正证明了什么

不是"代码能跑"，而是四件更难的事：

1. **契约自洽。** 6 类输入/输出对象在同一轮运行里互相咬合，每个都过 schema 与语义校验。
2. **选择可复算。** 同输入、同策略版本，两次运行产出逐字节相同的结果。
3. **未知不被吞掉。** 语种超出能力范围的候选进入 `ABSTAIN`，而不是被当成"无关"消失。
4. **容量来自政策。** 把 `core_items_max` 从 5 改成 1，交付立刻少一条、多一条 `MONITOR`——代码里没有硬编码数字。

---

## 2. 流水线

```text
fixtures/m1/<case>/
  policy-bundle.json  policy-config.json  demand-snapshot.json
  candidates/*.json   measurements/*.json
        │
        ├─ 1. 契约校验（policy-bundle / config / demand / candidate / measurement）
        ├─ 2. select()            → SelectionDecision × N + delivery_order + constraint_checks + actions
        ├─ 3. build_delivery()    → DeliveryObject
        ├─ 4. build_manifest()    → RunManifest
        ├─ 5. 输出校验（selection / delivery / manifest）
        └─ 6. 落盘 runs/<dir>/{selection-decisions,delivery-object,run-manifest}.json
```

模块分工与注册表一致：

| 步骤 | 能力 | 代码 |
|---|---|---|
| 准入 / 分量 / 组合 / 容量 / 决定 | `CAP-SEL-01..05` | `selection/engine.py` |
| 交付装配 | `CAP-DEL-01` | `delivery/builder.py` |
| 清单装配 | `CAP-RUN-03`（雏形，尚未标 IMPLEMENTED） | `runtime/manifest.py` |
| 编排 | 未实现（M1 是脚本，不是 Run Manager） | `runtime/offline_run.py` |

---

## 3. 选择规则（可审查、可 diff）

### 3.1 准入判定顺序

| # | 条件 | 结果 | 理由码 |
|---|---|---|---|
| 1 | 标题或主张命中 `constraints.excluded_topics` | `INELIGIBLE` | `POLICY_EXCLUDED` |
| 2 | 没有 `RELEVANCE` 测量 | `ABSTAIN` | `MEASUREMENT_MISSING` |
| 3 | `RELEVANCE` 测量非 OK（ABSTAIN/UNKNOWN/PARTIAL） | `ABSTAIN` | `EVIDENCE_INSUFFICIENT` |
| 4 | `RELEVANCE = UNRELATED` | `INELIGIBLE` | `NO_DEMAND_MATCH` |
| 5 | `RELEVANCE = INSUFFICIENT_EVIDENCE` | `ABSTAIN` | `EVIDENCE_INSUFFICIENT` |
| 6 | `EXPLORATORY` 但本轮探索通道关闭 | `INELIGIBLE` | `NO_DEMAND_MATCH` |
| 7 | 其余 | `ELIGIBLE` | `DEMAND_MATCH` |

**第 2/3 条是整个设计的关键**：模型没判、判不了、语言超出范围，都不构成"这条信息无关"的证据。

### 3.2 排序：逐项词典序，不是加权总分

```yaml
ordering:
  CORE:        [decision, task, capability, recency]
  EXPLORATION: [exploration, capability, recency]
# 最后以 candidate_id 升序兜底
```

任一项的差距都不能被其他项的高分补偿——这正是 `Selection ≠ Ranking` 要求的可解释性。
排序键来自 `selection/policy.yaml` 的 `label_values` 表（标签 → 分量值），可审查、可 diff。

### 3.3 容量与 MONITOR

- 通道独立上限：`CORE` 用 `core_items_max`，`EXPLORATION` 用 `exploration_items_max`。
- **`MONITOR` 不占交付容量**：它表示"存在但本轮不交付"，不是"挤掉了别人"。
- 两类 `MONITOR`：
  - 只监视规则命中（`maturity = ANNOUNCED` 或 `claim_evidence = CONFLICTING`）→ `AWAITING_EVIDENCE`
  - 通过准入但超出容量 → `CAPACITY_LIMIT`

### 3.4 S 级破格

政策 §3.8 规定「S 级重大技术变化可单独提示，但必须解释打破容量限制的原因」。
选择器现在真的执行这条：

- 触发条件：`policy_config.capacity.s_breakout_allowed == true`
  **且** 该候选有 `IMPORTANCE` 测量、状态 `OK`、标签 `S`；
- 效果：该候选进入交付，但 `CORE_CAPACITY` 检查如实显示 `actual > limit`、`passed: false`，
  决策理由补 `S_LEVEL_BREAKOUT`；
- **未知不算 S**：`ABSTAIN` 或 `INSUFFICIENT_EVIDENCE` 不具备破格资格
  （`test_unknown_importance_does_not_break_capacity`）；
- 能力 `CAP-MEA-10 importance-assess` 负责产出这个测量，目前 `CONTRACTED`——
  机制已测，判断者还没有。

---

## 4. 三个案例

| 案例 | 内容 | 期望 |
|---|---|---|
| `basic` | 5 个候选：技术更新、示例第二方向文章、土耳其语论文、标题党、粗野主义海报 | 交付 2 条 CORE + 1 条 EXPLORATION；1 条 `ABSTAIN`；1 条 `DISCARD` |
| `capacity` | 同 basic，但 `core_items_max = 1` | CORE 只交付 1 条，另一个降为 `MONITOR(CAPACITY_LIMIT)` |
| `empty` | 只有"无关"和"语种超范围"两个候选 | 空 `DeliveryObject`，`empty_reason = NO_ELIGIBLE_CANDIDATES` |

```powershell
python -m runtime.offline_run --case fixtures/m1/basic --run-id run-demo --now 2026-10-18T03:00:00+08:00 --out runs/demo
```

---

## 5. 明确不做

- 不抓取、不解析网页（`CAP-INF-*` 仍是 M2）。
- 不加载任何模型；测量值全部来自夹具。
- 不翻译、不渲染简报、不推送（`CAP-DEL-02..04` 仍是 M2）。
- 没有单实例锁与状态机（`CAP-RUN-01` 未实现）——**现在还不能挂到调度器上**。

---

## 6. 验收证据（可复现）

```powershell
python -m pytest -q                       # 51 passed
python tools/validate_objects.py --all-fixtures   # 43 个契约夹具
```

| 验收点 | 测试 |
|---|---|
| 无关候选被丢弃而不是交付 | `test_unrelated_candidate_is_discarded_not_delivered` |
| 弃权测量不被当作负面证据 | `test_abstained_measurement_is_not_negative_evidence` |
| 容量来自配置 | `test_capacity_comes_from_config_not_from_code` |
| 超额降级为监视 | `test_over_capacity_candidate_becomes_monitor` |
| 每分量保留测量来源 | `test_every_component_keeps_its_source_measurement` |
| 选择器不导入模型/网络库 | `test_selection_and_delivery_do_not_import_models_or_network` |
| 端到端产出且契约通过 | `test_basic_case_produces_delivery_and_manifest`、`test_every_output_validates_against_its_contract` |
| 空结果有解释 | `test_empty_case_produces_an_explained_empty_delivery` |
| 可复现 | `test_run_is_reproducible` |
| 非法输入在产出前被拒 | `test_invalid_input_is_rejected_before_any_output` |

---

## 7. 已知局限（下一步要解决的）

1. **夹具是手写的。** 它们代表"我们希望的输入形状"，不代表真实网页噪声。
2. **分量阈值未校准。** `label_values` 是初版规则表，没有经过真实评价数据检验。
3. **探索通道没有预留机制。** 当前探索内容与主线竞争同一注意力预算，只受独立上限约束；
   是否要"至少保留 1 个探索位"是政策问题，留到有真实反馈后决定。
4. **交付无译文。** `delivery_language_mode=ZH_TRANSLATION` 但 `rendered_title=null`，
   已在 `required_caveats` 里如实标注。
5. **没有 run 级防护。** 没有锁、没有检查点、没有超时；这些是 WP-R7 的内容。
