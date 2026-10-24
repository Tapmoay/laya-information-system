# 自适应参数（公开说明）

> 这里只说明机制，不包含个人学习历史。参数版本存放在 `state/params/`，运行时数据不进 git。

## 机制

1. 先验参数在 `judgment/params.py`；
2. 用户反馈与 Agent 的取舍通过 `tools/learn.py` 进入参数库；
3. 每次提交有步长上限、回放护栏、版本号与回滚；
4. `tools/agent_select.py --build` 读取当前版本，并把版本写进当天产物。

## 为什么阈值不放进用户配置

阈值是学习结果，不是环境设置。放进 `config/user.yaml` 会让"阈值变了"和"内容变了"无法区分；
因此它只走 `learn.py`。

## 换 provider 时

阈值对应的是某个 provider 的分数分布。换本地/云端之前先跑
`tools/compare_judges.py`，再决定是否重标定；不要直接沿用旧阈值。
