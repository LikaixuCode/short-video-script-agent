import json
import math
import re
from decimal import Decimal
from hashlib import sha256

from pydantic import ValidationError

from .models import Material, Request, Rules, Script


class ScriptTools:
    """本次执行的业务边界。模型只可通过这些工具访问素材和提交脚本。"""

    def __init__(self, request: Request, materials: list[Material], rules: Rules, max_tool_calls: int = 16, fail_once: str | None = None):
        self.request = request
        self.materials = materials
        self.rules = rules
        self.max_tool_calls = max_tool_calls
        self.fail_once = fail_once
        self.failed_once = False
        self.calls = 0
        self.limit_hit = False
        self.retrieved: dict[str, Material] = {}
        self.estimates: dict[str, dict] = {}
        self.checks: dict[str, dict] = {}
        self.final: dict | None = None
        self.events: list[dict] = []

    def call(self, name: str, arguments: dict) -> dict:
        reason = arguments.get("reason", "")
        if self.calls >= self.max_tool_calls:
            self.limit_hit = True
            result = {"ok": False, "code": "max_tool_calls", "message": "业务工具调用次数已达上限"}
        else:
            self.calls += 1
            if self.fail_once == name and not self.failed_once:
                self.failed_once = True
                result = {"ok": False, "code": "injected_tool_failure", "message": f"{name} 模拟失败"}
            else:
                result = getattr(self, f"_{name}")(arguments)
        self.events.append({"step": len(self.events) + 1, "tool": name, "arguments": arguments, "result": result, "decision_basis": reason})
        return result

    def _search_materials(self, args: dict) -> dict:
        query = str(args.get("query", "")).strip().lower()
        if not query:
            return {"ok": False, "code": "empty_query", "message": "查询词不能为空"}
        normalize = lambda value: re.sub(r"[\s，。,.!?！？]+", "", value.lower())
        query_key = normalize(query)
        if not query_key:
            return {"ok": False, "code": "empty_query", "message": "查询词不能为空"}
        original_key = normalize(self.request.topic)
        if not original_key:
            return {"ok": False, "code": "empty_topic", "message": "主题没有可用于检索的内容"}
        found = [item for item in self.materials if any(normalize(term) in query_key or query_key in normalize(term) for term in item.search_terms)
                 and any(normalize(topic) in original_key or original_key in normalize(topic) for topic in item.topics)]
        for item in found:
            self.retrieved[item.id] = item
        return {"ok": True, "query": query, "materials": [item.model_dump() for item in found], "count": len(found)}

    def _parse_script(self, args: dict) -> tuple[Script | None, dict | None]:
        try:
            script = Script.model_validate_json(args.get("script_json", ""))
        except ValidationError as exc:
            return None, {"ok": False, "code": "invalid_script", "message": "脚本格式不正确", "errors": exc.errors(include_url=False, include_input=False)}
        return script, None

    def _fingerprint(self, script: Script) -> str:
        normalized = json.dumps(script.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(normalized.encode()).hexdigest()

    def _estimate_duration(self, args: dict) -> dict:
        script, error = self._parse_script(args)
        if error:
            return error
        scenes = []
        for number, scene in enumerate(script.scenes, 1):
            spoken = len(re.sub(r"[\s\W_]", "", scene.narration, flags=re.UNICODE))
            minimum = max(self.rules.minimum_scene_seconds, math.ceil(spoken / self.rules.narration_characters_per_second * 10) / 10)
            scenes.append({"scene": number, "source_id": scene.source_id, "fact_id": scene.fact_id, "requested_seconds": scene.duration_seconds, "minimum_seconds": minimum, "long_enough": scene.duration_seconds >= minimum})
        total = sum((Decimal(str(scene.duration_seconds)) for scene in script.scenes), Decimal(0))
        target = Decimal(str(self.request.target_seconds))
        result = {"ok": True, "script_id": self._fingerprint(script), "scenes": scenes, "total_seconds": float(total), "target_seconds": self.request.target_seconds, "over_by_seconds": float(max(Decimal(0), total - target)), "within_target": total <= target and all(item["long_enough"] for item in scenes), "estimate_method": f"旁白按每秒 {self.rules.narration_characters_per_second:g} 个字估算下限，镜头按填写时长精确求和"}
        self.estimates[result["script_id"]] = result
        return result

    def _check_rules(self, args: dict) -> dict:
        script, error = self._parse_script(args)
        if error:
            return error
        violations = []
        citations = []
        allowed_titles = self.rules.allowed_generic_titles + [item.title for item in self.retrieved.values()]
        if script.title not in allowed_titles:
            violations.append({"scene": None, "code": "unverified_title", "detail": "标题不在素材标题或通用标题中"})
        for number, scene in enumerate(script.scenes, 1):
            material = self.retrieved.get(scene.source_id)
            fact = next((fact for fact in material.facts if fact.id == scene.fact_id), None) if material else None
            if fact is None:
                violations.append({"scene": number, "code": "unknown_source", "detail": "来源未检索到或事实编号不存在"})
                continue
            if scene.narration != fact.text or scene.subtitle != fact.text:
                violations.append({"scene": number, "code": "unsupported_claim", "detail": "旁白和字幕须逐字等于素材原文"})
            if scene.visual != fact.visual:
                violations.append({"scene": number, "code": "unsupported_visual", "detail": "画面须使用素材中可追溯的画面说明"})
            citations.append({"scene": number, "source_id": material.id, "fact_id": fact.id, "source_text": fact.text})
        text = json.dumps(script.model_dump(), ensure_ascii=False)
        for term in self.rules.forbidden_terms:
            if term in text:
                violations.append({"scene": None, "code": "forbidden_term", "detail": f"出现禁用词：{term}"})
        result = {"ok": True, "script_id": self._fingerprint(script), "passed": not violations, "violations": violations, "citations": citations, "scope": "逐句严格匹配已检索素材并检查禁用词"}
        self.checks[result["script_id"]] = result
        return result

    def _finalize_script(self, args: dict) -> dict:
        script, error = self._parse_script(args)
        if error:
            return error
        script_id = self._fingerprint(script)
        estimate = self.estimates.get(script_id)
        check = self.checks.get(script_id)
        if not estimate or not estimate["within_target"] or not check or not check["passed"]:
            return {"ok": False, "code": "unchecked_or_invalid_script", "message": "同一版脚本必须先通过时长和规则检查"}
        self.final = {"script_id": script_id, "title": script.title, "scenes": script.model_dump()["scenes"], "estimated_total_seconds": estimate["total_seconds"], "citations": check["citations"], "risks": []}
        return {"ok": True, "script_id": script_id, "message": "已提交通过检查的可编辑脚本"}

    def output(self, terminal: dict, mode: str) -> dict:
        success = bool(self.final) and terminal.get("status") == "completed" and not self.limit_hit
        if success:
            reason = None
        elif self.limit_hit or (self.calls >= self.max_tool_calls and not self.final):
            reason = "max_tool_calls"
        elif terminal.get("status") != "completed":
            reason = terminal.get("reason", "model_error")
        elif any(not e["result"]["ok"] and e["result"].get("code") == "injected_tool_failure" for e in self.events):
            reason = "tool_failure"
        elif any(e["tool"] == "search_materials" and e["result"].get("count") == 0 for e in self.events) and not self.retrieved:
            reason = "no_material"
        else:
            reason = "no_valid_script"
        trace = []
        for index, event in enumerate(self.events):
            if index + 1 < len(self.events):
                next_action = self.events[index + 1]["tool"]
            elif terminal.get("status") == "completed":
                next_action = "finish"
            else:
                next_action = "interrupted"
            trace.append({**event, "next_action": next_action})
        return {"status": "completed" if success else "incomplete", "reason": reason, "mode": mode, "request": self.request.model_dump(mode="json"), "script": self.final, "risks": [] if success else [reason], "terminal": {**terminal, "tool_calls_executed": self.calls, "max_tool_calls": self.max_tool_calls}, "trace": trace}
