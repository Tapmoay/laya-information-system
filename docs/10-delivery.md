# 交付呈现与推送（Delivery）

> **交付物对应**：v0.3 §8.6、§10 WP-R8、`CAP-DEL-02..04`。
> **当前通道**：本地文件（`file`）。真实消息渠道等凭据与频率策略确定后再接。

---

## 1. 三段职责

```text
DeliveryObject  ──render──▶  RenderedBrief  ──push──▶  DeliveryStatus
（结构化选择结果）        （可读简报，不加事实）      （推送状态，与 run 分开）
```

| 阶段 | 代码 | 能做什么 | 不能做什么 |
|---|---|---|---|
| 装配 | `delivery/builder.py` | 排序、分组、动作映射 | 重新筛选、改排名 |
| 呈现 | `delivery/renderer.py` | 排版、编组、加链接 | 补事实、改结论、覆盖原文标题 |
| 推送 | `delivery/push.py` | 写文件、记账、重试 | 重复推送、未配置就发送 |

---

## 2. 呈现层只表达，不判断

渲染器只能读 `DeliveryObject` 的字段。测试 `test_brief_adds_no_facts_beyond_the_delivery_object`
逐项比对：标题、链接、动作、分组、警示、依据引用必须与交付对象完全一致。

- **原文标题永不被覆盖**：`original_title` 原样保留，`displayed_title` 在译文缺失时回退到原文。
- **缺译文要明说**：`language_mode=ZH_TRANSLATION` 而 `rendered_title` 为空时，
  简报顶部和 `warnings` 都会标注"译文尚未接入"。
- **空结果要解释**：没有条目时简报写"本轮没有合格候选"，而不是留白。
- **"为什么是它"说人话**：把选择分量对应的测量标签翻译成一句短语
  （`任务价值：直接相关`），而不是只留 `依据：sel-cand-...` 这种内部 ID。
  这是**翻译**，不是判断：测试 `test_explanation_only_uses_recorded_labels`
  逐条核对每个短语都能对应到某条 measurement 的 label。

---

## 3. 推送幂等

幂等键 = `sha256(delivery_ref + 条目列表 + 通道)`。

**为什么不把正文算进键**：正文里带渲染时间戳，同一份交付每次重跑都会变成"新内容"，
幂等就失效了。条目列表（标题、链接、动作、警示）才是用户真正看到的东西。

- 第一次：写 `<target>/<brief_id>.md`，追加一行到 `<target>/.push-ledger.jsonl`，状态 `SUCCESS`。
- 第二次（同样内容）：键命中且文件存在 → 状态 `ALREADY_SENT`，**不写第二份文件、不追加账本**。
- 文件被删但账本还在：会重新推送（避免"记录说发过了、用户手里没有"）。
- 条目变了（标题、警示、动作）：键变化 → 视为新推送。
- 只改了排版或生成时间：键不变 → `ALREADY_SENT`，已发送的文件不会被覆盖。

`max_attempts` 控制单次推送内的重试；超过后抛 `PushFailed`。

---

## 4. 推送失败不等于整轮失败

| 情况 | `run_status` | `delivery_status` | PUSH 阶段 |
|---|---|---|---|
| 没配通道 | `SUCCESS` | `NOT_ATTEMPTED` | `SKIPPED` |
| 推送成功 | `SUCCESS` | `SUCCESS` | `OK` |
| 推送重试后仍失败 | `PARTIAL` | `FAILED` | `PARTIAL` |

理由：选择与交付装配已经完成，那份 `DeliveryObject` 是有效资产；把整轮记成 `FAILED`
会诱导"重跑一遍"，而重跑会重新测量、重新选择，浪费且可能产生不同结果。

**重试推送**：用同一个 `run_id` 再跑一次。`SELECTION` 与 `DELIVERY_BUILD` 的检查点会命中，
实际只会重试 PUSH。目前还没有单独的 `--retry-push` 命令，这是下一步。

---

## 5. 验收测试（`tests/test_delivery.py`）

| 保护的性质 | 测试 |
|---|---|
| 顺序与候选集合不变 | `test_brief_preserves_order_and_candidate_set` |
| 每条都有原文链接 | `test_brief_keeps_a_source_link_for_every_item` |
| 不新增事实 | `test_brief_adds_no_facts_beyond_the_delivery_object` |
| 空结果有解释 | `test_empty_delivery_is_explained_in_the_brief` |
| 缺译文有告警 | `test_brief_warns_when_translation_is_missing` |
| 推送写出状态与文件 | `test_push_writes_artifact_and_status` |
| 幂等 | `test_push_is_idempotent_across_retries` |
| 键忽略渲染时间、跟踪条目 | `test_push_key_ignores_render_time_but_tracks_items` |
| 未配置通道不发送 | `test_channel_none_means_no_push_attempt` |
| 推送失败不改写 run 成功 | `test_push_failure_does_not_flip_run_to_failed` |
| 未知通道报错 | `test_push_brief_rejects_unknown_channel` |

---

## 6. 已知缺口

1. **只有文件通道**：没有消息推送、没有通知、没有"发送失败要不要提醒用户"的机制。
2. **译文缺失**：`ZH_TRANSLATION` 模式下标题仍是原文。接翻译必须先做质量与语义风险标注
   （v0.3 §1.4 规则 4），不能直接接一个翻译 API 就上线。
3. **没有单独的推送重试入口**：现在靠"同 run_id 再跑一遍 + 检查点"变相实现。
4. **账本没有清理策略**：`.push-ledger.jsonl` 会一直增长。
