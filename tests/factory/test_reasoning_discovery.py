import json
import tempfile
import unittest
from pathlib import Path

from factory.reasoning.discovery import (
    GitHubClient,
    discover_repositories,
    expand_keywords,
    load_keywords,
    metadata_rejection,
    pin_repository_heads,
    write_catalog,
)


def repository(repo_id, name, *, stars=20, size=100, **overrides):
    value = {
        "id": repo_id,
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "clone_url": f"https://github.com/{name}.git",
        "default_branch": "main",
        "description": "scientific methods",
        "topics": ["science"],
        "language": "Python",
        "size": size,
        "stargazers_count": stars,
        "forks_count": 2,
        "archived": False,
        "fork": False,
        "disabled": False,
        "license": {"spdx_id": "BSD-3-Clause"},
        "pushed_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
    }
    value.update(overrides)
    return value


class ReasoningDiscoveryTests(unittest.TestCase):
    def test_retrieve_filter_deduplicates_overlapping_queries(self):
        rows = {
            "ode": [
                repository(1, "science/solver", stars=30),
                repository(2, "science/tiny", stars=3),
            ],
            "simulation": [
                repository(1, "science/solver", stars=30),
                repository(3, "science/archived", archived=True),
                repository(4, "science/huge", size=5000),
            ],
        }

        def search(keyword, **kwargs):
            self.assertEqual(kwargs["min_stars"], 10)
            return rows[keyword]

        found = discover_repositories(
            ["ode", "simulation"],
            search_fn=search,
            min_stars=10,
            max_size_kb=1000,
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["full_name"], "science/solver")
        self.assertEqual(found[0]["matched_keywords"], ["ode", "simulation"])

    def test_keyword_expansion_keeps_seeds_and_deduplicates(self):
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "queries": [
                                    "ODE",
                                    "finite volume",
                                    "computational plasma",
                                ],
                            }
                        )
                    }
                }
            ]
        }
        expanded = expand_keywords(
            ["ODE", "PDE"],
            chat_fn=lambda *_a, **_k: response,
            model="expander",
            max_new=2,
        )
        self.assertEqual(
            expanded, ["ODE", "PDE", "finite volume", "computational plasma"]
        )

    def test_catalog_write_is_jsonl_and_keyword_comments_are_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            keywords = root / "keywords.txt"
            catalog = root / "catalog.jsonl"
            keywords.write_text(
                "# taxonomy\nODE\node  # duplicate\nPDE\n", encoding="utf-8"
            )
            self.assertEqual(load_keywords(keywords), ["ODE", "PDE"])
            write_catalog(catalog, [{"repo_id": "1"}, {"repo_id": "2"}])
            self.assertEqual(
                [
                    json.loads(line)["repo_id"]
                    for line in catalog.read_text().splitlines()
                ],
                ["1", "2"],
            )

    def test_head_pinning_records_immutable_commit_after_filtering(self):
        rows = discover_repositories(
            ["ode"],
            search_fn=lambda *_a, **_k: [repository(1, "science/solver")],
        )
        calls = []

        def resolve(full_name, branch):
            calls.append((full_name, branch))
            return "a" * 40

        pinned = pin_repository_heads(rows, resolve_fn=resolve)
        self.assertEqual(calls, [("science/solver", "main")])
        self.assertEqual(pinned[0]["head_sha"], "a" * 40)
        self.assertNotIn("head_sha", rows[0])

    def test_discovery_records_individual_failures_and_continues(self):
        errors = []

        def search(keyword, **_kwargs):
            if keyword == "broken":
                raise RuntimeError("temporary failure")
            return [repository(1, "science/solver")]

        rows = discover_repositories(["broken", "ode"], search_fn=search, errors=errors)
        self.assertEqual([row["full_name"] for row in rows], ["science/solver"])
        self.assertEqual(errors[0]["stage"], "search")

        pin_errors = []

        def resolve(name, _branch):
            if name == "science/solver":
                return "a" * 40
            raise RuntimeError("gone")

        pinned = pin_repository_heads(
            [*rows, {**rows[0], "repo_id": "2", "full_name": "gone/repo"}],
            resolve_fn=resolve,
            errors=pin_errors,
        )
        self.assertEqual(len(pinned), 1)
        self.assertEqual(pin_errors[0]["repository"], "gone/repo")

    def test_documentation_collections_are_rejected_before_clone_or_llm(self):
        documentation = repository(
            1,
            "org/awesome-numerics",
            description="A curated list of numerical analysis resources",
        )
        implementation = repository(
            2,
            "org/finite-volume-solver",
            description="Finite-volume simulation library for conservation laws",
        )
        rejections = []
        rows = discover_repositories(
            ["numerical analysis"],
            search_fn=lambda *_a, **_k: [documentation, implementation],
            rejections=rejections,
        )
        self.assertEqual(
            [row["full_name"] for row in rows], [implementation["full_name"]]
        )
        self.assertEqual(rejections[0]["reason"], "documentation_or_collection_name")
        self.assertIsNone(metadata_rejection(rows[0]))
        self.assertEqual(
            metadata_rejection(
                repository(3, "idrl-lab/PINNpapers", description="PINN research")
            ),
            "documentation_or_collection_name",
        )

    def test_default_search_is_high_precision_metadata_scope(self):
        urls = []
        client = GitHubClient(request_interval=0)

        def get(url):
            urls.append(url)
            return {"items": []}, {}

        client._get = get
        client.search(
            "numerical analysis",
            min_stars=10,
            language="Python",
            pages=1,
            per_page=10,
        )
        self.assertIn("in%3Aname%2Cdescription", urls[0])
        self.assertNotIn("readme", urls[0])


if __name__ == "__main__":
    unittest.main()
