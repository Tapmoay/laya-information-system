# 能力注册表（Capability Registry）

> **交付物对应**：v0.3 §3、§10 `WP-R1`。
> **事实源**：`capabilities/registry.yaml`。**本文件不重复 YAML 的字段内容**，
> 只说明约定、阶段划分、不变量与扩展方式；§5 的索引表由 `tools/validate_registry.py --index` 生成。
>
> 为什么这样分工：同一份事实维护两处一定会漂移。`tests/test_registry.py` 里的
> `test_docs_index_matches_registry` 会在索引与 YAML 不一致时失败。

---

## 1. 阶段划分

| 阶段 | 是否属于业务循环 | 说明 |
|---|---|---|
| `POLICY` | 否（扩展 E-1） | 读政策、算哈希、编译机器可读参数。不产出候选或推荐。 |
| `DEMAND` | 是 | 需求快照与监测范围。 |
| `INFORMATION` | 是 | 检索、抓取、规范化、去重、语种、衍生文本。 |
| `MEASUREMENT` | 是 | 测量候选属性。测量不等于决策。 |
| `SELECTION` | 是 | 确定性准入、组合、容量、弃权。 |
| `DELIVERY` | 是 | 装配交付对象、渲染、推送。 |
| `RUNTIME` | 否（扩展 E-1） | 锁、状态机、检查点、运行清单、资源释放。 |
| `FEEDBACK` | 是 | 明示反馈、可观察行为、Agent 推断分开存储。 |
| `POLICY_UPDATE` | 是 | 离线 diff、人工闸门激活或回滚。 |

**扩展 E-1 的理由**：v0.3 §3.1 的 `stage` 枚举只有七个业务阶段，但 §10 的 WP-R7 要求
Run Manager、锁、检查点、调度；这些能力不属于任何业务阶段。若不引入 `RUNTIME`，
它们只能被硬塞进 `DELIVERY` 或 `POLICY_UPDATE`，反而破坏职责边界。`POLICY` 同理：读政策不等于"需求"。
两个扩展阶段都禁止外部副作用（`INV-10`），也不得产出推荐决策。

---

## 2. 责任方

| owner_type | 含义 | 允许的典型输出 |
|---|---|---|
| `DETERMINISTIC_CODE` | 可精确定义、可重复执行 | 哈希、时间、URL、校验、状态机 |
| `RULE_SET` | 显式规则表，可审查、可 diff | 准入、组合、去重、来源身份 |
| `MODEL_ADAPTER` | 某模型的一次测量调用 | 带范围的测量值 + 证据引用 |
| `AI_AGENT` | 需要语义理解的编排或推断 | 结构化的候选判断、提案 |
| `HUMAN_GATE` | 必须由用户确认 | 激活、回滚、高影响动作 |
| `HYBRID` | 语义部分 + 确定性校验的组合 | 由 Agent 生成、由代码校验的对象 |

`HYBRID` **不允许**出现在 `SELECTION`：混合体最容易藏住"模型其实在做决定"。
选择阶段只接受 `RULE_SET` 与 `DETERMINISTIC_CODE`（`INV-02`）。

---

## 3. 不变量

不变量不是文档装饰，而是可执行检查。注册表声明 `INV-01`–`INV-11`，
校验器必须实现**恰好**这一组；声明了没实现、实现了没声明，都会报错。设计意图：

- **INV-02 是整套架构的支点。** 它把"模型不能接管选择"从口号变成 schema 约束 + 测试。
  想把模型塞进 SELECTION，必须同时改 schema、改校验器、改测试——三步都留痕。
- **INV-05 / INV-11 保护编排。** 依赖成环会让运行卡死；阶段倒置（选择依赖交付）说明职责已经混了。
- **INV-08 保护可替换性。** 能力名里一旦出现 `laya`，换模型就变成改能力标识，追溯链断裂。
- **INV-06 / INV-07 保护可审计。** 没有能力的阶段等于没人负责；没有追溯字段的能力无法复核。

新增不变量时：先写规则与理由到 YAML → 在校验器里实现 → 加一条故意违反的测试。
顺序反了就会出现"文档说不允许、代码其实不管"。

---

## 4. 里程碑语义

`milestone` 的含义是：**该能力必须可运行的最早批次**；契约本身在批次 1 已全部定义。

- **M1（13 个）**：离线链路。政策加载、选择、交付装配、运行管理、一个确定性测量（新鲜度）。
  测量与候选由夹具提供，**不调用任何模型、不访问网络**。
- **M2（20 个）**：真实来源、测量能力、模型适配器、调度、推送。
- **M3（5 个）**：反馈、结果关联、规则提案、离线 diff、人工闸门。

**跨里程碑依赖不是错误。** 例如 `CAP-SEL-01`（M1）依赖 8 个测量能力（M2），
在 M1 里这些依赖由夹具满足。为了不靠人记"这一批需要哪些夹具"，
校验器把这些边自动列出来：

```powershell
python tools/validate_registry.py --fixtures
```

当前输出表明 M1 需要三类夹具：`CANDIDATE-RECORD`、`MEASUREMENT-RECORD`，
以及未在清单中列出但同样必需的 `DEMAND-SNAPSHOT`（由 `CAP-DEM-01` 在 M2 产出）。

---

## 5. 当前分布

- 能力总数：**40**（M1: 13 / M2: 21 / M3: 6）
- 状态：`IMPLEMENTED` 26、`CONTRACTED` 13、`PROPOSED` 1
- 责任方：`DETERMINISTIC_CODE` 17、`HYBRID` 6、`RULE_SET` 6、`MODEL_ADAPTER` 6、`AI_AGENT` 2、`HUMAN_GATE` 1

**注意**：目前能跑的是选择（`CAP-SEL-01..05`）、交付装配（`CAP-DEL-01`）与运行管理
（`CAP-RUN-01..04`，含调度决策）、呈现与推送（`CAP-DEL-02..04`）、
测量调用边界（`CAP-MEA-01`，只有测试桩）、信息接入的确定性部分（`CAP-INF-03..05`）、
反馈记录与结果关联（`CAP-FBK-01/02`）、规则对照（`CAP-PUP-01`）、规则激活闸门（`CAP-PUP-02`）、
受控抓取（`CAP-INF-02`），
由 `runtime/offline_run.py` 串成一条可运行链路
（见 `docs/07-m1-offline-chain.md`、`docs/08-run-manager.md`、`docs/10-delivery.md`）。
其余 25 个能力仍不可运行；`CONTRACTED` 只表示契约已定。

---

## 6. 索引（自动生成，勿手改）

<!-- BEGIN GENERATED INDEX -->
| 能力 ID | 名称 | 阶段 | 责任方 | 状态 | 里程碑 |
|---|---|---|---|---|---|
| CAP-POL-01 | policy-bundle-load | POLICY | DETERMINISTIC_CODE | IMPLEMENTED | M1 |
| CAP-POL-02 | machine-config-compile | POLICY | DETERMINISTIC_CODE | IMPLEMENTED | M1 |
| CAP-DEM-01 | demand-snapshot-build | DEMAND | HYBRID | CONTRACTED | M2 |
| CAP-DEM-02 | monitoring-profile-derive | DEMAND | HYBRID | CONTRACTED | M2 |
| CAP-INF-01 | query-plan-generate | INFORMATION | HYBRID | CONTRACTED | M2 |
| CAP-INF-02 | source-fetch | INFORMATION | DETERMINISTIC_CODE | IMPLEMENTED | M2 |
| CAP-INF-03 | candidate-normalize | INFORMATION | DETERMINISTIC_CODE | IMPLEMENTED | M2 |
| CAP-INF-04 | source-identity-resolve | INFORMATION | RULE_SET | IMPLEMENTED | M2 |
| CAP-INF-05 | language-identify | INFORMATION | DETERMINISTIC_CODE | IMPLEMENTED | M2 |
| CAP-INF-06 | text-representation-build | INFORMATION | HYBRID | CONTRACTED | M2 |
| CAP-MEA-01 | decision-invoke | MEASUREMENT | DETERMINISTIC_CODE | IMPLEMENTED | M2 |
| CAP-MEA-02 | relevance-measure | MEASUREMENT | MODEL_ADAPTER | IMPLEMENTED | M2 |
| CAP-MEA-03 | growth-value-measure | MEASUREMENT | MODEL_ADAPTER | CONTRACTED | M2 |
| CAP-MEA-04 | recency-compute | MEASUREMENT | DETERMINISTIC_CODE | CONTRACTED | M1 |
| CAP-MEA-05 | decision-impact-measure | MEASUREMENT | MODEL_ADAPTER | CONTRACTED | M2 |
| CAP-MEA-06 | maturity-classify | MEASUREMENT | MODEL_ADAPTER | CONTRACTED | M2 |
| CAP-MEA-07 | claim-evidence-assess | MEASUREMENT | MODEL_ADAPTER | CONTRACTED | M2 |
| CAP-MEA-08 | exploration-value-measure | MEASUREMENT | MODEL_ADAPTER | CONTRACTED | M2 |
| CAP-MEA-09 | source-credibility-evidence | MEASUREMENT | RULE_SET | CONTRACTED | M2 |
| CAP-MEA-10 | importance-assess | MEASUREMENT | MODEL_ADAPTER | CONTRACTED | M2 |
| CAP-SEL-01 | eligibility-screen | SELECTION | RULE_SET | IMPLEMENTED | M1 |
| CAP-SEL-02 | priority-components | SELECTION | RULE_SET | IMPLEMENTED | M1 |
| CAP-SEL-03 | portfolio-construct | SELECTION | RULE_SET | IMPLEMENTED | M1 |
| CAP-SEL-04 | capacity-constraint-check | SELECTION | RULE_SET | IMPLEMENTED | M1 |
| CAP-SEL-05 | decision-emit | SELECTION | DETERMINISTIC_CODE | IMPLEMENTED | M1 |
| CAP-DEL-01 | delivery-object-build | DELIVERY | DETERMINISTIC_CODE | IMPLEMENTED | M1 |
| CAP-DEL-02 | presentation-render | DELIVERY | DETERMINISTIC_CODE | IMPLEMENTED | M2 |
| CAP-DEL-03 | push-dispatch | DELIVERY | DETERMINISTIC_CODE | IMPLEMENTED | M2 |
| CAP-DEL-04 | delivery-status-record | DELIVERY | DETERMINISTIC_CODE | IMPLEMENTED | M2 |
| CAP-RUN-01 | run-manager | RUNTIME | DETERMINISTIC_CODE | IMPLEMENTED | M1 |
| CAP-RUN-02 | checkpoint-resume | RUNTIME | DETERMINISTIC_CODE | IMPLEMENTED | M1 |
| CAP-RUN-03 | run-manifest-persist | RUNTIME | DETERMINISTIC_CODE | IMPLEMENTED | M1 |
| CAP-RUN-04 | scheduler-entry | RUNTIME | DETERMINISTIC_CODE | IMPLEMENTED | M2 |
| CAP-RUN-05 | resource-release | RUNTIME | DETERMINISTIC_CODE | CONTRACTED | M1 |
| CAP-FBK-01 | feedback-capture | FEEDBACK | DETERMINISTIC_CODE | IMPLEMENTED | M3 |
| CAP-FBK-02 | outcome-association | FEEDBACK | DETERMINISTIC_CODE | IMPLEMENTED | M3 |
| CAP-FBK-03 | policy-change-propose | FEEDBACK | AI_AGENT | PROPOSED | M3 |
| CAP-FBK-04 | observation-detect | FEEDBACK | DETERMINISTIC_CODE | IMPLEMENTED | M3 |
| CAP-PUP-01 | policy-diff-evaluate | POLICY_UPDATE | DETERMINISTIC_CODE | IMPLEMENTED | M3 |
| CAP-PUP-02 | rule-activation-gate | POLICY_UPDATE | HUMAN_GATE | IMPLEMENTED | M3 |
<!-- END GENERATED INDEX -->

---

## 7. 新增一个能力的步骤

1. 先问：现有能力能否扩展？能力只在**职责边界真的不同**时才新增
   （v0.3 §5.2："不要为了未来可能性一次创建大量空壳"）。
2. 在 `registry.yaml` 的 `capabilities` 末尾追加条目，填全必填字段；
   `unknown_behavior` 必须取自 `docs/03-error-and-unknown-semantics.md` §2。
3. 跑 `python tools/validate_registry.py`，按报错修正。
4. 跑 `python tools/validate_registry.py --index`，把新表粘贴回本文件 §6。
5. 跑 `--backlog` 与 `--fixtures`，确认新对象进了待办、新跨里程碑依赖已被识别。

---

## 8. 尚未落地的对象契约（WP-R2 待办）

运行 `python tools/validate_registry.py --backlog` 可得到当前被引用但还没有 schema 文件的
`schema_ref` 清单（当前 22 个）。这份清单是**从注册表推导**出来的，不需要手工维护；
它同时就是 WP-R2 的对象契约清单。每个对象落地后，清单自动缩短
（已落地 8 个：PolicyBundle、PolicyConfig、DemandSnapshot、CandidateRecord、
MeasurementRecord、SelectionDecision、DeliveryObject、RunManifest，见 `docs/06-object-contracts.md`）。
