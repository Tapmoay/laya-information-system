"""摘要的页头清理：日期/作者/阅读时长不是内容。"""


def test_strip_leading_meta_removes_page_header():
    from judgment.text import strip_leading_meta

    assert strip_leading_meta("日期：2026年7月4日 | 预计阅读时间：31分钟 | 作者：某某") == ""
    assert strip_leading_meta("作者：某某 | 正文从这里开始") == "正文从这里开始"
    assert strip_leading_meta("正文本来就没有页头") == "正文本来就没有页头"
