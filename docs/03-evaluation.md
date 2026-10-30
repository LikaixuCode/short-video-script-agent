# 测试与实际运行记录

初版开发与验证时间：2026-10-30 23:27 至 23:36（北京时间，约 9 分钟，包含等待真实调用）。第二轮修复约从 2026-10-30 23:56 至 10-31 00:28，约 32 分钟并行墙钟时间，包含等待真实调用；各 Agent 的个人工时未独立测量，不将并行墙钟时间冒充个人投入。

## 当前代码的自动化测试

运行 `uv run --locked python -m pytest -q`：**76 项通过**，原始结果见 [test-results.txt](../examples/test-results.txt)。测试覆盖正常生成、必填信息、无素材、工具失败、时长范围和口播下限、主题事实类别缺口、付费与免费试用、安卓与 iOS、否定和历史事实、改写检索、错误事实标注、重复 ID、同版复查、旧稿后新稿失败、执行上限和同批工具调用标识。新版真实 Trace 有自动化工具结果重放和 SDK 请求、返回 ID 核对；重放不重新调用模型。

## 当前代码的离线样例

| 场景 | 输入与实际输出 | 结果 |
| --- | --- | --- |
| 正常 | [输入](../examples/normal.input.json) / [Mock](../examples/normal.mock.output.json) | 目标 30 秒，输出 30 秒，逐镜对应素材。 |
| 缺信息 | [输入](../examples/missing.input.json) / [Mock](../examples/missing.mock.output.json) | 缺目标时长；未启动模型。 |
| 无素材 | [输入](../examples/no-material.input.json) / [Mock](../examples/no-material.mock.output.json) | 未找到可确认的主题素材，返回未完成。 |
| 工具失败 | [输入](../examples/failure.input.json) / [Mock](../examples/failure.mock.output.json) | 注入一次检索失败，Mock 停止并返回未完成。 |
| 超时长 | [输入](../examples/overlong.input.json) / [Mock](../examples/overlong.mock.output.json) | 初稿 24 秒、目标 10 秒；固定流程改为 10 秒并重新检查。 |
| 宽目标对照 | [输入](../examples/wide-target.input.json) / [Mock](../examples/wide-target.mock.output.json) | 初稿与超时场景相同、目标变为 30 秒；24 秒位于允许范围内。Mock 随后仍按固定流程生成 30 秒稿，不代表模型决定。 |
| 改写检索 | [输入](../examples/rewrite-compositional.input.json) / [Mock](../examples/rewrite-compositional.mock.output.json) | `视频配文字` 初搜 0；固定流程改写为 `字幕` 后命中相关素材。 |
| 主题缺口 | [价格输入](../examples/unsupported-price.input.json) / [Mock](../examples/unsupported-price.mock.output.json)、[安卓输入](../examples/unsupported-android.input.json) / [Mock](../examples/unsupported-android.mock.output.json) | 没有价格或安卓支持事实，均返回 `incomplete/missing_topic_evidence`。 |
| 替换素材 | [输入](../examples/normal.input.json) / [替换素材](../examples/changed-materials.json) / [Mock](../examples/changed-material.mock.output.json) | 相同主题下输出改为引用“支持导入字幕文件”的原文；与正常输出的来源事实不同。 |
| 调用上限 | [输入](../examples/normal.input.json) / [Mock](../examples/limit.mock.output.json) | 上限为 1，后续调用被拒并返回未完成。 |

Mock 是固定流程，只验证工具和状态，不证明模型决策。每份输出均包含实际输入、执行结果和 Trace。

## 真实模型记录与版本边界

修复前提交 `910070e2aa0ce8bbd7b3c16ec0d7c9f05ced7361` 的真实 GLM 记录保留于 [normal.glm.output.json](../examples/normal.glm.output.json)、[overlong.glm.output.json](../examples/overlong.glm.output.json)、[failure.glm.output.json](../examples/failure.glm.output.json)、[no-material.glm.output.json](../examples/no-material.glm.output.json) 和 [rewrite.glm.output.json](../examples/rewrite.glm.output.json)。旧版素材与规则快照见 [pre-review-materials.json](../examples/pre-review-materials.json)、[pre-review-brand_rules.json](../examples/pre-review-brand_rules.json)。这些记录可证明旧版的实际 SDK 调用，**不能证明当前代码已经复现相同行为**。

旧版 `rewrite` 将“视频转文字”等同于添加字幕并完成；当前版本把转写作为独立主题要求，原始素材没有转写事实，因此相同输入现在返回资料缺口（[当前 Mock](../examples/rewrite.mock.output.json)）。旧版替换素材调用还出现过 [HTTP 429](../examples/rate-limit.glm.output.json)，不得当作替换场景的成功证据。

第二轮修复中间版的真实 GLM 对照使用相同的 24 秒初稿：[目标 10 秒](../examples/overlong.dev.glm.output.json) 时，模型观察估时未通过后修改并重估为 8.2 秒；[目标 30 秒](../examples/wide-target.dev.glm.output.json) 时，模型观察 24 秒符合允许范围后直接检查原稿。它们和 [正常场景](../examples/normal.dev.glm.output.json) 均在最后一次原子事实语法修复之前录制；之后已用当前代码与数据逐工具重放，结果和 SDK ID 一致。逐步重放不代表用最终代码再次调用了模型。

最终冻结版的真实对照使用相同的 24 秒初稿：[目标 10 秒](../examples/overlong.v2.glm.output.json) 时，模型观察初稿未通过后修改并重估为 9.0 秒，再提交脚本；[目标 30 秒](../examples/wide-target.v2.glm.output.json) 时，模型观察 24 秒符合要求后直接检查原稿并提交。10 秒场景的第二次估时与规则检查属于同一条模型消息，它们是并列请求，不代表模型先看到第二次估时结果才决定检查规则；初稿估时及之后的修改来自不同消息，且前一次结果先返回。

[最终版正常真实记录](../examples/normal.v2.glm.output.json) 使用目标 30 秒、无初稿输入，模型检索素材并提交了通过检查的 28 秒脚本。该结果由最终冻结代码真实调用并录制，已逐步重放。

[最终版检索改写真实记录](../examples/rewrite-compositional.v2.glm.output.json) 先查询 `视频配文字`，工具返回 0 条；模型在下一条消息将查询改为 `视频字幕`，命中 P01 后生成 30 秒脚本。首个检索结果先于改写请求返回，SDK 事件及工具结果均保留在记录中。

[最终版工具失败真实记录](../examples/failure.v2.glm.output.json) 对首次检索注入失败；模型收到失败后在下一条消息重新检索，随后生成并提交了 26 秒脚本。失败与恢复均保留在 Trace 中。

[最终版价格缺口真实记录](../examples/unsupported-price.v2.glm.output.json) 的用户主题为“字幕工具价格介绍”；模型检索后停止，系统返回 `incomplete/missing_topic_evidence`，明确缺少 `subtitle.price`，没有提交脚本。

[最终版无素材真实记录](../examples/no-material.v2.glm.output.json) 对“咖啡豆烘焙”执行四次查询或改写，均未命中；模型停止后系统返回 `incomplete/no_material`，没有提交脚本。

[素材替换真实 GLM 记录](../examples/changed-material.v2.glm.output.json) 使用和正常场景完全相同的用户输入，仅将素材文件替换为 [changed-materials.json](../examples/changed-materials.json)；最终脚本使用替换后的“支持导入字幕文件。”原文，估时 30 秒并完成。替换素材仅有一条事实，模型为达到目标时长把同一事实重复三遍；来源可追溯，但脚本的表达质量有限。该记录在原子事实语法修复后录制，并已用当前代码重放。

模型调用不是确定性测试，复跑的动作可能不同。

## 限制

- 主题概念和事实类别使用有限词表与合成数据标注，原文证据正则及原子事实语法仅覆盖定义的格式，不能验证任意自然语言的语义或外部事实真实性。无法确认的复合句、否定、时间条件或未支持的句式保守拒绝；更换资料时需要人工核对主题、事实及标注。
- 旁白、字幕和画面说明严格引用素材，风格化空间有限。估时依据固定语速，不是实际配音测量。
- Trace 的 `next_action` 是执行顺序，`next_action_relation` 和 SDK 消息 ID 区分同消息并列调用与不同消息；不能把同一消息里的两个调用解释成观察结果后的因果决策。
- 智谱 SDK 接口连通不证明本业务场景符合 Coding Plan 套餐适用规则；请以[智谱 FAQ](https://docs.bigmodel.cn/cn/coding-plan/faq)为准。
