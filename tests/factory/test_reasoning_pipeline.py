import json
import tempfile
import unittest
from pathlib import Path

from factory.reasoning.pipeline import run_pipeline

THINKING = "Derive the regimes, compare stable forms, and justify the selection."
FINAL = "The stable method follows.\n```python\ndef method(x):\n    return x\n```"


def critic_response():
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "scores": {
                                "scientific_depth": 4,
                                "multi_step_dependency": 4,
                                "decision_requirement": 3,
                                "nontriviality": 4,
                            },
                            "shallow_failure_mode": None,
                            "rationale": "Dependent scientific decisions are explicit.",
                        }
                    )
                }
            }
        ],
        "usage": {"completion_tokens": 80},
    }


def judge_response():
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "scores": {
                                "scientific_validity": 4,
                                "causal_coherence": 4,
                                "strategy": 3,
                                "evidence_use": 3,
                                "self_correction": 1,
                                "insight_density": 4,
                                "degeneracy": 0,
                            },
                            "rationale": "Substantive scientific reasoning.",
                            "message_annotations": [
                                {
                                    "message_index": 2,
                                    "train_reasoning": True,
                                    "train_content": True,
                                    "quality": "good",
                                    "rationale": "Grounded derivation.",
                                }
                            ],
                        }
                    )
                }
            }
        ],
        "usage": {"completion_tokens": 120},
    }


def verifier_response():
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "scores": {
                                "scientific_validity": 4,
                                "source_grounding": 4,
                                "answerability": 4,
                                "constraint_consistency": 4,
                                "shortcut_resistance": 3,
                            },
                            "fatal_issues": [],
                            "evidence": [
                                {
                                    "claim": "A scientific model is explicitly evaluated.",
                                    "source_quote": "Evaluate a stable scientific model.",
                                    "assessment": "supports",
                                },
                                {
                                    "claim": "The implementation uses a scaled bounded ratio.",
                                    "source_quote": "return x / (scale + abs(x))",
                                    "assessment": "supports",
                                },
                            ],
                            "rationale": "The task is grounded and internally consistent.",
                        }
                    )
                }
            }
        ],
        "usage": {"completion_tokens": 100},
    }


def solver_response():
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "reasoning_content": THINKING,
                    "content": FINAL,
                }
            }
        ],
        "usage": {"completion_tokens": 240},
    }


def candidate(index):
    return {
        "function": f"scientific_method_{index}",
        "file": f"science/method_{index}.py",
        "module_hint": f"science.method_{index}",
        "n_lines": 30,
        "n_args": 2,
        "docstring_first_line": "Evaluate a stable scientific model.",
        "source": (
            f"def scientific_method_{index}(x, scale):\n"
            '    """Evaluate a stable scientific model."""\n'
            "    return x / (scale + abs(x))\n"
        ),
    }


def author_spec(archetype):
    payloads = {
        "derive_implement": {
            "assumptions": ["smooth response", "finite precision arithmetic"],
            "derivation_target": "derive a stable bounded transformation",
        },
        "diagnose_revise": {
            "observations": [
                "the residual grows in the large-magnitude regime",
                "rescaling delays but does not remove the failure",
            ],
            "candidate_causes": ["unstable algebra", "incorrect normalization"],
        },
        "compare_justify": {
            "alternatives": ["direct ratio", "scaled reciprocal form"],
            "decision_criteria": ["stability", "accuracy", "cost"],
        },
    }
    return {
        "problem": {
            "question": (
                "Derive and analyze a stable scientific response model, compare "
                "the relevant numerical regimes, justify the method selected for "
                "finite precision, and implement the requested complete function "
                "without invoking an equivalent black-box routine."
            ),
            "background": (
                "Bounded response models arise in saturation and inverse problems. "
                "Equivalent analytic forms may have different finite-precision "
                "behavior, so assumptions and asymptotic regimes matter."
            ),
        },
        "deliverable": {
            "kind": "analysis_and_code",
            "requirements": [
                "derive the governing relation",
                "compare numerical regimes",
                "provide complete Python code",
            ],
        },
        "reasoning_contract": {
            "cognitive_operations": [
                "derive the model from assumptions",
                "analyze and compare limiting regimes",
                "justify and implement a stable strategy",
            ],
            "scientific_concepts": ["asymptotic behavior", "finite precision"],
            "evidence_expected": [
                "a derivation tied to assumptions",
                "an explicit regime comparison",
            ],
            "failure_modes": ["unstable evaluation"],
            "forbidden_shortcuts": ["copy source without scientific analysis"],
        },
        "archetype_payload": payloads[archetype],
    }


class ReasoningPipelineTests(unittest.TestCase):
    def test_offline_end_to_end_and_resume(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mined = root / "mined.jsonl"
            repo_meta = root / "repo.json"
            output = root / "run"
            mined.write_text(
                "".join(json.dumps(candidate(i)) + "\n" for i in range(3)),
                encoding="utf-8",
            )
            repo_meta.write_text(
                json.dumps(
                    {
                        "url": "https://example.test/science.git",
                        "commit": "deadbeef",
                        "license": "BSD-3-Clause",
                        "slug": "science",
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_chat(messages, **kwargs):
                text = messages[0]["content"]
                calls.append((text, kwargs["model"], kwargs["max_tokens"]))
                if "authoring a reasoning-intensive" in text:
                    archetype = text.split("Task archetype: ", 1)[1].splitlines()[0]
                    return {
                        "choices": [
                            {"message": {"content": json.dumps(author_spec(archetype))}}
                        ],
                        "usage": {"completion_tokens": 300},
                    }
                if "independent critic" in text:
                    return critic_response()
                if "independent scientific task verifier" in text:
                    return verifier_response()
                if "TRAINING VALUE" in text:
                    return judge_response()
                result = solver_response()
                result["choices"][0]["message"]["reasoning_content"] = THINKING
                result["choices"][0]["message"]["content"] = FINAL
                return result

            first = run_pipeline(
                mined,
                repo_meta,
                output,
                chat_fn=fake_chat,
                author_model="author",
                critic_model="critic",
                verifier_model="verifier",
                solver_model="solver",
                judge_model="judge",
                additional_judge_models=("judge-2",),
                limit=3,
                concurrency=2,
                factory_commit="commit123",
                max_tokens=8000,
                author_max_tokens=12000,
                solver_max_tokens=16000,
                critic_max_tokens=3000,
                verifier_max_tokens=4000,
                judge_max_tokens=5000,
            )
            call_count = len(calls)
            second = run_pipeline(
                mined,
                repo_meta,
                output,
                chat_fn=fake_chat,
                author_model="author",
                critic_model="critic",
                verifier_model="verifier",
                solver_model="solver",
                judge_model="judge",
                additional_judge_models=("judge-2",),
                limit=3,
                concurrency=2,
                factory_commit="commit123",
                max_tokens=8000,
                author_max_tokens=12000,
                solver_max_tokens=16000,
                critic_max_tokens=3000,
                verifier_max_tokens=4000,
                judge_max_tokens=5000,
            )
            tasks = [
                json.loads(line)
                for line in (output / "tasks.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            traces = [
                json.loads(line)
                for line in (output / "traces.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            sft = [
                json.loads(line)
                for line in (output / "sft.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                {task["archetype"] for task in tasks},
                {"derive_implement", "diagnose_revise", "compare_justify"},
            )
            self.assertEqual(len(traces), 3)
            self.assertEqual(len(sft), 3)
            self.assertTrue(
                all(row["messages"][-1]["reasoning_content"] for row in sft)
            )
            self.assertEqual(first["factory_commit"], "commit123")
            self.assertEqual(first["artifacts"]["sft"]["rows"], 3)
            self.assertEqual(first["artifacts"]["grades"]["rows"], 6)
            self.assertEqual(first["models"]["judges"], ["judge", "judge-2"])
            self.assertEqual(first["parameters"]["context_window_tokens"], 262144)
            self.assertEqual(first["parameters"]["author_max_tokens"], 12000)
            self.assertEqual(first["parameters"]["solver_max_tokens"], 16000)
            self.assertEqual(first["parameters"]["critic_max_tokens"], 3000)
            self.assertTrue(
                all(
                    max_tokens == 3000
                    for prompt, _model, max_tokens in calls
                    if "independent critic" in prompt
                )
            )
            self.assertTrue(
                all(
                    max_tokens == 4000
                    for prompt, _model, max_tokens in calls
                    if "independent scientific task verifier" in prompt
                )
            )
            self.assertTrue(
                all(
                    max_tokens == 5000
                    for prompt, _model, max_tokens in calls
                    if "TRAINING VALUE" in prompt
                )
            )
            self.assertTrue(
                all(
                    max_tokens == 12000
                    for prompt, model, max_tokens in calls
                    if model == "author"
                )
            )
            self.assertTrue(
                all(
                    max_tokens == 16000
                    for prompt, model, max_tokens in calls
                    if model == "solver"
                )
            )
            self.assertEqual(len(calls), call_count)
            self.assertEqual(second["stages"]["author"]["skipped"], 3)
            self.assertTrue(all("automatic_review" in row for row in sft))
            mined.write_text(
                mined.read_text(encoding="utf-8") + json.dumps(candidate(3)) + "\n",
                encoding="utf-8",
            )
            recovered = run_pipeline(
                mined,
                repo_meta,
                output,
                chat_fn=fake_chat,
                author_model="author",
                critic_model="critic",
                verifier_model="verifier",
                solver_model="solver",
                judge_model="judge",
                additional_judge_models=("judge-2",),
                limit=4,
                concurrency=2,
                factory_commit="commit123",
                author_max_tokens=12000,
                solver_max_tokens=16000,
                critic_max_tokens=3000,
                verifier_max_tokens=4000,
                judge_max_tokens=5000,
                reuse_existing_tasks=True,
            )
            self.assertEqual(len(calls), call_count)
            self.assertTrue(recovered["stages"]["author"]["reused_existing_tasks"])
            self.assertEqual(recovered["artifacts"]["tasks"]["rows"], 3)


if __name__ == "__main__":
    unittest.main()
