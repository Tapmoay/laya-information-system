"""给人做标注的网页：一屏一条，点「要 / 不要 / 说不准」，可以补一句理由，导出一份 JSON。

和终选复核页的区别：
- 复核页是「评价一个已有决定」；
- 标注页是「只问内容本身」，且**不显示模型分数**——分数会锚定人的判断。
"""

from __future__ import annotations

from html import escape
from typing import Any, Sequence

STYLE = "".join([
    ":root{--bg:#fbfaf8;--card:#fff;--ink:#1f2328;--muted:#6b7280;--line:#e5e7eb;--ok:#059669;--no:#dc2626;--skip:#9ca3af}",
    "*{box-sizing:border-box}",
    "body{margin:0;padding:28px 20px 150px;background:var(--bg);color:var(--ink);",
    "font-family:Segoe UI,Microsoft YaHei,system-ui,sans-serif;line-height:1.65}",
    "main{max-width:900px;margin:0 auto}",
    "h1{font-size:22px;margin:0 0 6px}",
    ".sub{color:var(--muted);font-size:13px;margin-bottom:18px}",
    ".ask{background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:12px 16px;",
    "margin-bottom:20px;font-size:14px;color:#78350f}",
    ".ask em{font-style:normal;font-weight:600}",
    ".row{background:var(--card);border:1px solid var(--line);border-radius:12px;",
    "padding:16px 18px;margin-bottom:14px}",
    ".row.done{border-color:#cbd5e1}",
    ".head{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin-bottom:6px}",
    ".title{font-weight:600;font-size:16px}",
    ".badge{font-size:12px;padding:1px 9px;border-radius:999px;background:#eef2ff;color:#3730a3}",
    ".thumbs{display:flex;gap:8px;margin:10px 0;flex-wrap:wrap}",
    ".thumbs img{height:130px;border-radius:8px;border:1px solid var(--line);object-fit:cover;background:#f1f5f9}",
    ".thumbs a{display:block;line-height:0}",
    ".summary{background:#f8fafc;border-left:3px solid #cbd5e1;border-radius:0 8px 8px 0;",
    "padding:10px 14px;margin:10px 0 12px;font-size:14px;color:#334155}",
    ".orig{color:#64748b;font-size:13px;margin-top:6px;line-height:1.5}",
    "details{font-size:13px;color:#475569;margin-top:6px}",
    ".cn{color:#059669;font-size:12px;margin-left:6px}",
    ".btns{display:flex;gap:10px;align-items:center;flex-wrap:wrap}",
    ".note{flex:1;min-width:240px;border:1px solid var(--line);border-radius:8px;",
    "padding:7px 10px;font-size:13px;font-family:inherit;min-height:36px;resize:vertical}",
    ".btn{border:1px solid var(--line);background:#fff;border-radius:10px;padding:7px 22px;",
    "font-size:14px;cursor:pointer}",
    ".btn:hover{border-color:#94a3b8}",
    ".btn.sel-ok{background:var(--ok);border-color:var(--ok);color:#fff}",
    ".btn.sel-no{background:var(--no);border-color:var(--no);color:#fff}",
    ".btn.sel-skip{background:var(--skip);border-color:var(--skip);color:#fff}",
    ".bar{position:fixed;left:0;right:0;bottom:0;background:rgba(255,255,255,.96);",
    "border-top:1px solid var(--line);padding:12px 20px;display:flex;gap:14px;",
    "align-items:center;justify-content:center;font-size:14px}",
    ".bar button{background:#111827;color:#fff;border:0;border-radius:8px;padding:8px 20px;cursor:pointer}",
    ".bar button.ghost{background:#fff;color:#111827;border:1px solid var(--line)}",
    "#out{width:100%;height:90px;margin-top:12px;font:12px monospace;border:1px solid var(--line);",
    "border-radius:8px;padding:8px}",
])

SCRIPT = "".join([
    "var state = {};",
    "function ensure(id){ if (!state[id]) state[id] = {v: null, note: \"\"}; return state[id]; }",
    "function mark(id, value){",
    "  ensure(id).v = value;",
    "  var row = document.getElementById(id);",
    "  row.classList.add('done');",
    "  row.querySelectorAll('.btn').forEach(function(b){ b.className = 'btn'; });",
    "  var map = {'要':'ok','不要':'no','说不准':'skip'};",
    "  row.querySelectorAll('.btn').forEach(function(b){ if (b.dataset.v === value) b.classList.add('sel-' + map[value]); });",
    "  update();",
    "}",
    "function saveNote(id, text){ ensure(id).note = text; }",
    "function update(){",
    "  var total = document.querySelectorAll('.row').length;",
    "  var done = 0;",
    "  Object.keys(state).forEach(function(k){ if (state[k].v) done += 1; });",
    "  document.getElementById('progress').textContent = '已标 ' + done + ' / ' + total;",
    "}",
    "function exportJSON(){",
    "  var blob = new Blob([JSON.stringify(state, null, 2)], {type: 'application/json'});",
    "  var a = document.createElement('a');",
    "  a.href = URL.createObjectURL(blob);",
    "  a.download = 'labels-result.json';",
    "  a.click();",
    "}",
    "function showJSON(){ document.getElementById('out').value = JSON.stringify(state, null, 2); }",
    "document.querySelectorAll('.btn').forEach(function(b){",
    "  b.onclick = function(){ mark(b.closest('.row').id, b.dataset.v); };",
    "});",
    "document.querySelectorAll('.note').forEach(function(t){",
    "  t.oninput = function(){ saveNote(t.closest('.row').id, t.value); };",
    "});",
])


def _row(
    item: dict[str, Any],
    *,
    summary: str,
    images: Sequence[str],
    translation: dict[str, str] | None = None,
) -> str:
    item_id = escape(item["item_id"])
    title = escape((item.get("title") or item_id).split(" | ")[0].strip())
    domain = escape(str(item.get("domain") or ""))
    thumbs = "".join(
        '<a href="' + escape(url) + '" target="_blank" rel="noreferrer">'
        + '<img loading="lazy" src="' + escape(url) + '" alt="配图"></a>'
        for url in list(images)[:3]
    )
    block = "<div class=thumbs>" + thumbs + "</div>" if thumbs else ""
    text = "<div class=summary>" + escape(summary) + "</div>" if summary else ""
    shown_title = title
    if translation:
        if translation.get("summary"):
            text = ("<div class=summary><span class=cn>译</span> " + escape(translation["summary"]) + "</div>")
        if translation.get("title"):
            shown_title = escape(translation["title"]) + "<span class=cn>译</span>"
        original = "<div class=orig>" + title
        if summary:
            original += "<br>" + escape(summary)
        original += "</div>"
        text += "<details><summary>看原文</summary>" + original + "</details>"
    buttons = "".join(
        "<button class=btn data-v=" + value + ">" + value + "</button>"
        for value in ("要", "不要", "说不准")
    )
    note = "<textarea class=note rows=1 placeholder=为什么？写一句也行（可选）></textarea>"
    return (
        "<div class=row id=" + item_id + ">"
        "<div class=head><span class=badge>" + domain + "</span>"
        "<span class=title>" + shown_title + "</span></div>"
        + block + text
        + "<div class=btns>" + buttons + note + "</div></div>"
    )


def render(
    items: Sequence[dict[str, Any]],
    *,
    summaries: dict[str, str] | None = None,
    images: dict[str, Sequence[str]] | None = None,
    demand: dict[str, Any] | None = None,
    translations: dict[str, dict[str, str]] | None = None,
    generated_at: str = "",
) -> str:
    summaries = summaries or {}
    images = images or {}
    translations = translations or {}
    criteria = ""
    if demand:
        from judgment.questions import slot_values

        slots = slot_values(demand)
        names = (("primary", "主目标"), ("secondary", "第二方向"), ("exploration", "探索位"), ("task", "当前任务"))
        criteria = "<ul>" + "".join(
            "<li><b>" + label + "</b>" + escape(str(slots.get(key) or "（空）")) + "</li>"
            for key, label in names
        ) + "</ul>"
    rows = "".join(
        _row(
            item,
            summary=summaries.get(item["item_id"], ""),
            images=images.get(item["item_id"], ()),
            translation=translations.get(item["item_id"]),
        )
        for item in items
    )
    return (
        "<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
        "<meta name=viewport content=width=device-width,initial-scale=1>"
        "<title>标注</title><style>" + STYLE + "</style></head><body><main>"
        "<h1>标注：这条内容，你要不要？</h1>"
        "<div class=sub>共 " + str(len(items)) + " 条 · 生成于 " + escape(generated_at) + "</div>"
        "<div class=ask><b>只判内容，不看别的。</b>不要考虑模型给多少分、别人怎么选——"
        "这里连分数都没显示。拿不准就点「说不准」，那也是有用的信息。"
        "下面那个框可以写一句为什么，写不写都行——但写了的，会直接变成调参依据。</div>"
        + ("<div class=ask><b>判断依据</b>" + criteria + "</div>" if criteria else "")
        + rows
        + "<div class=bar><span id=progress>已标 0 / " + str(len(items)) + "</span>"
        + "<button onclick=exportJSON()>导出 JSON</button>"
        + "<button class=ghost onclick=showJSON()>显示结果</button></div>"
        + "<textarea id=out placeholder=点「显示结果」在这里看到 JSON></textarea>"
        + "<script>" + SCRIPT + "</script></main></body></html>"
    )
