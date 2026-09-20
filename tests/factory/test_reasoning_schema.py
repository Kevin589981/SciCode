import copy
import unittest

from factory.reasoning.schema import (
    GRADE_SCHEMA,
    TASK_SCHEMA,
    TRACE_SCHEMA,
    SchemaError,
    canonical_hash,
    validate_grade,
    validate_task,
    validate_trace,
)


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


def trace_for(task=None):
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
        "outcome": {"status": "fail", "kind": "auxiliary_check"},
        "usage": {"completion_tokens": 200},
        "timing_sec": 1.5,
        "provenance": {"factory_commit": "abc123", "task_set_hash": "def456"},
    }


def grade_for(trace=None):
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
        "trainable": True,
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


class ReasoningSchemaTests(unittest.TestCase):
    def test_all_archetypes_validate(self):
        for archetype in (
            "derive_implement",
            "diagnose_revise",
            "compare_justify",
        ):
            with self.subTest(archetype=archetype):
                self.assertEqual(validate_task(task_for(archetype))["archetype"], archetype)

    def test_canonical_hash_ignores_mapping_order(self):
        left = {"a": 1, "b": {"x": 2, "y": 3}}
        right = {"b": {"y": 3, "x": 2}, "a": 1}
        self.assertEqual(canonical_hash(left), canonical_hash(right))
        self.assertEqual(len(canonical_hash(left)), 64)

    def test_shallow_task_is_rejected(self):
        task = task_for()
        task["reasoning_contract"]["cognitive_operations"] = ["write code"]
        with self.assertRaisesRegex(SchemaError, "cognitive_operations"):
            validate_task(task)

    def test_archetype_payload_is_required(self):
        task = task_for("compare_justify")
        del task["archetype_payload"]["alternatives"]
        with self.assertRaisesRegex(SchemaError, "alternatives"):
            validate_task(task)

    def test_trace_requires_native_reasoning(self):
        trace = trace_for()
        del trace["messages"][-1]["reasoning_content"]
        with self.assertRaisesRegex(SchemaError, "reasoning_content"):
            validate_trace(trace)

    def test_grade_annotations_must_cover_assistant_messages(self):
        trace = trace_for()
        grade = grade_for(trace)
        self.assertTrue(validate_grade(grade, trace)["trainable"])
        broken = copy.deepcopy(grade)
        broken["message_annotations"][0]["message_index"] = 1
        with self.assertRaisesRegex(SchemaError, "assistant message"):
            validate_grade(broken, trace)

    def test_grade_scores_are_bounded(self):
        grade = grade_for()
        grade["scores"]["scientific_validity"] = 5
        with self.assertRaisesRegex(SchemaError, "scientific_validity"):
            validate_grade(grade)


if __name__ == "__main__":
    unittest.main()
