# 链接抓取与取证链（2026-10-18）

> 目标：不只靠 RSS 拿内容，而且每一条都能回答「它从哪来、什么时候抓的、抓的东西有没有被改过」。

## 1. 为什么放弃「只用 RSS」

上一轮试了 12 个艺术/音乐类 RSS，全部失败，于是得出「网上没有这些内容」的结论。
这个结论是错的：**错在方法**。RSS 是少数站点才提供的接口，网页本身从来不缺。

换成「列表页 + 路径正则」之后，同样一台机器、同样的网络，可通行来源从 4 个变成 10 个。

## 2. 方法：列表页 + 路径正则

```
列表页 HTML ──路径正则──▶ 文章链接 ──逐篇抓取──▶ 正文 + 取证字段
```

为什么是路径正则，而不是「通用正文抽取」或「自动找列表」：

- **正则可断言**：它等于我们对「这个站点的文章长什么样」的唯一声明，可以写进配置文件、被人复核；
- **正则可复现**：同一份 HTML 永远得到同一组链接，不会因为启发式阈值不同而漂移；
- **错了会立刻暴露**：正则写错时命中 0 条或命中一堆导航链接，一眼能看出来。

不做 JS 渲染：需要渲染才能拿到列表的站点（站酷、虎嗅）直接放弃，而不是假装抓到了。

## 3. 可达性实测

| 结果 | 站点 | 原因 |
|---|---|---|
| 可用 | Midifan 叉烧网 | 音乐制作/音频设备 |
| 可用 | 数英、广告门 | 设计/营销案例 |
| 可用 | 谷德设计网 | 建筑设计 |
| 可用 | It's Nice That、Eye on Design | 视觉设计（英文） |
| 可用 | Colossal、Hyperallergic | 艺术（英文） |
| 可用 | Aeon Essays | 思想/哲学（英文） |
| 可用 | 钛媒体 | 科技（故意保留的干扰项来源） |
| 不可用 | 设计癖、在库言库、Noema、AudioBar | TLS 握手超时 |
| 不可用 | 站酷、虎嗅 | 首页 0 个 `<a>`，内容靠 JS 渲染 |
| 不可用 | 极客公园、MIT Press Reader | HTTP 403 |
| 不可用 | 建筑学院、中国音响 | 列表页 404 / 链接太少 |
| 主动放弃 | 古田路 9 号 | `robots.txt` 禁止，尊重它 |

## 4. 来源登记表

`config/sources-index.yaml` 是「我们会去哪些地方找东西」的唯一事实来源。每条包含：

| 字段 | 作用 |
|---|---|
| `id` / `name` | 稳定标识，同一来源重抓不会变 |
| `domain` / `language` | 用于统计与语言作用域判断，不覆盖实际检测结果 |
| `indexes[].url` / `pattern` | 列表页地址 + 判定「这是文章」的路径正则 |
| `exclude` | 明确排除的导航路径 |
| `per_source` | 单个来源每轮抓多少条 |
| `status` | `VERIFIED`（实测能拿到正文）/ `UNSTABLE`（列表噪声大） |
| `why` | 为什么收这个源——给三个月后的自己看 |

抓取白名单由登记表**生成**，不是手写维护：手写的白名单一定会和登记表脱节。

## 5. 取证链：四件套

每条内容落盘时同时写入：

```json
{
  "item_id": "item-5854d9f22c8d",
  "source_id": "SRC-MIDIFAN",
  "index_url": "https://www.midifan.com/",
  "index_fetched_at": "2026-10-18T08:07:41+00:00",
  "list_position": 83,
  "matched_rule": "/modulearticle-detailview-7891.htm",
  "requested_url": "https://www.midifan.com/modulearticle-detailview-7891.htm",
  "final_url": "https://www.midifan.com/modulearticle-detailview-7891.htm",
  "article_fetched_at": "2026-10-18T08:07:41+00:00",
  "http_status": 200,
  "content_digest": "sha256:e9de733ab835fd9a...",
  "raw_ref": "runs/crawl-.../raw/item-5854d9f22c8d.html"
}
```

回溯任何一条内容只需要四步（输出目录里有一份同样的 README）：

1. `provenance.jsonl` —— 按 `item_id` 找到列表页地址、抓取时间、列表位置、匹配到的路径、HTTP 状态、内容摘要；
2. `discovered.json` —— 该列表页当时用哪条正则、共抽到多少链接、为什么挑中这些；
3. `raw/<item_id>.html` —— 原始 HTML，可重新抽正文核对摘要是否一致；
4. `crawl-manifest.json` —— 本次时间、来源清单、失败原因汇总。

`item_id` 由规范化 URL 的 sha256 前 12 位生成：同一条内容重抓仍是同一个 id，
标注与反馈不会因为重抓而断链。

## 6. 过程中修掉的两个真 bug

**（1）字符集**：Midifan 响应头不带 charset，只在 HTML 里写 `gbk`。
原来的实现一律按 utf-8 解，正文变成一堆替换字符，而「抓到了但内容不可读」
会以「质量分低」的形式悄悄污染标定数据——这比抓不到更危险。
现在先看响应头，再看 `<meta charset>`，`gb2312/gbk` 一律升到 `gb18030`。

**（2）相对 canonical**：Aeon 的 `<link rel="canonical" href="/">` 是相对地址，
直接写进 `FetchResult` 会违反契约并让整次抓取抛异常。现在统一按最终地址绝对化。

顺带修掉：链接路径的尾斜杠与 `?utm_*` 参数会制造「同一篇文章的多个身份」，
现在匹配前先规范化，去重也按规范化后的地址。

## 7. 结果

```
SRC-MIDIFAN          music        items=10 fail= 0
SRC-DIGITALING       design       items=10 fail= 0
SRC-ADQUAN           design       items=10 fail= 0
SRC-GOOOOD           architecture items=10 fail= 0
SRC-TMTPOST          technology   items=10 fail= 0
SRC-ITSNICETHAT      design       items= 8 fail= 0
SRC-EYEONDESIGN      design       items= 8 fail= 0
SRC-COLOSSAL         art          items= 8 fail= 0
SRC-AEON             philosophy   items= 6 fail= 0
SRC-HYPERALLERGIC    art          items= 6 fail= 0

合计 86 条 · 来源 10 个 · 失败 0 · 去重 0
按领域: music 10 / design 36 / architecture 10 / technology 10 / art 14 / philosophy 6
```

86 条全部通过契约校验并生成了 `CandidateRecord`；没有任何一条短于 800 字符。

## 8. 已知限制

| 限制 | 影响 |
|---|---|
| 只抓列表首页，未做分页 | 每轮最多拿到列表页当屏的条目 |
| 列表页改版会让正则失效 | 表现为「命中 0 条」，`crawl-manifest.json` 里能看到 |
| 英文内容占 36 条 | 判断模型的语言作用域只验证过中文，英文行的测量结果不能直接采信 |
| 只落原始 HTML，不落渲染后 DOM | JS 站点无法处理（已明确放弃，而不是静默失败） |

## 9. 一条命令

```
python tools/crawl.py --max-total 90
python tools/crawl.py --only SRC-MIDIFAN --per-source 5
python tools/crawl.py --derive-only runs/<既有抓取目录>
```

## 10. 抓到 ≠ 读到：跨页去导航（第九轮补）

第一版抓下来的正文长这样：

```
登陆账号 站内信 微信公众号 官方微博 二手微博 Midifan 厂商大全 知名厂商 A B C D E F ...
```

模型读的是导航栏，于是「Novation 88 键键盘评测」被判断成 `PRODUCT`（产品广告）。
**这不是模型判错，是我们喂错了。**

修法不靠站点适配，靠**同一来源多页重复**：一页里反复出现的行是页面框架，只在某页出现的才是内容。
同源 10 条就足以算出清单，而且确定性——同样的页集合永远得到同样的清单。

```
python tools/crawl.py --derive-only runs/crawl-20261018T080741Z
```

（`--derive-only` 不联网，只对已有 items.json 重算派生字段：规则改了不该逼着重抓一遍。）

| 来源 | 识别出的框架行 | 正文压缩比 |
|---|---|---|
| Midifan | 184 | 4%–44% |
| 数英 | 128 | 18%–31% |
| 广告门 | 59 | 14%–64% |
| 谷德 | 88 | 3%–43% |
| 钛媒体 | 98 | 9%–28% |
| It's Nice That | 113 | 7%–52% |
| Eye on Design | 75 | 14%–24% |
| Colossal | 80 | 27%–44% |
| Aeon | 11 | 1%–1% |
| Hyperallergic | 40 | 13%–53% |

去导航之后，Midifan 的正文第一句是：

```
在MIDI键盘的世界里，61键和88键之间的选择，从来不是一个简单的「键数多寡」问题，
而是一道关于工作流、空间成本和演奏哲学的判断。
```

**两个都留**：`text` 是原始正文（取证要看原文），`text_clean` 是去导航后的（测量要用干净文本）。
框架清单落在 `chrome/<source_id>.json`，线上单页抓取时可以直接套用。

效果（同一批 6 条音乐内容，只看质量分）：

| 条目 | 质量分（前 → 后） |
|---|---|---|
| Novation 88 键评测 | 0.63 → **0.80** |
| ICON aio2 评测 | 0.71 → 0.74 |
| 1176 压缩评测 | 0.78 → 0.81 |

分数仍然是 0——因为方向不对（音乐设备不是 AI 用户要的东西），这是**应该的**；
变的是质量与形态判断，而那才是标定参数真正依赖的输入。


## 11. 列表页能打开但一条都不命中（INDEX_EMPTY）

2026-10-19 真抓 7 个新来源时发现的。Check Point Research 第一次探测给了 57 个锚点、
命中 6 条；真抓那一刻它回了 202 和一个空壳页面，锚点数 0。
抓取记录里这一条显示为 items=0 fail=0——和一个"今天没更新"的站长得一模一样。

已经修掉：列表页命中 0 条时记一条失败

    {"stage": "INDEX", "url": ..., "reason_code": "INDEX_EMPTY", "http_status": 202, "anchors": 0}

理由是这条失败属于"看起来像正常"的那一类。之前抓取只在 HTTP 出错或正文抽不到时留痕，
而现在最坏的情况不是报错，是安静地返回 0 条：报告依然成功，审计依然通过，
只是那个来源从那天起再没贡献过内容，没人会注意到。

同一个源因此降为 UNSTABLE，理由写在登记表的 why 里，而不是留在这次对话里。


## 12. 代理只加在列表页上（2026-10-19）

接三个直连被阻断的源时踩到的，值得单独记一笔，因为它的表现形式非常像"站点问题"。

第一次给 Project Zero 加了 `--proxy` 之后的结果：

```
SRC-PROJECTZERO      security     items= 0 fail= 5 dup= 0
```

五条失败全部是 `stage: ARTICLE` / `reason_code: SOURCE_UNREACHABLE`，而列表页明明是 200、
抽到 5 条链接。原因是 `crawl_source()` 里有两处 `_policy_for()` 调用：
列表页那处传了代理，正文那处漏了。于是**列表通了、文章全挂**，
失败理由写的是"源不可达"——看起来像站点挂掉，实际是自己少传了一个参数。

更麻烦的是它很慢：每条正文按 2 次重试 × 20 秒算，5 条就是 200 秒，
在日志里表现为"卡住了"。也就是说，这个 bug 同时伪装成**站点故障**和**性能问题**。

修法是一行：正文抓取也传 `proxy`。修完同一条命令直接 `items=2 fail=0`。

教训：**故障分类的价值取决于参数有没有传全。** 分类表做得再细，
只要有一处漏传，所有信号都会指向错误的方向。所以 `INGEST` 这条链上凡是
"列表页一处、正文一处"的地方，都要当成一处漏传就会误导全局的地方来对待。
