# 31 · 消息去重的既有做法：调查与选型

> 这一轮不是发明方法，是把别人已经踩过的路走一遍，然后决定我们站在哪一段。
> 结论先说：**去重不是一个算法，是四层的组合**，每层成本差一个量级。

## 1. 结论：四层，按成本排

| 层 | 方法 | 成本 | 抓什么 | 抓不到什么 |
|---|---|---|---|---|
| 1 | 规范化 URL / 内容摘要 | ≈0 | 同一条被重抓 | 转载、改写 |
| 2 | shingle Jaccard（精确） | ≈0（CPU 毫秒级） | 整段照抄、加编者按、删段落 | 改写、跨语言 |
| 3 | SimHash / MinHash+LSH | ≈0，但要建索引 | 第 2 层在**百万级**文档下的近似版 | 跨语言；短文本失真 |
| 4 | 多语言 embedding + 近邻 + union-find | 每条一次向量（几分钱/千条） | 跨语言、换说法 | 细粒度事实差异 |
| 5 | LLM / agent 抽事实 | 每条一次生成 | "到底是不是同一件事" | 贵；需要人校验 |

第 2 层和第 3 层是同一个东西的两种规模：**规模小就用精确 Jaccard，规模大才需要指纹**。
第 4 层是跨语言的唯一解。第 5 层是用户这次真正要的"还原事实"。

## 2. 调查到了什么

**MinHash + LSH 是教科书答案。** 切 shingle（3-gram）→ MinHash 压成 100–200 个值 →
LSH 分桶。核心性质：两个签名最小值相同的概率等于原集合的 Jaccard。
这就是 2000 年代 Google News 与网页爬虫用的方法。
（[datasketch 文档](https://pypi.org/project/datasketch/) 与
[Zilliz 的 MINHASH_LSH 说明](https://docs.zilliz.com/docs/minhash-lsh) 都有实现与参数说明。）

**SimHash 是另一条线**：把文档压成 64 位指纹，比汉明距离。网页去重常用的阈值是 ≤3。
它的优势是**存储与比较都极便宜**，缺点是对短文本敏感。

**跨语言是 MinHash 的死角。** 这一条有现成的工程记录：一个跑 17 种语言、180+ RSS 源的
新闻聚合器写了详细复盘——同一个事件被 BBC（英）、NHK（日）、Le Monde（法）、
新华社（中）报道时，**shingle 的 Jaccard 是 0**，MinHash 会「正确地」报告它们毫无关系。
（[Cross-Lingual News Dedup at $100/month](https://yingjiezhao.com/en/articles/Cross-Lingual-News-Dedup-at-100-Dollar-a-Month/)）

他们的做法：多语言 embedding（1536 维）→ pgvector KNN → 两段式聚类：

| 参数 | 取值 | 作用 |
|---|---|---|
| 余弦相似度阈值 | 0.70 | 实测调出来的；更低会误并 |
| 时间间隔 | 18 小时 | 防止「同主题、不同月份」被并（比如两次地震） |
| 故事寿命 | 36 小时 | 老故事不再吸收新文章 |
| 第二段 | union-find 连通分量 | 只对没匹配上的新文章做 |
| 故事向量 | 最近 3 篇的滑动平均 | 故事焦点会演化，不能用历史平均 |

整条链路跑在 Cloudflare Workers + PostgreSQL(pgvector) 上，约 **$100/月**。
（讨论见 [Hacker News 条目](https://hn.svelte.dev/item/47245815)）

**共同的结构**：所有认真做过的系统都是「便宜的先筛，贵的只处理剩下的」。
没有人拿 LLM 逐条跑全量。

## 3. 在我们自己的语料上实测

200 条真实内容（15 个中文源），跨源两两比较：

| 指标 | 结果 |
|---|---|
| 精确 shingle Jaccard 最高的一对 | **0.019**（郑钦文那条，两篇不同角度的报道） |
| 同一篇加「编者按」后 | **0.996** |
| 同一篇删掉 20% | **0.798** |
| 完全不相关 | **0.009** |
| SimHash 汉明距离：同一篇 | 0 |
| SimHash 汉明距离：删掉 20% | **11**（那一篇；不同文本差异很大，见下） |
| SimHash 汉明距离：最像的一对真实跨源 | **15** |

两条结论：

1. **Jaccard 的判别空间极宽**：不相关 ≤0.02，近似重复 ≥0.80。阈值取 0.55，
   两边都留着巨大余量。所以第 2 层用**精确 Jaccard**，不上指纹。
2. **SimHash 的阈值不能照搬**：真实语料里有一篇删两成后汉明距离到 11，
   但同一批合成文本上同样的删减只变化 0–1——这个距离对文本分布很敏感。
   网页去重的 ≤3 是从长网页得出的经验值，搬到短新闻上要么漏、要么误并。
   所以 SimHash 只作为**万级以上**的扩展路径保留，当前不启用。

## 4. 映射到本项目

| 层 | 我们的实现 | 状态 |
|---|---|---|
| 1 URL / 摘要 | `tools/crawl.py` + `ingest/dedupe.py` | 已有 |
| 2 精确 Jaccard | `judgment/simhash.py` 的 `jaccard()`，接进 `judgment/events.py` 的聚簇规则 | **本轮新增** |
| 3 SimHash | 同模块的 `fingerprint()` / `hamming()`，留作扩展 | 本轮新增，暂不启用 |
| 4 embedding | 没有 | **缺口** |
| 5 事实 | `judgment/facts.py` + `store/fact_library.py`，生成交给 agent | 本轮改成 agent 侧 |

阈值全部写死在常量里并附实测依据，不放在配置文件里——它们不是可调参数，
是"这套语料上这个值安全"的记录。

## 5. 事实还原交给谁：改了

上一轮我拿本机的 Qwen2.5-3B 去抽事实，抽不出来（见 [docs/30](30-fact-library.md) 第 6 节）。
用户指出：**本机那个是判断模型，不是干这个的；事实还原应该交给 API / AI agent。**

已经改成两段式工具：

```powershell
# 1) 产出题面（不跑模型）
python tools/extract_facts.py --items runs/crawl-<日期> --out runs/crawl-<日期>/facts.jsonl

# 2) agent 按 {item_id, raw} 填答案
# 3) 解析答案、写进事实库
python tools/extract_facts.py --items runs/crawl-<日期> ^
    --import-answers runs/crawl-<日期>/facts.answers.jsonl ^
    --out runs/crawl-<日期>/facts.jsonl --library store/fact-library.jsonl
```

实测（两条 HF 论文页，由 agent 作答）：分别抽出 **4 条**与 **5 条**事实，
主体/关系/对象都可读，解析零失败：

```
Waypoint-OPD | 改进 | policy-shift distillation
Waypoint-OPD | 提升 | reasoning performance
OmniCompass | 评估 | vision-language and audio-visual reasoning
OmniCompass | 包含 | temporal grounding
```

同一批次里本机模型给的是 `NONE`。**差别不在提示词，在谁来生成。**

## 6. 还缺 embedding 层

第 4 层是唯一能同时解决「跨语言」与「换说法」的。没有它，
事实库只能匹配"说法几乎一样"的条目，换个说法就漏。

选型待定，两条路：

| 方案 | 成本 | 顾虑 |
|---|---|---|
| 本地小 embedding（multilingual-e5-small / bge-small，约 100MB，CPU 可跑） | 一次性下载，之后零成本 | 需验证中文新闻上的实际区分度 |
| API embedding（如 text-embedding-3-small） | 每千条几分钱 | 要联网；数据出本机 |

两者都是"每条一次向量"，比抽事实便宜两个量级。**建议先试本地小模型**：
下载成本一次，之后完全离线，符合这套系统"本机能跑完整链路"的设计。

## 7. 下一步

1. 装一个多语言 embedding 小模型，在现有 200 条语料上测区分度（用"删 20%""换说法"两组样本）。
2. 若区分度够，把第 4 层接进 `judgment/events.py`，替代/补强锚点规则。
3. 把 agent 抽事实那一环接进日常流程（现在是独立工具）。

## 8. 参考

- [Cross-Lingual News Dedup at $100/month](https://yingjiezhao.com/en/articles/Cross-Lingual-News-Dedup-at-100-Dollar-a-Month/)——17 语言新闻聚合器的完整工程复盘，含阈值与成本
- [Hacker News 讨论](https://hn.svelte.dev/item/47245815)
- [datasketch](https://pypi.org/project/datasketch/)——MinHash / LSH 的 Python 实现
- [Zilliz: MINHASH_LSH](https://docs.zilliz.com/docs/minhash-lsh)——算法与参数说明
- [Princeton COS435: Finding near-duplicate documents](https://www.cs.princeton.edu/courses/archive/spring09/cos435/Notes/duplicateDocs.pdf)——shingling / SimHash 的教学材料

## 9. 照抄结果：一半能抄，一半抄不了

按上面的方案装了 `paraphrase-multilingual-MiniLM-L12-v2`（384 维，约 470MB，从 hf-mirror 下），
用 transformers 手动平均池化，不引 sentence-transformers。

### 9.1 先按他们的参数试

他们的阈值是余弦 0.7。在我们的语料上直接套，测出来的结果是这样的：

| 情形 | 余弦 | 0.7 会怎么判 |
|---|---|---|
| 同一篇加编者按 | 0.904 | 合并 ✅ |
| 跨语言同事件（英↔中） | 0.964 / 0.965 | 合并 ✅ |
| **中文改写（同一件事换说法）** | **0.680** | **漏掉 ❌** |
| **不相关（霸王茶姬 × 快手亚运）** | **0.889** | **误并 ❌** |
| 同主题不同事件（两篇郑钦文） | 0.754 | 误并 ❌ |

真实语料 200 条，跨源两两比较：最高 0.889，p99 0.694，中位数 0.328。
**最高分那一对是不相关的两条。** 也就是说同语言里"像"和"是同一件事"根本不是一回事——
中文营销/设计媒体的用词太同质，向量被文风主导，不是被事件主导。

### 9.2 为什么他们行我们不行

不是他们的做法错，是**语料性质不同**：

| | 他们 | 我们 |
|---|---|---|
| 内容 | 世界新闻，同一事件多家报道 | 垂类媒体，各写各的选题 |
| 跨源重复 | 多（每天大量） | 极少（200 条里 0 组） |
| 同质化 | 低 | 高（都是"发布/评测/案例"的语气） |

他们还叠了时间窗（18 小时）和故事寿命（36 小时），这两条会挡掉大部分误并；
我们的条目本来就集中在同几天，时间窗挡不住。

### 9.3 改成了什么

**embedding 只用于跨语言，不用于同语言。**

| 语言关系 | 判据 | 依据 |
|---|---|---|
| 同语言 | 锚点 + shingle Jaccard | 这两层在真实语料上零误并；向量会把不相关的并起来 |
| 跨语言 | 余弦 ≥ 0.90 | 不相关最高 0.672（p99 0.052），同事件 0.96，间隔极宽 |

跨语言这一格是字面方法永远做不到的——两种语言的 shingle 交集恒为 0，
所以这里只能相信向量。其余情况一律不碰向量。

### 9.4 实测

| 语料 | 条目 | 触发合并 |
|---|---|---|
| crawl-mixed（中英混排） | 90 | 0 |
| crawl-20261019c（中文） | 200 | 0 |
| crawl-hf2（英文） | 6 | 0 |

零误并符合预期（跨语言不相关最高只有 0.672，离 0.90 很远）。
**但召回也没被验证**：这三批语料里本来就没有跨语言的同事件对。
要验证得等一个中英同时报道的事件，或者造一组对照样本。

### 9.5 一句话

照抄对了**方法**（多语言向量 + 阈值 + 两段式思路），抄错了**参数**（0.7 是他们的语料调出来的）。
真正能抄的是"跨语言用向量、同语言别用"，这恰好也是他们那篇文章真正的洞察——
只是他们没说透，因为他们的语料里同语言重复也用得上向量。
