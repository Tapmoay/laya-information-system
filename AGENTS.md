# AGENTS.md（本仓库）

进入这个仓库工作的 AI，先读这三样，再动手：

1. [MISSION.md](MISSION.md)——**主旨**。这个系统为什么存在、相信什么。
2. [information-policy.md](information-policy.md)——**政策模板**。使用前请改成用户自己的版本。
3. [docs/17-architecture.md](docs/17-architecture.md)——全貌地图。

## 动手前的三条硬约束

- **测量不等于决策。** 模型只产出测量，选择由确定性规则做，终选由 agent 做。
- **未知必须可表达。** `UNKNOWN` 与 `ERROR` 不得互相冒充；
  把"不知道"当成"很旧"或当成"很新"都是编造。
- **理由必须指向"它能帮我做什么"。** 说不出"这条很好"以外的东西，就允许少于上限。

## 改完之后

```powershell
python tools/check.py
```

五项全过才算改完：能力注册表、契约夹具、信息源库、文档数字、回归测试。

## 第一次使用

先跑 `python tools/setup.py`，写 `config/user.yaml`、`config/USER.md` 与
`demand/demand-<日期>.json`；不要用别人的目标直接抓取。配置说明见
[agent/skills/personal-information-environment/FIRST-SETUP.md](agent/skills/personal-information-environment/FIRST-SETUP.md)。

## 不知道从哪开始

[README.md](README.md) 的「当前状态」是事实来源；
[docs/17-architecture.md](docs/17-architecture.md) 是全貌地图；
[agent/skills/personal-information-environment/SKILL.md](agent/skills/personal-information-environment/SKILL.md)
是日常运营流程。
