"""Shared deterministic records for reasoning-factory tests."""

from factory.reasoning.schema import GRADE_SCHEMA, TASK_SCHEMA, TRACE_SCHEMA, canonical_hash


def task_for(archetype="derive_implement"):
    payloads = {
        "derive_implement": {
            "assumptions": ["smooth solution", "finite precision arithmetic"],
            "derivation_target": "derive a stable update before implementing it",
        },
        "diagnose_revise": {
            "observations": [
                "the residual falls initially and then grows near the boundary",
                "halving the step changes the failure location",
            ],
            "candidate_causes": ["unstable update", "incorrect boundary treatment"],
        },
        "compare_justify": {
            "alternatives": ["direct evaluation", "log-domain evaluation"],
            "decision_criteria": ["stability", "accuracy", "cost"],
        },
    }
    return {
        "schema_version": TASK_SCHEMA,
        "task_id": f"scipy-demo-{archetype}",
        "archetype": archetype,
        "source": {
            "repo": "https://github.com/scipy/scipy",
            "commit": "0123456789abcdef",
            "file": "scipy/special/demo.py",
            "symbol": "stable_demo",
            "license": "BSD-3-Clause",
            "source": "def stable_demo(x):\n    return x / (1 + abs(x))",
        },
        "problem": {
            "question": (
                "Develop a numerically stable method for the stated scientific "
                "model. Explain how the assumptions determine the algorithm, "
                "analyze the limiting regimes, and implement stable_demo(x) "
                "without relying on a black-box equivalent routine."
            ),
            "background": (
                "The model maps an unbounded response to a bounded observable. "
                "Finite precision and asymptotic behavior both affect the choice "
                "of a reliable implementation."
            ),
        },
        "deliverable": {
            "kind": "analysis_and_code",
            "requirements": [
                "state the governing relation",
                "justify the numerical method",
                "provide a complete Python implementation",
            ],
        },
        "reasoning_contract": {
            "cognitive_operations": [
                "derive the governing transformation",
                "analyze limiting regimes",
                "justify a stable implementation",
            ],
            "scientific_concepts": ["asymptotic behavior", "finite precision"],
            "evidence_expected": [
                "a derivation tied to the assumptions",
                "analysis of at least two numerical regimes",
            ],
            "failure_modes": ["catastrophic cancellation"],
            "forbidden_shortcuts": ["copy a library implementation without analysis"],
        },
        "archetype_payload": payloads[archetype],
        "authoring": {"model": "fake-author", "temperature": 0.3},
    }


def trace_for(task=None, *, outcome="fail"):
    task = task or task_for()
    return {
        "schema_version": TRACE_SCHEMA,
        "trace_id": f"{task['task_id']}::fake-solver::0",
        "task_id": task["task_id"],
        "task_hash": canonical_hash(task),
        "model": "fake-solver",
        "temperature": 0.7,
        "max_tokens": 16384,
        "messages": [
            {"role": "system", "content": "Solve the scientific task."},
            {"role": "user", "content": task["problem"]["question"]},
            {
                "role": "assistant",
                "reasoning_content": "I derive the asymptotes and compare stable forms.",
                "content": "```python\ndef stable_demo(x):\n    return x / (1 + abs(x))\n```",
            },
        ],
        "outcome": {"status": outcome, "kind": "auxiliary_check"},
        "usage": {"completion_tokens": 200},
        "timing_sec": 1.5,
        "provenance": {"factory_commit": "abc123", "task_set_hash": "def456"},
    }


def grade_for(trace=None, *, trainable=True):
    trace = trace or trace_for()
    return {
        "schema_version": GRADE_SCHEMA,
        "trace_id": trace["trace_id"],
        "scores": {
            "scientific_validity": 4,
            "causal_coherence": 4,
            "strategy": 3,
            "evidence_use": 3,
            "self_correction": 2,
            "insight_density": 4,
            "degeneracy": 0,
        },
        "trainable": trainable,
        "rationale": "The reasoning is scientifically grounded despite the check failure.",
        "message_annotations": [
            {
                "message_index": 2,
                "train_reasoning": True,
                "train_content": False,
                "quality": "good",
                "rationale": "Strong derivation; final formatting failed.",
            }
        ],
        "judge": {"model": "fake-judge"},
    }
