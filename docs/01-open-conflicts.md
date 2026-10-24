# 已知冲突与待决项（公开版）

> 公开模板不再保留个人决策历史；这里只列仍然开放、会影响使用者的项。

## C-01 政策版本自述

政策模板的顶部与 §10 都写 `v0.3`。修改时两处要一起改，否则读取层会报版本冲突。

## C-02 模型权重与许可

仓库不包含权重。本地运行前要自己下载，并确认模型许可与显存预算；
不下载时可以用云端 API（`judge.provider=cloud`）。

## C-03 推送渠道

内置 `email` / `file` / `none`。其他渠道需要按适配器契约自己加，见
`agent/skills/personal-information-environment/references/adaptation.md`。

## C-04 个人数据

`config/user.yaml`、`config/USER.md`、`demand/demand-*.json`、`store/*.jsonl`
是本机数据，不进 git；分发时不要带上。
