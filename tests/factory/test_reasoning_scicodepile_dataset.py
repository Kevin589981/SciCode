import csv
import json
import tempfile
import unittest
from pathlib import Path

from factory.reasoning.discovery import load_scicodepile_manifest
from factory.reasoning.scicodepile_dataset import (
    prepare_dataset,
    safe_relative_path,
)


FIELDS = [
    "keyword",
    "repo_name",
    "file_path",
    "file_extension",
    "file_size",
    "line_count",
    "content",
    "language",
]


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def row(keyword, repo, path, content, language="Python"):
    return {
        "keyword": keyword,
        "repo_name": repo,
        "file_path": path,
        "file_extension": Path(path).suffix,
        "file_size": len(content.encode()),
        "line_count": content.count("\n") + 1,
        "content": content,
        "language": language,
    }


class SciCodePileDatasetTests(unittest.TestCase):
    def test_paths_reject_traversal_and_absolute_values(self):
        self.assertEqual(safe_relative_path("src\\solver.py"), "src/solver.py")
        self.assertIsNone(safe_relative_path("../secret.py"))
        self.assertIsNone(safe_relative_path("/tmp/secret.py"))
        self.assertIsNone(safe_relative_path("C:\\secret.py"))

    def test_prepares_clean_snapshots_and_resumes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw = root / "raw"
            first = [
                row("ODE", "science/solver", "README.md", "A" * 120, "Markdown"),
                row(
                    "ODE",
                    "science/solver",
                    "src/solver.py",
                    "def solve():\n    return 1\n",
                ),
                row("CFD", "science/flow", "flow.py", "def flux():\n    return 2\n"),
                row("CFD", "science/cpp-only", "main.cpp", "int main(){}", "C++"),
                row("ODE", "bad/repo", "../escape.py", "raise SystemExit"),
            ]
            second = [
                row(
                    "Numerics",
                    "science/solver",
                    "src/solver.py",
                    "def solve():\n    return 1\n",
                ),
            ]
            write_csv(raw / "data" / "dataset_a.csv", first)
            write_csv(raw / "data" / "dataset_b.csv", second)
            prepared = root / "prepared"
            catalog = prepared / "catalog.jsonl"
            report = prepare_dataset(
                raw_dir=raw,
                prepared_root=prepared,
                catalog_path=catalog,
                index_path=prepared / "index.sqlite3",
                repository_limit=2,
                dataset="SciCodePile/test",
                revision="a" * 40,
            )
            self.assertEqual(report["repositories"], 3)
            self.assertEqual(report["python_repositories"], 2)
            self.assertEqual(report["prepared_repositories"], 2)
            candidates = load_scicodepile_manifest(catalog)
            self.assertEqual(
                {candidate["full_name"] for candidate in candidates},
                {"science/solver", "science/flow"},
            )
            solver = next(
                candidate
                for candidate in candidates
                if candidate["full_name"] == "science/solver"
            )
            self.assertEqual(
                (Path(solver["snapshot_path"]) / "src" / "solver.py").read_text(),
                "def solve():\n    return 1\n",
            )
            self.assertEqual(set(solver["matched_keywords"]), {"ODE", "Numerics"})
            metadata = json.loads(
                (
                    Path(solver["snapshot_path"]) / ".scicodepile_snapshot.json"
                ).read_text()
            )
            self.assertEqual(metadata["snapshot_hash"], solver["snapshot_hash"])

            resumed = prepare_dataset(
                raw_dir=raw,
                prepared_root=prepared,
                catalog_path=catalog,
                index_path=prepared / "index.sqlite3",
                repository_limit=2,
                dataset="SciCodePile/test",
                revision="a" * 40,
            )
            self.assertEqual(resumed["newly_indexed_files"], 0)
            self.assertEqual(resumed["newly_extracted_rows"], 0)


if __name__ == "__main__":
    unittest.main()
