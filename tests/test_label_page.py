

def test_label_page_renders_each_item_with_summary_and_buttons():
    from judgment.label_page import render

    items = [
        {"item_id": "item-a", "title": "标题甲 | 站点", "domain": "art"},
        {"item_id": "item-b", "title": "标题乙", "domain": "music"},
    ]
    html = render(items, summaries={"item-a": "摘要甲。"}, images={"item-b": ["https://x/y.jpg"]})
    assert html.count("class=row ") == 2
    assert "摘要甲。" in html
    assert "https://x/y.jpg" in html
    assert html.count("data-v=") == 6
    assert "标题甲" in html
    assert "item-a" in html


def test_label_page_hides_model_scores():
    from judgment.label_page import render

    items = [{"item_id": "item-a", "title": "甲", "domain": "art", "score": 0.87}]
    html = render(items)
    assert "0.87" not in html


def test_label_page_has_reason_box():
    from judgment.label_page import render

    items = [{"item_id": "item-a", "title": "甲", "domain": "art"}]
    html = render(items)
    assert html.count("class=note") == 1
    assert "saveNote" in html
    assert "为什么" in html


def test_import_accepts_verdict_objects_with_notes():
    from tools.import_review import _flat_to_labels

    rows = _flat_to_labels(
        {
            "item-a": {"v": "要", "note": "构图可以拆", "x": 1},
            "item-b": {"v": "不要", "note": ""},
            "item-c": {"v": "说不准", "note": "看不清"},
            "item-d": "要",
        }
    )
    by_id = {row["item_id"]: row for row in rows}
    assert by_id["item-a"]["label"] == 1 and by_id["item-a"]["note"] == "构图可以拆"
    assert by_id["item-b"]["label"] == 0 and "note" not in by_id["item-b"]
    assert "item-c" not in by_id
    assert by_id["item-d"]["label"] == 1


def test_label_page_shows_translation_and_keeps_original():
    from judgment.label_page import render

    items = [{"item_id": "item-a", "title": "Original Title", "domain": "art"}]
    html = render(
        items,
        summaries={"item-a": "Original summary."},
        translations={"item-a": {"title": "原始标题", "summary": "原始摘要。"}},
    )
    assert "原始标题" in html
    assert "原始摘要。" in html
    assert "Original Title" in html and "Original summary." in html
    assert "<details>" in html
