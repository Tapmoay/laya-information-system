# M2 在线链路（Live Run）

> **交付物对应**：v0.3 §2（单轮状态机）、§9（端到端链路）、§10 WP-R5/R6/R7/R8 的串联。
> **当前状态**：链路已跑通，但候选来自**本机 HTTP 服务器**——本机没有出站网络。

---

## 1. 链路

```text
URL 列表
  │
  ├─ PRECHECK        校验 demand/config、核对来源白名单
  ├─ DISCOVERY        受控抓取（ingest/fetch.py）
  ├─ NORMALIZATION    规范化 + 去重（ingest/normalize.py, dedupe.py）
  ├─ MEASUREMENT      测试桩适配器 → MeasurementRecord
  ├─ SELECTION        确定性选择（selection/engine.py）
  ├─ DELIVERY_BUILD   装配 + 渲染（delivery/builder.py, renderer.py）
  └─ PUSH             文件通道 + 幂等（delivery/push.py）
```

阶段名与 `RunManifest` 的枚举一一对应（不是能力阶段，映射见 C-12）。

入口：`runtime/live_run.py::run_live()`。

---

## 2. 一个来源失败不等于整轮失败

```text
DISCOVERY     PARTIAL   （3 个 URL，1 个 404）
NORMALIZATION OK        （2 条候选）
MEASUREMENT   OK
SELECTION     OK
DELIVERY_BUILD OK
PUSH          OK
────────────
run_status        PARTIAL
delivery_status   SUCCESS
```

这是有意的：来源会坏，但"今天有一条值得读的内容、并且已经发出去"仍然是成功的一天。
把整轮记成 `FAILED` 会让用户重跑，而重跑大概率还是同一个 404。

反过来，全部来源失败时：

- `DISCOVERY = PARTIAL`、`NORMALIZATION = PARTIAL`（0 条候选）
- 交付对象为空，但带 `empty_reason = NO_ELIGIBLE_CANDIDATES`
- `run_status = PARTIAL`，**仍然推送**——"今天没有值得推的"是一个结论，不是错误。

---

## 3. 换适配器就换交付

同一批 URL、同一个选择器：

| profile | 交付的候选 |
|---|---|
| `MODEL-PROFILE-STUB-AGENT` | 技术文章（含 decision/model 关键词） |
| `MODEL-PROFILE-STUB-VISUAL` | 示例第二方向文章（含 叙事 关键词） |

Selector 一行没改（`test_swapping_the_adapter_swaps_the_delivery`）。

---

## 4. 验收证据

```powershell
python -m pytest -q tests/test_live_run.py -q
```

| 保护的性质 | 测试 |
|---|---|
| 端到端跑通，阶段状态正确 | `test_live_run_goes_end_to_end` |
| 只交付匹配的候选，其余进 DISCARD | `test_only_the_matching_candidate_is_delivered` |
| 换适配器换交付 | `test_swapping_the_adapter_swaps_the_delivery` |
| 全失败也产出有解释的空结果 | `test_all_sources_failing_still_produces_an_explained_run` |
| 产物齐全 | `test_artifacts_are_written` |
| 白名单外来源在预检就被拒 | `test_source_outside_allowlist_aborts_before_fetching` |
| 测量带语言与未验证标记 | `test_measurements_carry_language_and_method` |

一次运行的产物：

```text
runs/<run_id>/
  fetch-results.json       每次抓取的结果与状态
  candidates.json          规范化后的候选
  duplicates.json          被判重复的候选（带 duplicate_of）
  measurements.json        测量记录（method=RULE，带"未验证桩"不确定项）
  selection-decisions.json 每条决定与理由
  delivery-object.json     结构化交付
  rendered-brief.json      可读简报
  delivery-status.json     推送状态
  run-manifest.json        阶段、版本、模型 revision、计数
```

---

## 4.5 政策输入

`LiveRunConfig.policy_doc` 可以指向真实政策文件：预检阶段编译 `PolicyConfig`，
冲突默认阻断（`VERSION_CONFLICT`），获准后把 `policy-bundle.json` / `policy-config.json`
写进运行目录。与离线入口行为一致。

---

## 5. 诚实说明

1. **候选来自 localhost**。本机无出站网络（SSL 握手超时），所以没有真实的公网来源。
2. **测量来自测试桩**，`method.kind=RULE`、`uncertainties` 里明确写着"未验证测试桩"。
   这批交付不代表任何真实模型的能力。
3. **推送写本地文件**，没有消息通道。
4. **没有 UI 触发**，只能命令行调用。

---

## 6. 距离"真正可用"还差什么

| 缺什么 | 阻塞项 |
|---|---|
| 公网抓取 | 网络可用 + 一个允许抓取的来源清单 |
| 真实测量 | 模型下载授权（P-05）+ 评测集 |
| 消息推送 | 渠道与凭据（P-03） |
| 定时运行 | 把 `runtime/schedule.py` 接到 `run_live`，并挂到系统任务计划 |

## 6. 真实运行记录（2026-10-18）

### 第二轮：真实需求 + 真实政策 + 真实模型

```text
run_id=run-live-202610181335
需求   demand/demand-2026-01-01.json（取自 information-policy.md §1.1）
政策   information-policy.md v0.3（编译成 PolicyConfig，状态 OK）
来源   量子位 3 篇 + InfoQ 中文 3 篇
模型   MODEL-PROFILE-QWEN25-3B @72629def1f9e04ae（judgment_language=zh-Hans）
结果   run_status=SUCCESS  delivery_status=SUCCESS
计数   candidates=6 measurements=6 selected=6 delivered=6
```

清单里的 `model_revisions` 记录了模型档案与内容标识，
每条测量带 `method.kind=MODEL`、`model_revision` 与 `evidence_refs`，
每个决定带 `source_measurement_refs`——DoD-09 的追溯链是完整的。

### 第一轮（英文需求夹具）

```text
run_id=run-live-202610181320
来源   量子位 3 篇 + InfoQ 中文 3 篇（RSS 挑选，discovery_method=MANUAL）
模型   MODEL-PROFILE-QWEN25-3B（judgment_language=zh-Hans）
结果   run_status=SUCCESS  delivery_status=SUCCESS
计数   candidates=6 measurements=6 selected=6 delivered=6
政策   v0.3（自洽，无冲突）
```

产物在 `runs/real-2026-10-18/`：抓取原始结果、候选、测量、决定、交付对象、简报、清单、outbox。

简报节选：

```markdown
1. **清华开源！智能体操作系统任务成本降83%，AI记忆可查可改** · 暂存
   - 任务价值：邻接相关
5. **Claude半小时解开密码学悬案，学界集体走偏20年** · 暂存
   - 任务价值：探索性
```

两个诚实的观察：

1. 全部条目的动作都是"暂存"——因为在线链路目前只测了 `CAP-MEA-02`（相关性），
   没有增长价值/成熟度等测量，动作推断只能落到默认值。
2. 没有一条被判成 DIRECT：需求快照是英文的 M1 夹具，与中文 AI 新闻的措辞对不齐。
   这是需求与来源不匹配的问题，不是模型的问题。
