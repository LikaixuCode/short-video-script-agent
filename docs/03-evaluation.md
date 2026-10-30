# 测试与实际运行记录

本轮本地开发与验证时间：2026-10-30 23:27 至 23:36（北京时间，约 9 分钟，包含等待真实调用；不包含此前题目及 GitHub 交付耗时）。以下是本项目实际运行结果，不将离线 Mock 视作模型能力证据。

执行 `uv run --locked python -m pytest -q`：**24 项通过**，原始结果见 [test-results.txt](../examples/test-results.txt)。测试包括素材原文和 ID 核对、编造百分比和画面、未检索来源、修改素材、不同目标时长、旁白最低秒数、3.004 秒不能误判为 3 秒、脚本变更后两项重新检查、执行上限和工具失败。

| 验收点 | 实际输入与运行输出 | 实际结果 |
| --- | --- | --- |
| 正常生成 | [输入](../examples/normal.input.json) / [真实 GLM](../examples/normal.glm.output.json) | 检索 P01；估时 30 秒，规则通过，提交 3 镜脚本。 |
| 缺少关键信息 | [输入](../examples/missing.input.json) / [离线 Mock](../examples/missing.mock.output.json) | 缺 `target_seconds`；未启动模型或调用工具。 |
| 无素材 | [输入](../examples/no-material.input.json) / [真实 GLM](../examples/no-material.glm.output.json) | 检索和改写均未命中，任务 `incomplete/no_material`，没有脚本。 |
| 工具失败 | [输入](../examples/failure.input.json) / [真实 GLM](../examples/failure.glm.output.json) / [离线 Mock](../examples/failure.mock.output.json) | 注入首次检索失败；真实模型改写查询后成功检索并完成，失败事件保留；Mock 明确停止。 |
| 超时长修正 | [输入](../examples/overlong.input.json) / [真实 GLM](../examples/overlong.glm.output.json) / [离线 Mock](../examples/overlong.mock.output.json) | 目标 10 秒，初稿实测工具估计 24 秒；真实模型调整镜头时长并再次估算为 9.8 秒，完成规则复查后提交。 |
| 改写查询 | [输入](../examples/rewrite.input.json) / [真实 GLM](../examples/rewrite.glm.output.json) | `视频转文字` 检索为空，模型继续改写；`字幕 语音识别 自动生成` 命中 P01 并提交 15 秒脚本。 |
| 更换素材 | [替换素材](../examples/changed-materials.json) / [离线 Mock](../examples/changed-material.mock.output.json) | 新一轮输出引用替换后的事实；自动化测试同时验证旧脚本文案不能通过新素材检查。真实模型对照暂未完成。 |
| 工具次数上限 | [输入](../examples/normal.input.json) / [离线 Mock](../examples/limit.mock.output.json) | 上限为 1；第二次工具尝试被拒，任务未完成。 |

`*.glm.output.json` 包含每一步实际工具参数、返回、决策依据、下一动作，以及 SDK 的工具请求/返回 ID。`overlong` 和 `rewrite` 两条路径显示模型读取不同工具结果后作出了不同后续选择。运行结果由真实调用生成，模型输出具有随机性，复跑不保证完全相同。

[rate-limit.glm.output.json](../examples/rate-limit.glm.output.json) 保存一次对替换素材发起的真实调用失败：供应商返回 HTTP 429 限速，SDK 未产生工具调用，项目返回 `incomplete/api_error`。这不证明替换素材场景的模型表现；相关确定性业务行为由离线记录与测试覆盖。

限制：检索仅支持预先配置的主题同义词和索引；旁白、字幕与画面说明被限制为素材原文；受众/平台/风格交给模型使用但无法保证风格化文案或平台适配；估时只是规则估计，并非配音实测。真实 SDK 调用连通不代表 Coding Plan 适用于该自建应用，套餐使用以智谱规则为准。
