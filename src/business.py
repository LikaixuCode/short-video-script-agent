import json
import math
import re
from decimal import Decimal
from hashlib import sha256

from pydantic import ValidationError

from .models import Material, Request, Rules, Script


# Curated positive, current, atomic facts for the synthetic catalogue. Unknown
# sentences and compound claims require manual splitting/review rather than
# being promoted to verified topic coverage.
SUBJECT = r"(?:该|此|本)?(?:字幕工具|字幕产品|字幕|产品|工具|软件)"
END = r"[。.!！]?$"
ATOMIC_FACT_PATTERNS = {
    "feature": rf"^(?:{SUBJECT})?(?:支持|提供|具有)(?:为视频添加字幕|(?:调整|更改|设置)字幕(?:的)?(?:字体|样式|颜色)(?:样式)?|(?:导出|下载|导入)字幕(?:文件)?|裁剪视频片段|调整片段顺序|字幕功能){END}",
    "style": rf"^(?:{SUBJECT})?(?:支持|提供)(?:调整|更改|修改|设置)?(?:字幕)?(?:的)?(?:字体|样式|颜色)(?:样式)?{END}",
    "export": rf"^(?:{SUBJECT})?(?:支持|提供)(?:导出|下载)(?:字幕文件|字幕|文件){END}",
    "transcription": rf"^(?:{SUBJECT})?(?:支持|提供)(?:识别|转换|转写)(?:视频)?(?:语音|音频)(?:为字幕|为文字)?{END}",
    "privacy": rf"^(?:{SUBJECT})?(?:字幕)?(?:数据|隐私)(?:加密|加密保护|安全存储|保护){END}",
    "specification": rf"^(?:{SUBJECT})?(?:字幕)?(?:技术规格|规格|参数|分辨率)(?:为|是)?\s*\d+(?:\.\d+)?\s*(?:像素|mb|gb|帧|秒|分钟|小时){END}",
    "performance": rf"^(?:{SUBJECT})?(?:字幕)?(?:处理)?(?:效率|速度|性能)(?:提升|提高|缩短)(?:了)?\s*\d+(?:\.\d+)?\s*(?:%|秒|分钟|小时){END}",
    "price": rf"^{SUBJECT}(?:(?:价格|收费|费用|售价)(?:为|是)?|每(?:月|年))\s*\d+(?:\.\d+)?\s*(?:元|人民币|美元){END}",
    "free": rf"^{SUBJECT}(?:完全|永久|长期)?(?:免费|不收费|0元){END}",
    "free_trial": rf"^{SUBJECT}(?:支持|提供)?(?:免费试用|免费体验)(?:\d+(?:天|小时|周|月))?{END}",
    "paid": rf"^{SUBJECT}(?:(?:收费|付费|不免费|非免费)|每(?:月|年)\s*\d+(?:\.\d+)?\s*元){END}",
    "compatibility": rf"^{SUBJECT}(?:兼容|支持|适配)\s*(?:安卓|android|ios|iphone|苹果系统|windows|macos)\s*(?:系统|设备|平台)?{END}",
    "android": rf"^{SUBJECT}(?:兼容|支持|适配)\s*(?:安卓|android)\s*(?:系统|设备|平台)?{END}",
    "ios": rf"^{SUBJECT}(?:兼容|支持|适配)\s*(?:ios|iphone|苹果系统)\s*(?:系统|设备|平台)?{END}",
}


class ScriptTools:
    """本次执行的业务边界。模型只可通过这些工具访问素材和提交脚本。"""

    def __init__(self, request: Request, materials: list[Material], rules: Rules, max_tool_calls: int = 16, fail_once: str | None = None):
        if len({item.id for item in materials}) != len(materials):
            raise ValueError("素材 ID 必须唯一")
        for item in materials:
            if len({fact.id for fact in item.facts}) != len(item.facts):
                raise ValueError(f"素材 {item.id} 的事实 ID 必须唯一")
            for fact in item.facts:
                for aspect in fact.aspects:
                    pattern = rules.aspect_evidence_patterns.get(aspect)
                    atomic = ATOMIC_FACT_PATTERNS.get(aspect)
                    evidence = fact.text
                    if aspect == "free":
                        evidence = evidence.replace("不收费", "免费")
                    elif aspect == "paid":
                        evidence = evidence.replace("不免费", "付费").replace("非免费", "付费")
                    # Categories denote current positive facts. Negated or past
                    # claims require a separate explicitly modeled category.
                    uncertain = re.search(r"不|未|无|非|没|禁止|拒绝|取消|终止|废止|曾经|过去|以前", evidence)
                    if not pattern or not atomic or uncertain or not re.search(pattern, fact.text, flags=re.IGNORECASE) or not re.fullmatch(atomic, fact.text, flags=re.IGNORECASE):
                        raise ValueError(f"素材 {item.id}/{fact.id} 的 {aspect} 标注缺少原文证据")
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
        self.previous_submissions: list[dict] = []
        self.events: list[dict] = []

    def call(self, name: str, arguments: dict) -> dict:
        reason = arguments.get("reason", "")
        previous = self.final if name in {"estimate_duration", "check_rules", "finalize_script"} else None
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
        if previous:
            same_valid = (result.get("ok") and result.get("script_id") == previous["script_id"]
                          and (name != "estimate_duration" or result.get("within_target"))
                          and (name != "check_rules" or result.get("passed")))
            if not same_valid:
                self.previous_submissions.append(previous)
                if name != "finalize_script" or not result.get("ok"):
                    self.final = None
        self.events.append({"step": len(self.events) + 1, "tool": name, "arguments": arguments, "result": result, "decision_basis": reason})
        return result

    def _topic_analysis(self) -> dict:
        """Recognize explicit topic concepts and facets; unknown text is not assumed covered."""
        normalize = lambda value: re.sub(r"[^\w\u4e00-\u9fff]", "", value.lower())
        topic = normalize(self.request.topic)
        matched = []
        recognized = set()
        for item in self.materials:
            aliases = [normalize(term) for term in item.topics if normalize(term)]
            direct = [term for term in aliases if term in topic or (len(topic) >= 2 and topic in term)]
            pattern_matches = []
            for pattern in item.topic_patterns:
                terms = [next((normalize(term) for term in sorted(group, key=len, reverse=True) if normalize(term) and normalize(term) in topic), None) for group in pattern]
                if terms and all(terms):
                    pattern_matches.extend(terms)
            if direct or pattern_matches:
                matched.append(item)
                recognized.update(direct + pattern_matches)
        # Longer recognized phrases own their character spans: e.g. “不免费”
        # expresses paid status, not both “免费” and “收费”.
        spans = []
        for aspect, terms in self.rules.topic_aspects.items():
            for term in terms:
                normalized_term = normalize(term)
                for match in re.finditer(re.escape(normalized_term), topic) if normalized_term else []:
                    spans.append((match.start(), match.end(), aspect, normalized_term))
        chosen = []
        for start, end, aspect, term in sorted(spans, key=lambda item: (-(item[1] - item[0]), item[0])):
            if not any(start < other_end and other_start < end for other_start, other_end, _, _ in chosen):
                chosen.append((start, end, aspect, term))
        aspects = {aspect for _, _, aspect, _ in chosen}
        if not aspects:
            aspects = {"feature"}
        recognized.update(term for _, _, _, term in chosen)
        recognized.update(normalize(term) for term in self.rules.topic_filler if normalize(term))
        remaining = topic
        for term in sorted(recognized, key=len, reverse=True):
            remaining = remaining.replace(term, "")
        concepts = {item.topic_concept or item.id for item in matched}
        available = {concept: set() for concept in concepts}
        for item in matched:
            available[item.topic_concept or item.id].update(aspect for fact in item.facts for aspect in fact.aspects)
        missing = [{"concept": concept, "aspect": aspect} for concept in sorted(concepts) for aspect in sorted(aspects - available[concept])]
        if remaining:
            missing.append({"concept": "unknown", "aspect": remaining})
        return {"topic": topic, "materials": matched, "concepts": concepts, "required_aspects": aspects, "missing": missing}

    def _search_materials(self, args: dict) -> dict:
        query = str(args.get("query", "")).strip().lower()
        if not query:
            return {"ok": False, "code": "empty_query", "message": "查询词不能为空"}
        normalize = lambda value: re.sub(r"[^\w\u4e00-\u9fff]", "", value.lower())
        query_key = normalize(query)
        if not query_key:
            return {"ok": False, "code": "empty_query", "message": "查询词不能为空"}
        topic = self._topic_analysis()
        if not topic["topic"]:
            return {"ok": False, "code": "empty_topic", "message": "主题没有可用于检索的内容"}
        found = [item for item in topic["materials"] if any(normalize(term) and (normalize(term) in query_key or (len(query_key) >= 2 and query_key in normalize(term))) for term in item.search_terms)]
        for item in found:
            self.retrieved[item.id] = item
        return {"ok": True, "query": query, "materials": [item.model_dump() for item in found], "count": len(found), "required_aspects": sorted(topic["required_aspects"]), "missing_information": topic["missing"]}

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
        minimum_total = target * (Decimal(1) - Decimal(str(self.request.duration_tolerance)))
        result = {"ok": True, "script_id": self._fingerprint(script), "scenes": scenes, "total_seconds": float(total), "target_seconds": self.request.target_seconds, "minimum_target_seconds": float(minimum_total), "duration_tolerance": self.request.duration_tolerance, "over_by_seconds": float(max(Decimal(0), total - target)), "short_by_seconds": float(max(Decimal(0), minimum_total - total)), "within_target": minimum_total <= total <= target and all(item["long_enough"] for item in scenes), "estimate_method": f"旁白按每秒 {self.rules.narration_characters_per_second:g} 个字估算下限，镜头按填写时长精确求和"}
        self.estimates[result["script_id"]] = result
        return result

    def _check_rules(self, args: dict) -> dict:
        script, error = self._parse_script(args)
        if error:
            return error
        violations = []
        citations = []
        topic = self._topic_analysis()
        coverage: dict[str, set[str]] = {}
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
            coverage.setdefault(material.topic_concept or material.id, set()).update(fact.aspects)
            citations.append({"scene": number, "source_id": material.id, "fact_id": fact.id, "source_text": fact.text})
        missing = list(topic["missing"])
        for concept in sorted(topic["concepts"]):
            for aspect in sorted(topic["required_aspects"] - coverage.get(concept, set())):
                item = {"concept": concept, "aspect": aspect}
                if item not in missing:
                    missing.append(item)
        if not topic["concepts"]:
            missing.append({"concept": "unknown", "aspect": "主题没有可确认的相关素材"})
        if missing:
            violations.append({"scene": None, "code": "topic_not_covered", "detail": "脚本或素材未覆盖主题要求", "missing_information": missing})
        text = json.dumps(script.model_dump(), ensure_ascii=False)
        for term in self.rules.forbidden_terms:
            if term in text:
                violations.append({"scene": None, "code": "forbidden_term", "detail": f"出现禁用词：{term}"})
        result = {"ok": True, "script_id": self._fingerprint(script), "passed": not violations, "violations": violations, "citations": citations, "missing_information": missing, "scope": "逐句严格匹配已检索素材，检查主题事实类别和禁用词"}
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
        missing_information = list(self._topic_analysis()["missing"]) if self.retrieved else []
        if not success:
            latest_check = next((event["result"] for event in reversed(self.events) if event["tool"] == "check_rules" and event["result"].get("ok")), None)
            for missing in latest_check.get("missing_information", []) if latest_check else []:
                if missing not in missing_information:
                    missing_information.append(missing)
        if success:
            reason = None
        elif self.limit_hit or (self.calls >= self.max_tool_calls and not self.final):
            reason = "max_tool_calls"
        elif terminal.get("status") != "completed":
            reason = terminal.get("reason", "model_error")
        elif any(not e["result"]["ok"] and e["result"].get("code") == "injected_tool_failure" for e in self.events):
            reason = "tool_failure"
        elif missing_information:
            reason = "missing_topic_evidence"
        elif any(e["tool"] == "search_materials" and e["result"].get("count") == 0 for e in self.events) and not self.retrieved:
            reason = "no_material"
        else:
            reason = "no_valid_script"
        trace = [dict(event) for event in self.events]
        requests = [event for event in terminal.get("sdk_events", []) if event.get("type") == "tool_request"]
        for event in trace:
            for index, request in enumerate(requests):
                if request["tool"].rsplit("__", 1)[-1] == event["tool"] and request.get("arguments") == event["arguments"]:
                    event["model_message_id"] = request.get("message_id")
                    event["tool_use_id"] = request.get("tool_use_id")
                    requests.pop(index)
                    break
        for index, event in enumerate(trace):
            if index + 1 < len(trace):
                next_event = trace[index + 1]
                next_action = next_event["tool"]
                current_message = event.get("model_message_id")
                next_message = next_event.get("model_message_id")
                if current_message and next_message:
                    relation = "same_model_message" if current_message == next_message else "different_model_message"
                else:
                    relation = "execution_order_only"
            elif terminal.get("status") == "completed":
                next_action = "finish"
                relation = "terminal"
            else:
                next_action = "interrupted"
                relation = "terminal"
            event["next_action"] = next_action
            event["next_action_relation"] = relation
        return {"status": "completed" if success else "incomplete", "reason": reason, "mode": mode, "request": self.request.model_dump(mode="json"), "script": self.final, "previous_submissions": self.previous_submissions, "missing_information": missing_information, "risks": [] if success else [reason], "terminal": {**terminal, "tool_calls_executed": self.calls, "max_tool_calls": self.max_tool_calls}, "trace": trace}
