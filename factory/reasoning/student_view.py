"""The one canonical, source-free task view shown to a solver."""

from __future__ import annotations

import json

from .schema import canonical_hash, validate_task

STUDENT_VIEW_POLICY = "student-visible-v3"

ARCHETYPE_LABELS = {
    "derive_implement": "derive and implement",
    "diagnose_revise": "diagnose and revise",
    "compare_justify": "compare and justify",
}

PAYLOAD_SECTIONS = {
    "derive_implement": (
        ("Assumptions supplied with the problem", "assumptions"),
        ("Derivation target", "derivation_target"),
    ),
    "diagnose_revise": (
        ("Observations to diagnose", "observations"),
        ("Candidate causes to distinguish", "candidate_causes"),
    ),
    "compare_justify": (
        ("Alternatives to compare", "alternatives"),
        ("Decision criteria", "decision_criteria"),
    ),
}


def student_task_view(task: dict) -> dict:
    """Return exactly the task facts and requirements made visible to a solver.

    Source code, authoring metadata, and the private reasoning rubric are never
    silently promoted into the student prompt. All archetype-specific *given*
    facts are explicit, including observations and candidate causes.
    """
    task = validate_task(task)
    return {
        "policy_version": STUDENT_VIEW_POLICY,
        "archetype": task["archetype"],
        "problem": task["problem"],
        "deliverable": task["deliverable"],
        "given": task["archetype_payload"],
    }


def _show(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(f"- {_show(item)}" for item in value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def render_student_user(task: dict) -> str:
    view = student_task_view(task)
    problem = view["problem"]
    deliverable = view["deliverable"]
    parts = [
        problem["question"],
        f"Scientific background:\n{problem['background']}",
        f"Task type: {ARCHETYPE_LABELS[view['archetype']]}",
    ]
    for key in sorted(set(problem) - {"question", "background"}):
        parts.append(f"Additional problem information ({key}):\n{_show(problem[key])}")
    for label, key in PAYLOAD_SECTIONS[view["archetype"]]:
        parts.append(f"{label}:\n{_show(view['given'][key])}")
    required = {key for _label, key in PAYLOAD_SECTIONS[view["archetype"]]}
    for key in sorted(set(view["given"]) - required):
        parts.append(f"Additional task information ({key}):\n{_show(view['given'][key])}")
    parts.append(
        "Deliverable type: "
        + deliverable["kind"].replace("_", " ")
        + "\nDeliverables:\n"
        + "\n".join(f"- {item}" for item in deliverable["requirements"])
    )
    for key in sorted(set(deliverable) - {"kind", "requirements"}):
        parts.append(f"Additional deliverable information ({key}):\n{_show(deliverable[key])}")
    parts.append(
        "Make the scientific method, assumptions, comparisons, and justification "
        "explicit before presenting the final implementation or analysis."
    )
    return "\n\n".join(parts) + "\n"


def student_view_hash(task: dict) -> str:
    return canonical_hash(
        {"policy_version": STUDENT_VIEW_POLICY, "user": render_student_user(task)}
    )
