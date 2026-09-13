import json
from pathlib import Path

from scripts.query_schema import validate_query
from scripts.synthesize_queries import QuerySynthesizer, SynthesisConfig, json_hash


def test_small_batch_has_exact_count_and_separate_metadata(tmp_path: Path):
    result = QuerySynthesizer(
        SynthesisConfig(target_queries=9, output_dir=tmp_path, seed=12)
    ).run()
    assert result["status"] == "finished"
    queries = [json.loads(line) for line in (tmp_path / "queries.jsonl").read_text().splitlines()]
    metadata = [json.loads(line) for line in (tmp_path / "metadata.jsonl").read_text().splitlines()]
    assert len(queries) == len(metadata) == 9
    assert result["subproblem_count"] == 27
    assert result["model_calls"] == 0
    assert result["kimi_api_calls"] == 0
    for query, meta in zip(queries, metadata):
        validate_query(query)
        assert query["problem_id"] == meta["query_id"]
        assert meta["query_sha256"] == json_hash(query)


def test_batch_is_deterministic_for_same_seed(tmp_path: Path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    QuerySynthesizer(SynthesisConfig(4, first_dir, seed=8)).run()
    QuerySynthesizer(SynthesisConfig(4, second_dir, seed=8)).run()
    assert (first_dir / "queries.jsonl").read_bytes() == (second_dir / "queries.jsonl").read_bytes()


def test_resume_does_not_duplicate_records(tmp_path: Path):
    config = SynthesisConfig(target_queries=3, output_dir=tmp_path, seed=2)
    QuerySynthesizer(config).run()
    result = QuerySynthesizer(config).run()
    lines = (tmp_path / "queries.jsonl").read_text().splitlines()
    assert result["status"] == "finished"
    assert len(lines) == 3
    assert len({json.loads(line)["problem_id"] for line in lines}) == 3


def test_max_attempts_reports_incomplete_without_infinite_loop(tmp_path: Path, monkeypatch):
    import scripts.synthesize_queries as module

    def bad_builder(*args, **kwargs):
        raise ValueError("synthetic builder failure")

    monkeypatch.setattr(module, "build_blueprint", bad_builder)
    result = QuerySynthesizer(
        SynthesisConfig(target_queries=2, output_dir=tmp_path, max_attempts=2)
    ).run()
    assert result["status"] == "incomplete"
    assert result["accepted_queries"] == 0
    assert result["rejected_queries"] == 2
    assert len((tmp_path / "errors.jsonl").read_text().splitlines()) == 2


def test_answer_artifacts_are_not_created(tmp_path: Path):
    QuerySynthesizer(SynthesisConfig(target_queries=1, output_dir=tmp_path)).run()
    names = {path.name for path in tmp_path.iterdir()}
    assert not {"answer.jsonl", "oracle.h5", "trace.jsonl"} & names
