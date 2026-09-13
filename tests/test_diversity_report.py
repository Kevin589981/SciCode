from pathlib import Path

from scripts.diversity_report import audit_batch
from scripts.synthesize_queries import QuerySynthesizer, SynthesisConfig


def test_report_counts_component_metadata(tmp_path: Path):
    QuerySynthesizer(SynthesisConfig(61, tmp_path / "batch", seed=4)).run()
    report = audit_batch(
        tmp_path / "batch" / "queries.jsonl",
        tmp_path / "batch" / "metadata.jsonl",
    )
    assert report["status"] == "ok"
    assert report["query_count"] == 61
    assert report["unique_complete_records"] == 61
    assert report["unique_normalized_signatures"] > 20
    assert sum(report["domain_distribution"].values()) == 61


def test_report_detects_duplicate_records(tmp_path: Path):
    query_path = tmp_path / "queries.jsonl"
    metadata_path = tmp_path / "metadata.jsonl"
    QuerySynthesizer(SynthesisConfig(2, tmp_path / "batch", seed=9)).run()
    query_path.write_text(
        (tmp_path / "batch" / "queries.jsonl").read_text(encoding="utf-8")
        + (tmp_path / "batch" / "queries.jsonl").read_text(encoding="utf-8").splitlines()[0]
        + "\n",
        encoding="utf-8",
    )
    metadata_path.write_text(
        (tmp_path / "batch" / "metadata.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    report = audit_batch(query_path, metadata_path)
    assert report["status"] == "failed"
    assert report["duplicate_complete_records"] == 1
