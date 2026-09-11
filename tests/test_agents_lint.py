from pathlib import Path

from scripts.lint_agents import lint


def test_agents_standard_is_imperative_and_complete():
    errors = lint(Path(__file__).parents[1] / "AGENTS.md")
    assert errors == []


def test_linter_rejects_modal_requirement(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text("## 1. Keep the two model roles separate\nThe agent should run it.\n", encoding="utf-8")
    errors = lint(path)
    assert any("modal wording" in error for error in errors)
