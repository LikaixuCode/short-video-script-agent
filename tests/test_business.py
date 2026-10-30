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
