import json
import os

from .business import ScriptTools


def run_mock(service: ScriptTools) -> dict:
    """固定决策序列只用于离线验证流程，不证明真实模型能力。"""
    terminal = {"status": "completed", "source": "scripted_mock", "reason": "scripted_finish"}
    found = service.call("search_materials", {"query": service.request.topic, "reason": "检索主题素材"})
    if not found["ok"] or not found["materials"]:
        return service.output(terminal, "mock")
    material = found["materials"][0]
    if service.request.initial_script:
        first = json.dumps(service.request.initial_script, ensure_ascii=False)
        service.call("estimate_duration", {"script_json": first, "reason": "先检查用户提供的初稿"})
    fact = material["facts"][0]
    minimum = max(service.rules.minimum_scene_seconds, len(fact["text"].strip("。")) / service.rules.narration_characters_per_second)
    script = {"title": material["title"], "scenes": [{"source_id": material["id"], "fact_id": fact["id"], "narration": fact["text"], "subtitle": fact["text"], "visual": fact["visual"], "duration_seconds": max(3, minimum)}]}
    script_json = json.dumps(script, ensure_ascii=False)
    estimate = service.call("estimate_duration", {"script_json": script_json, "reason": "估算引用素材的精简脚本"})
    if not estimate["ok"] or not estimate["within_target"]:
        return service.output(terminal, "mock")
    check = service.call("check_rules", {"script_json": script_json, "reason": "检查素材和品牌规则"})
    if check["ok"] and check["passed"]:
        service.call("finalize_script", {"script_json": script_json, "reason": "提交已经通过两项检查的脚本"})
    return service.output(terminal, "mock")


async def run_glm(service: ScriptTools, max_turns: int = 12) -> dict:
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ClaudeSDKError,
        ResultError, ResultMessage, ToolResultBlock, ToolUseBlock, UserMessage,
        create_sdk_mcp_server, tool,
    )

    token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not token:
        return service.output({"status": "incomplete", "reason": "missing_credential"}, "glm")

    def response(name, args):
        result = service.call(name, args)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "is_error": not result["ok"]}

    @tool("search_materials", "检索可用于当前主题的合成产品素材。没有结果可尝试改写查询或请求补充。", {"query": str, "reason": str})
    async def search_materials(args):
        return response("search_materials", args)

    @tool("estimate_duration", "估算脚本每个镜头和总时长。script_json 是脚本 JSON 字符串。", {"script_json": str, "reason": str})
    async def estimate_duration(args):
        return response("estimate_duration", args)

    @tool("check_rules", "验证脚本逐镜素材来源、旁白、字幕、画面及品牌禁用词。script_json 是脚本 JSON 字符串。", {"script_json": str, "reason": str})
    async def check_rules(args):
        return response("check_rules", args)

    @tool("finalize_script", "提交同一版本已通过时长和规则检查的脚本。script_json 是脚本 JSON 字符串。", {"script_json": str, "reason": str})
    async def finalize_script(args):
        return response("finalize_script", args)

    names = ["search_materials", "estimate_duration", "check_rules", "finalize_script"]
    server = create_sdk_mcp_server(name="video", tools=[search_materials, estimate_duration, check_rules, finalize_script])
    options = ClaudeAgentOptions(
        tools=[], allowed_tools=[f"mcp__video__{name}" for name in names], mcp_servers={"video": server},
        strict_mcp_config=True, setting_sources=[], permission_mode="dontAsk", max_turns=max_turns,
        model=os.environ.get("GLM_MODEL", "glm-5.3-flash"),
        env={"ANTHROPIC_AUTH_TOKEN": token, "ANTHROPIC_BASE_URL": os.environ.get("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic"), "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_MAX_RETRIES": "0", "API_TIMEOUT_MS": "60000"},
        system_prompt=(
            "你是短视频脚本 Agent。必须先检索本次主题的素材；检索没有结果可改写查询，但不可使用与主题无关素材。"
            "只能依据检索到的素材构造可编辑脚本。脚本格式为 JSON 对象：title 是素材 title 或'产品功能介绍'；"
            "scenes 为非空数组，每个对象包含 source_id、fact_id、narration、subtitle、visual、duration_seconds。"
            "旁白和字幕须逐字等于该 source_id 的 fact_id 对应 text；visual 须逐字等于该 fact 的 visual，不可编造产品效果或事实。"
            "根据目标时长选用适当事实、合理编排镜头，每次工具参数 script_json 必须是完整脚本的 JSON 字符串。"
            "如果用户提供 initial_script，检索素材后应先估算这个初稿，随后根据返回结果调整。"
            "估算时长和规则检查后，如果超时长、镜头太短或规则不通过，修改脚本并重新执行对应检查；每次修改后两项检查均需重新通过。"
            "满足两项检查才调用 finalize_script；无法解决时直接说明未完成，不要声称成功。"
            "每次工具调用的 reason 写一句简短公开决策依据，不输出内部思维过程。"
        ),
    )
    terminal = {"status": "incomplete", "reason": "no_result", "model": options.model}
    sdk_events = []
    reported_models = set()
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(json.dumps(service.request.model_dump(mode="json"), ensure_ascii=False))
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    if message.model:
                        reported_models.add(message.model)
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            sdk_events.append({"type": "tool_request", "tool_use_id": block.id, "message_id": message.message_id, "tool": block.name, "arguments": block.input})
                elif isinstance(message, UserMessage) and isinstance(message.content, list):
                    for block in message.content:
                        if isinstance(block, ToolResultBlock):
                            sdk_events.append({"type": "tool_result", "tool_use_id": block.tool_use_id, "is_error": block.is_error})
                elif isinstance(message, ResultMessage):
                    terminal = {"status": "completed" if not message.is_error and message.subtype == "success" else "incomplete", "reason": message.terminal_reason or message.subtype, "turns": message.num_turns, "model": options.model}
                    if terminal["status"] == "incomplete":
                        terminal["api_error_status"] = message.api_error_status
                        terminal["detail"] = (message.result or "").replace(token, "[REDACTED]")[:500]
    except ResultError as exc:
        terminal = {"status": "incomplete", "reason": exc.terminal_reason or exc.subtype or "sdk_result_error", "model": options.model}
    except ClaudeSDKError as exc:
        terminal = {"status": "incomplete", "reason": type(exc).__name__, "model": options.model}
    except Exception as exc:
        terminal = {"status": "incomplete", "reason": type(exc).__name__, "model": options.model}
    terminal["reported_models"] = sorted(reported_models)
    terminal["sdk_events"] = sdk_events
    return service.output(terminal, "glm")
