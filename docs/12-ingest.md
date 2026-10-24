# 信息接入（Ingest）

> **交付物对应**：v0.3 §8.3、§10 WP-R6 的离线部分、`CAP-INF-03..05`。
> **范围**：抓取之后、测量之前的所有**确定性**处理。不联网、不调用模型。

---

## 1. 为什么这段必须是确定性代码

v0.3 §4 的责任分工写得很直白：时间解析、URL 规范化、内容哈希、精确去重、字段校验
**不消耗模型**。原因不是省钱，是**可复算**：模型今天说这两个 URL 是同一篇、明天可能说不是；
而 `sha256` 永远一样。

---

## 2. 流水线

```text
FetchResult ──build_candidate──▶ { CandidateRecord, SourceIdentity }
                                        │
                                   dedupe_candidates
                                        │
                                  （唯一候选 + 重复清单）
```

### 2.1 URL 规范化（`ingest/urls.py`）

| 规则 | 例子 |
|---|---|
| scheme / host 小写 | `HTTPS://Example.org` → `https://example.org` |
| 去掉默认端口 | `:443` / `:80` |
| 去掉末尾斜杠（根路径除外） | `/a/b/` → `/a/b` |
| 去掉跟踪参数 | `utm_*`、`fbclid`、`gclid`、`ref`… |
| 查询参数排序 | `?b=2&a=1` → `?a=1&b=2` |
| 去掉 fragment | `#section` |
| 非 http(s) 拒绝 | `ftp://` 抛错 |

### 2.2 内容摘要（`ingest/normalize.py`）

`sha256(空白折叠 + 去首尾 + 小写)`。大小写与空白差异不算新内容；
"改标题转载"**不算**重复——那需要证据判断，不是字符串比较。

### 2.3 时间（`parse_published_at`）

只接受带时区的时间。`2026-10-17`、`昨天`、没有偏移的 `2026-10-17T12:00:00`
一律视为"不知道发布时间"，写 `UNKNOWN + EVIDENCE_INSUFFICIENT`。
时区不明的"昨天"会把今天算进去，这类错误比缺字段更难发现。

### 2.4 语种（`ingest/language.py`）

用**汉字数 vs 英文词数**，不是字母数：

```text
ratio = 汉字数 / (汉字数 + 英文词数)
ratio >= 0.7 → zh-Hans
ratio <= 0.3 → en
其余        → MIXED
总数为 0    → UNKNOWN（method=UNKNOWN）
```

按字母算会把正常中文文本误判成偏英文（英文一个词平均五个字母）。
正文太短（<40 字符）时退回 `HTML_LANG` 声明；两者都不可用时保持 UNKNOWN。

---

## 3. 来源身份（`ingest/identity.py`）

| 证据 | relation | upstream |
|---|---|---|
| 页面 canonical link 指向**别的主机** | `REPOST` | `CONFIRMED` + evidence `fetch/canonical-link` |
| 页面自称"转载自 X"（无链接） | `REPOST` | `PROBABLE` |
| canonical 指向自身且有作者信息 | `ORIGINAL` | `null` |
| 什么都没有 | `UNKNOWN` | `null` |

**没有链接不等于原创。** 这是整套规则里最重要的一条：缺证据时关系是 `UNKNOWN`，
不是 `ORIGINAL`。发布者的 `identity_status` 也从不会仅凭页面信号升级为 `CONFIRMED`。

`independence_count()` 按"上游优先、否则发布者"计键：三个站转载同一篇只算 **1** 个独立来源
（policy §5.2）。

---

## 4. 去重（`ingest/dedupe.py`）

按 `(observed_at, candidate_id)` 排序后顺序扫描：

1. 规范化 URL 相同 → `SAME_URL`
2. 内容摘要相同 → `SAME_CONTENT`

保留最早观测到的那条，其余进重复清单（带 `duplicate_of`）。
确定性的部分只到这里；语义级"同一事件的不同报道"不合并。

---

## 5. 验收测试（`tests/test_ingest.py`）

| 保护的性质 | 测试 |
|---|---|
| 五种 URL 写法收敛 | `test_url_canonicalization` |
| 非 http(s) 拒绝 | `test_non_http_url_is_rejected` |
| 摘要忽略空白/大小写 | `test_digest_ignores_whitespace_and_case` |
| 无时区不算"知道时间" | `test_published_at_requires_timezone` |
| 语种启发式含 MIXED/UNKNOWN | `test_language_heuristic` |
| canonical 跨域确认上游 | `test_canonical_link_to_other_host_confirms_upstream` |
| 自称转载只是 PROBABLE | `test_upstream_hint_is_only_probable` |
| 无信号 → UNKNOWN | `test_no_signal_means_unknown_relation` |
| 三转载只算一个来源 | `test_reposts_of_one_origin_count_as_one_source` |
| 组装出的记录过契约 | `test_build_candidate_produces_a_valid_record` |
| 重定向被记录 | `test_redirects_are_recorded` |
| 抓取失败仍留记录 | `test_error_fetch_still_produces_a_record` |
| 按 URL / 按内容去重 | `test_dedupe_by_canonical_url`、`test_dedupe_by_content_digest` |

---

## 6. 已知缺口

1. **没有真实抓取**：`CAP-INF-02` 仍未实现，`FetchResult` 目前由夹具或测试构造。
2. **没有检索计划**：`CAP-INF-01`（多语言查询生成）依赖模型，未实现。
3. **没有监测范围**：`MONITORING-PROFILE/1.0` 还没建，候选的 `monitoring_profile_ref` 只能为空。
4. **付费墙只按状态码判断**（401/402/403），软付费墙识别不了。
5. **重定向链只记录终点**，中间跳转不留痕。
6. **不做语义去重**：同一事件的多篇报道仍会被当成多个候选。
