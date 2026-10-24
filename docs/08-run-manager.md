# 运行管理（Run Manager）

> **交付物对应**：v0.3 §2（定时触发、单轮完成、自动退出）、§10 WP-R7、§13 DoD 第 10 条的一部分。
> **当前范围**：M1 需要的锁、阶段状态机、检查点、失败清单。
> **尚未做**：调度入口、超时、并发上限、推送幂等（属于 M2）。

---

## 1. 为什么先做锁

在没有锁的系统里，"定时任务"和"手动补跑"会在同一分钟相遇，结果是重复抓取、重复推送、
以及两份互相矛盾的 RunManifest。锁不是性能优化，是**正确性**的一部分。

实现（`runtime/lock.py`）：

- 文件存在即被持有；内容记录 `run_id / pid / acquired_at / host`。
- 获取用 `O_CREAT|O_EXCL`，由文件系统保证原子性。
- **失效接管**：如果持锁进程已经不存在，或持锁时间超过 `stale_after_seconds`（默认 6 小时），
  新运行可以接管，并在清单里留下 `reclaimed` 记录。
- 释放时校验 `run_id + pid` 是否是自己，避免误删别人的锁。

```text
acquire:
  create-exclusive ─成功→ 写入 → 返回 LockInfo
        │失败
        ├─ 读锁文件 → 进程活着且未超时 → LockHeld（拒绝启动）
        └─ 进程已死或超时 → 删除 → 重试一次 → 接管
```

---

## 2. 阶段状态机

`runtime/state.py` 固定了运行阶段（与能力阶段是两套枚举，映射见 C-12）：

```text
PRECHECK → DEMAND_BUILD → DISCOVERY → NORMALIZATION → MEASUREMENT
        → SELECTION → DELIVERY_BUILD → PUSH → PERSIST_RUN_REPORT → RELEASE_RESOURCES
```

M1 实际执行 `PRECHECK → SELECTION → DELIVERY_BUILD`，`PUSH` 标 `SKIPPED`。

规则：

1. 任一阶段抛异常 → 该阶段 `FAILED`，**后续阶段标 `BLOCKED` 且不执行**。
2. `run_status` 由阶段推导，调用方不能自己填：有 `FAILED` → `FAILED`；有 `PARTIAL` → `PARTIAL`；
   否则 `SUCCESS`。
3. `SKIPPED` 不写时间戳——没执行的事不该有开始时间。

---

## 3. 检查点

`runtime/checkpoints.py`：

- key = `sha256(阶段名 + 输入指纹)`；输入指纹来自**文件内容**，不看时间戳。
- 只复用 key 完全一致的成功产物；输入改一个字，缓存自动失效。
- M1 里 `SELECTION` 与 `DELIVERY_BUILD` 可缓存，`PRECHECK` 不缓存——预检必须每次真跑，
  否则"上次能读到的文件这次读不到"这类问题会被缓存掩盖。

检查点只解决"同一轮重试不重复计算"，不解决"跨轮复用"——后者要等真实来源稳定后再评估。

---

## 4. 失败也要留下证据

- 无论成功还是失败，`RunManifest` 都会写入（v0.3 §8.9）。
- 阶段错误在清单里只存 `error_ref`（如 `trace/run-x/selection/error`），**不存正文**；
  正文写进同目录的 `errors.json`。理由见 `docs/03-error-and-unknown-semantics.md` §6：
  状态字段只放引用，自由文本另存。
- 失败时 `delivery_status = NOT_ATTEMPTED`，不会被写成"成功"。

---

## 5. 一次失败运行长什么样

```text
runs/<run_id>/
  run-manifest.json      run_status=FAILED, stages: PRECHECK FAILED / SELECTION BLOCKED / ...
  errors.json            {"PRECHECK": "ValueError: cand-... 违反 candidate-record 契约：..."}
  checkpoints/           失败前的成功阶段（如果有）
```

没有 `delivery-object.json`——因为交付对象根本没被构造出来，而不是"构造了一个空的"。

---

## 6. 验收测试（`tests/test_run_manager.py`）

| 保护的性质 | 测试 |
|---|---|
| 同一时间只能有一个运行 | `test_lock_blocks_a_second_run` |
| 崩溃留下的锁可被安全接管 | `test_dead_pid_lock_is_reclaimed` |
| 失败后锁必须释放 | `test_lock_is_released_even_when_a_stage_fails` |
| 锁记录持有者 | `test_lock_records_owner` |
| 失败阻断下游 | `test_failed_stage_blocks_the_rest` |
| 跳过的阶段没有时间戳 | `test_skipped_stage_has_no_timestamps` |
| 检查点只复用一致输入 | `test_checkpoint_reuses_only_matching_inputs` |
| 检查点内容可复查 | `test_checkpoint_file_is_readable` |
| 失败也写清单 | `test_invalid_input_fails_the_run_but_still_writes_a_manifest`（M1 离线） |

---

## 7. 已知缺口

1. **没有调度入口**：`CAP-RUN-04` 未实现，现在只能手动触发。
2. **没有超时**：阶段卡住会一直等；`run_limits.max_duration_minutes` 仍是 `null`。
3. **没有并发上限以外的速率控制**：同一 run_id 不会并发，但不同 run_id 排队策略未定。
4. **检查点没有 TTL**：旧检查点会一直躺在目录里，等 M2 加清理。
5. **推送幂等未验证**：因为没有推送。等 `CAP-DEL-03` 落地后再补"重试不重复推送"的端到端测试。
