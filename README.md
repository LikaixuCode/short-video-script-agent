# 短视频脚本 Agent

题号 4。这个项目提供 Python CLI：模型根据主题、受众、平台、目标时长和风格检索合成素材，生成带分镜和出处的脚本，并按时长及品牌检查结果决定修改或结束。只生成可编辑的 JSON 脚本，不制作视频，也不发布内容。

模型由 Claude Agent SDK 驱动。素材检索、时长估算和规则检查由本地业务代码执行；只有**同一版脚本**通过时长和规则检查后，提交工具才会返回可用脚本。题目规定的素材和品牌规则均为合成数据。

## 安装和测试

需要 Python 3.12、`uv`。本项目锁定 `claude-agent-sdk==0.2.175`，SDK 提供内置 Claude Code 运行时、进程内自定义 MCP 工具和模型循环。

```bash
uv sync --locked --python 3.12
uv run --locked python -m pytest -q
```

## 离线运行

Mock 使用固定脚本调用相同业务工具，供无密钥时验证流程，**不能证明真实模型决策效果**。非零退出码表示未完成或输入错误。

```bash
uv run --locked python -m src.main --mode mock --input examples/normal.input.json
uv run --locked python -m src.main --mode mock --input examples/overlong.input.json
uv run --locked python -m src.main --mode mock --input examples/missing.input.json
uv run --locked python -m src.main --mode mock --input examples/failure.input.json --fail-once search_materials
```

## 真实模型运行

复制模板，在本地编辑 `.env` 填写密钥后加载变量；不要提交 `.env`：

```bash
cp .env.example .env
# 本地编辑 .env，填写 ANTHROPIC_AUTH_TOKEN
set -a
. ./.env
set +a
uv run --locked python -m src.main --mode glm --input examples/normal.input.json
uv run --locked python -m src.main --mode glm --input examples/overlong.input.json
uv run --locked python -m src.main --mode glm --input examples/rewrite.input.json
uv run --locked python -m src.main --mode glm --input examples/failure.input.json --fail-once search_materials
```

默认使用 `glm-5.3-flash` 和智谱 Anthropic 兼容请求地址 `https://open.bigmodel.cn/api/anthropic`。真实模式不自动切换到 Mock；缺密钥、工具未恢复、调用失败或达到限制均返回未完成。

智谱支持 [Claude Code 接入 Coding Plan](https://docs.bigmodel.cn/cn/coding-plan/tool/claude)，其 [FAQ](https://docs.bigmodel.cn/cn/coding-plan/faq) 对自建应用要求使用标准 API；SDK 嵌入本业务 Agent 的套餐适用范围仍需以智谱规则为准。实际接口调用成功不能证明套餐适用已获确认。

## 输入、输出与边界

输入 JSON 包含 `topic`、`audience`、`platform`、`target_seconds` 和 `style`；可选 `initial_script` 用于提交待检查的初稿。缺任一必填项时不调用模型。`--materials` 和 `--rules` 可替换合成素材及规则文件；`--fail-once search_materials|estimate_duration|check_rules` 可注入一次失败。

输出 `script` 有标题、分镜、逐镜旁白、字幕、画面、镜头时长、素材 ID、事实 ID、对应原文以及预计总时长；`status`、`reason`、`risks` 和 `trace` 记录任务状态、原因和工具结果。`completed` 表示模型运行结束且通过业务检查的脚本已经提交；`incomplete` 不声称生成成功。

- 事实可追溯：每镜的旁白和字幕必须逐字等于已检索的某条素材原文，画面必须等于该素材事实提供的画面说明；标题限素材标题或规则中的通用标题。模型不能仅靠自报通过检查，也不能添加来源中不存在的功效、数字或效果数据。这是有意缩小表达范围的保守校验，并非开放域事实核查。
- 时长：每镜需要填写预计时长；工具以旁白字数和每秒 4 个字计算镜头最低秒数，校验每镜下限，并以十进制相加判断总时长不超目标。它是简化估计，实际口播速度、停顿、转场需人工调整。
- 受众、平台和风格传入模型，模型可调整镜头选择、顺序和节奏；由于旁白必须引用原文，项目不保证自由的风格化改写，也不验证平台特定规则。
- 检索使用素材预设的主题同义词限定范围，再用检索词匹配素材索引。例如 `视频转文字` 首次可能未命中，改写为 `字幕` 可以命中；未配置的同义词无法保证召回，应补充素材索引或提供更多资料。这不是通用语义检索。
- `--max-turns` 限 SDK 模型轮次（默认 12）；`--max-tool-calls` 限业务工具实际调用次数（默认 16）。执行到上限仍未完成时返回 `incomplete`。Trace 的每步包含参数、工具返回、模型提供的一句决策依据和下一动作；SDK 事件记录实际工具请求和对应返回 ID。

## 评审入口

- [需求与验收](docs/01-requirements.md)、[方案设计](docs/02-solution.md)、[测试与实际运行记录](docs/03-evaluation.md)
- [AI 使用说明](docs/04-ai-usage.md)、[复盘](docs/05-retrospective.md)
- `examples/*.input.json` 为合成输入，`examples/*.glm.output.json` 为真实模型实际运行结果，`examples/*.mock.output.json` 为固定流程的离线结果。输出内包含 Trace；`examples/test-results.txt` 为自动化测试结果。

SDK 工具注册参考 [Claude Agent SDK 自定义工具文档](https://code.claude.com/docs/en/agent-sdk/custom-tools)。目录组织参考 [agent-campus-submission-example](https://github.com/lwz1357/agent-campus-submission-example)；该示例不构成真实模型决策的证据。
