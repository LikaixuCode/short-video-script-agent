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
uv run --locked python -m src.main --mode mock --input examples/wide-target.input.json
uv run --locked python -m src.main --mode mock --input examples/missing.input.json
uv run --locked python -m src.main --mode mock --input examples/failure.input.json --fail-once search_materials
uv run --locked python -m src.main --mode mock --input examples/rewrite-compositional.input.json
uv run --locked python -m src.main --mode mock --input examples/unsupported-price.input.json
uv run --locked python -m src.main --mode mock --input examples/normal.input.json --materials examples/changed-materials.json
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
uv run --locked python -m src.main --mode glm --input examples/wide-target.input.json
uv run --locked python -m src.main --mode glm --input examples/rewrite-compositional.input.json
uv run --locked python -m src.main --mode glm --input examples/failure.input.json --fail-once search_materials
uv run --locked python -m src.main --mode glm --input examples/normal.input.json --materials examples/changed-materials.json
```

默认使用 `glm-5.3-flash` 和智谱 Anthropic 兼容请求地址 `https://open.bigmodel.cn/api/anthropic`。真实模式不自动切换到 Mock；缺密钥、工具未恢复、调用失败或达到限制均返回未完成。

智谱支持 [Claude Code 接入 Coding Plan](https://docs.bigmodel.cn/cn/coding-plan/tool/claude)，其 [FAQ](https://docs.bigmodel.cn/cn/coding-plan/faq) 对自建应用要求使用标准 API；SDK 嵌入本业务 Agent 的套餐适用范围仍需以智谱规则为准。实际接口调用成功不能证明套餐适用已获确认。

## 输入、输出与边界

输入 JSON 包含 `topic`、`audience`、`platform`、`target_seconds` 和 `style`；可选 `initial_script` 用于提交待检查的初稿，`duration_tolerance` 是允许低于目标的比例，默认 `0.2`。缺任一必填项时不调用模型。`--materials` 和 `--rules` 可替换合成素材及规则文件；`--fail-once search_materials|estimate_duration|check_rules` 可注入一次失败。

输出 `script` 有标题、分镜、逐镜旁白、字幕、画面、镜头时长、素材 ID、事实 ID、对应原文以及预计总时长；`status`、`reason`、`risks` 和 `trace` 记录任务状态、原因和工具结果。`completed` 表示模型运行结束且通过业务检查的脚本已经提交；`incomplete` 不声称生成成功。

- 事实可追溯：每镜的旁白、字幕和画面说明必须逐字对应已检索素材的同一事实，标题限素材标题或通用标题。素材/事实 ID 必须唯一；事实类别同时满足规则文件的证据模式和代码中限定的原子事实格式。当前类别只表示**当前肯定事实**；无法确认的复合句、否定或历史限定会保守拒绝，明确的“不收费”和“不免费”分别标为免费和付费。主题中的价格、免费、免费试用、安卓、iOS、规格等要求必须由选中事实覆盖；未识别的主题限定和资料缺口返回未完成。有限的格式不能替代通用语义判断或真实性核验，扩展素材格式时需要人工核查并补测试。
- 时长：默认总时长需在目标的 80%–100% 内，且不得超出目标；可通过 `duration_tolerance` 调整下限，例如 `0.1` 表示 90%–100%。每镜按旁白字数和每秒 4 个字估算最低口播秒数，并用十进制相加检查总时长。它是估计值，实际口播、停顿和转场需人工调整。
- 受众、平台和风格传入模型，模型可调整镜头选择、顺序和节奏；由于旁白必须引用原文，项目不保证自由的风格化改写，也不验证平台特定规则。
- 检索用别名与可组合的主题词组确定素材概念，再根据当前查询匹配索引。例如 `视频配文字` 可先检索为空，再改写为 `字幕`；`咖啡豆烘焙` 改写为 `字幕` 不能命中。未配置的词组可能无法召回，这不是通用语义检索。`视频转文字` 涉及转写，当前素材没有转写事实，会返回资料缺口。
- 如已提交脚本后又尝试新稿或后续检查失败，旧稿只保留在 `previous_submissions`，不冒充本次完成结果；对同一脚本的成功复查不撤销已有提交。
- `--max-turns` 限 SDK 模型轮次（默认 12）；`--max-tool-calls` 限业务工具实际调用次数（默认 16）。`next_action` 只表示工具执行顺序，`next_action_relation` 标明同一模型消息、不同模型消息或仅有执行顺序；同一消息中的并列调用不表示模型已经观察前一个工具结果。

## 评审入口

- [需求与验收](docs/01-requirements.md)、[方案设计](docs/02-solution.md)、[测试与实际运行记录](docs/03-evaluation.md)
- [AI 使用说明](docs/04-ai-usage.md)、[复盘](docs/05-retrospective.md)
- `examples/*.input.json` 为合成输入，`*.mock.output.json` 为当前版本固定流程的离线结果；未带版本后缀的历史 `*.glm.output.json` 属于修复前提交，`*.dev.glm.output.json` 是第二轮修复中间版，不能把它们冒充最终版本的模型运行。最终版与中间版记录的具体边界、限流情况见评估文档。输出内包含 Trace；`examples/test-results.txt` 为自动化测试结果。

SDK 工具注册参考 [Claude Agent SDK 自定义工具文档](https://code.claude.com/docs/en/agent-sdk/custom-tools)。目录组织参考 [agent-campus-submission-example](https://github.com/lwz1357/agent-campus-submission-example)；该示例不构成真实模型决策的证据。
