"""Author and judge prompt templates for reasoning-first SciCode tasks."""
from __future__ import annotations

import json

from .schema import ARCHETYPES

ARCHETYPE_GUIDANCE = {
    "derive_implement": """
Build a derive-and-implement task. The solver must connect explicit scientific
assumptions to a mathematical or numerical method, reason about at least two
regimes or constraints, and then implement the result. Do not turn the source
docstring into a longer paraphrase.

archetype_payload must contain:
- assumptions: at least two explicit assumptions;
- derivation_target: the relation or algorithm the solver must derive.
""",
    "diagnose_revise": """
Build a diagnose-and-revise task. Give scientifically meaningful observations
of a plausible but flawed method or result. The solver must distinguish at
least two candidate causes, use the observations to diagnose the issue, and
construct a revision. The symptom must not reveal the diagnosis directly.

archetype_payload must contain:
- observations: at least two diagnostic observations;
- candidate_causes: at least two plausible causes the solver must distinguish.
""",
    "compare_justify": """
Build a compare-and-justify task. Present at least two plausible scientific or
numerical approaches whose suitability changes with the stated regime. The
solver must compare them using explicit criteria, justify a choice, and produce
the requested implementation or analysis.

archetype_payload must contain:
- alternatives: at least two methods or models;
- decision_criteria: at least two scientific or numerical criteria.
""",
}


def author_prompt(candidate: dict, archetype: str, repo_meta: dict) -> str:
    """Render a strict task-authoring request grounded in scientific source."""
    if archetype not in ARCHETYPES:
        raise ValueError(f"unknown archetype: {archetype}")
    source_context = {
        "repository": repo_meta.get("url"),
        "commit": repo_meta.get("commit"),
        "file": candidate.get("file"),
        "symbol": candidate.get("function"),
        "docstring_summary": candidate.get("docstring_first_line", ""),
        "source": candidate.get("source", ""),
    }
    return f"""You are authoring a reasoning-intensive scientific computing task for SciCode.

The training target is the solver's scientific reasoning, not merely whether a
short function passes tests. The task must require a chain of distinct cognitive
operations such as deriving, comparing assumptions, diagnosing evidence,
selecting a method, analyzing regimes, or revising a model. Reject in your own
reasoning any task that reduces to translating a docstring, copying the source,
or implementing branches mechanically.
The solver will see problem.question, problem.background, deliverable.kind and
requirements, and EVERY archetype_payload field, but will NOT see the source
or reasoning_contract. All necessary observations, assumptions, alternatives,
and criteria must be in those visible fields. Payload fields must state neutral
givens, never the diagnosis, preferred alternative, derived result, or answer.
Do not hide essential numerical values in reasoning_contract.evidence_expected.

Task archetype: {archetype}
{ARCHETYPE_GUIDANCE[archetype]}

GROUNDING SOURCE (private author evidence; never mention it in the problem):
{json.dumps(source_context, ensure_ascii=False, indent=2)}

Return ONLY one JSON object with these keys:
{{
  "problem": {{
    "question": "self-contained task, at least 120 characters",
    "background": "scientific context, assumptions and meanings, at least 60 characters"
  }},
  "deliverable": {{
    "kind": "analysis" | "analysis_and_code" | "analysis_and_experiment",
    "requirements": ["at least two explicit deliverables"]
  }},
  "reasoning_contract": {{
    "cognitive_operations": ["at least three distinct operations"],
    "scientific_concepts": ["at least two concepts"],
    "evidence_expected": ["at least two signs of a strong reasoning trace"],
    "failure_modes": ["at least one scientifically meaningful reasoning failure"],
    "forbidden_shortcuts": ["at least one shortcut that makes the task shallow"]
  }},
  "archetype_payload": {{"use the archetype-specific keys described above": "..."}}
}}

Do not emit tests, reference code, a solution, markdown fences, or prose outside
the JSON object.
"""

