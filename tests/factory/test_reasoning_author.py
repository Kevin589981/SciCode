import json
import tempfile
import unittest
from pathlib import Path

from factory.reasoning.author import AuthorError, compose_task, run_authoring
from factory.reasoning.schema import ARCHETYPES, TASK_SCHEMA, validate_task


def candidate(name="stable_demo"):
    return {
        "function": name,
        "file": "special/demo.py",
        "module_hint": "scipy.special.demo",
        "n_lines": 24,
        "n_args": 1,
        "docstring_first_line": "Map a response to a bounded observable.",
        "source": (
            f"def {name}(x):\n"
            "    \"\"\"Map a response to a bounded observable.\"\"\"\n"
            "    ax = abs(x)\n"
            "    return x / (1.0 + ax)\n"
        ),
    }


def spec_for(archetype):
    payloads = {
        "derive_implement": {
            "assumptions": ["smooth response", "finite precision arithmetic"],
            "derivation_target": "derive a stable bounded transformation",
        },
        "diagnose_revise": {
            "observations": [
                "the naive response overflows for large magnitude input",
                "the sign is lost in one asymptotic regime",
            ],
            "candidate_causes": ["unstable algebra", "incorrect sign handling"],
        },
        "compare_justify": {
            "alternatives": ["direct ratio", "piecewise reciprocal form"],
            "decision_criteria": ["stability", "accuracy", "complexity"],
        },
    }
    return {
        "problem": {
            "question": (
                "Construct a scientifically justified bounded-response algorithm. "
                "Derive its asymptotic behavior, distinguish the finite-precision "
                "regimes, explain the selected numerical strategy, and implement "
                "the requested function without calling an equivalent routine."
            ),
            "background": (
                "Bounded response models occur in saturation and robust inverse "
                "problems. Their mathematical equivalence does not guarantee equal "
                "behavior when evaluated using finite precision arithmetic."
            ),
        },
        "deliverable": {
            "kind": "analysis_and_code",
            "requirements": [
                "derive the mathematical relation",
                "analyze numerical regimes",
                "provide complete Python code",
            ],
        },
        "reasoning_contract": {
            "cognitive_operations": [
                "derive a transformation from assumptions",
                "analyze asymptotic regimes",
                "justify a stable numerical strategy",
            ],
            "scientific_concepts": ["asymptotic behavior", "finite precision"],
            "evidence_expected": [
                "a derivation tied to assumptions",
                "a comparison of at least two regimes",
            ],
            "failure_modes": ["overflow or loss of sign"],
            "forbidden_shortcuts": ["copy the reference source"],
        },
        "archetype_payload": payloads[archetype],
    }


def response_for(archetype):
    return {
        "choices": [{"message": {"content": json.dumps(spec_for(archetype))}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 200},
    }


REPO_META = {
    "url": "https://github.com/scipy/scipy",
    "commit": "0123456789abcdef",
    "license": "BSD-3-Clause",
    "slug": "scipy",
}


class ReasoningAuthorTests(unittest.TestCase):
    def test_composes_each_archetype_and_keeps_model_metadata(self):
        for archetype in ARCHETYPES:
            with self.subTest(archetype=archetype):
                calls = []

                def fake_chat(messages, **kwargs):
                    calls.append((messages, kwargs))
                    return response_for(archetype)

                task = compose_task(
                    candidate(), archetype, REPO_META,
                    chat_fn=fake_chat, model="author-model", max_tokens=8000,
                )
                validate_task(task)
                self.assertEqual(task["schema_version"], TASK_SCHEMA)
                self.assertEqual(task["archetype"], archetype)
                self.assertEqual(task["authoring"]["model"], "author-model")
                self.assertEqual(task["authoring"]["usage"]["completion_tokens"], 200)
                self.assertEqual(calls[0][1]["max_tokens"], 8000)
                self.assertIn("GROUNDING SOURCE", calls[0][0][0]["content"])

    def test_malformed_response_is_reported(self):
        def bad_chat(messages, **kwargs):
            return {"choices": [{"message": {"content": "not json"}}]}

        with self.assertRaisesRegex(AuthorError, "JSON"):
            compose_task(candidate(), "derive_implement", REPO_META, chat_fn=bad_chat)

    def test_run_authoring_resumes_without_duplicate_calls(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mined = root / "mined.jsonl"
            meta = root / "repo.json"
            out = root / "tasks.jsonl"
            mined.write_text(json.dumps(candidate()) + "\n", encoding="utf-8")
            meta.write_text(json.dumps(REPO_META), encoding="utf-8")
            calls = []

            def fake_chat(messages, **kwargs):
                calls.append(1)
                return response_for("derive_implement")

            first = run_authoring(
                mined, meta, out, archetypes=["derive_implement"],
                chat_fn=fake_chat, model="fake",
            )
            second = run_authoring(
                mined, meta, out, archetypes=["derive_implement"],
                chat_fn=fake_chat, model="fake",
            )
            rows = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertEqual(first["written"], 1)
            self.assertEqual(second["skipped"], 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
