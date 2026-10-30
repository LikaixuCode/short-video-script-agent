import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args):
    proc = subprocess.run([sys.executable, "-m", "src.main", *args], cwd=ROOT, capture_output=True, text=True)
    return proc, json.loads(proc.stdout)


def test_missing_input_does_not_start_model():
    proc, result = run("--input", "examples/missing.input.json", "--mode", "glm")
    assert proc.returncode == 1 and result["missing_fields"] == ["target_seconds"]
    assert result["terminal"]["status"] == "not_started" and result["trace"] == []


def test_cli_mock_runs():
    proc, result = run("--input", "examples/normal.input.json", "--mode", "mock")
    assert proc.returncode == 0 and result["status"] == "completed"


def test_cli_mock_tool_failure():
    proc, result = run("--input", "examples/failure.input.json", "--mode", "mock", "--fail-once", "search_materials")
    assert proc.returncode == 1 and result["reason"] == "tool_failure"


def test_bad_limit_is_reported():
    proc, result = run("--input", "examples/normal.input.json", "--mode", "mock", "--max-tool-calls", "0")
    assert proc.returncode == 2 and result["status"] == "error"
