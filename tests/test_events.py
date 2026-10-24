"""事件去重：并对了才是去重，并错了是把两件事说成一件事。"""

from __future__ import annotations

from judgment.events import anchors, cluster, compare, event_key


def test_compound_model_name_is_split_into_name_and_version():
    """连写型号不拆开，两条同一事件的报道就永远对不上。"""
    tokens = anchors("DeepSeek-V4.1-Flash-1017 发布", "")
    assert "deepseek" in tokens["names"]
    assert "4.1" in tokens["strong"]


def test_same_event_with_rewritten_chinese_titles():
    left = anchors("DeepSeek-V4.1-Flash-1017 发布，多基准成绩提升且 API 价格不变", "")
    right = anchors("DeepSeek 发布 V4.1-Flash-1017：基准全面上调，API 价格不变", "")
    same, where = compare(left, right)
    assert same and where["rule"] in {"SHARED_VERSION", "VERSION_AND_NAME", "NUMERIC_AND_NAME"}


def test_same_event_with_rewritten_english_titles():
    left = anchors("Anthropic expands the Cyber Verification Program", "")
    right = anchors("Cyber Verification Program: Anthropic widens access", "")
    same, where = compare(left, right)
    assert same and where["rule"] == "TITLE_WORDS"


def test_series_episodes_are_not_the_same_event():
    left = anchors("给电子音乐人的 74 个建议（61）随机化与责任", "")
    right = anchors("给电子音乐人的 74 个建议（60）故意用烂耳朵听", "")
    same, _ = compare(left, right)
    assert not same


def test_unrelated_titles_are_not_merged():
    left = anchors("量子位：DeepSeek 新模型发布", "")
    right = anchors("钛媒体：iPhone Duo 首发秒售罄", "")
    same, _ = compare(left, right)
    assert not same


def test_cluster_only_merges_across_sources():
    """同一来源内部的近似是连载，不是重复。"""
    items = [
        {"item_id": "item-a", "source_id": "SRC-MIDIFAN", "title": "给电子音乐人的 74 个建议（61）随机化与责任", "text": ""},
        {"item_id": "item-b", "source_id": "SRC-MIDIFAN", "title": "给电子音乐人的 74 个建议（60）故意用烂耳朵听", "text": ""},
        {"item_id": "item-c", "source_id": "SRC-TMTPOST", "title": "DeepSeek-V4.1-Flash-1017 发布", "text": ""},
    ]
    mapping = cluster(items)
    # 同源的两期连载不能被并起来
    assert mapping["item-a"]["event_size"] == 1
    assert mapping["item-b"]["event_size"] == 1
    assert mapping["item-a"]["event_cluster_id"] != mapping["item-b"]["event_cluster_id"]


def test_event_key_is_a_pure_function_of_the_item():
    """跨天比对时昨天的条目已经不在语料里，键不能依赖语料。"""
    tokens = anchors("DeepSeek-V4.1-Flash-1017 发布会", "")
    again = anchors("DeepSeek-V4.1-Flash-1017 发布会", "")
    assert event_key(tokens) == event_key(again)
    assert event_key(tokens).startswith("ev-")


def test_cluster_is_deterministic():
    items = [
        {"item_id": "item-b", "source_id": "SRC-A", "title": "GPT-6 发布，编码能力提升", "text": ""},
        {"item_id": "item-a", "source_id": "SRC-B", "title": "OpenAI 发布 GPT-6：编码能力提升", "text": ""},
    ]
    first = cluster(items)
    second = cluster(items)
    assert first == second
    assert first["item-a"]["event_cluster_id"] == first["item-b"]["event_cluster_id"]

def test_copied_article_with_editor_note_is_merged():
    """整段照抄、只加一句编者按，是中文科技媒体最常见的重复形态。"""
    text = "某公司今天发布新模型，参数量提升到 3000 亿，API 价格不变。" * 20
    items = [
        {"item_id": "item-a", "source_id": "SRC-A", "title": "某公司发布新模型", "text": text},
        {"item_id": "item-b", "source_id": "SRC-B", "title": "某公司发布新模型（转）", "text": "编者按：本文转自某处。" + text},
    ]
    mapping = cluster(items)
    assert mapping["item-a"]["event_cluster_id"] == mapping["item-b"]["event_cluster_id"]


def test_trimmed_copy_is_still_merged():
    text = "一段很长的正文，讲同一件事的细节，参数量、价格、发布时间都在里面。" * 20
    items = [
        {"item_id": "item-a", "source_id": "SRC-A", "title": "长文", "text": text},
        {"item_id": "item-b", "source_id": "SRC-B", "title": "长文（节选）", "text": text[: int(len(text) * 0.8)]},
    ]
    mapping = cluster(items)
    assert mapping["item-a"]["event_cluster_id"] == mapping["item-b"]["event_cluster_id"]


def test_different_articles_with_shared_topic_stay_apart():
    left = "关于郑钦文中网夺冠的庆祝文案与品牌赞助观察。" * 15
    right = "关于郑钦文中网夺冠后的商业价值与赞助商选择的另一篇分析。" * 15
    items = [
        {"item_id": "item-a", "source_id": "SRC-A", "title": "郑钦文中网夺冠，品牌庆祝文案刷屏", "text": left},
        {"item_id": "item-b", "source_id": "SRC-B", "title": "郑钦文中网夺冠热度，赞助商谁接住了", "text": right},
    ]
    mapping = cluster(items)
    assert mapping["item-a"]["event_cluster_id"] != mapping["item-b"]["event_cluster_id"]

def test_cluster_does_not_chain_through_a_shared_middle_event():
    """A≈B、B≈C 不能推出 A≈C：代表制下 C 必须自己够得上代表。"""
    items = [
        {"item_id": "item-a", "source_id": "SRC-A", "title": "DeepSeek-V4.1-Flash-1017 发布", "text": ""},
        {"item_id": "item-b", "source_id": "SRC-B", "title": "OpenAI DeepSeek-V4.1-Flash-1017 对比 GPT-6-500K", "text": ""},
        {"item_id": "item-c", "source_id": "SRC-C", "title": "OpenAI GPT-6-500K 实测", "text": ""},
    ]
    mapping = cluster(items)
    assert mapping["item-a"]["event_cluster_id"] == mapping["item-b"]["event_cluster_id"]
    assert mapping["item-c"]["event_cluster_id"] != mapping["item-a"]["event_cluster_id"]


def test_same_source_copy_is_merged_by_text_overlap():
    """同源转发（同一篇挂了两个地址）应该合并；连载不同内容则不合并。"""
    text = "".join("第{}段：某公司发布新模型，参数 {} 亿，价格不变。".format(i, 3000 + i) for i in range(40))
    items = [
        {"item_id": "item-a", "source_id": "SRC-A", "title": "某公司发布新模型", "text": text},
        {"item_id": "item-b", "source_id": "SRC-A", "title": "某公司发布新模型（全文）", "text": text},
    ]
    mapping = cluster(items, cross_source_only=False)
    assert mapping["item-a"]["event_cluster_id"] == mapping["item-b"]["event_cluster_id"]

def test_cross_lingual_pair_is_merged_by_embedding():
    """不同语言的两条内容字面重合恒为 0，只能靠向量。"""
    items = [
        {"item_id": "item-en", "source_id": "SRC-EN", "title": "EU approves new AI regulation framework", "text": "", "detected_language": "en"},
        {"item_id": "item-zh", "source_id": "SRC-ZH", "title": "欧盟批准新的人工智能监管框架", "text": "", "detected_language": "zh-Hans"},
    ]
    embeddings = {"item-en": [1.0, 0.0, 0.0], "item-zh": [0.98, 0.1, 0.0]}
    mapping = cluster(items, embeddings=embeddings)
    assert mapping["item-en"]["event_cluster_id"] == mapping["item-zh"]["event_cluster_id"]


def test_same_language_pair_is_not_merged_by_embedding_alone():
    """同语言实测不相关能到 0.889，所以向量不许单独决定合并。"""
    items = [
        {"item_id": "item-a", "source_id": "SRC-A", "title": "霸王茶姬买一送一爆单", "text": "", "detected_language": "zh-Hans"},
        {"item_id": "item-b", "source_id": "SRC-B", "title": "电竞少年摘金，快手把亚运做成了品牌事件", "text": "", "detected_language": "zh-Hans"},
    ]
    embeddings = {"item-a": [1.0, 0.0], "item-b": [0.95, 0.31]}
    mapping = cluster(items, embeddings=embeddings)
    assert mapping["item-a"]["event_cluster_id"] != mapping["item-b"]["event_cluster_id"]


def test_cross_lingual_below_threshold_stays_apart():
    items = [
        {"item_id": "item-en", "source_id": "SRC-EN", "title": "Sewell Killingstad on art for art's sake", "text": "", "detected_language": "en"},
        {"item_id": "item-zh", "source_id": "SRC-ZH", "title": "同样是地标大屏，为什么有的刷屏有的没人看", "text": "", "detected_language": "zh-Hans"},
    ]
    embeddings = {"item-en": [0.6, 0.8], "item-zh": [1.0, 0.0]}
    mapping = cluster(items, embeddings=embeddings)
    assert mapping["item-en"]["event_cluster_id"] != mapping["item-zh"]["event_cluster_id"]


def test_embedding_rule_is_opt_in():
    """不传向量时不加载模型，也不改变原有判断。"""
    items = [
        {"item_id": "item-en", "source_id": "SRC-EN", "title": "EU approves new AI regulation framework", "text": ""},
        {"item_id": "item-zh", "source_id": "SRC-ZH", "title": "欧盟批准新的人工智能监管框架", "text": ""},
    ]
    mapping = cluster(items)
    assert mapping["item-en"]["event_cluster_id"] != mapping["item-zh"]["event_cluster_id"]
