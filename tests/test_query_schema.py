import pytest

from scripts.query_schema import QuerySchemaError, validate_query


def record() -> dict:
    problem_id = "synth-000001"
    return {
        "problem_name": "Finite-volume transport",
        "problem_id": problem_id,
        "problem_description_main": "Implement a transport calculation.",
        "problem_io": "Return a NumPy array.",
        "required_dependencies": "import numpy as np",
        "sub_steps": [
            {
                "step_number": f"{problem_id}.1",
                "step_description_prompt": "Build the flux operator.",
                "function_header": "def flux(q, dx):",
                "test_cases": ["assert np.allclose(flux(q, dx), target)"],
                "return_line": "return result",
                "step_background": "Use conservative periodic fluxes.",
            }
        ],
        "general_tests": ["assert True"],
        "problem_background_main": "A periodic domain is used.",
    }


def test_valid_record_passes():
    validate_query(record())


def test_unexpected_answer_field_is_rejected():
    value = record()
    value["ground_truth_code"] = "return 1"
    with pytest.raises(QuerySchemaError, match="unexpected"):
        validate_query(value)


def test_step_number_must_be_contiguous_and_bound_to_id():
    value = record()
    value["sub_steps"][0]["step_number"] = "other-000001.1"
    with pytest.raises(QuerySchemaError, match="step_number"):
        validate_query(value)


def test_scientific_content_is_not_semantically_judged():
    value = record()
    value["problem_description_main"] = "A deliberately unusual but valid question."
    validate_query(value)
