# 模型边界（Model Boundary）

> **交付物对应**：v0.3 §5（能力接口固定、实现可替换）、§10 WP-R3 的离线部分、`CAP-MEA-01`。
> **当前状态**：边界与契约已实现并有测试；**没有任何真实模型**，只有一个确定性测试桩。

---

## 1. 为什么先做边界，而不是先选模型

如果先接模型，业务代码会不知不觉长在某个 SDK 上：prompt 拼在选逻辑里、revision 写死在字符串里、
异常处理到处开花。等到要换模型时，改的不是一个适配器，而是整条链路。

所以顺序反过来：**先定边界，再往里塞实现**。现在边界已经能证明一件事——
换掉适配器，Selector 一行都不用改（`test_swapping_adapters_changes_measurements_not_the_flow`）。

---

## 2. 调用链

```text
CandidateRecord ──build_request──▶ DecisionRequest
                                        │
                                   invoke_decision
                                        │  ├─ 校验请求契约
                                        │  ├─ 校验注册表授权 / 状态 / 语言
                                        │  ├─ 调 adapter.evaluate()
                                        │  └─ 填入 profile 身份 + 校验结果契约
                                        ▼
                                  DecisionResult
                                        │
                                   to_measurement
                                        ▼
                                 MeasurementRecord
```

**适配器能做的只有一件事**：读 request，返回 `AdapterOutput(status, label, evidence)`。
它**不能**填写 `model_profile_id`、`model_revision`、`adapter_version`——
这些由 `invoke_decision` 从注册表填入。测试 `test_profile_metadata_cannot_be_spoofed`
用两个只有 revision 不同的 profile 证明这一点。

---

## 3. `invoke_decision` 的五道检查

| # | 检查 | 失败时 |
|---|---|---|
| 1 | 请求符合 `DECISION-REQUEST/1.1` | 抛 `AdapterError` |
| 2 | `capability_id` 在该 profile 的授权列表里 | 抛 `AdapterError` |
| 3 | 适配器版本与注册表一致 | 抛 `AdapterError` |
| 4 | 正式运行时必须是 `REAL` + `ACTIVE` | 抛 `AdapterError` |
| 5 | 判断语言 ∈ `validated_languages` | 返回 `ABSTAIN` + `OUT_OF_VALIDATED_LANGUAGE_SCOPE` |

第 5 条是重点：**不是所有失败都该抛异常**。语言没验证过是"能力范围不足"，
属于正常的弃权，必须变成可审计的 `ABSTAIN`，而不是崩掉或者硬判。

适配器内部异常统一映射为 `ERROR + ADAPTER_ERROR`（新增的原因码），
业务层永远看不到 traceback。

---

## 4. 注册表规则（`models/registry.yaml`）

- `revision` 不允许 `latest / main / master`（schema 正则强制）。
- `status=ACTIVE` 必须同时满足：`implementation_kind=REAL`、`validated_languages` 非空、
  `eval_report_id` 非空。**没有评测报告就不许激活**。
- `resource_profile` 四项全部是"本机实测值"，没有测就填 `null`。
  厂商声明不能写进这里（v0.3 §6.1 的教训）。
- 当前注册表里只有两个 `STUB`：`MODEL-PROFILE-STUB-AGENT`、`MODEL-PROFILE-STUB-VISUAL`，
  `status: CANDIDATE`、`implementation_kind: STUB`，**结构上不可能被激活**。

```powershell
python -c "from capabilities.adapters.models.registry import load_profiles, validate_profiles; print(validate_profiles(load_profiles()))"
```

---

## 5. 测试桩的定位

`KeywordStubAdapter` 只是把关键词命中映射成标签。它：

- 不代表任何真实模型的能力；
- 只能在 `allow_unvalidated=True` 时运行；
- 产出的 `MeasurementRecord.method.kind` 是 `RULE` 而不是 `MODEL`；
- `uncertainties` 里必须写明"适配器为未验证测试桩"。

拿它做演示可以，做决策不行。

---

## 6. 验收测试（`tests/test_model_boundary.py`）

| 保护的性质 | 测试 |
|---|---|
| 注册表合法、桩不可激活 | `test_registry_is_valid`、`test_stub_is_registered_but_not_activatable` |
| 桩不能进正式运行 | `test_stub_is_refused_in_production` |
| 显式放行后可用 | `test_stub_runs_only_when_explicitly_allowed` |
| 适配器版本必须匹配 | `test_adapter_version_must_match_registry` |
| 身份由注册表决定 | `test_profile_metadata_cannot_be_spoofed` |
| 未验证语言弃权 | `test_unvalidated_language_abstains` |
| 已验证语言出测量 | `test_validated_language_produces_a_measurement` |
| 异常变 ERROR | `test_adapter_exception_becomes_error` |
| 越界标签变 INVALID | `test_label_outside_options_is_invalid` |
| 未验证测量被标记 | `test_unvalidated_measurement_is_flagged` |
| 翻译路径保留引用 | `test_translation_path_keeps_reference` |
| **换模型不改流程** | `test_swapping_adapters_changes_measurements_not_the_flow` |

---

## 7. 已知缺口（必须等真实模型才能补）

1. **零真实模型**：`LaYA`、`Strands Decider` 都没有下载、没有跑过、没有评测报告。
2. **没有评测集**：`Personal Information Decision Set` 还不存在，唯一指标是"能跑通"。
3. **没有显存与耗时实测**：`resource_profile` 全是 `null`，8GB 预算仍是设计值不是实测值。
4. **没有 shadow 模式**：`SHADOW` 状态在 schema 里，但没有并行对照的实现。
5. **没有超时与批处理**：适配器调用是同步的，长上下文会一直等；
   `run_limits.model_timeout_seconds` 仍是 `null`。
6. **没有概率校准**：`probabilities` 一旦出现就要求 `calibration_profile_id`，
   但目前没有任何校准流程。

## 9. 真实模型（2026-10-18 接入）

| 项 | 值 |
|---|---|
| 档案 | `MODEL-PROFILE-QWEN25-3B` |
| 权重 | Qwen2.5-3B-Instruct（ModelScope 下载，Apache-2.0） |
| 内容标识 | `72629def1f9e04ae`（两个 safetensors 分片哈希合并） |
| 运行 | RTX 4060 Laptop 8GB，transformers + bfloat16 |
| 冷启动 | 12.4s |
| 单条耗时 | 0.334s |
| 显存峰值 | 5947MB |

### 评测与激活规则

留出集 `relevance-v2-heldout`（20 条，中英各半，写于提示词定稿之后）：

| 模型 | 总体 | 中文 | 英文 |
|---|---|---|---|
| Qwen2.5-3B | 65.0% | **80.0%** | 50.0% |
| Qwen2.5-1.5B | 25.0% | 20.0% | 30.0% |

只有中文达到可用线，所以 `validated_languages: [zh-Hans]`。
英文请求不会偷偷降级：`invoke_decision` 返回
`ABSTAIN / OUT_OF_VALIDATED_LANGUAGE_SCOPE`，在测量记录里看得见。

1.5B 保持 `CANDIDATE`，作为对照与回退候选。

## 10. 与"判断模型"的差距（2026-10-18 记）

v0.3 §6 指定的判断模型是 **Strands Decider 2B**（Qwen3.5-2B-Base + LoRA + pointer head）
或 **LaYA**（421M typed decisions），不是通用指令模型。

当前状态：

| 项 | 状态 |
|---|---|
| Strands Decider 2B 仓库 | 已下载（LoRA、pointer head、评测产物） |
| 依赖的基座 Qwen3.5-2B-Base | **未获取**（hf-mirror 返回 403；权限或名称待核对） |
| `strands-decider` 运行时包 | **未安装成功**（pip 安装在时间预算内未完成） |
| 现在跑的是 | Qwen2.5-3B-Instruct（通用模型），角色只是**相关性粗筛** |

为什么这件事重要：通用模型输出自由文本，再用正则收窄成标签；
判断模型输出的是 **typed decision**（noul / choice / score + 指针头），
天然没有"模型自由发挥"这一步。前者可以当加速器，不能当"判断依据"。

下一步（待确认）：把 Qwen3.5-2B-Base 与 `strands-decider` 装起来，
用同一套 20 条留出集对比"通用模型 + 提示词"与"typed decision"的准确率与拒绝行为。
