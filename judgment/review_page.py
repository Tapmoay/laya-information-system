"""把终选结果渲染成一页可以点选的 HTML。

为什么要有它：Markdown 表格在编辑器里格子对不齐、长理由挤成一团，
而终选复核恰恰需要「一眼看清一条 + 一键表态」。
页面自带导出按钮，判完点一下就能拿到 JSON，不必手抄 item_id。
"""

from __future__ import annotations

from html import escape
from typing import Any, Sequence

STYLE_LINES = [
    ":root{--bg:#fbfaf8;--card:#fff;--ink:#1f2328;--muted:#6b7280;--line:#e5e7eb;",
    "--core:#2563eb;--explore:#7c3aed;--ok:#059669;--no:#dc2626;--skip:#9ca3af}",
    "*{box-sizing:border-box}",
    "body{margin:0;padding:32px 20px 160px;background:var(--bg);color:var(--ink);",
    "font-family:Segoe UI,Microsoft YaHei,system-ui,sans-serif;line-height:1.65}",
    "main{max-width:960px;margin:0 auto}",
    "h1{font-size:22px;margin:0 0 6px}",
    ".sub{color:var(--muted);font-size:13px;margin-bottom:24px}",
    ".ask{background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:12px 16px;",
    "margin-bottom:22px;font-size:14px;color:#78350f;line-height:1.7}",
    ".ask em{font-style:normal;font-weight:600}",
    ".criteria{background:var(--card);border:1px solid var(--line);border-radius:10px;",
    "padding:14px 18px;margin-bottom:26px;font-size:14px}",
    ".criteria b{color:var(--muted);font-weight:600;margin-right:6px}",
    ".criteria li{margin:3px 0}",
    "h2{font-size:16px;margin:30px 0 12px;padding-bottom:6px;border-bottom:2px solid var(--line)}",
    ".row{background:var(--card);border:1px solid var(--line);border-radius:10px;",
    "padding:14px 16px;margin-bottom:10px}",
    ".row.done{border-color:#cbd5e1;background:#fcfdfe}",
    ".head{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}",
    ".title{font-weight:600;font-size:15px}",
    ".badge{font-size:12px;padding:1px 9px;border-radius:999px;color:#fff;white-space:nowrap}",
    ".badge.core{background:var(--core)}.badge.explore{background:var(--explore)}",
    ".badge.exclude{background:#64748b}.badge.score{background:#eef2ff;color:#3730a3}",
    ".thumbs{display:flex;gap:8px;margin:10px 0 12px;flex-wrap:wrap}",
    ".thumbs img{height:110px;border-radius:8px;border:1px solid var(--line);",
    "object-fit:cover;background:#f1f5f9}",
    ".thumbs a{display:block;line-height:0}",
    ".summary{background:#f8fafc;border-left:3px solid #cbd5e1;border-radius:0 8px 8px 0;",
    "padding:10px 14px;margin:10px 0 12px;font-size:13.5px;color:#334155}",
    ".btns{display:flex;gap:8px}",
    ".btn{border:1px solid var(--line);background:#fff;border-radius:8px;padding:5px 16px;",
    "font-size:13px;cursor:pointer;transition:all .12s}",
    ".btn:hover{border-color:#94a3b8;transform:translateY(-1px)}",
    ".btn.sel-ok{background:var(--ok);border-color:var(--ok);color:#fff}",
    ".btn.sel-no{background:var(--no);border-color:var(--no);color:#fff}",
    ".btn.sel-skip{background:var(--skip);border-color:var(--skip);color:#fff}",
    ".bar{position:fixed;left:0;right:0;bottom:0;background:rgba(255,255,255,.95);",
    "border-top:1px solid var(--line);padding:12px 20px;display:flex;gap:14px;",
    "align-items:center;justify-content:center;font-size:14px}",
    ".bar button{background:#111827;color:#fff;border:0;border-radius:8px;padding:8px 20px;",
    "font-size:14px;cursor:pointer}",
    ".bar button.ghost{background:#fff;color:#111827;border:1px solid var(--line)}",
    "#out{width:100%;height:90px;margin-top:12px;font:12px monospace;display:block;",
    "border:1px solid var(--line);border-radius:8px;padding:8px}",
]

SCRIPT_LINES = [
    "var state = {};",
    "function mark(id, value){",
    "  state[id] = value;",
    "  var row = document.getElementById(id);",
    "  row.classList.add('done');",
    "  row.querySelectorAll('.btn').forEach(function(b){ b.className = 'btn'; });",
    "  var map = {'要':'ok','不要':'no','说不准':'skip'};",
    "  row.querySelectorAll('.btn').forEach(function(b){ if (b.dataset.v === value) b.classList.add('sel-' + map[value]); });",
    "  update();",
    "}",
    "function update(){",
    "  var total = document.querySelectorAll('.row').length;",
    "  document.getElementById('progress').textContent = '已判 ' + Object.keys(state).length + ' / ' + total;",
    "}",
    "function result(){",
    "  var picks = {}, exclusions = {};",
    "  document.querySelectorAll('.row').forEach(function(row){",
    "    if (!state[row.id]) return;",
    "    if (row.dataset.slot === 'exclude') exclusions[row.id] = state[row.id];",
    "    else picks[row.id] = state[row.id];",
    "  });",
    "  return {picks: picks, exclusions: exclusions};",
    "}",
    "function exportJSON(){",
    "  var blob = new Blob([JSON.stringify(result(), null, 2)], {type: 'application/json'});",
    "  var a = document.createElement('a');",
    "  a.href = URL.createObjectURL(blob);",
    "  a.download = 'review-result.json';",
    "  a.click();",
    "}",
    "function showJSON(){ document.getElementById('out').value = JSON.stringify(result(), null, 2); }",
    "document.querySelectorAll('.btn').forEach(function(b){",
    "  b.onclick = function(){ mark(b.closest('.row').id, b.dataset.v); };",
    "});",
]
STYLE = chr(10).join(STYLE_LINES)
SCRIPT = chr(10).join(SCRIPT_LINES)


def _criteria(demand: dict[str, Any] | None) -> str:
    if not demand:
        return ""
    from judgment.questions import slot_values

    slots = slot_values(demand)
    names = (("primary", "主目标"), ("secondary", "第二方向"), ("exploration", "探索位"), ("task", "当前任务"))
    items = "".join(
        "<li><b>{}</b>{}</li>".format(label, escape(str(slots.get(key) or "（空）")))
        for key, label in names
    )
    return "<div class=criteria><b>判断标准</b><ul>" + items + "</ul></div>"


def _row(
    entry: dict[str, Any],
    *,
    slot: str,
    title: str,
    score: float | None,
    summary: str = "",
    images: Sequence[str] = (),
) -> str:
    item_id = escape(entry["item_id"])
    label = {"core": "核心位", "exploration": "探索位"}.get(slot, "排除")
    badge = "<span class=badge " + slot + ">" + label + "</span>"
    if score is not None:
        badge += "<span class=badge score>{:.3f}</span>".format(score)
    buttons = "".join(
        "<button class=btn data-v=" + value + ">" + value + "</button>"
        for value in ("要", "不要", "说不准")
    )
    body = ""
    if images:
        thumbs = "".join(
            '<a href="' + escape(url) + '" target="_blank" rel="noreferrer">'
            + '<img loading="lazy" src="' + escape(url) + '" alt="配图"></a>'
            for url in images[:3]
        )
        body += "<div class=thumbs>" + thumbs + "</div>"
    if summary:
        body += "<div class=summary>" + escape(summary) + "</div>"
    return (
        "<div class=row id=" + item_id + " data-slot=" + slot + ">"
        "<div class=head>" + badge + "<span class=title>" + escape(title) + "</span></div>"
        + body
        + "<div class=reason><b>理由：</b>" + escape(entry.get("reason", "")) + "</div>"
        "<div class=btns>" + buttons + "</div>"
        "</div>"
    )


def render(
    candidates: Sequence[dict[str, Any]],
    final: dict[str, Any],
    *,
    demand: dict[str, Any] | None = None,
    summaries: dict[str, str] | None = None,
    images: dict[str, Sequence[str]] | None = None,
    generated_at: str = "",
) -> str:
    by_id = {row["item_id"]: row for row in candidates}
    summaries = summaries or {}
    images = images or {}

    def title_of(item_id: str) -> str:
        title = (by_id.get(item_id, {}).get("provenance") or {}).get("title") or item_id
        return title.split(" | ")[0].strip()

    def score_of(item_id: str) -> float | None:
        row = by_id.get(item_id) or {}
        value = row.get("score")
        return float(value) if value is not None else None

    picked = [
        _row(
            entry,
            slot=key,
            title=title_of(entry["item_id"]),
            score=score_of(entry["item_id"]),
            summary=summaries.get(entry["item_id"], ""),
            images=images.get(entry["item_id"], ()),
        )
        for key in ("core", "exploration")
        for entry in final.get(key, [])
    ]
    excluded = [
        _row(
            entry,
            slot="exclude",
            title=title_of(entry["item_id"]),
            score=score_of(entry["item_id"]),
            summary=summaries.get(entry["item_id"], ""),
            images=images.get(entry["item_id"], ()),
        )
        for entry in final.get("excluded", [])
    ]
    total = len(picked) + len(excluded)
    head = (
        "<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
        "<meta name=viewport content=width=device-width,initial-scale=1>"
        "<title>终选复核</title><style>" + STYLE + "</style></head><body><main>"
        "<h1>终选复核</h1>"
        "<div class=sub>共 {} 条 · 生成于 {} · 判完点底部「导出 JSON」</div>".format(
            total, escape(generated_at))
        + "<div class=ask><b>判的是内容，不是 Agent。</b>只问一句：<em>这条内容，我要不要它进最终推荐？</em>"
        + "不要去想「Agent 选得对不对」——那个由我拿你的答案反过来算："
        + "选中的你标「不要」= 误选，排除的你标「要」= 漏选。</div>"
        + _criteria(demand)
        + "<h2>一、Agent 选中的（{} 条）</h2>".format(len(picked))
        + "".join(picked)
        + "<h2>二、Agent 排除的（{} 条）</h2>".format(len(excluded))
        + "".join(excluded)
    )
    tail = (
        "<div class=bar><span id=progress>已判 0 / " + str(total) + "</span>"
        "<button onclick=exportJSON()>导出 JSON</button>"
        "<button class=ghost onclick=showJSON()>显示结果</button></div>"
        "<textarea id=out placeholder=点「显示结果」在这里看到 JSON></textarea>"
        "<script>" + SCRIPT + "</script></main></body></html>"
    )
    return head + tail
