# 反馈与规则演进（Feedback & Policy Update）

> **交付物对应**：v0.3 §8.7、§10 WP-R9；policy §7（推荐记录）与 §9（自主规则生成）。
> **当前范围**：反馈记录的**结构分离**、结果关联的**证据门槛**、规则提案的**构造**、规则变更的**离线对照**。
> **未做**：Agent 自己"发现该改什么"、人工激活闸门、长期存储。

---

## 1. 为什么先做"不能混写"

policy §7.3 要求把用户明示、客观观察、Agent 推断分开存储。如果只是"约定不要混"，
实现里第一个赶工的人就会把它们塞进同一个字段，然后所有下游都开始猜。

所以三类在 schema 层互斥：

| kind | 只允许的 source | 必填字段 | 其余两类 |
|---|---|---|---|
| `USER_STATED` | `USER_MESSAGE` | `user_stated.statement` | 必须为 `null` |
| `OBSERVED_ACTION` | `UI_ACTION` / `SYSTEM_LOG` | `observed_action.action` | 必须为 `null` |
| `AGENT_INFERRED` | `AGENT` | `agent_inference.inference + confidence` | 必须为 `null` |

写错组合，契约校验直接失败（`test_mixing_statement_with_action_is_invalid`）。

---

## 2. 点击不是收益

`build_outcome()` 的规则：

1. 没有 `observed` → `status=UNKNOWN`、`benefit_judgement=NOT_EVALUATED`。
2. 声称 `OBSERVED` 但没有 `evidence_refs` → 报错。
3. 判 `SUPPORTED` / `NOT_SUPPORTED` 但没观察 → 报错。
4. `summarize_actions()` 只做计数：`{"OPENED": 1, "READ_TIME": 1}`，不翻译成"有用"。

测试 `test_clicks_do_not_become_learning` 用两条点击记录证明：结果仍是"未评估"。

---

## 3. 提案：先核对现状

`build_proposal()` 要求每条变更都写 `pointer / before / after`，并且**当前策略里该字段的值必须等于 before**。
否则报 `ProposalError`——防止拿两周前的配置做决定。

- `status` 只能是 `PROPOSED` 或 `TRIAL`；写 `ACTIVE` 直接违反契约。
- `side_effects` 至少一条：没有副作用的提案通常意味着没想清楚。
- `rollback_condition` 必填。

---

## 4. 对照：必须报告挤压

`evaluate_diff()` 在同一批历史输入上跑两次选择（变更前 / 变更后），输出：

- `affected_candidates`：决定发生变化的候选
- `channel_deltas`：每个通道交付数的前后对比
- `improvements` / `squeezes`

**校验器会拦**：只要有通道变少而 `squeezes` 为空，报告无效
（`_check_diff_squeezes`）。只报好消息的对照等于没做。

实测：把 `DEMONSTRATED` 加进"只监视"规则后，CORE 交付从 2 降到 1，
`cand-m1-0001` 由 `DELIVER` 变为 `MONITOR`，报告如实写出挤压。

---

## 5. 检测：观察不等于提案（`CAP-FBK-04`）

`feedback/detector.py` 在最近 N 轮运行清单与反馈里找**反复出现的模式**：

| 代码 | 触发条件 | 含义 |
|---|---|---|
| `SOURCE_UNSTABLE` | 窗口内 ≥3 轮抓取 `PARTIAL/FAILED` | 来源稳定性在拖累链路 |
| `EMPTY_DELIVERY_STREAK` | 窗口内 ≥3 轮 `selected == 0` | 准入过严或来源枯竭 |
| `FEEDBACK_GAP` | 交付 ≥3 条但没有任何用户明示反馈 | 收益无法判断 |

两条边界：

1. **只报告模式，不给改法。** 观察对象里没有 `changes`、没有 `policy` 字段，
   测试 `test_observations_satisfy_contract` 断言这一点。
2. **窗口不足不说话。** 2 次异常不足以形成结论，阈值默认 3、窗口默认 5。

它和提案的分工是 v0.3 §9.2 的前两步（观察、诊断）与第三步（提出规则）的区别：
检测器负责"我注意到什么"，提案与批准负责"所以改什么"。

```powershell
python -m feedback.detector --runs runs --feedback runs/feedback
```

---

## 6. 命令行入口（`feedback/cli.py`）

不必写 Python 也能记录反馈：

```powershell
python -m feedback.cli user-stated --store runs/feedback --id fb-1 --candidate cand-001 --text "这个方向想深入"
python -m feedback.cli observed   --store runs/feedback --id fb-2 --candidate cand-001 --action OPENED
python -m feedback.cli outcome    --store runs/feedback --id out-1 --candidate cand-001 `
    --use APPLIED --observed-status OBSERVED --evidence runs/x/run-manifest.json
python -m feedback.cli list       --store runs/feedback
```

命令行能保证的事：

- 没有"把用户说的话塞进行为记录"这种选项（三类互斥）；
- 声称 `OBSERVED` 却没给 `--evidence` → 直接拒绝，不落盘；
- 重复 id → 拒绝；
- `outcome` 会自动关联同一候选已有的反馈记录。

实测：记录一条用户明示后，检测器的 `FEEDBACK_GAP` 立刻消失。

---

## 7. 验收测试（`tests/test_feedback.py`）

| 保护的性质 | 测试 |
|---|---|
| 三类反馈结构互斥 | `test_three_kinds_are_structurally_separated`、`test_mixing_statement_with_action_is_invalid` |
| 推断必须带置信度 | `test_agent_inference_needs_confidence` |
| 点击不算学习 | `test_clicks_do_not_become_learning` |
| 观察必须有证据 | `test_observed_outcome_requires_evidence` |
| 收益判定需要观察 | `test_benefit_needs_observation` |
| 提案核对现状 | `test_proposal_rejects_stale_before` |
| 提案不能自我激活 | `test_proposal_cannot_be_active` |
| 变更不改原策略 | `test_apply_changes_does_not_mutate_baseline` |
| 对照报告挤压 | `test_policy_diff_reports_the_squeeze` |
| 相同策略无变化 | `test_identical_policy_reports_no_change` |

---

## 8. 已知缺口

1. **没有 Agent 提案生成**：`CAP-FBK-03` 仍是 `PROPOSED`——"该改什么"需要长期观察，目前只做了构造器。
2. **没有人工激活闸门**：`CAP-PUP-02`（`HUMAN_GATE`）未实现，提案落不了地。
3. **没有反馈入口**：没有 UI/消息通道把用户反馈送进来，目前只能由代码调用。
4. **没有长期存储**：反馈与结果只存在于单次调用返回的对象里，没有落盘与检索。
5. **没有跨轮对照**：`evaluate_diff` 用的是夹具里的历史输入，不是真实运行记录。
