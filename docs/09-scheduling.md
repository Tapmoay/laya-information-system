# 调度（Scheduling）

> **交付物对应**：v0.3 §2.2（调度器职责）、§10 WP-R7、`CAP-RUN-04`。
> **定位**：本模块**不是**常驻守护进程。每次被操作系统定时器唤醒一次，
> 判断"该不该跑"，该跑就跑一轮然后退出。

---

## 1. 分工

| 谁 | 负责什么 |
|---|---|
| 操作系统（Windows 任务计划 / systemd timer / cron） | 在某个时刻启动进程 |
| `runtime/schedule.py` | 读配置、解析日历规则、判断该不该跑、处理错过 |
| `runtime/scheduled_entry.py` | 入口：决策 → 执行一轮 → 写 `trigger.json` → 退出 |
| `runtime/run_manager.py` | 锁、阶段、检查点、清单（见 `docs/08-run-manager.md`） |

**为什么不在应用里做常驻轮询**：v0.3 §1.2 第 9 条——`Scheduled Run ≠ Continuous Service`。
多一个常驻进程就多一份崩溃、内存泄漏和时区错误的可能。

---

## 2. 配置

```yaml
schedule:
  enabled: false                  # 手动跑通多轮之前保持关闭
  timezone: "Asia/Shanghai"       # 必填，没有隐式默认
  calendar_rule: "0 9 * * *"      # 标准 5 段 cron：分 时 日 月 周
  misfire_grace_seconds: 3600     # 超过这个延迟就算"错过"
  misfire_policy: RUN_ONCE        # RUN_ONCE | SKIP
  max_concurrent_runs: 1          # 只能为 1
  manual_trigger_enabled: true
```

示例文件：`config/schedule.example.yaml`。缺 `timezone` 直接报错，不会回落到主机时区。

日历规则支持 `*`、`*/n`、`a-b`、`a,b`，周日可写 `0` 或 `7`。
日与周同时被限定（都不是 `*`）时按 Vixie cron 语义**取并集**——"每月 1 号**或**每周一"。

---

## 3. 决策

```text
enabled = false                     → DISABLED
最近一个计划时刻不存在               → SKIP
last_run_at >= 最近的计划时刻        → ALREADY_RUN
延迟 <= misfire_grace               → RUN（misfire=false）
延迟 >  grace 且 policy = SKIP       → MISFIRE_SKIPPED
延迟 >  grace 且 policy = RUN_ONCE   → RUN（misfire=true，只补这一次）
```

- **不排队**：错过 3 次也只补 1 次。补跑多轮会让简报里堆满过期内容，也会和下一次计划撞车。
- **不重复**：`last_run_at` 由调用方传入（来自上一次 `RunManifest`），已跑过的计划时刻不会再跑。

每次决策都落一条 `TriggerRecord`（`trigger.json`），包含时区、规则、计划时刻、延迟、
策略与理由——"为什么今天没跑"必须是可查的。

---

## 4. 时间预算与 ABORTED

`RunManager(max_duration_seconds=...)` 给整轮一个墙钟预算：

- 每个阶段开始前检查；超预算就不再启动下一个阶段，剩余阶段记 `BLOCKED`。
- `run_status` 记为 `ABORTED`（不是 `SUCCESS`，也不是 `FAILED`）——
  没有东西坏掉，只是没做完。
- 阶段内部也可以调 `context.check_budget()` 主动放弃。这是**协作式**超时：
  Python 没法安全强杀正在执行的阶段，硬杀会留下半写文件。真正的强超时要等
  模型/网络调用下沉到子进程之后再做。

```text
ABORTED 与 FAILED 的区别：
  FAILED  = 某个阶段出错了（异常、契约违反）
  ABORTED = 还来得及，但预算用完了，主动停在这里
```

---

## 5. 接上操作系统定时器

以 Windows 任务计划为例（示例，未启用）：

```powershell
schtasks /Create /TN "LaYA-Daily" /SC DAILY /ST 09:00 /TR ^
  "cmd /c cd /d . && python -m runtime.scheduled_entry --config config\schedule.yaml --case fixtures\m1\basic --out runs\scheduled"
```

真实启用前必须先把 `enabled` 改成 `true`、把 `--case` 换成真实来源的入口，
并确认 `misfire_policy` 与宽限时间。

---

## 6. 验收测试（`tests/test_schedule.py`）

| 保护的性质 | 测试 |
|---|---|
| cron 能算出"上一个/下一个"计划时刻 | `test_daily_rule_matches_and_rolls_back_a_day`、`test_next_occurrence_*` |
| 星期与步长/列表字段 | `test_weekday_rule_only_fires_on_that_weekday`、`test_step_and_list_fields` |
| 时区必须显式 | `test_timezone_must_be_explicit`、`test_invalid_timezone_is_reported` |
| 未启用不跑 | `test_disabled_schedule_never_runs` |
| 已跑过不重复 | `test_already_ran_is_not_repeated` |
| 宽限内补跑、宽限外按策略 | `test_misfire_within_grace_still_runs`、`test_misfire_beyond_grace_skips_under_skip_policy`、`test_misfire_beyond_grace_catches_up_once_under_run_once` |
| 决策记录过契约 | `test_trigger_record_passes_contract` |
| 入口端到端 | `test_scheduled_entry_writes_trigger_and_runs`、`test_scheduled_entry_skips_when_disabled` |
| 超预算不撒谎 | `test_budget_exhaustion_aborts_instead_of_lying`、`test_stage_can_check_its_own_budget` |

---

## 7. 已知缺口

1. **没有在真实任务计划上验证过**：目前只在测试里模拟时钟。
2. ~~DST 边界未测~~ → **已测并修掉一个真 bug**（见 §8）。中国时区没有 DST，所以这个问题直到
   换成 `America/New_York` 才暴露。
3. **没有"运行中"探测**：如果上一次运行还没结束，新的一次会在拿锁时被拒绝，
   但不会通知用户。等推送通道落地后补通知。
4. **没有补跑队列**：明确不排队，符合 v0.3 的设计，但意味着长时间离线会丢内容。

---

## 7.5 重复运行防护与产物布局

真实调度器每 N 分钟被唤醒一次，没人会手动传 `--last-run`。于是：

1. **"上一次跑过没有"从产物里读**：`last_successful_run()` 扫描 `--out` 下的
   `run-manifest.json`，取 `run_status ∈ {SUCCESS, PARTIAL}` 的最大 `started_at`。
   失败的运行**不阻止**下一次尝试（`test_failed_run_does_not_block_the_next_attempt`）。
2. **每轮一个目录**：产物写进 `<out>/<run_id>/`。扁平布局会让第二轮覆盖第一轮，
   历史直接消失——这是测试先发现的（`test_next_day_runs_again` 一开始只找到一个清单）。
3. **触发记录留档**：`<out>/trigger.json` 是最新一次决策，`<out>/triggers/<trigger_id>.json` 是完整历史。
4. **没有 `--out` 就拒绝执行**：没有产物目录就没有"上次运行"的记忆，
   重复运行保护形同虚设，所以调度入口直接退出 2（`test_run_without_out_directory_is_refused`）。

实测两次唤醒：

```text
09:00 → action=RUN         last_run=None（来源：none）
09:05 → action=ALREADY_RUN last_run=2026-10-18T09:00:00+08:00（来源：manifest）
```

---

## 8. 夏令时：一个直到换时区才暴露的 bug

第一版用 `candidate <= cursor` 比较两个 `datetime`，两者共用同一个 `ZoneInfo`。
Python 在这种情况下**忽略时区、只比墙上时间**：

```text
02:30 EST = 07:30 UTC      ← 夏令时跳变当天并不存在的时刻
03:15 EDT = 07:15 UTC
墙上比：02:30 <= 03:15  →  "已经过了"
实际：  07:30 >  07:15  →  "还没到"
```

后果是 `previous_at_or_before` 会把一个未来时刻当成"最近一次计划时刻"，
在跳变当天可能提前触发或算错 `scheduled_for`。

修法：所有跨时刻比较统一先 `astimezone(timezone.utc)`（见 `_aware_utc`）。

两条回归测试：
- `test_spring_forward_gap_runs_once`：跳变当天只跑一次；
- `test_fall_back_hour_does_not_run_twice`：01:30 出现两次时也只跑一次。
