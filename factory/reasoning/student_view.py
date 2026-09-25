"""The one canonical, source-free task view shown to a solver."""

from __future__ import annotations

from .schema import canonical_hash, validate_task

STUDENT_VIEW_POLICY = "student-visible-v2"

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
        "given": {
            key: task["archetype_payload"][key]
            for _label, key in PAYLOAD_SECTIONS[task["archetype"]]
        },
    }


def render_student_user(task: dict) -> str:
    view = student_task_view(task)
    problem = view["problem"]
    deliverable = view["deliverable"]
    parts = [
        problem["question"],
        f"Scientific background:\n{problem['background']}",
        f"Task type: {ARCHETYPE_LABELS[view['archetype']]}",
    ]
    for label, key in PAYLOAD_SECTIONS[view["archetype"]]:
        value = view["given"][key]
        if isinstance(value, list):
            content = "\n".join(f"- {item}" for item in value)
        else:
            content = value
        parts.append(f"{label}:\n{content}")
    parts.append(
        "Deliverable type: "
        + deliverable["kind"].replace("_", " ")
        + "\nDeliverables:\n"
        + "\n".join(f"- {item}" for item in deliverable["requirements"])
    )
    parts.append(
        "Make the scientific method, assumptions, comparisons, and justification "
        "explicit before presenting the final implementation or analysis."
    )
    return "\n\n".join(parts) + "\n"


def student_view_hash(task: dict) -> str:
    return canonical_hash(
        {"policy_version": STUDENT_VIEW_POLICY, "user": render_student_user(task)}
    )
