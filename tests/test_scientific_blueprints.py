import json

from scripts.query_schema import STEP_FIELDS, TOP_LEVEL_FIELDS, validate_query
from scripts.scientific_blueprints import BLUEPRINTS, build_blueprint


def test_catalog_has_multiple_scientific_families():
    assert len(BLUEPRINTS) >= 5
    assert len({blueprint.name for blueprint in BLUEPRINTS}) == len(BLUEPRINTS)


def test_each_blueprint_emits_valid_multistep_query():
    for index in range(len(BLUEPRINTS)):
        query, name = build_blueprint(f"synth-{index + 1:06d}", index, seed=123)
        validate_query(query)
        assert name == BLUEPRINTS[index].name
        assert len(query["sub_steps"]) == 3
        assert set(query) == set(TOP_LEVEL_FIELDS)
        assert all(set(step) == set(STEP_FIELDS) for step in query["sub_steps"])
        assert all("ground_truth_code" not in json.dumps(step) for step in query["sub_steps"])


def test_same_seed_is_reproducible_and_indices_vary():
    first, first_name = build_blueprint("synth-000042", 42, seed=7)
    second, second_name = build_blueprint("synth-000042", 42, seed=7)
    other, other_name = build_blueprint("synth-000043", 43, seed=7)
    assert first == second
    assert first_name == second_name
    assert first != other
    assert other_name != first_name or other["problem_name"] != first["problem_name"]
