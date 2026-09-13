import json
import re
import threading
import time
from pathlib import Path

import pytest

from scripts.generate_queries import (
    GeneratorConfig,
    ProviderError,
    QueryGenerator,
    extract_json_object,
    normalize_query,
)


def raw_query(name: str = "A scientific problem") -> dict:
    return {
        "problem_name": name,
        "problem_id": "model-id",
        "problem_description_main": "Compute a scientific quantity.",
        "problem_io": "Inputs and outputs are NumPy arrays.",
        "required_dependencies": "import numpy as np",
        "sub_steps": [
            {
                "step_number": "old.1",
                "step_description_prompt": "Implement the first calculation.",
                "function_header": "def calculate(x):",
                "test_cases": ["x = 1\nassert calculate(x) == target"],
                "return_line": "return value",
                "step_background": "Use the stated scientific convention.",
                "ignored_author_field": "must not be emitted",
            }
        ],
        "general_tests": ["assert True"],
        "problem_background_main": "",
        "ignored_top_level_field": "must not be emitted",
    }


def response_for(query: dict) -> dict:
    return {
        "id": "cmpl-test",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(query),
                    "reasoning_content": "select a scientific setting",
                },
            }
        ],
        "usage": {
            "prompt_tokens": 17,
            "completion_tokens": 31,
            "total_tokens": 48,
            "completion_tokens_details": {"reasoning_tokens": 9},
        },
    }


def test_extract_json_object_accepts_fence_and_trailing_text():
    value = extract_json_object("Here is the record:\n```json\n{\"a\": 1}\n```\nDone")
    assert value == {"a": 1}


def test_normalize_query_keeps_original_schema_and_assigns_id():
    value = normalize_query(raw_query(), "direct-000007")
    assert set(value) == {
        "problem_name",
        "problem_id",
        "problem_description_main",
        "problem_io",
        "required_dependencies",
        "sub_steps",
        "general_tests",
        "problem_background_main",
    }
    assert value["problem_id"] == "direct-000007"
    assert set(value["sub_steps"][0]) == {
        "step_number",
        "step_description_prompt",
        "function_header",
        "test_cases",
        "return_line",
        "step_background",
    }
    assert value["sub_steps"][0]["step_number"] == "direct-000007.1"


def test_normalize_query_rejects_missing_structural_field():
    value = raw_query()
    del value["required_dependencies"]
    with pytest.raises(ValueError, match="required_dependencies"):
        normalize_query(value, "direct-000001")


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def config(tmp_path: Path, **overrides) -> GeneratorConfig:
    values = {
        "target_queries": 1,
        "concurrency": 1,
        "output_dir": tmp_path,
        "model": "test-model",
        "temperature": 1.0,
        "max_tokens": 1024,
        "timeout_seconds": 2.0,
        "max_retries": 0,
        "retry_backoff_seconds": 0,
        "max_attempts": 10,
    }
    values.update(overrides)
    return GeneratorConfig(**values)


def test_generator_writes_query_response_and_manifest(tmp_path: Path):
    client = FakeClient([response_for(raw_query())])
    result = QueryGenerator(config(tmp_path), client=client).run()

    assert result["status"] == "finished"
    assert result["accepted_queries"] == 1
    query = json.loads((tmp_path / "queries.jsonl").read_text().splitlines()[0])
    response = json.loads((tmp_path / "responses.jsonl").read_text().splitlines()[0])
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert query["problem_id"] == "direct-000001"
    assert response["query_id"] == "direct-000001"
    assert response["usage"]["total_tokens"] == 48
    assert response["usage"]["completion_tokens_details"]["reasoning_tokens"] == 9
    assert response["finish_reason"] == "stop"
    assert response["reasoning_content"] == "select a scientific setting"
    assert manifest["target_queries"] == 1


def test_generator_retries_provider_error_and_preserves_attempts(tmp_path: Path):
    client = FakeClient(
        [
            ProviderError("temporary", status_code=503, retryable=True),
            response_for(raw_query("after retry")),
        ]
    )
    result = QueryGenerator(
        config(tmp_path, max_retries=1), client=client
    ).run()

    assert result["accepted_queries"] == 1
    row = json.loads((tmp_path / "responses.jsonl").read_text().splitlines()[0])
    assert row["attempt_count"] == 2
    assert row["attempts"][0]["error"] == "temporary"
    assert row["attempts"][1]["finish_reason"] == "stop"


def test_generator_records_rejected_response_and_stops_at_attempt_cap(tmp_path: Path):
    client = FakeClient([{"choices": [{"message": {"content": "not json"}}]}])
    result = QueryGenerator(
        config(tmp_path, target_queries=1, max_attempts=1), client=client
    ).run()

    assert result["status"] == "incomplete"
    assert result["accepted_queries"] == 0
    rejected = json.loads((tmp_path / "rejected.jsonl").read_text().splitlines()[0])
    assert rejected["query_id"] == "direct-000001"
    assert "JSON" in rejected["error"]


def test_generator_resume_skips_existing_query_ids(tmp_path: Path):
    first = normalize_query(raw_query("existing"), "direct-000001")
    (tmp_path / "queries.jsonl").write_text(json.dumps(first) + "\n")
    client = FakeClient([response_for(raw_query("new"))])
    result = QueryGenerator(
        config(tmp_path, target_queries=2), client=client
    ).run()

    assert result["accepted_queries"] == 2
    ids = [json.loads(line)["problem_id"] for line in (tmp_path / "queries.jsonl").read_text().splitlines()]
    assert ids == ["direct-000001", "direct-000002"]
    assert len(client.calls) == 1


def test_generator_respects_concurrency(tmp_path: Path):
    lock = threading.Lock()
    active = 0
    maximum = 0

    class ConcurrentClient:
        def complete(self, prompt, **kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.01)
            match = re.search(r"direct-\d{6}", prompt)
            answer = raw_query(match.group(0) if match else "parallel")
            with lock:
                active -= 1
            return response_for(answer)

    result = QueryGenerator(
        config(tmp_path, target_queries=5, concurrency=2), client=ConcurrentClient()
    ).run()
    assert result["accepted_queries"] == 5
    assert maximum <= 2
