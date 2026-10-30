import json
from copy import deepcopy
from pathlib import Path

import pytest

from src.agent import run_mock
from src.business import ScriptTools
from src.models import Material, Request, Rules

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def inputs():
    request = Request.model_validate_json((ROOT / "examples/normal.input.json").read_text())
    materials = [Material.model_validate(item) for item in json.loads((ROOT / "data/materials.json").read_text())]
    rules = Rules.model_validate_json((ROOT / "data/brand_rules.json").read_text())
    return request, materials, rules


def candidate(material):
    fact = material.facts[0]
    return {"title": material.title, "scenes": [{"source_id": material.id, "fact_id": fact.id, "narration": fact.text, "subtitle": fact.text, "visual": fact.visual, "duration_seconds": 5}]}


def call(service, name, script):
    return service.call(name, {"script_json": json.dumps(script, ensure_ascii=False), "reason": "测试"})


def find(service, query="视频字幕"):
    return service.call("search_materials", {"query": query, "reason": "测试检索"})


def test_normal_script_has_traceable_citations_and_real_duration(inputs):
    request, materials, rules = inputs
    service = ScriptTools(request, materials, rules)
    output = run_mock(service)
    assert output["status"] == "completed"
    assert output["script"]["estimated_total_seconds"] <= request.target_seconds
    scene = output["script"]["scenes"][0]
    assert scene["source_id"] == output["script"]["citations"][0]["source_id"]
    assert scene["narration"] == output["script"]["citations"][0]["source_text"]
    assert [event["tool"] for event in output["trace"]] == ["search_materials", "estimate_duration", "check_rules", "finalize_script"]
    assert output["terminal"]["source"] == "scripted_mock"


def test_overlong_initial_script_is_checked_and_replaced(inputs):
    request, materials, rules = inputs
    request = Request.model_validate_json((ROOT / "examples/overlong.input.json").read_text())
    output = run_mock(ScriptTools(request, materials, rules))
    estimates = [event["result"] for event in output["trace"] if event["tool"] == "estimate_duration"]
    assert not estimates[0]["within_target"] and estimates[0]["over_by_seconds"] == 14
    assert estimates[1]["within_target"]
    assert output["script"]["estimated_total_seconds"] <= 10


def test_no_match_and_original_topic_prevents_unrelated_rewrite(inputs):
    request, materials, rules = inputs
    request = request.model_copy(update={"topic": "咖啡豆烘焙"})
    service = ScriptTools(request, materials, rules)
    assert find(service, "咖啡")["count"] == 0
    assert find(service, "字幕")["count"] == 0
    assert service.output({"status": "completed"}, "mock")["reason"] == "no_material"


def test_punctuation_only_topic_cannot_select_arbitrary_material(inputs):
    request, materials, rules = inputs
    service = ScriptTools(request.model_copy(update={"topic": "！！！"}), materials, rules)
    assert find(service, "字幕")["code"] == "empty_topic"


def test_fuzzy_rewrite_finds_relevant_topic(inputs):
    request, materials, rules = inputs
    service = ScriptTools(request.model_copy(update={"topic": "如何在短视频上显示字幕"}), materials, rules)
    assert find(service, "视频字幕")['count'] == 1


def test_rewrite_after_no_results_stays_within_curated_aliases(inputs):
    request, materials, rules = inputs
    service = ScriptTools(request.model_copy(update={"topic": "视频转文字"}), materials, rules)
    assert find(service, "视频转文字")["count"] == 0
    assert find(service, "字幕")["materials"][0]["id"] == "P01"
    assert find(service, "剪辑")["count"] == 0


@pytest.mark.parametrize("failed_tool", ["search_materials", "estimate_duration", "check_rules"])
def test_failed_tool_cannot_claim_success(inputs, failed_tool):
    request, materials, rules = inputs
    service = ScriptTools(request, materials, rules, fail_once=failed_tool)
    output = run_mock(service)
    assert output["status"] == "incomplete"
    assert output["reason"] == "tool_failure"
    assert output["script"] is None
    assert any(event["tool"] == failed_tool and event["result"].get("code") == "injected_tool_failure" for event in output["trace"])


def test_unsupported_percentage_and_title_rejected(inputs):
    request, materials, rules = inputs
    service = ScriptTools(request, materials, rules)
    find(service)
    script = candidate(materials[0])
    script["scenes"][0]["narration"] += "效率提升80%"
    script["title"] = "效率提升80%"
    codes = {v["code"] for v in call(service, "check_rules", script)["violations"]}
    assert {"unsupported_claim", "forbidden_term", "unverified_title"} <= codes


def test_fabricated_sources_facts_and_visual_rejected(inputs):
    request, materials, rules = inputs
    service = ScriptTools(request, materials, rules)
    find(service)
    script = candidate(materials[0])
    script["scenes"][0]["source_id"] = "P99"
    assert call(service, "check_rules", script)["violations"][0]["code"] == "unknown_source"
    script["scenes"][0]["source_id"] = "P01"
    script["scenes"][0]["fact_id"] = "F99"
    assert call(service, "check_rules", script)["violations"][0]["code"] == "unknown_source"
    script["scenes"][0]["fact_id"] = "F01"
    script["scenes"][0]["visual"] = "展示销量第一。"
    assert "unsupported_visual" in {v["code"] for v in call(service, "check_rules", script)["violations"]}


def test_not_retrieved_source_cannot_pass(inputs):
    request, materials, rules = inputs
    service = ScriptTools(request, materials, rules)
    assert call(service, "check_rules", candidate(materials[0]))["passed"] is False


def test_changed_material_invalidates_old_words(inputs):
    request, materials, rules = inputs
    script = candidate(materials[0])
    changed = deepcopy(materials)
    changed[0].facts[0].text = "支持导入字幕。"
    service = ScriptTools(request, changed, rules)
    assert find(service)["materials"][0]["facts"][0]["text"] == "支持导入字幕。"
    assert not call(service, "check_rules", script)["passed"]
    script["scenes"][0]["narration"] = script["scenes"][0]["subtitle"] = "支持导入字幕。"
    assert call(service, "check_rules", script)["passed"]


def test_duration_is_checked_against_new_target(inputs):
    request, materials, rules = inputs
    request = request.model_copy(update={"target_seconds": 5})
    script = candidate(materials[0])
    service = ScriptTools(request, materials, rules)
    assert call(service, "estimate_duration", script)["within_target"]
    tight = ScriptTools(request.model_copy(update={"target_seconds": 3}), materials, rules)
    result = call(tight, "estimate_duration", script)
    assert not result["within_target"] and result["over_by_seconds"] == 2


def test_speaking_speed_minimum_cannot_be_faked(inputs):
    request, materials, rules = inputs
    service = ScriptTools(request, materials, rules)
    script = candidate(materials[0])
    script["scenes"][0]["duration_seconds"] = 0.1
    result = call(service, "estimate_duration", script)
    assert not result["within_target"] and result["scenes"][0]["minimum_seconds"] > 0.1


def test_fractional_overage_is_not_rounded_away(inputs):
    request, materials, rules = inputs
    service = ScriptTools(request.model_copy(update={"target_seconds": 3}), materials, rules)
    script = candidate(materials[0])
    script["scenes"][0]["duration_seconds"] = 3.004
    result = call(service, "estimate_duration", script)
    assert not result["within_target"]
    assert result["total_seconds"] == 3.004 and result["over_by_seconds"] == 0.004


def test_version_must_pass_both_checks_before_finalize(inputs):
    request, materials, rules = inputs
    request = request.model_copy(update={"target_seconds": 5})
    service = ScriptTools(request, materials, rules)
    find(service)
    script = candidate(materials[0])
    assert call(service, "estimate_duration", script)["within_target"]
    assert not call(service, "finalize_script", script)["ok"]
    assert call(service, "check_rules", script)["passed"]
    changed = deepcopy(script)
    changed["scenes"][0]["duration_seconds"] = 4
    assert not call(service, "finalize_script", changed)["ok"]
    assert call(service, "estimate_duration", changed)["within_target"]
    assert not call(service, "finalize_script", changed)["ok"]
    assert call(service, "check_rules", changed)["passed"]
    assert call(service, "finalize_script", changed)["ok"]
    assert service.final["scenes"] == changed["scenes"]


def test_limits_are_honest_and_calls_beyond_limit_do_not_execute(inputs):
    request, materials, rules = inputs
    service = ScriptTools(request, materials, rules, max_tool_calls=1)
    find(service)
    failed = find(service)
    assert failed["code"] == "max_tool_calls"
    assert service.calls == 1
    output = service.output({"status": "completed"}, "mock")
    assert output["status"] == "incomplete" and output["reason"] == "max_tool_calls"
    assert output["trace"][-1]["result"]["code"] == "max_tool_calls"


def test_model_failure_even_after_finalization_is_incomplete(inputs):
    request, materials, rules = inputs
    service = ScriptTools(request, materials, rules)
    run_mock(service)
    output = service.output({"status": "incomplete", "reason": "max_turns"}, "glm")
    assert output["status"] == "incomplete" and output["reason"] == "max_turns"


def test_initial_script_with_unknown_field_is_rejected(inputs):
    request, materials, rules = inputs
    service = ScriptTools(request, materials, rules)
    script = candidate(materials[0])
    script["published"] = True
    assert call(service, "estimate_duration", script)["code"] == "invalid_script"


@pytest.mark.parametrize("topic,missing_aspect", [
    ("字幕工具价格介绍", "price"),
    ("字幕工具收费说明", "paid"),
    ("字幕工具兼容安卓", "compatibility"),
    ("字幕工具技术规格", "specification"),
    ("字幕工具隐私说明", "privacy"),
    ("视频转文字", "transcription"),
])
def test_uncovered_topic_aspect_cannot_be_completed(inputs, topic, missing_aspect):
    request, materials, rules = inputs
    service = ScriptTools(request.model_copy(update={"topic": topic}), materials, rules)
    found = find(service, "字幕")
    assert found["count"] == 1
    assert any(item["aspect"] == missing_aspect for item in found["missing_information"])
    script = candidate(materials[0])
    result = call(service, "check_rules", script)
    assert not result["passed"]
    assert any(item["aspect"] == missing_aspect for item in result["missing_information"])
    assert not call(service, "finalize_script", script)["ok"]
    output = service.output({"status": "completed"}, "mock")
    assert output["status"] == "incomplete"
    assert output["reason"] == "missing_topic_evidence"


@pytest.mark.parametrize("topic", ["视频配文字", "给影像添加文本", "影片加上文字"])
def test_compositional_topic_rewrite_can_find_the_related_material(inputs, topic):
    request, materials, rules = inputs
    service = ScriptTools(request.model_copy(update={"topic": topic}), materials, rules)
    assert find(service, topic)["count"] == 0
    found = find(service, "字幕")
    assert [item["id"] for item in found["materials"]] == ["P01"]
    assert not found["missing_information"]
    assert find(service, "剪辑")["count"] == 0


def test_unknown_topic_modifier_is_reported_instead_of_ignored(inputs):
    request, materials, rules = inputs
    output = run_mock(ScriptTools(request.model_copy(update={"topic": "字幕工具服务地点介绍"}), materials, rules))
    assert output["status"] == "incomplete"
    assert output["reason"] == "missing_topic_evidence"
    assert any("服务地点" in item["aspect"] for item in output["missing_information"])


def test_available_aspect_must_appear_in_selected_script(inputs):
    request, materials, rules = inputs
    service = ScriptTools(request.model_copy(update={"topic": "字幕字体样式介绍"}), materials, rules)
    assert find(service, "字幕")["missing_information"] == []
    script = candidate(materials[0])  # F01 only covers basic feature, not style.
    result = call(service, "check_rules", script)
    assert not result["passed"]
    assert {item["aspect"] for item in result["missing_information"]} == {"style"}
    fact = materials[0].facts[1]
    script["scenes"][0].update({"fact_id": fact.id, "narration": fact.text, "subtitle": fact.text, "visual": fact.visual})
    assert call(service, "check_rules", script)["passed"]


def test_topics_from_multiple_concepts_must_all_be_covered(inputs):
    request, materials, rules = inputs
    service = ScriptTools(request.model_copy(update={"topic": "字幕和视频剪辑介绍"}), materials, rules)
    assert find(service, "字幕和剪辑")["count"] == 2
    result = call(service, "check_rules", candidate(materials[0]))
    assert not result["passed"]
    assert {item["concept"] for item in result["missing_information"]} == {"editing"}


@pytest.mark.parametrize("which", ["material", "fact"])
def test_duplicate_ids_are_rejected(inputs, which):
    request, materials, rules = inputs
    changed = deepcopy(materials)
    if which == "material":
        changed.append(deepcopy(changed[0]))
    else:
        changed[0].facts.append(deepcopy(changed[0].facts[0]))
    with pytest.raises(ValueError, match="ID 必须唯一"):
        ScriptTools(request, changed, rules)


@pytest.mark.parametrize("aspect", ["price", "compatibility", "specification", "transcription"])
def test_facet_metadata_without_corresponding_source_evidence_is_rejected(inputs, aspect):
    request, materials, rules = inputs
    changed = deepcopy(materials)
    changed[0].facts[0].aspects = [aspect]
    with pytest.raises(ValueError, match="标注缺少原文证据"):
        ScriptTools(request, changed, rules)


def test_duration_range_matches_target_and_is_configurable(inputs):
    request, materials, rules = inputs
    worker = ScriptTools(request, materials, rules)
    script = candidate(materials[0])
    script["scenes"][0]["duration_seconds"] = 3
    result = call(worker, "estimate_duration", script)
    assert not result["within_target"] and result["short_by_seconds"] == 21
    script["scenes"][0]["duration_seconds"] = 28
    assert call(worker, "estimate_duration", script)["within_target"]
    script["scenes"][0]["duration_seconds"] = 15
    flexible = ScriptTools(request.model_copy(update={"duration_tolerance": 0.5}), materials, rules)
    assert call(flexible, "estimate_duration", script)["within_target"]


def test_failed_later_submission_does_not_reuse_successful_old_script(inputs):
    request, materials, rules = inputs
    worker = ScriptTools(request.model_copy(update={"target_seconds": 5}), materials, rules)
    find(worker)
    good = candidate(materials[0])
    assert call(worker, "estimate_duration", good)["within_target"]
    assert call(worker, "check_rules", good)["passed"]
    assert call(worker, "finalize_script", good)["ok"]
    bad = deepcopy(good)
    bad["scenes"][0]["narration"] = "虚构的内容"
    assert not call(worker, "check_rules", bad)["passed"]
    assert not call(worker, "finalize_script", bad)["ok"]
    output = worker.output({"status": "completed"}, "mock")
    assert output["status"] == "incomplete" and output["script"] is None
    assert output["previous_submissions"][0]["scenes"] == good["scenes"]
    assert output["trace"][-1]["result"]["ok"] is False


def test_successful_recheck_of_identical_final_script_keeps_submission(inputs):
    request, materials, rules = inputs
    worker = ScriptTools(request.model_copy(update={"target_seconds": 5}), materials, rules)
    find(worker)
    script = candidate(materials[0])
    assert call(worker, "estimate_duration", script)["within_target"]
    assert call(worker, "check_rules", script)["passed"]
    assert call(worker, "finalize_script", script)["ok"]
    assert call(worker, "check_rules", script)["passed"]
    output = worker.output({"status": "completed"}, "mock")
    assert output["status"] == "completed" and output["script"]["scenes"] == script["scenes"]
    assert not output["previous_submissions"]


def test_free_topic_cannot_be_satisfied_by_paid_price(inputs):
    request, materials, rules = inputs
    changed = deepcopy(materials)
    changed[0].facts[0].text = "该字幕工具每月10元。"
    changed[0].facts[0].aspects = ["price", "paid"]
    worker = ScriptTools(request.model_copy(update={"topic": "免费字幕"}), changed, rules)
    found = find(worker, "字幕")
    assert {item["aspect"] for item in found["missing_information"]} == {"free"}
    assert not call(worker, "check_rules", candidate(changed[0]))["passed"]
    changed[0].facts[0].text = "字幕工具不免费。"
    changed[0].facts[0].aspects = ["free"]
    with pytest.raises(ValueError, match="标注缺少原文证据"):
        ScriptTools(request, changed, rules)


def test_conditional_free_trial_does_not_prove_unconditional_free(inputs):
    request, materials, rules = inputs
    changed = deepcopy(materials)
    changed[0].facts[0].text = "字幕工具免费试用7天，之后每月10元。"
    changed[0].facts[0].aspects = ["free", "paid"]
    with pytest.raises(ValueError, match="标注缺少原文证据"):
        ScriptTools(request, changed, rules)
    # Compound facts must be split before they can support a specific claim.
    changed[0].facts[0].aspects = ["free_trial", "paid"]
    with pytest.raises(ValueError, match="标注缺少原文证据"):
        ScriptTools(request, changed, rules)
    changed[0].facts[0].text = "字幕工具免费试用7天。"
    changed[0].facts[0].aspects = ["free_trial"]
    worker = ScriptTools(request.model_copy(update={"topic": "免费字幕"}), changed, rules)
    assert {item["aspect"] for item in find(worker, "字幕")["missing_information"]} == {"free"}
    trial = ScriptTools(request.model_copy(update={"topic": "免费试用字幕"}), changed, rules)
    assert find(trial, "字幕")["required_aspects"] == ["free_trial"]
    assert call(trial, "check_rules", candidate(changed[0]))["passed"]


@pytest.mark.parametrize("aspect,negative,positive", [
    ("free_trial", "字幕工具不提供免费试用。", "字幕工具支持免费试用7天。"),
    ("paid", "字幕工具不需要付费。", "字幕工具每月10元。"),
    ("export", "不支持导出字幕文件。", "支持导出字幕文件。"),
    ("style", "无法更改字体样式。", "支持调整字幕字体样式。"),
    ("transcription", "不能识别语音。", "支持识别视频语音。"),
    ("privacy", "字幕数据不加密。", "字幕数据加密保护。"),
    ("feature", "过去支持为视频添加字幕。", "支持为视频添加字幕。"),
    ("export", "字幕工具曾支持导出字幕文件。", "字幕工具支持导出字幕文件。"),
])
def test_negative_or_past_fact_cannot_be_labeled_as_a_current_positive_aspect(inputs, aspect, negative, positive):
    request, materials, rules = inputs
    changed = deepcopy(materials)
    changed[0].facts[0].text = negative
    changed[0].facts[0].aspects = [aspect]
    with pytest.raises(ValueError, match="标注缺少原文证据"):
        ScriptTools(request, changed, rules)
    changed[0].facts[0].text = positive
    ScriptTools(request, changed, rules)


def test_android_topic_cannot_be_satisfied_by_ios_only_source(inputs):
    request, materials, rules = inputs
    changed = deepcopy(materials)
    changed[0].facts[0].text = "字幕工具支持 iOS 系统。"
    changed[0].facts[0].aspects = ["compatibility", "ios"]
    worker = ScriptTools(request.model_copy(update={"topic": "安卓字幕"}), changed, rules)
    found = find(worker, "字幕")
    assert {item["aspect"] for item in found["missing_information"]} == {"android"}
    assert not call(worker, "check_rules", candidate(changed[0]))["passed"]
    changed[0].facts[0].aspects = ["android"]
    with pytest.raises(ValueError, match="标注缺少原文证据"):
        ScriptTools(request, changed, rules)


@pytest.mark.parametrize("text", ["字幕工具支持iOS但不支持安卓。", "字幕工具支持iOS不兼容安卓。"])
def test_multi_platform_negation_cannot_be_annotated_as_android_support(inputs, text):
    request, materials, rules = inputs
    changed = deepcopy(materials)
    changed[0].facts[0].text = text
    changed[0].facts[0].aspects = ["android"]
    with pytest.raises(ValueError, match="标注缺少原文证据"):
        ScriptTools(request, changed, rules)


def test_negated_topic_uses_paid_facet_without_treating_it_as_free(inputs):
    request, materials, rules = inputs
    changed = deepcopy(materials)
    changed[0].facts[0].text = "字幕工具不免费。"
    changed[0].facts[0].aspects = ["paid"]
    worker = ScriptTools(request.model_copy(update={"topic": "不免费字幕"}), changed, rules)
    found = find(worker, "字幕")
    assert found["required_aspects"] == ["paid"]
    assert found["missing_information"] == []
    assert call(worker, "check_rules", candidate(changed[0]))["passed"]


def test_trace_marks_same_model_message_without_implying_result_observation(inputs):
    request, materials, rules = inputs
    worker = ScriptTools(request, materials, rules)
    a = {"query": "字幕", "reason": "查询"}
    b = {"query": "字幕", "reason": "再查"}
    worker.call("search_materials", a)
    worker.call("search_materials", b)
    terminal = {"status": "completed", "sdk_events": [
        {"type": "tool_request", "tool": "mcp__video__search_materials", "arguments": a, "message_id": "msg-1", "tool_use_id": "call-1"},
        {"type": "tool_request", "tool": "mcp__video__search_materials", "arguments": b, "message_id": "msg-1", "tool_use_id": "call-2"},
    ]}
    output = worker.output(terminal, "glm")
    assert output["trace"][0]["next_action"] == "search_materials"
    assert output["trace"][0]["next_action_relation"] == "same_model_message"
    assert output["trace"][1]["tool_use_id"] == "call-2"
