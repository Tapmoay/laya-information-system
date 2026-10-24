---
name: crawl-network
description: 抓取失败分类与代理操作：区分"抓不到"和"内容不好"，处理 TIMEOUT / TLS 阻断 / 403 / robots / 空壳列表页，新增国外来源时判断要不要走代理。当抓取出现失败码、某个源长期 0 条、或要加新源时使用。
---

# Skill: 抓取网络与代理（crawl-network）

> 触发场景：抓取出现失败（TIMEOUT / SOURCE_UNREACHABLE / 403 / 空壳），
> 或者要新增一个国外来源、要判断"这个源到底是内容不好还是我拿不到"。

## 一句话原则

**"抓不到"和"内容不好"是两件事。** 前者换条路就能解决，后者换什么路都没用。
先把这两件事分开，再决定要不要动代理、要不要放弃这个源。

## 第一步：先分类，再动手

拿到失败之后，先看 `reason_code`，不要一律当成网络问题。

| reason_code | 含义 | 该做什么 |
|---|---|---|
| `TIMEOUT` | 连接超时 | 大概是被墙。换代理再试 |
| `SOURCE_UNREACHABLE` + `detail` 含 `tls-blocked` | TLS 被中间阻断 | 换代理再试（实测 projectzero.google、huggingface.co、deepmind.google 属于这类） |
| `SOURCE_UNREACHABLE`（无 tls 标记） | 站点真的挂了或域名错 | 先手动访问确认，别急着上代理 |
| `SOURCE_PAYWALLED` | 401/402/403 | 反爬或付费墙。**不要改 User-Agent 骗过去**，那是绕过对方意愿 |
| `ACCESS_DISALLOWED` + `robots` | robots.txt 禁止 | **到此为止**。实测 reddit、lobsters 属于这类，不是抓不到，是人家不允许 |
| `SOURCE_GONE` | 404/410 | 源已失效，从登记表里撤掉 |
| `INDEX_EMPTY` | 列表页打开了但一条都没命中 | 两种可能：正则失效，或者站点回了空壳（实测 Check Point 回 202 挑战页）。看 `http_status` 与 `anchors` |
| `NEEDS_PROXY` | 登记表标了 `network: proxy`，但这次没给代理 | 加 `--proxy` 重跑即可，不是源的错 |

**还有一种失败不在这里**：列表页能打开、锚点很多、但都指不到文章（实测 Trail of Bits 73833 字节只有 1 个锚点、DeepMind 33 个锚点只有 1 个同主机）。
这是**列表靠 JS 渲染**，属于抓取层的设计边界，换代理没用。

## 第二步：开代理

本机（Windows）通常是 Clash Verge，HTTP 代理端口 **7897**。先确认它在跑：

```powershell
Get-NetTCPConnection -State Listen |
  Where-Object { $_.LocalPort -in 7897,7890,1080,10809 } |
  Select-Object LocalAddress,LocalPort,OwningProcess
```

看不到端口就先启动客户端，不要凭空猜一个端口写进命令里。

确认代理真的能通（**只测一个被墙的站，不要测能直连的站**——那种通过说明不了任何事）：

```powershell
python -c "import urllib.request as u; p={'https':'http://127.0.0.1:7897'}; o=u.build_opener(u.ProxyHandler(p)); print(o.open('https://huggingface.co', timeout=15).status)"
```

## 第三步：把代理交给抓取

两种给法，选一种：

```powershell
# 单次
python tools/crawl.py --field ai --proxy http://127.0.0.1:7897

# 整个会话（PowerShell）
$env:LAYA_PROXY = "http://127.0.0.1:7897"
python tools/crawl.py --field ai
```

代理是**机器属性，不是来源属性**，所以它不写进 `config/sources-index.yaml` 的具体地址里。
登记表里只标 `network: proxy` 表示"这个源直连进不来"，具体走哪个代理由命令行或环境变量给。

先验一遍再真抓：

```powershell
python tools/source_index.py --verify --proxy http://127.0.0.1:7897
```

## 第四步：收尾要留痕

- 新源能通过代理进来 → 登记表里写 `network: proxy`，并在 `why` 里写清"直连被阻断，必须走代理"。
- 实测过但进不来 → 写进 `docs/27-source-audit.md` 的第 5 节，并写明是"内容不好"还是"拿不到"。
- 正则改过之后 → 立刻 `--verify`。正则写错不会报错，只会永远匹配不上。
  反斜杠掉了（`\d` 变成 `d`）是最常见的一种，`--check` 现在会拦。

## 不要做的事

1. **不要为了过反爬改 User-Agent。** 换代理是换出口，改 UA 是伪装成别人。
2. **不要绕过 robots.txt。** 被禁止就记下来，然后换源。
3. **不要给 JS 渲染的站硬加等待。** 抓取层不做渲染是设计约束，破了这条，
   "同一份 HTML 永远得到同一组链接"就不再成立。
4. **不要把 `INDEX_EMPTY` 当成"今天没更新"。** 这两件事在日志里长得一样，处理方式完全不同。
