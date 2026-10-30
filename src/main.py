import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .agent import run_glm, run_mock
from .business import ScriptTools
from .models import Material, Request, Rules

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="短视频脚本 Agent")
    parser.add_argument("--input", type=Path, required=True, help="输入 JSON 文件")
    parser.add_argument("--mode", choices=("glm", "mock"), default="glm")
    parser.add_argument("--materials", type=Path, default=ROOT / "data/materials.json")
    parser.add_argument("--rules", type=Path, default=ROOT / "data/brand_rules.json")
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--max-tool-calls", type=int, default=16)
    parser.add_argument("--fail-once", choices=("search_materials", "estimate_duration", "check_rules"))
    args = parser.parse_args()
    try:
        if args.max_turns < 1 or args.max_tool_calls < 1:
            raise ValueError("执行上限必须大于零")
        raw = json.loads(args.input.read_text())
        if not isinstance(raw, dict):
            raise ValueError("输入必须是 JSON 对象")
        missing = [field for field in ("topic", "audience", "platform", "target_seconds", "style") if raw.get(field) is None or (isinstance(raw.get(field), str) and not raw[field].strip())]
        if missing:
            result = {"status": "incomplete", "reason": "missing_input", "missing_fields": missing, "mode": args.mode, "script": None, "trace": [], "terminal": {"status": "not_started", "reason": "missing_input"}}
        else:
            request = Request.model_validate(raw)
            materials = [Material.model_validate(item) for item in json.loads(args.materials.read_text())]
            rules = Rules.model_validate_json(args.rules.read_text())
            service = ScriptTools(request, materials, rules, args.max_tool_calls, args.fail_once)
            result = run_mock(service) if args.mode == "mock" else asyncio.run(run_glm(service, args.max_turns))
        exit_code = 0 if result["status"] == "completed" else 1
    except (OSError, ValueError, ValidationError) as exc:
        result = {"status": "error", "error_type": type(exc).__name__, "message": str(exc)}
        exit_code = 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
