# 系统架构地图

> 给"三个月后的自己"和"第一次看这个仓库的人"用。
> 每一节都指向具体文件；不要在这里重复代码细节，代码会变，地图只标位置。

---

## 1. 一条数据主线

```text
来源 URL
  │  ingest/fetch.py             受控抓取（白名单 / robots / 重试 / 字节上限）
  ▼
FetchResult ── ingest/normalize.py ──▶ CandidateRecord
  │                 ├─ ingest/urls.py        规范化键
  │                 ├─ ingest/language.py    语种启发式
  │                 └─ ingest/identity.py    发布者 / 上游 / 独立来源计数
  ▼
MeasurementRecord ◀── CAP-MEA-02 ◀── capabilities/adapters/models/{invoke,measurement}.py
  │                                        （模型注册表：models/registry.yaml）
  ▼
SelectionDecision ◀── selection/engine.py + selection/policy.yaml
  ▼
DeliveryObject ◀── delivery/builder.py
  ▼
RenderedBrief ◀── delivery/renderer.py
  ▼
DeliveryStatus ◀── delivery/push.py（幂等账本）
  ▼
RunManifest ◀── runtime/manifest_shared.py
```

离线版把最上面两段换成夹具：`runtime/offline_run.py`。
在线版：`runtime/live_run.py`。调度：`runtime/scheduled_entry.py`。

---

## 2. 模块地图

| 目录 | 负责 | 不该做什么 |
|---|---|---|
| `contracts/schemas/` | 23 个对象契约 + 公共定义 | 不做业务判断 |
| `capabilities/` | 能力注册表与适配器边界 | 不直接决定推荐 |
| `selection/` | 准入、分量、组合、容量 | **不导入模型/网络**（AST 测试保证） |
| `delivery/` | 装配、渲染、推送 | 不重新筛选、不加事实 |
| `ingest/` | 抓取与规范化 | 不做语义判断 |
| `feedback/` | 反馈、结果、检测、提案、对照、激活 | 不自己签名 |
| `runtime/` | 锁、状态机、检查点、调度、清单 | 不解释内容 |
| `tools/` | 校验器与演示脚本 | 不参与运行时决策 |
| `tests/` | 回归测试 | — |

---

## 3. 不变量（谁都改不了）

| 不变量 | 落在哪 |
|---|---|
| 选择阶段不调用模型 | `INV-02`（schema + 校验器）、`test_selection.py` 的 AST 扫描 |
| 状态与原因码必须相容 | `tools/validate_objects.py:REASON_ALLOWED` |
| 失败也写清单，错误正文不进清单 | `runtime/run_manager.py` + `errors.json` |
| 推送幂等 | `delivery/push.py` 的 `idempotency_key` |
| 规则变更必须报告挤压 | `_check_diff_squeezes` |
| 规则激活必须有人签名 | `feedback/activation.py` |
| 抓取默认拒绝 | `ingest/fetch.py:_host_allowed` |

---

## 4. 怎么操作

```powershell
# 1) 契约与注册表是否自洽
python tools/validate_registry.py
python tools/validate_objects.py --all-fixtures

# 2) 离线跑一轮（夹具）
python -m runtime.offline_run --case fixtures/m1/basic --run-id run-demo --out runs/demo

# 2.5) 记一条反馈，然后看检测器怎么说
python -m feedback.cli user-stated --store runs/feedback --id fb-1 --candidate cand-001 --text "..."
python -m feedback.detector --runs runs --feedback runs/feedback

# 3) 在线跑一轮（本机演示服务器）
python -m tools.demo_live_run --out runs/demo-live

# 4) 调度决策（默认不启用）
python -m runtime.scheduled_entry --config config/schedule.example.yaml --mode offline --case fixtures/m1/basic --out runs/sched

# 5) 规则回路演示（含命令行批准）
python -m feedback.diff --proposal runs/demo-approval/proposal.json --case fixtures/m1/basic --out runs/diff.json
python -m feedback.approve --proposal runs/demo-approval/proposal.json --diff runs/diff.json --store policy/versions --approver "你" --confirm RULE-ADJACENT-CHANNEL --dry-run

python -m tools.demo_activation --out runs/demo-activation

# 5.5) 端到端验收：M1 + M2 + M3 + 定时（7 步，产物在 runs/acceptance）
python tools/acceptance.py

# 6) 一条命令检查全仓库自洽（契约 + 夹具 + 文档数字 + 测试）
python tools/check.py

# 7) 只跑测试
python -m pytest -q
```

---

## 5. 每个 WP 落在哪

| WP | 内容 | 主要文件 |
|---|---|---|
| R0 | 基线审计 | `docs/00-baseline-inventory.md`、`docs/01-open-conflicts.md` |
| R1 | 能力注册 | `capabilities/registry.yaml`、`tools/validate_registry.py` |
| R2 | 对象契约 | `contracts/schemas/`、`fixtures/contracts/` |
| R3 | 适配器边界 | `capabilities/adapters/models/`、`models/registry.yaml` |
| R4 | 选择器 | `selection/` |
| R5 | 离线链路 | `runtime/offline_run.py` |
| R6 | 抓取与规范化 | `ingest/` |
| R7 | 运行管理 | `runtime/{lock,run_manager,schedule,checkpoints}.py` |
| R8 | 交付与推送 | `delivery/` |
| R9 | 反馈与规则 | `feedback/` |
| R10 | 真实运行 | 未开始（缺外部条件） |

---

## 6. 真实 vs 桩

| 部分 | 状态 |
|---|---|
| 选择、交付、推送、运行管理、规范化、抓取 | 真实实现 |
| 模型测量 | **测试桩**，`method.kind=RULE`，带"未验证"标记 |
| 来源 | **本机 HTTP 服务器**，没有公网 |
| 推送通道 | 本地文件，没有消息服务 |
| 语言 | 启发式，只覆盖中/英 |

看测试时先看 `tests/test_model_boundary.py` 与 `tests/test_live_run.py`，
它们标出了"哪些是真的、哪些只是形状对"。

---

## 7. 下一步（如果只有时间做一件事）

补 `CAP-FBK-03` 的 Agent 判断：`feedback/detector.py` 已经能发现"最近三轮都没有交付"
这类模式，但**改什么**仍要人或 Agent 来定。目前提案必须由人写出 `changes`，
这是 M3 唯一还没闭环的一环。
