# 个人信息环境 · 技能包

版本 **1.0.0**（2026-10-24）

这个包是给"另一个 agent / 另一个人"用的**操作手册**，不是可执行系统。
它告诉你的 agent：怎么问需求、怎么判断、怎么推送、失败怎么处理、什么时候该停下来问用户。

## 包里有什么

| 文件 | 内容 |
|---|---|
| `SKILL.md` | 日常流程：九阶段、质量门、反模式、失败矩阵 |
| `FIRST-SETUP.md` | **一次性**配置清单；配完删除 |
| `references/onboarding.md` | 六个问题、判断/推送选项、能力矩阵 |
| `references/adaptation.md` | 换环境/换用户怎么适配；推送渠道由用户决定 |
| `references/cloud-judgment.md` | 云端 13 问契约、失败码、换模型协议 |
| `references/operations.md` | 定时、凭据、代理、日志、巡检、回滚 |
| `docs/38-daily-workflow.md` | 工作流规范：阶段契约、通过条件、变更协议 |
| `docs/39-judge-comparison.md` | 本地 vs 云端判断的实测对照 |
| `docs/40-portable-onboarding.md` | 配置分层与可移植性设计 |
| `config/user.example.yaml` | 配置模板（不含真实凭据） |
| `manifest.json` | 文件清单与哈希；由打包工具生成 |

## 包里没有什么

- 37 个信息源、抓取/测量/打分/终选/交付的代码：在**仓库**里。
- 模型权重、API key、SMTP 授权码、聊天工具凭据：**永远不在包里**。
- 作者的 `config/user.yaml`、`config/USER.md`、`runs/`、`store/`：个人数据，不带。

**技能 + 仓库**才是完整系统。只有技能时，拿到的是方法与契约，需要自己实现或向作者要仓库。

## 第一次使用（agent 必读）

1. 读 `FIRST-SETUP.md`，按它问用户六个问题并写三份文件；
2. 配置完成后，从**安装副本**删除 `FIRST-SETUP.md`；
3. 之后只读 `SKILL.md` 与日常参考。

## 安装

```powershell
# 从仓库安装到用户级技能目录（替换已有副本）
python tools/package_skill.py --install

# 或只生成分发包
python tools/package_skill.py
```

安装后目录形如 `~/.agents/skills/personal-information-environment/`。
要更新：重新 `--install --force`；要卸载：删掉该目录。

## 适配提醒

- 用户的领域可能完全不同：不要照抄作者的目标、来源、阈值、示例读物。
- 推送入口由用户决定：邮箱/文件/企业微信/其它；没有的渠道按
  `references/adaptation.md` 的适配器契约自己加。
- 换判断 provider 前先做 30–50 条对照，阈值不随 provider 自动迁移。
- 改配置/参数前先向用户解释；`tools/setup.py --set` 强制 `--why`。

## 安全

- 凭据只从环境变量或 `~/.dsh/.credentials.yaml` 读；
- 不要把 `config/user.yaml` 发给别人；
- 不要把这份包和任何 key 放进同一个压缩包。
