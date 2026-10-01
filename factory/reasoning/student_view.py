"""The one canonical, source-free task view shown to a solver."""

from __future__ import annotations

import json

from .schema import canonical_hash, validate_task

STUDENT_VIEW_POLICY = "student-visible-v3"
REUSED_PROMPT_POLICY = "audited-sft-prompt-v1"

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


def trace_student_prompt(task: dict, trace: dict) -> str:
    """Verify the exact prompt a solver saw and return its user message."""
    task = validate_task(task)
    messages = trace.get("messages") or []
    provenance = trace.get("provenance") or {}
    policy = provenance.get("student_view_policy")
    if policy == STUDENT_VIEW_POLICY:
        expected_user = render_student_user(task)
        expected_hash = student_view_hash(task)
        if not any(
            msg.get("role") == "user" and msg.get("content") == expected_user
            for msg in messages
        ):
            raise ValueError("trace does not contain the current task prompt")
    elif policy == REUSED_PROMPT_POLICY:
        prompt = messages[:2]
        if (
            len(prompt) != 2
            or [msg.get("role") for msg in prompt] != ["system", "user"]
            or any(not isinstance(msg.get("content"), str) for msg in prompt)
        ):
            raise ValueError("trace does not contain the reused system/user prompt")
        expected_user = prompt[1]["content"]
        if task["problem"]["question"] not in expected_user:
            raise ValueError("reused prompt does not contain the task question")
        expected_hash = canonical_hash({
            "policy_version": REUSED_PROMPT_POLICY,
            "messages": prompt,
        })
    else:
        raise ValueError(f"unsupported student prompt policy: {policy}")
    if provenance.get("student_view_hash") != expected_hash:
        raise ValueError("trace has a stale or mismatched student prompt hash")
    return expected_user
