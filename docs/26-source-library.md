# 26 · 信息源库：分类与索引

> 本文件由 `python tools/source_index.py --render` 生成，不要手改。
> 分类法与来源定义在 `config/sources-index.yaml`；哪个源符合要求、哪个要降级，是人的判断，写在 `docs/27-source-audit.md`。

## 1. 规模

共 **37** 个来源，覆盖 **11** 个领域、**7** 种来源形态；状态 `VERIFIED` 的 35 个。

分类维度不是装饰：`field` 决定它是什么主题，`kind` 决定它的密度和时效长什么样，`role` 决定它进核心位还是探索位。一个源说不出这三件事，就不该被每天抓。

## 2. 分类法

### 2.1 `field`

| 取值 | 含义 | 来源数 |
|---|---|---|
| `ai` | AI 模型、智能体、AI 工程与评测 | 12 |
| `security` | 网络安全、逆向工程、漏洞与攻防研究 | 6 |
| `engineering` | 软件工程、系统架构、分布式与运维实践 | 4 |
| `tooling` | 数字工具、效率方法与软硬件使用 | 3 |
| `design` | 视觉设计、平面与交互、广告创意 | 5 |
| `art` | 当代艺术、装置、影像与艺术批评 | 2 |
| `architecture` | 建筑、空间与构成 | 1 |
| `music` | 音乐制作、声音设计与音频设备 | 1 |
| `culture` | 游戏、亚文化与叙事 | 1 |
| `philosophy` | 思想、哲学与伦理 | 1 |
| `business` | 科技商业、产业与资本动向 | 1 |

### 2.2 `kind`

| 取值 | 含义 | 来源数 |
|---|---|---|
| `preprint` | 学术预印本：有摘要、有方法、可回溯到 abs 页 | 9 |
| `aggregator` | 人工或 AI 精编的聚合，每条都带一手来源链接 | 1 |
| `lab_release` | 机构一手公告、更新日志、官方技术博客 | 4 |
| `engineering_blog` | 工程团队或从业者的技术长文 | 5 |
| `media` | 行业媒体的报道与编译 | 13 |
| `critique` | 批评、评论与策展，作品与分析并重 | 4 |
| `community` | 社区讨论：从业者的实际抱怨与经验，密度低但环境价值高 | 1 |

### 2.3 `role`

| 取值 | 含义 | 来源数 |
|---|---|---|
| `core` | 服务长期主要方向，应该每天都看一眼 | 22 |
| `task` | 服务当前任务；任务换了，它就跟着换 | 6 |
| `explore` | 跨领域或陌生视角，主要喂探索位 | 7 |
| `control` | 同主题低密度对照源，用来暴露「凡是 AI 就收」这类偏差 | 2 |

### 2.4 `density`

| 取值 | 含义 | 来源数 |
|---|---|---|
| `high` | 读完能拿到机制、数据或可复现的做法 | 16 |
| `medium` | 有事实和判断，但还需要往一手材料追一层 | 18 |
| `low` | 以消息和观点为主，认知增量有限 | 3 |

## 3. 覆盖：每个目标靠哪些源喂

判断一个源该不该留的第一个问题不是它好不好，而是**没有它哪块会空**。

| 类别 | 层级 | 目标 | 说明 | 来源数 | 语言 |
|---|---|---|---|---|---|
| 长期目标 | T1 | `goal-agent-systems` | 主方向：示例领域 A（换成你自己的长期方向） | 19 | en、zh-Hans |
| 长期目标 | T2 | `goal-visual-narrative` | 第二方向：示例领域 B（可留空） | 11 | en、zh-Hans |
| 长期目标 | T3 | `goal-security` | 第三方向：示例领域 C（可留空） | 7 | en、zh-Hans |
| 任务候选 | T0 | `task-ai-agent` | 当前任务：示例任务（换成你正在推进的事）（2026-10-24 起的当前任务） | 10 | en、zh-Hans |
| 任务候选 | T0 | `task-security-foundation` | 网络安全基础与授权渗透测试入门（可在候选池里切换） | 4 | en、zh-Hans |
| 任务候选 | T0 | `task-system-build` | 系统构建：把要做的东西真的搭起来，而不是只读别人的做法 | 4 | en、zh-Hans |
| 任务候选 | T0 | `task-ai-project` | 其他 AI 项目：模型、工具链、评测与自用系统 | 8 | en、zh-Hans |
| 任务候选 | T0 | `task-virtual-intelligence` | 虚拟聚生智能：虚拟的 AI 人、AI 伴侣、AI 直播（含二次元虚拟主播） | 4 | en、zh-Hans |

## 4. 按领域索引

### ai（AI 模型、智能体、AI 工程与评测）

| id | 名称 | kind | role | 语言 | 密度 | 出口 | 状态 | serves |
|---|---|---|---|---|---|---|---|---|
| `SRC-AIHOT` | AIHOT AI 日报 | aggregator | core | zh-Hans | medium | direct | VERIFIED | goal-agent-systems, task-ai-project, task-virtual-intelligence, task-ai-agent |
| `SRC-HF-BLOG` | Hugging Face Blog | engineering_blog | explore | en | medium | proxy | VERIFIED | goal-agent-systems, task-ai-project, task-ai-agent |
| `SRC-LILIANWENG` | Lil'Log（翁丽莲） | engineering_blog | core | en | high | direct | VERIFIED | goal-agent-systems, task-ai-agent |
| `SRC-SIMONW` | Simon Willison's Weblog | engineering_blog | core | en | high | direct | VERIFIED | goal-agent-systems, task-ai-project, task-ai-agent |
| `SRC-ANTHROPIC` | Anthropic News | lab_release | core | en | medium | direct | VERIFIED | goal-agent-systems, task-ai-agent |
| `SRC-QBITAI` | 量子位 | media | core | zh-Hans | medium | direct | VERIFIED | goal-agent-systems, task-ai-project, task-ai-agent |
| `SRC-ARXIV-CS-AI` | arXiv cs.AI 最新 | preprint | core | en | high | direct | VERIFIED | goal-agent-systems, task-ai-project, task-ai-agent |
| `SRC-ARXIV-CS-CL` | arXiv cs.CL 最新 | preprint | core | en | high | direct | VERIFIED | goal-agent-systems, task-ai-project, task-virtual-intelligence, task-ai-agent |
| `SRC-ARXIV-CS-LG` | arXiv cs.LG 最新 | preprint | core | en | high | direct | VERIFIED | goal-agent-systems, task-ai-agent |
| `SRC-ARXIV-CS-MA` | arXiv cs.MA 多智能体 | preprint | core | en | high | direct | VERIFIED | goal-agent-systems |
| `SRC-ARXIV-CS-RO` | arXiv cs.RO 机器人 | preprint | core | en | high | direct | VERIFIED | goal-agent-systems |
| `SRC-HF-PAPERS` | Hugging Face Papers | preprint | core | en | high | proxy | VERIFIED | goal-agent-systems, task-ai-project, task-virtual-intelligence, task-ai-agent |

### security（网络安全、逆向工程、漏洞与攻防研究）

| id | 名称 | kind | role | 语言 | 密度 | 出口 | 状态 | serves |
|---|---|---|---|---|---|---|---|---|
| `SRC-SCHNEIER` | Schneier on Security | critique | task | en | medium | direct | VERIFIED | goal-security |
| `SRC-CHECKPOINT` | Check Point Research | lab_release | task | en | high | direct | UNSTABLE | goal-security, task-security-foundation |
| `SRC-PROJECTZERO` | Google Project Zero | lab_release | task | en | high | proxy | VERIFIED | goal-security, task-security-foundation |
| `SRC-ZDI` | Zero Day Initiative Blog | lab_release | task | en | medium | direct | VERIFIED | goal-security |
| `SRC-ANQUANKE` | 安全客 | media | task | zh-Hans | medium | direct | VERIFIED | goal-security, task-security-foundation |
| `SRC-ARXIV-CS-CR` | arXiv cs.CR 最新 | preprint | task | en | high | direct | VERIFIED | goal-security, task-security-foundation |

### design（视觉设计、平面与交互、广告创意）

| id | 名称 | kind | role | 语言 | 密度 | 出口 | 状态 | serves |
|---|---|---|---|---|---|---|---|---|
| `SRC-EYEONDESIGN` | Eye on Design (AIGA) | critique | core | en | medium | direct | VERIFIED | goal-visual-narrative |
| `SRC-ADQUAN` | 广告门 | media | core | zh-Hans | medium | direct | VERIFIED | goal-visual-narrative |
| `SRC-DIGITALING` | 数英 DIGITALING | media | core | zh-Hans | medium | direct | VERIFIED | goal-visual-narrative |
| `SRC-ITSNICETHAT` | It's Nice That | media | core | en | medium | direct | VERIFIED | goal-visual-narrative |
| `SRC-ARXIV-CS-HC` | arXiv cs.HC 人机交互 | preprint | explore | en | high | direct | VERIFIED | goal-visual-narrative, task-virtual-intelligence |

### engineering（软件工程、系统架构、分布式与运维实践）

| id | 名称 | kind | role | 语言 | 密度 | 出口 | 状态 | serves |
|---|---|---|---|---|---|---|---|---|
| `SRC-CLOUDFLARE` | Cloudflare Blog | engineering_blog | core | en | high | direct | VERIFIED | task-system-build, goal-security |
| `SRC-MARTINFOWLER` | martinfowler.com | engineering_blog | core | en | high | direct | VERIFIED | task-system-build, goal-agent-systems |
| `SRC-INFOQ` | InfoQ 中文 | media | core | zh-Hans | medium | direct | VERIFIED | task-system-build, goal-agent-systems |
| `SRC-ARXIV-CS-SE` | arXiv cs.SE 最新 | preprint | core | en | high | direct | VERIFIED | task-system-build, goal-agent-systems |

### tooling（数字工具、效率方法与软硬件使用）

| id | 名称 | kind | role | 语言 | 密度 | 出口 | 状态 | serves |
|---|---|---|---|---|---|---|---|---|
| `SRC-V2EX` | V2EX | community | explore | zh-Hans | low | direct | VERIFIED | goal-agent-systems, task-ai-project |
| `SRC-IFANR` | 爱范儿 | media | control | zh-Hans | low | direct | VERIFIED | goal-agent-systems |
| `SRC-SSPAI` | 少数派 | media | explore | zh-Hans | medium | direct | VERIFIED | goal-agent-systems |

### art（当代艺术、装置、影像与艺术批评）

| id | 名称 | kind | role | 语言 | 密度 | 出口 | 状态 | serves |
|---|---|---|---|---|---|---|---|---|
| `SRC-HYPERALLERGIC` | Hyperallergic | critique | core | en | medium | direct | UNSTABLE | goal-visual-narrative |
| `SRC-COLOSSAL` | Colossal | media | core | en | medium | direct | VERIFIED | goal-visual-narrative |

### architecture（建筑、空间与构成）

| id | 名称 | kind | role | 语言 | 密度 | 出口 | 状态 | serves |
|---|---|---|---|---|---|---|---|---|
| `SRC-GOOOOD` | 谷德设计网 | media | core | zh-Hans | medium | direct | VERIFIED | goal-visual-narrative |

### business（科技商业、产业与资本动向）

| id | 名称 | kind | role | 语言 | 密度 | 出口 | 状态 | serves |
|---|---|---|---|---|---|---|---|---|
| `SRC-TMTPOST` | 钛媒体 | media | control | zh-Hans | low | direct | VERIFIED | goal-agent-systems |

### culture（游戏、亚文化与叙事）

| id | 名称 | kind | role | 语言 | 密度 | 出口 | 状态 | serves |
|---|---|---|---|---|---|---|---|---|
| `SRC-GCORES` | 机核 | media | explore | zh-Hans | medium | direct | VERIFIED | goal-visual-narrative |

### music（音乐制作、声音设计与音频设备）

| id | 名称 | kind | role | 语言 | 密度 | 出口 | 状态 | serves |
|---|---|---|---|---|---|---|---|---|
| `SRC-MIDIFAN` | Midifan 叉烧网 | media | explore | zh-Hans | medium | direct | VERIFIED | goal-visual-narrative |

### philosophy（思想、哲学与伦理）

| id | 名称 | kind | role | 语言 | 密度 | 出口 | 状态 | serves |
|---|---|---|---|---|---|---|---|---|
| `SRC-AEON` | Aeon Essays | critique | explore | en | high | direct | VERIFIED | goal-visual-narrative |

## 5. 全部来源与它为什么在库里

#### `SRC-ADQUAN` 广告门

- 分类：design / media / core；语言 zh-Hans；出口 direct；每源预算 8
- 列表页：`https://www.adquan.com/`
  - 路径正则 `^/article/\d+$`，锚文本至少 8 字
- 为什么在库里：创意与品牌实务；口号和画面之外还有投放与人群约束

#### `SRC-AEON` Aeon Essays

- 分类：philosophy / critique / explore；语言 en；出口 direct；每源预算 5
- 列表页：`https://aeon.co/essays`
  - 路径正则 `^/essays/[a-z0-9-]+$`，锚文本至少 8 字
- 为什么在库里：思想与哲学长文；探索位需要的是这种东西，不是又一个科技号

#### `SRC-AIHOT` AIHOT AI 日报

- 分类：ai / aggregator / core；语言 zh-Hans；出口 direct；每源预算 10
- 列表页：`https://aihot.news/all`、`https://aihot.news/daily`
  - 路径正则 `^/items/[a-z0-9]+$`，锚文本至少 8 字
  - 路径正则 `^/items/[a-z0-9]+$`，锚文本至少 8 字
- 为什么在库里：每天 08:00 的精编日报，每条都标了原始来源并能跳回原文；适合快速判断「今天有没有值得深挖的东西」，不适合代替一手材料

#### `SRC-ANQUANKE` 安全客

- 分类：security / media / task；语言 zh-Hans；出口 direct；每源预算 6
- 列表页：`https://www.anquanke.com/`
  - 路径正则 `^/post/id/\d+$`，锚文本至少 8 字
- 为什么在库里：中文安全社区，有翻译也有原创分析；英文读不动的时候这是唯一能持续跟的入口

#### `SRC-ANTHROPIC` Anthropic News

- 分类：ai / lab_release / core；语言 en；出口 direct；每源预算 5
- 列表页：`https://www.anthropic.com/news`
  - 路径正则 `^/news/[a-z0-9-]+$`，锚文本至少 8 字
- 为什么在库里：模型能力边界的一手说法；判断二手解读是否夸大，只能回到这里

#### `SRC-ARXIV-CS-AI` arXiv cs.AI 最新

- 分类：ai / preprint / core；语言 en；出口 direct；每源预算 6
- 列表页：`https://arxiv.org/list/cs.AI/recent`
  - 路径正则 `^/abs/\d{4}\.\d{4,5}$`，锚文本至少 8 字
- 为什么在库里：AI 的通用方法层；摘要本身就是一份完整的机制说明，比媒体转述可信得多

#### `SRC-ARXIV-CS-CL` arXiv cs.CL 最新

- 分类：ai / preprint / core；语言 en；出口 direct；每源预算 6
- 列表页：`https://arxiv.org/list/cs.CL/recent`
  - 路径正则 `^/abs/\d{4}\.\d{4,5}$`，锚文本至少 8 字
- 为什么在库里：语言模型与评测的主战场；新方法与失败案例都先出现在这里。角色扮演、人格一致性、长期对话记忆都在这一类里，也正是虚拟人最先要解决的问题

#### `SRC-ARXIV-CS-CR` arXiv cs.CR 最新

- 分类：security / preprint / task；语言 en；出口 direct；每源预算 6
- 列表页：`https://arxiv.org/list/cs.CR/recent`
  - 路径正则 `^/abs/\d{4}\.\d{4,5}$`，锚文本至少 8 字
- 为什么在库里：安全里少数能把攻击与防御讲到可复现程度的来源；安全媒体大多是二次转述

#### `SRC-ARXIV-CS-HC` arXiv cs.HC 人机交互

- 分类：design / preprint / explore；语言 en；出口 direct；每源预算 5
- 列表页：`https://arxiv.org/list/cs.HC/recent`
  - 路径正则 `^/abs/\d{4}\.\d{4,5}$`，锚文本至少 8 字
- 为什么在库里：人怎么读、怎么误读界面；示例第二方向要落地，绕不开这一层实证。虚拟主播「像不像人」也归这里

#### `SRC-ARXIV-CS-LG` arXiv cs.LG 最新

- 分类：ai / preprint / core；语言 en；出口 direct；每源预算 6
- 列表页：`https://arxiv.org/list/cs.LG/recent`
  - 路径正则 `^/abs/\d{4}\.\d{4,5}$`，锚文本至少 8 字
- 为什么在库里：训练与推理的底层机制；判断一个模型说法是否站得住，要靠这一层

#### `SRC-ARXIV-CS-MA` arXiv cs.MA 多智能体

- 分类：ai / preprint / core；语言 en；出口 direct；每源预算 6
- 列表页：`https://arxiv.org/list/cs.MA/recent`
  - 路径正则 `^/abs/\d{4}\.\d{4,5}$`，锚文本至少 8 字
- 为什么在库里：多智能体协作与涌现行为。注意它和「虚拟聚生智能」不是一回事——后者是虚拟人/伴侣，不是一个系统里多个智能体协作

#### `SRC-ARXIV-CS-RO` arXiv cs.RO 机器人

- 分类：ai / preprint / core；语言 en；出口 direct；每源预算 5
- 列表页：`https://arxiv.org/list/cs.RO/recent`
  - 路径正则 `^/abs/\d{4}\.\d{4,5}$`，锚文本至少 8 字
- 为什么在库里：具身智能：智能体有了身体之后，哪些结论会变、哪些会失效

#### `SRC-ARXIV-CS-SE` arXiv cs.SE 最新

- 分类：engineering / preprint / core；语言 en；出口 direct；每源预算 6
- 列表页：`https://arxiv.org/list/cs.SE/recent`
  - 路径正则 `^/abs/\d{4}\.\d{4,5}$`，锚文本至少 8 字
- 为什么在库里：软件开发本身的研究：契约、测试、代码生成、可维护性；系统构建最缺的就是这类可检验结论

#### `SRC-CHECKPOINT` Check Point Research

- 分类：security / lab_release / task；语言 en；出口 direct；每源预算 6
- 列表页：`https://research.checkpoint.com/`
  - 路径正则 `^/\d{4}/[a-z0-9-]+$`，锚文本至少 8 字
- 为什么在库里：漏洞与恶意代码的逆向分析，带具体手法；安全入门最缺的就是这种「看得见的攻击链」。实测会间歇性返回 202 挑战页（锚点 0 个），此时该次抓取记为 INDEX_EMPTY 而不是「今天没更新」

#### `SRC-CLOUDFLARE` Cloudflare Blog

- 分类：engineering / engineering_blog / core；语言 en；出口 direct；每源预算 6
- 列表页：`https://blog.cloudflare.com/`
  - 路径正则 `^/[a-z][a-z0-9-]{9,}$`，锚文本至少 8 字
- 为什么在库里：真实规模的系统怎么被做出来、怎么被打破；工程与攻防在同一批文章里

#### `SRC-COLOSSAL` Colossal

- 分类：art / media / core；语言 en；出口 direct；每源预算 6
- 列表页：`https://www.thisiscolossal.com/`
  - 路径正则 `^/\d{4}/\d{2}/[^/]+$`，锚文本至少 8 字
- 为什么在库里：当代视觉艺术；作品即证据

#### `SRC-DIGITALING` 数英 DIGITALING

- 分类：design / media / core；语言 zh-Hans；出口 direct；每源预算 8
- 列表页：`https://www.digitaling.com/`
  - 路径正则 `^/articles/\d+\.html$`，锚文本至少 8 字
- 为什么在库里：设计与营销案例；能看到创意怎么被约束条件逼出来

#### `SRC-EYEONDESIGN` Eye on Design (AIGA)

- 分类：design / critique / core；语言 en；出口 direct；每源预算 6
- 列表页：`https://eyeondesign.aiga.org/`
  - 路径正则 `^/[a-z0-9-]+$`，锚文本至少 8 字
- 为什么在库里：设计批评与设计史；提供「为什么这样设计是错的」这类纠错视角

#### `SRC-GCORES` 机核

- 分类：culture / media / explore；语言 zh-Hans；出口 direct；每源预算 6
- 列表页：`https://www.gcores.com/`
  - 路径正则 `^/articles/\d+$`，锚文本至少 8 字
- 为什么在库里：游戏、叙事与文化批评；示例第二方向常在这里有比设计媒体更具体的讨论

#### `SRC-GOOOOD` 谷德设计网

- 分类：architecture / media / core；语言 zh-Hans；出口 direct；每源预算 8
- 列表页：`https://www.gooood.cn/category/architecture`
  - 路径正则 `^/[a-z0-9-]+\.htm$`，锚文本至少 8 字
- 为什么在库里：建筑的空间与构成；「把一个视角安排在哪里」在建筑里讲得比在设计媒体更清楚

#### `SRC-HF-BLOG` Hugging Face Blog

- 分类：ai / engineering_blog / explore；语言 en；出口 proxy；每源预算 5
- 列表页：`https://huggingface.co/blog`
  - 路径正则 `^/blog/[A-Za-z0-9._-]+(/[a-z0-9-]+)?$`，锚文本至少 8 字
- 为什么在库里：社区作者写的实践长文，密度参差；价值在于「有人真的这么做了一遍」。直连被阻断，必须走代理

#### `SRC-HF-PAPERS` Hugging Face Papers

- 分类：ai / preprint / core；语言 en；出口 proxy；每源预算 6
- 列表页：`https://huggingface.co/papers`
  - 路径正则 `^/papers/\d{4}\.\d{4,5}$`，锚文本至少 8 字
- 为什么在库里：带社区讨论的论文榜单：同一篇论文有没有人复现、有没有人骂，比摘要本身更有用。直连被阻断，必须走代理

#### `SRC-HYPERALLERGIC` Hyperallergic

- 分类：art / critique / core；语言 en；出口 direct；每源预算 5
- 列表页：`https://hyperallergic.com/`
  - 路径正则 `^/[a-z0-9-]+$`，锚文本至少 8 字
- 为什么在库里：艺术评论；列表里混有导航链接，靠锚文本长度过滤

#### `SRC-IFANR` 爱范儿

- 分类：tooling / media / control；语言 zh-Hans；出口 direct；每源预算 6
- 列表页：`https://www.ifanr.com/`
  - 路径正则 `^/digest/\d+$`，锚文本至少 8 字
- 为什么在库里：消费科技消息面。留在这里是当对照：如果它总能进核心位，说明我们对「新产品」的偏好在压过信息密度

#### `SRC-INFOQ` InfoQ 中文

- 分类：engineering / media / core；语言 zh-Hans；出口 direct；每源预算 8
- 列表页：`https://www.infoq.cn/`
  - 路径正则 `^/article/[A-Za-z0-9]+$`，锚文本至少 8 字
- 为什么在库里：工程实践、架构与评测案例；与系统构建直接相关

#### `SRC-ITSNICETHAT` It's Nice That

- 分类：design / media / core；语言 en；出口 direct；每源预算 6
- 列表页：`https://www.itsnicethat.com/`
  - 路径正则 `^/(articles|features)/[^/]+$`，锚文本至少 8 字
- 为什么在库里：视觉设计、插画与字体；作品本身就是证据，不解释方法也算

#### `SRC-LILIANWENG` Lil'Log（翁丽莲）

- 分类：ai / engineering_blog / core；语言 en；出口 direct；每源预算 5
- 列表页：`https://lilianweng.github.io/`
  - 路径正则 `^/posts/\d{4}-\d{2}-\d{2}-[a-z0-9-]+$`，锚文本至少 0 字
- 为什么在库里：把一个方向的论文串成一条线，正好补上「读单篇论文看不出全局」这个缺口

#### `SRC-MARTINFOWLER` martinfowler.com

- 分类：engineering / engineering_blog / core；语言 en；出口 direct；每源预算 5
- 列表页：`https://martinfowler.com/`
  - 路径正则 `^/articles/[a-z0-9-]+\.html$`，锚文本至少 8 字
- 为什么在库里：系统构建里的取舍怎么写清楚；架构决策的表述方式本身值得学

#### `SRC-MIDIFAN` Midifan 叉烧网

- 分类：music / media / explore；语言 zh-Hans；出口 direct；每源预算 6
- 列表页：`https://www.midifan.com/`
  - 路径正则 `^/modulearticle-detailview-\d+\.htm$`，锚文本至少 8 字
- 为什么在库里：声音与节奏是叙事的一部分；音乐制作里的结构与张力可以直接搬进示例第二方向

#### `SRC-PROJECTZERO` Google Project Zero

- 分类：security / lab_release / task；语言 en；出口 proxy；每源预算 5
- 列表页：`https://projectzero.google/`
  - 路径正则 `^/\d{4}/\d{2}/[a-z0-9-]+\.html$`，锚文本至少 8 字
- 为什么在库里：漏洞研究里最讲「为什么这条攻击链成立」的地方；0day 到利用的完整推理过程。直连被 TLS 阻断，必须走代理

#### `SRC-QBITAI` 量子位

- 分类：ai / media / core；语言 zh-Hans；出口 direct；每源预算 10
- 列表页：`https://www.qbitai.com/`
  - 路径正则 `^/\d{4}/\d{2}/\d+\.html$`，锚文本至少 8 字
- 为什么在库里：中文 AI 报道里较快、较全的一手编译；用它发现事件，再回到论文或官方博客核实

#### `SRC-SCHNEIER` Schneier on Security

- 分类：security / critique / task；语言 en；出口 direct；每源预算 5
- 列表页：`https://www.schneier.com/blog/`
  - 路径正则 `^/blog/archives/\d{4}/\d{2}/[a-z0-9_-]+\.html$`，锚文本至少 8 字
- 为什么在库里：安全的政治与判断层；技术细节不多，但常指出「这套防护假设本身是错的」

#### `SRC-SIMONW` Simon Willison's Weblog

- 分类：ai / engineering_blog / core；语言 en；出口 direct；每源预算 6
- 列表页：`https://simonwillison.net/`
  - 路径正则 `^/\d{4}/[A-Z][a-z]{2}/\d{1,2}/[a-z0-9-]+$`，锚文本至少 8 字
- 为什么在库里：动手试出来的结论，常带失败案例与可复现代码；比发布会更接近「这东西实际能干什么」

#### `SRC-SSPAI` 少数派

- 分类：tooling / media / explore；语言 zh-Hans；出口 direct；每源预算 6
- 列表页：`https://sspai.com/`
  - 路径正则 `^/post/\d+$`，锚文本至少 8 字
- 为什么在库里：工具与工作流的实际用法；同一条工具在这里和在发布公告里是两回事

#### `SRC-TMTPOST` 钛媒体

- 分类：business / media / control；语言 zh-Hans；出口 direct；每源预算 8
- 列表页：`https://www.tmtpost.com/`
  - 路径正则 `^/\d+\.html$`，锚文本至少 8 字
- 为什么在库里：科技商业叙事。同样是对照源：AI 行业的资本与情绪，认知增量通常低于一篇技术分析

#### `SRC-V2EX` V2EX

- 分类：tooling / community / explore；语言 zh-Hans；出口 direct；每源预算 6
- 列表页：`https://www.v2ex.com/`
  - 路径正则 `^/t/\d+$`，锚文本至少 8 字
- 为什么在库里：从业者真正在抱怨什么：额度、限流、哪个模型今天变笨了。密度低，但「环境价值」这一项只有这里最早出现；放探索位，不该常进核心位

#### `SRC-ZDI` Zero Day Initiative Blog

- 分类：security / lab_release / task；语言 en；出口 direct；每源预算 5
- 列表页：`https://www.zerodayinitiative.com/blog`
  - 路径正则 `^/blog/\d{4}/\d{1,2}/\d{1,2}/[a-z0-9-]+$`，锚文本至少 8 字
- 为什么在库里：漏洞披露与利用细节；能看出一个漏洞值多少钱、为什么难利用

## 6. 索引怎么用

机器可查的索引在 `config/sources.index.json`，字段与登记表一一对应；按条件筛选：

```powershell
python tools/source_index.py --query field=ai --query role=core
python tools/source_index.py --query kind=preprint
python tools/source_index.py --hosts
python tools/source_index.py --verify          # 联网逐个列表页实测（有网络时才跑）
python tools/source_index.py --verify --proxy http://127.0.0.1:7897

出口为 `proxy` 的来源直连会被 TLS 阻断（实测 projectzero.google、huggingface.co）。代理不写进登记表的具体地址——那是机器属性，用 `--proxy` 或环境变量 `LAYA_PROXY` 给。
```

加一个源必须先回答三件事：

1. `field` 它讲哪个领域（决定会不会挤占别的领域的名额）
2. `kind` 它是怎么产出的（预印本/一手公告/媒体编译，密度差一个量级）
3. `serves` 它喂哪个目标：说不出喂哪个目标，就说明它只是看着像好东西

任何一项缺失，`--check` 都会报错，不允许悄悄进库。

