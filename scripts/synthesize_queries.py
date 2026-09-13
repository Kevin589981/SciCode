#!/usr/bin/env python3
"""Synthesize SciCode problem queries without any model or network call."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # Works as a script and when imported by tests.
    from .query_schema import QuerySchemaError, validate_query
    from .scientific_blueprints import build_blueprint
    from .scientific_components import catalog_summary, render_component_query
except ImportError:  # pragma: no cover
    from query_schema import QuerySchemaError, validate_query
    from scientific_blueprints import build_blueprint
    from scientific_components import catalog_summary, render_component_query


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, index: int) -> str:
    return f"{prefix}-{index:06d}"


def json_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass
class SynthesisConfig:
    target_queries: int
    output_dir: Path
    seed: int = 0
    id_prefix: str = "synth"
    max_attempts: int | None = None

    def validate(self) -> None:
        if self.target_queries < 1:
            raise ValueError("target_queries must be positive")
        if self.max_attempts is not None and self.max_attempts < 1:
            raise ValueError("max_attempts must be positive when supplied")
        if not self.id_prefix or not self.id_prefix.replace("_", "").replace("-", "").isalnum():
            raise ValueError("id_prefix must be path-safe")


class QuerySynthesizer:
    def __init__(self, config: SynthesisConfig) -> None:
        config.validate()
        self.config = config
        self.config.output_dir = Path(config.output_dir)
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        self.query_path = self.config.output_dir / "queries.jsonl"
        self.metadata_path = self.config.output_dir / "metadata.jsonl"
        self.errors_path = self.config.output_dir / "errors.jsonl"
        self.manifest_path = self.config.output_dir / "manifest.json"

    def _existing(self) -> tuple[set[str], int, int, int]:
        ids: set[str] = set()
        largest = 0
        subproblems = 0
        for line in self.query_path.read_text(encoding="utf-8").splitlines() if self.query_path.is_file() else []:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                validate_query(value)
            except (json.JSONDecodeError, QuerySchemaError):
                continue
            query_id = value["problem_id"]
            if not query_id.startswith(f"{self.config.id_prefix}-"):
                continue
            ids.add(query_id)
            try:
                largest = max(largest, int(query_id.rsplit("-", 1)[1]))
            except ValueError:
                pass
            subproblems += len(value["sub_steps"])
        rejected = 0
        if self.errors_path.is_file():
            rejected = sum(1 for line in self.errors_path.read_text(encoding="utf-8").splitlines() if line.strip())
        return ids, largest, subproblems, rejected

    def _write_manifest(
        self,
        *,
        status: str,
        started_at: str,
        accepted: int,
        attempted: int,
        rejected: int,
        subproblems: int,
        next_index: int,
    ) -> dict[str, Any]:
        value = {
            "schema": "scicode-local-query-synthesis-v2",
            "status": status,
            "started_at": started_at,
            "updated_at": utc_now(),
            "target_queries": self.config.target_queries,
            "accepted_queries": accepted,
            "attempted_query_ids": attempted,
            "rejected_queries": rejected,
            "subproblem_count": subproblems,
            "next_index": next_index,
            "generation_method": "hand_authored_component_composition",
            "model_calls": 0,
            "kimi_api_calls": 0,
            "official_scicode_dataset_read": False,
            "catalog": catalog_summary(),
            "config": {
                "target_queries": self.config.target_queries,
                "output_dir": str(self.config.output_dir),
                "seed": self.config.seed,
                "id_prefix": self.config.id_prefix,
                "max_attempts": self.config.max_attempts,
            },
            "files": {
                "queries": str(self.query_path),
                "metadata": str(self.metadata_path),
                "errors": str(self.errors_path),
            },
        }
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.manifest_path)
        return value

    @staticmethod
    def _append(path: Path, value: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()

    def run(self) -> dict[str, Any]:
        started_at = utc_now()
        accepted_ids, largest, subproblems, rejected = self._existing()
        accepted = len(accepted_ids)
        attempted = 0
        next_index = largest + 1
        self._write_manifest(
            status="running",
            started_at=started_at,
            accepted=accepted,
            attempted=attempted,
            rejected=rejected,
            subproblems=subproblems,
            next_index=next_index,
        )
        if accepted >= self.config.target_queries:
            return self._write_manifest(
                status="finished",
                started_at=started_at,
                accepted=accepted,
                attempted=attempted,
                rejected=rejected,
                subproblems=subproblems,
                next_index=next_index,
            )

        try:
            while accepted < self.config.target_queries:
                if self.config.max_attempts is not None and attempted >= self.config.max_attempts:
                    break
                query_id = stable_id(self.config.id_prefix, next_index)
                next_index += 1
                attempted += 1
                try:
                    # Keep the public builder hook for callers/tests that
                    # inject a failing builder, then obtain the richer
                    # component metadata from the same deterministic render.
                    query, _blueprint = build_blueprint(
                        query_id, next_index - 2, self.config.seed
                    )
                    _metadata_query, metadata = render_component_query(
                        query_id, next_index - 2, self.config.seed
                    )
                    if query != _metadata_query:
                        raise ValueError("builder and component renderer diverged")
                    validate_query(query)
                except (QuerySchemaError, ValueError, TypeError) as exc:
                    rejected += 1
                    self._append(
                        self.errors_path,
                        {
                            "query_id": query_id,
                            "error": str(exc),
                            "generation_method": "hand_authored_component_composition",
                        },
                    )
                    continue
                self._append(self.query_path, query)
                self._append(
                    self.metadata_path,
                    {
                        "query_id": query_id,
                        "seed": self.config.seed,
                        "subproblem_count": len(query["sub_steps"]),
                        "query_sha256": json_hash(query),
                        **metadata,
                    },
                )
                accepted_ids.add(query_id)
                accepted += 1
                subproblems += len(query["sub_steps"])
                if attempted % 25 == 0 or accepted >= self.config.target_queries:
                    self._write_manifest(
                        status="running",
                        started_at=started_at,
                        accepted=accepted,
                        attempted=attempted,
                        rejected=rejected,
                        subproblems=subproblems,
                        next_index=next_index,
                    )
        except KeyboardInterrupt:
            return self._write_manifest(
                status="stopped",
                started_at=started_at,
                accepted=accepted,
                attempted=attempted,
                rejected=rejected,
                subproblems=subproblems,
                next_index=next_index,
            )

        status = "finished" if accepted >= self.config.target_queries else "incomplete"
        return self._write_manifest(
            status=status,
            started_at=started_at,
            accepted=accepted,
            attempted=attempted,
            rejected=rejected,
            subproblems=subproblems,
            next_index=next_index,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-queries", type=int, default=10_000)
    parser.add_argument("--output-dir", type=Path, default=Path("runs/local-query-synthesis"))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--id-prefix", default="synth")
    parser.add_argument("--max-attempts", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = QuerySynthesizer(
            SynthesisConfig(
                target_queries=args.target_queries,
                output_dir=args.output_dir,
                seed=args.seed,
                id_prefix=args.id_prefix,
                max_attempts=args.max_attempts,
            )
        ).run()
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "finished" else 2


if __name__ == "__main__":
    raise SystemExit(main())
