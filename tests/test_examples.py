"""Replay saved real tool traces; this does not make another model call."""
import json
from collections import Counter
from pathlib import Path

import pytest

from src.business import ScriptTools
from src.models import Material, Request, Rules

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("filename,materials_path,fail_once", [
    ("overlong.v2.glm.output.json", "data/materials.json", None),
    ("wide-target.v2.glm.output.json", "data/materials.json", None),
    ("overlong.dev.glm.output.json", "data/materials.json", None),
    ("wide-target.dev.glm.output.json", "data/materials.json", None),
    ("normal.dev.glm.output.json", "data/materials.json", None),
    ("normal.v2.glm.output.json", "data/materials.json", None),
    ("changed-material.v2.glm.output.json", "examples/changed-materials.json", None),
    ("rewrite-compositional.v2.glm.output.json", "data/materials.json", None),
    ("failure.v2.glm.output.json", "data/materials.json", "search_materials"),
    ("unsupported-price.v2.glm.output.json", "data/materials.json", None),
    ("no-material.v2.glm.output.json", "data/materials.json", None),
])
def test_saved_glm_trace_replays_with_sdk_ids(filename, materials_path, fail_once):
    recorded = json.loads((ROOT / "examples" / filename).read_text())
    request = Request.model_validate(recorded["request"])
    materials = [Material.model_validate(item) for item in json.loads((ROOT / materials_path).read_text())]
    rules = Rules.model_validate_json((ROOT / "data/brand_rules.json").read_text())
    service = ScriptTools(request, materials, rules, fail_once=fail_once)
    for event in recorded["trace"]:
        assert service.call(event["tool"], event["arguments"]) == event["result"]
    replayed = service.output(recorded["terminal"], "glm")
    for field in ("status", "reason", "script", "previous_submissions", "missing_information", "trace"):
        assert replayed[field] == recorded[field], field
    requests = [event for event in recorded["terminal"]["sdk_events"] if event["type"] == "tool_request"]
    returns = [event for event in recorded["terminal"]["sdk_events"] if event["type"] == "tool_result"]
    assert Counter(event["tool_use_id"] for event in requests) == Counter(event["tool_use_id"] for event in returns)
    assert Counter(event["tool_use_id"] for event in recorded["trace"]) == Counter(event["tool_use_id"] for event in requests)


def test_final_glm_paths_change_after_observing_same_initial_script_at_different_targets():
    tight = json.loads((ROOT / "examples/overlong.v2.glm.output.json").read_text())
    wide = json.loads((ROOT / "examples/wide-target.v2.glm.output.json").read_text())
    assert tight["status"] == wide["status"] == "completed"
    assert tight["request"]["initial_script"] == wide["request"]["initial_script"]
    first_tight = next(event for event in tight["trace"] if event["tool"] == "estimate_duration")
    first_wide = next(event for event in wide["trace"] if event["tool"] == "estimate_duration")
    assert first_tight["result"]["total_seconds"] == first_wide["result"]["total_seconds"] == 24
    assert not first_tight["result"]["within_target"] and first_wide["result"]["within_target"]
    estimates = [event for event in tight["trace"] if event["tool"] == "estimate_duration"]
    assert len(estimates) > 1 and estimates[1]["result"]["within_target"]
    assert estimates[0]["model_message_id"] != estimates[1]["model_message_id"]
    sdk = tight["terminal"]["sdk_events"]
    first_result = next(i for i, event in enumerate(sdk) if event["type"] == "tool_result" and event["tool_use_id"] == estimates[0]["tool_use_id"])
    next_request = next(i for i, event in enumerate(sdk) if event["type"] == "tool_request" and event["tool_use_id"] == estimates[1]["tool_use_id"])
    assert first_result < next_request
    assert first_wide["next_action"] == "check_rules"


def test_final_glm_rewrites_after_observing_an_empty_search():
    recorded = json.loads((ROOT / "examples/rewrite-compositional.v2.glm.output.json").read_text())
    searches = [event for event in recorded["trace"] if event["tool"] == "search_materials"]
    assert recorded["status"] == "completed"
    assert searches[0]["arguments"]["query"] == "视频配文字" and searches[0]["result"]["count"] == 0
    assert searches[1]["arguments"]["query"] == "视频字幕" and searches[1]["result"]["count"] == 1
    assert searches[0]["model_message_id"] != searches[1]["model_message_id"]
    sdk = recorded["terminal"]["sdk_events"]
    result = next(i for i, event in enumerate(sdk) if event["type"] == "tool_result" and event["tool_use_id"] == searches[0]["tool_use_id"])
    rewrite = next(i for i, event in enumerate(sdk) if event["type"] == "tool_request" and event["tool_use_id"] == searches[1]["tool_use_id"])
    assert result < rewrite


def test_final_glm_retries_after_observing_a_tool_failure():
    recorded = json.loads((ROOT / "examples/failure.v2.glm.output.json").read_text())
    searches = [event for event in recorded["trace"] if event["tool"] == "search_materials"]
    assert recorded["status"] == "completed"
    assert not searches[0]["result"]["ok"] and searches[0]["result"]["code"] == "injected_tool_failure"
    assert searches[1]["result"]["ok"] and searches[1]["result"]["count"] > 0
    assert searches[0]["model_message_id"] != searches[1]["model_message_id"]
    sdk = recorded["terminal"]["sdk_events"]
    result = next(i for i, event in enumerate(sdk) if event["type"] == "tool_result" and event["tool_use_id"] == searches[0]["tool_use_id"])
    retry = next(i for i, event in enumerate(sdk) if event["type"] == "tool_request" and event["tool_use_id"] == searches[1]["tool_use_id"])
    assert sdk[result]["is_error"] is True and result < retry


def test_final_glm_returns_price_evidence_gap():
    recorded = json.loads((ROOT / "examples/unsupported-price.v2.glm.output.json").read_text())
    assert recorded["status"] == "incomplete" and recorded["reason"] == "missing_topic_evidence"
    assert recorded["script"] is None
    assert {item["aspect"] for item in recorded["missing_information"]} == {"price"}
    assert any(event["tool"] == "search_materials" and event["result"]["missing_information"] for event in recorded["trace"])


def test_final_glm_stops_without_material_after_multiple_searches():
    recorded = json.loads((ROOT / "examples/no-material.v2.glm.output.json").read_text())
    searches = [event for event in recorded["trace"] if event["tool"] == "search_materials"]
    assert recorded["status"] == "incomplete" and recorded["reason"] == "no_material"
    assert recorded["script"] is None and len(searches) >= 2
    assert all(event["result"]["count"] == 0 for event in searches)
