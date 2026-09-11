import json
from pathlib import Path

import h5py
import pytest

from scicode.pipeline.candidate import CandidateValidationError, load_candidate


def make_candidate(tmp_path: Path) -> Path:
    root = tmp_path / "candidate"
    (root / "public" / "solver_payload").mkdir(parents=True)
    (root / "public" / "checks").mkdir()
    (root / "public" / "prompt_snapshot").mkdir()
    (root / "source_notes").mkdir()
    (root / "oracle").mkdir()
    (root / "reference").mkdir()
    row = {
        "problem_name": "spectral transport",
        "problem_id": "candidate_001",
        "problem_description_main": "Compute a scientific spectrum.",
        "problem_io": "Inputs and outputs use SI units.",
        "required_dependencies": "import numpy as np",
        "sub_steps": [
            {
                "step_number": "candidate_001.1",
                "step_description_prompt": "Implement the spectrum function.",
                "function_header": "def spectrum(x):\n    \"\"\"Return a spectrum.\"\"\"",
                "test_cases": ["assert np.allclose(spectrum(x), target)"],
                "return_line": "    return y",
                "step_background": "Use the stated physical units.",
            }
        ],
        "general_tests": [],
        "problem_background_main": "",
    }
    (root / "public" / "problem.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )
    (root / "public" / "solver_payload" / "problem_context.json").write_text(
        json.dumps({"problem_id": row["problem_id"], "description": "public context"}),
        encoding="utf-8",
    )
    (root / "public" / "checks" / "check.py").write_text(
        "assert True\n", encoding="utf-8"
    )
    (root / "reference" / "reference.py").write_text(
        "def spectrum(x):\n    return x\n", encoding="utf-8"
    )
    (root / "oracle" / "schema.py").write_text(
        "print('oracle')\n", encoding="utf-8"
    )
    (root / "public" / "prompt_snapshot" / "candidate_001.1.txt").write_text(
        "Implement the spectrum function.\n", encoding="utf-8"
    )
    (root / "public" / "README.md").write_text(
        "Only the public solver payload is mounted.\n", encoding="utf-8"
    )
    (root / "source_notes" / "provenance.json").write_text(
        json.dumps(
            {
                "source_url": "https://example.org/science",
                "source_commit": "a" * 40,
                "license": "CC-BY-4.0",
                "paper_url": "https://doi.org/10.0000/example",
                "source_files": ["src/spectrum.py"],
                "source_fingerprint": "sha256:" + "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    with h5py.File(root / "oracle" / "targets.h5", "w") as handle:
        group = handle.create_group("candidate_001.1")
        group.create_group("test1").create_dataset("value", data=1.0)
    return root


def test_load_candidate_returns_stable_contract(tmp_path):
    manifest = load_candidate(make_candidate(tmp_path))
    assert manifest.mode == "strict"
    assert manifest.problem_count == 1
    assert manifest.subproblem_count == 1
    assert len(manifest.visible_contract_sha256) == 64
    assert manifest.oracle_sha256


def test_candidate_rejects_missing_step_field(tmp_path):
    root = make_candidate(tmp_path)
    row = json.loads((root / "public" / "problem.jsonl").read_text())
    del row["sub_steps"][0]["return_line"]
    (root / "public" / "problem.jsonl").write_text(json.dumps(row) + "\n")
    with pytest.raises(CandidateValidationError, match="return_line"):
        load_candidate(root)


def test_candidate_rejects_private_payload_marker(tmp_path):
    root = make_candidate(tmp_path)
    (root / "public" / "solver_payload" / "notes.txt").write_text(
        "targets.h5\n", encoding="utf-8"
    )
    with pytest.raises(CandidateValidationError, match="targets.h5"):
        load_candidate(root)


def test_candidate_rejects_official_marker(tmp_path):
    root = make_candidate(tmp_path)
    (root / "public" / "README.md").write_text(
        "SciCode1/SciCode\n", encoding="utf-8"
    )
    with pytest.raises(CandidateValidationError, match="official/private"):
        load_candidate(root)


def test_candidate_rejects_extra_solver_visible_fields(tmp_path):
    root = make_candidate(tmp_path)
    row = json.loads((root / "public" / "problem.jsonl").read_text())
    row["hidden_hint"] = "do not expose this"
    (root / "public" / "problem.jsonl").write_text(json.dumps(row) + "\n")
    with pytest.raises(CandidateValidationError, match="non-SciCode fields"):
        load_candidate(root)


def test_candidate_rejects_extra_step_fields(tmp_path):
    root = make_candidate(tmp_path)
    row = json.loads((root / "public" / "problem.jsonl").read_text())
    row["sub_steps"][0]["hidden_hint"] = "do not expose this"
    (root / "public" / "problem.jsonl").write_text(json.dumps(row) + "\n")
    with pytest.raises(CandidateValidationError, match="non-SciCode fields"):
        load_candidate(root)
