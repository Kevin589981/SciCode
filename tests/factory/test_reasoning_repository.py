import json
import unittest

from factory.reasoning.repository import rank_candidates, screen_repository


def candidate(function, module, source, lines=30):
    return {
        "function": function,
        "file": module.replace(".", "/") + ".py",
        "module_hint": module,
        "n_lines": lines,
        "n_args": 2,
        "docstring_first_line": source,
        "source": f"def {function}(x, y):\n    # {source}\n    return x + y\n",
    }


class ReasoningRepositoryTests(unittest.TestCase):
    def test_screening_uses_fixed_grounded_schema(self):
        body = {
            "relevant": True,
            "confidence": 4,
            "reason": "Implements finite-volume solvers for fluid equations.",
            "domain_keywords": ["finite volume", "fluid dynamics"],
            "summary": {
                "project_overview": "A computational fluid dynamics solver.",
                "methods": "Finite-volume flux reconstruction.",
                "dependencies": "NumPy and SciPy.",
                "inputs_outputs": "Meshes to conserved flow fields.",
                "runtime": "Python on CPU.",
            },
        }
        response = {
            "choices": [{"message": {"content": json.dumps(body)}}],
            "usage": {"completion_tokens": 100},
        }
        profile = screen_repository(
            {"full_name": "science/cfd", "matched_keywords": ["fluid dynamics"]},
            "This repository implements finite-volume computational fluid dynamics. "
            * 4,
            chat_fn=lambda *_a, **_k: response,
            model="profile-model",
        )
        self.assertTrue(profile["relevant"])
        self.assertEqual(profile["confidence"], 4)
        self.assertEqual(profile["model"], "profile-model")

    def test_short_readme_is_rejected_without_model_call(self):
        profile = screen_repository(
            {"full_name": "empty/repo"},
            "tiny",
            chat_fn=lambda *_a, **_k: self.fail("model should not be called"),
            model="profile-model",
        )
        self.assertFalse(profile["relevant"])

    def test_dual_evidence_ranking_balances_modules(self):
        profile = {
            "domain_keywords": ["fluid dynamics", "finite volume", "conservation"],
            "summary": {
                "project_overview": "Fluid dynamics simulation",
                "methods": "finite volume conservation flux",
            },
        }
        repository = {
            "matched_keywords": ["computational fluid dynamics"],
            "description": "finite volume fluid simulation",
            "topics": ["fluid-dynamics", "simulation"],
        }
        values = [
            candidate("flux", "pkg.solver", "finite volume conservation flux"),
            candidate("flux_fast", "pkg.solver", "finite volume fluid flux", 50),
            candidate("step", "pkg.integrator", "fluid conservation time step"),
            candidate("unrelated", "pkg.ui", "graphical user interface"),
        ]
        selected = rank_candidates(values, profile, repository, limit=2)
        self.assertEqual(len(selected), 2)
        self.assertEqual(
            {row["module_hint"] for row in selected},
            {"pkg.solver", "pkg.integrator"},
        )
        self.assertTrue(all(row["relevance"]["final_score"] > 0 for row in selected))


if __name__ == "__main__":
    unittest.main()
