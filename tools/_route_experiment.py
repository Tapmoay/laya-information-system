import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))
sys.path.insert(0, "models/_tf5")
from strands_decider.infer import load_engine
from strands_decider import schema

engine = load_engine("models/strands-decider-2b-local", device="cuda")

NOUL = {
    "value_task": "这条内容是否能帮助解决当前问题、推进项目或解除关键阻塞？",
    "value_capability": "这条内容是否能帮助形成可重复使用的知识、技能或认知模型？",
    "value_decision": "这条内容是否可能改变重要判断、选择、路线或行动？",
    "value_exploration": "这条内容是否提供值得检验的新视角、陌生领域或跨领域联系？",
    "value_environment": "这条内容是否揭示可能影响未来决策的重要技术、行业或外部变化？",
    "penalty": "这条内容是否主要为了消遣、八卦或情绪刺激，或只在宣传热度而没有说明实际能力与证据？",
}
RELEVANCE = {
    "DIRECT": "直接帮助构建或运行当前项目：架构、契约、评测、抓取、选择、交付、工具链",
    "ADJACENT": "同属 AI 或设计领域，但当前项目用不上",
    "EXPLORATORY": "其他领域的深度材料，可能带来启发",
    "UNRELATED": "消费、通知、购物、体育、娱乐内容",
    "INSUFFICIENT_EVIDENCE": "内容太短或没有实质信息",
}
ROUTE = {
    "CANDIDATE": "进入候选池：至少满足一项价值，但不必立刻推送",
    "PUSH": "直接推送：价值明确且当前就用得上",
    "DISCARD": "直接丢弃：无价值，或属于应减少的内容",
    "ESCALATE": "无法判断：交给 AI 代理处理",
}

data = json.load(open("runs/real-v2/fetch-results.json", encoding="utf-8"))
for item in data:
    text = (item.get("text") or "")[:2500]
    questions = {name: schema.NoulQuestion(instructions=q) for name, q in NOUL.items()}
    questions["relevance_5"] = schema.ChoiceQuestion(
        instructions="当前目标：构建个人信息决策系统（AI Agent 与系统工程），兼顾示例第二方向。判断这段内容与目标的关系类别。",
        criteria=RELEVANCE,
    )
    questions["route_4"] = schema.ChoiceQuestion(
        instructions="这条信息应该怎么处理？只依据内容本身与当前目标判断。",
        criteria=ROUTE,
    )
    start = time.time()
    resp = engine.ask(text, questions)
    elapsed = round(time.time() - start, 2)
    five = resp.answers["relevance_5"]
    four = resp.answers["route_4"]
    nouls = {k: round(resp.answers[k].noul, 2) for k in NOUL}
    print(f"== {item['title'][:30]}  ({elapsed}s)")
    print("   5选1:", {k: round(v, 3) for k, v in five.probabilities.items()}, "| max", round(max(five.probabilities.values()), 2))
    print("   4选1:", {k: round(v, 3) for k, v in four.probabilities.items()}, "| max", round(max(four.probabilities.values()), 2), "| 选择", four.choice)
    print("   价值:", nouls)
