# 受控抓取（Fetch）

> **交付物对应**：v0.3 §10 WP-R6、`CAP-INF-02`；policy §0「不越权行动」与 §5「来源规则」。
> **当前状态**：实现完成、测试通过，但**从未访问过公网**——本机没有出站网络，全部验证跑在本地 HTTP 服务器上。

---

## 1. 默认拒绝

抓取不是"给个 URL 就下载"，它是对外部世界的写操作（请求、带宽、对方日志）。
所以三道门按顺序过：

```text
1. 主机白名单   —— 白名单为空 = 拒绝一切（不是允许一切）
2. robots.txt  —— 不允许就不发请求，记 ACCESS_DISALLOWED
3. HTTP 请求    —— 有限重试、显式超时、字节上限、自定义 UA
```

**重定向也要过白名单**：如果 30x 跳到未授权主机，直接拒绝并记录
（`store/policy/allowlist#/redirect`）。

---

## 2. 提取（`ingest/html_extract.py`）

用标准库 `HTMLParser`，只取确定性字段：

| 字段 | 来源顺序 |
|---|---|
| 标题 | `og:title` → `<title>` |
| 摘要 | `og:description` → `meta[name=description]` |
| canonical | `<link rel="canonical">` |
| 发布时间 | `article:published_time` → `datePublished` → `date` |
| 作者 / 站点 | `meta[name=author]` / `og:site_name` |
| 语言 | `<html lang>` |
| 正文 | 去 `script/style/noscript/template` 后的文本，折叠空白 |

不执行 JS、不做可读性打分——同一份 HTML 必须每次都得到同样的结果。

---

## 3. 失败映射

| 情况 | outcome | reason_code |
|---|---|---|
| 主机不在白名单 / 重定向出白名单 | `ERROR` | `ACCESS_DISALLOWED` |
| robots.txt 禁止 | `ERROR` | `ACCESS_DISALLOWED` |
| 401 / 402 / 403 | `ERROR` | `SOURCE_PAYWALLED` |
| 404 / 410 | `ERROR` | `SOURCE_GONE` |
| 其他 4xx / 5xx（重试耗尽） | `ERROR` | `SOURCE_UNREACHABLE` |
| 连接超时 | `ERROR` | `TIMEOUT` |
| 内容超过字节上限 | `PARTIAL_FETCH` | —（截断但成功） |

5xx 会重试（默认 3 次，退避 0.05s×次数）；4xx **不重试**——重试一个 404 只是浪费对方资源。
robots 不可读时放行（记不到规则等于没有规则），并在结果里保留 `body_ref` 便于复核。

---

## 4. 一个真实的 bug

第一版把 robots 地址写成 `{scheme}://{host}/robots.txt`，**丢掉了端口**。
测试服务器跑在随机端口上，于是 robots 请求打到了 80 端口、失败、被当成"没有规则"，
`/private/` 被直接抓走。

修复方式是缓存键与请求都改用 `netloc`（含端口）。
这类 bug 的可怕之处在于：**它不会报错，只会让防护静默失效**。

---

## 5. 怎么验证的

本机没有出站网络（`https://example.com` SSL 握手超时），所以测试全部跑在
`ThreadingHTTPServer` + `fixtures/html/` 上：

- 真实 HTTP 往返、真实状态码、真实 robots.txt、真实重定向行为；
- `/flaky` 路径前两次返回 500，用来验证重试确实发生（断言命中 3 次）；
- `/big.html` 超过 500 字节上限，验证截断而不是失败。

`fixtures/html/robots.txt` 里写的是 `Disallow: /private/`，对应测试
`test_robots_disallow_is_respected`。

---

## 6. 验收测试（`tests/test_fetch.py`）

| 保护的性质 | 测试 |
|---|---|
| OG 优先、脚本进不了正文 | `test_extract_prefers_open_graph_and_drops_scripts` |
| 正常抓取产出合法 FetchResult | `test_fetch_returns_a_valid_result` |
| 404 → GONE | `test_fetch_404_is_gone` |
| robots 生效 | `test_robots_disallow_is_respected` |
| 白名单外不发起请求 | `test_host_outside_allowlist_is_refused_without_connecting` |
| 空白名单 = 拒绝一切 | `test_empty_allowlist_denies_everything` |
| 字节上限截断 | `test_byte_limit_truncates_instead_of_failing` |
| 5xx 重试 | `test_transient_500_is_retried` |
| 中文页面编码 | `test_utf8_chinese_page` |
| 配置项拼写错误要报错 | `test_policy_rejects_unknown_keys` |

---

## 7. 已知缺口

1. **从未访问公网**：真实站点会返回压缩内容、反爬页面、JS 渲染页，这些都没验证过。
2. ~~不处理 gzip/br~~ → **gzip / deflate 已支持**，且按**解码后**的字节截断
   （按压缩包截断会得到半个 gzip 流、解码直接失败）。br 仍未支持。
3. **不处理 JS 渲染**：纯前端渲染的页面只能拿到空壳。
4. **robots 只按通配规则**：没有按 UA 分组解析，也没有 Crawl-delay。
5. **没有 Cookie / 登录态**：需要登录的来源抓不到，也不该抓。
6. **重定向链只记录终点**。
