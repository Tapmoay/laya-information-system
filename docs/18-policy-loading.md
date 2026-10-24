# 政策读取与编译（Policy Loading）

> **交付物对应**：v0.3 §2.4 前置、§9.2（每轮输入）、`CAP-POL-01/02`。
> **意义**：系统第一次真正**读** `information-policy.md`，而不是靠夹具假装读过。

---

## 1. 读取（`policy/loader.py`）

`load_policy_bundle()` 做四件确定性的事：

1. 算每个文件的 `sha256` 与字节数；
2. 从**首个二级标题之前**抽自述版本（当前是 `v0.3`）；
3. 从 `## 10` 变更日志段落里抽另一个自述版本（当前是 `v0.2`）；
4. 两者不一致 → 记 `SELF_DECLARED_MISMATCH`。

这正是第一轮记录的 C-01 冲突。区别是：**现在它由代码发现，不由我读文档时记下来**。

### 三种状态

| 情况 | `state.status` |
|---|---|
| 没有冲突 | `OK` |
| 有冲突，显式 `proceed_allowed=True` | `PARTIAL`（`VERSION_CONFLICT`） |
| 有冲突，未获准 | `INVALID`（`VERSION_CONFLICT`） |

默认是 `INVALID`——**政策自己都说不出自己是哪一版时，系统不该假装知道**。

实测：

```text
declared_version             v0.3
declared_version_in_changelog v0.2
state                        INVALID（未获准）/ PARTIAL（获准）
```

---

## 2. 编译（`policy/compiler.py`）

容量上限**从政策原文里抽**，不是代码常量：

| 字段 | 来源正则 |
|---|---|
| `core_items_max` | `常规核心信息：最多 N 条` |
| `exploration_items_max` | `常规跨领域探索：最多 N 条` |
| `visual_match_max` / `visual_exploration_max` | `视觉通道单独运行时：最多 N 项匹配推荐 + M 项探索推荐` |
| `s_breakout_allowed` | 是否存在 `S 级重大技术变化可单独提示` |

**抽不到就报错**（`PolicyCompileError`），绝不退回到默认值。理由：政策措辞变了，
说明有人改了政策，应该有人去看；让代码悄悄用上一版数字，比报错危险得多。

语言默认值来自 ADR-0008（用户显式偏好），不从政策正文推断——政策里没有语言条款（C-05）。
`run_limits` 的 `null` 表示"尚未实测"，编译后仍然是 `null`，不会被填成任意常量。

---

## 3. 验收测试（`tests/test_policy_loader.py`）

| 保护的性质 | 测试 |
|---|---|
| 两个自述版本都能读到 | `test_bundle_reads_both_declared_versions` |
| 冲突默认阻止运行 | `test_self_declared_conflict_blocks_by_default` |
| 获准后可继续并留痕 | `test_conflict_can_be_acknowledged` |
| 期望版本不符也记录 | `test_expected_version_mismatch_is_recorded` |
| 哈希稳定、缺文件报错 | `test_hash_is_stable_and_missing_file_raises` |
| 容量真从原文抽出 | `test_capacity_is_extracted_from_policy_text` |
| 语言默认值符合 ADR-0008 | `test_language_defaults_follow_adr_0008` |
| 缺条款时拒绝编译 | `test_compile_fails_loudly_when_clause_is_missing` |
| 配置哈希确定且随内容变化 | `test_config_hash_is_deterministic`、`test_config_hash_ignores_nothing_it_should_not` |

---

## 4. 接进运行链路

`run_case(..., policy_doc=..., proceed_on_policy_conflict=...)` 让运行使用**从真实政策编译出来的**配置，
而不是夹具里的 `policy-config.json`：

```powershell
python -m runtime.offline_run --case fixtures/m1/basic --policy information-policy.md `
    --proceed-on-policy-conflict --out runs/policy-run
```

两条硬规则：

1. **政策包 `INVALID` 时预检直接失败**（默认行为），错误里带 `VERSION_CONFLICT`；
2. 获准继续时写 `PARTIAL`，并把 `policy-bundle.json` 与 `policy-config.json` 落进运行目录，
   清单的 `policy_version` 来自政策包，而不是夹具。

在线链路同样支持：`LiveRunConfig.policy_doc` / `proceed_on_policy_conflict`，
冲突同样在预检阶段阻断，并落下 `policy-bundle.json` 与 `policy-config.json`。

集成测试 `tests/test_policy_pipeline.py` 与 `tests/test_live_policy.py` 验证：编译配置能跑通、
冲突默认阻断、夹具容量与政策一致（否则说明夹具已过期）。

---

## 6. 配置项覆盖：每个字段要么被执行，要么被标记

`PolicyConfig` 的字段里，有一类最危险：**解析了、存下来了、传进链路了，然后没人读。**

`tests/test_config_knobs.py` 把每个字段强制归入两类之一：

| 类别 | 含义 | 当前 |
|---|---|---|
| `ENFORCED` | 有测试证明"改它会改变行为" | 5 个 |
| `DECLARED_NOT_ENFORCED` | 声明了但没接通，**必须写清缺什么** | 13 个 |

新增字段如果不归类，测试直接失败——不允许再出现"悄悄忽略"。

### 已接通

| 字段 | 证据 |
|---|---|
| `capacity.core_items_max` / `exploration_items_max` | `test_capacity_comes_from_config_not_from_code` |
| `capacity.s_breakout_allowed` | `test_s_level_candidate_may_break_capacity` |
| `language.delivery_language_mode` | 渲染层的语言模式与提示语 |
| `run_limits.max_duration_minutes` | `test_time_budget_comes_from_policy_config` |

### 未接通（节选）

| 字段 | 缺什么 |
|---|---|
| `capacity.visual_*` | 视觉通道未实现 |
| `language.retrieval_mode` / `allowed_retrieval_languages` | 没有检索器（CAP-INF-01/02 仍是 CONTRACTED） |
| `run_limits.stop_on_policy_version_mismatch` | 冲突处理当前由 `proceed_on_policy_conflict` 参数控制 |

### 重试与超时：政策给基线，来源配置可覆盖

上一轮列出的三个"两处各有一份"的字段已经合并，规则是：

1. **政策编译出的 `run_limits` 是基线**（`max_network_retries` → 抓取重试次数，
   `per_source_timeout_seconds` → 单源超时，`push_retry_limit` → 推送重试）；
2. **来源/推送配置可以逐项覆盖**——某个源确实需要更长超时，写在 YAML 里即可；
3. 都没写时才落到组件默认值。

`test_fetch_limits_come_from_policy_config`、`test_source_config_can_override_fetch_limits`、
`test_push_retry_limit_reaches_push_config`、`test_explicit_push_attempts_win` 分别钉住这四件事。

这样既不出现"改配置不生效"，也不会把"每个源都不一样"的情况锁死。

---
## 5. 已知缺口

1. **目标文档没有解析**：`GOAL` 角色只做哈希与版本记录，没有把目标条目结构化。
2. **C-01 仍未由你裁决**：机器只能发现冲突，改不改 §10 是你的决定。
3. **没有跨版本 diff**：两次读取之间政策改了什么，目前靠 git 或人眼。
4. **正则绑定中文措辞**：政策换一种说法，编译会失败——这是有意的，但意味着改政策时要同步改正则。
