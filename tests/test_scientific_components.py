import ast

from scripts.scientific_components import (
    RECIPES,
    SCENARIOS,
    catalog_summary,
    render_component_query,
)
from scripts.query_schema import validate_query


def test_catalog_has_thirty_reviewable_recipes_and_multiple_domains():
    summary = catalog_summary()
    assert summary["recipe_count"] >= 30
    assert len(summary["domains"]) >= 7
    assert summary["scenario_count"] >= 10
    assert summary["method_mode_count"] >= 40


def test_every_recipe_renders_without_unresolved_markers():
    for index, recipe in enumerate(RECIPES):
        query, metadata = render_component_query(f"component-{index:06d}", index, seed=91)
        validate_query(query)
        assert recipe.key == metadata["component"]
        assert "{{" not in repr(query)
        assert all(len(step["test_cases"]) >= 2 for step in query["sub_steps"])


def test_seed_changes_parameters_but_not_query_contract():
    first, first_meta = render_component_query("component-000001", 1, seed=1)
    second, second_meta = render_component_query("component-000001", 1, seed=2)
    validate_query(first)
    validate_query(second)
    assert first != second
    assert first_meta["component"] == second_meta["component"]


def test_scenarios_are_unique_and_have_test_guidance():
    keys = [item.key for item in SCENARIOS]
    assert len(keys) == len(set(keys))
    assert all(item.test_hint.strip() for item in SCENARIOS)


def test_rendered_headers_and_public_tests_are_python_syntax():
    for index in range(len(RECIPES)):
        query, _ = render_component_query(f"syntax-{index:06d}", index, seed=17)
        for step in query["sub_steps"]:
            ast.parse(step["function_header"] + "\n    pass")
            for test_case in step["test_cases"]:
                ast.parse(test_case)
