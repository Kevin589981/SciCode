#!/usr/bin/env python3
"""Audit a locally synthesized SciCode query batch.

The report is intentionally independent of an answer model and of private
oracle data.  It checks transport validity, exact-record duplicates, and the
component-level diversity metadata emitted by ``synthesize_queries.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:  # Works as a script and as an imported module.
    from .query_schema import QuerySchemaError, validate_query
except ImportError:  # pragma: no cover
    from query_schema import QuerySchemaError, validate_query


def _record_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            records.append(value)
    return records


def audit_batch(query_path: Path, metadata_path: Path | None = None) -> dict[str, Any]:
    queries = _load_jsonl(query_path)
    metadata = _load_jsonl(metadata_path) if metadata_path else []
    errors: list[str] = []
    ids: list[str] = []
    hashes: list[str] = []
    steps: Counter[str] = Counter()
    tests: Counter[str] = Counter()
    for index, query in enumerate(queries, start=1):
        try:
            validate_query(query)
        except QuerySchemaError as exc:
            errors.append(f"query line {index}: {exc}")
            continue
        ids.append(str(query["problem_id"]))
        hashes.append(_record_hash(query))
        steps[str(len(query["sub_steps"]))] += 1
        tests["/".join(str(len(step["test_cases"])) for step in query["sub_steps"])] += 1

    metadata_by_id = {str(item.get("query_id")): item for item in metadata}
    component = Counter()
    domains = Counter()
    subdomains = Counter()
    modes = Counter()
    scenarios = Counter()
    diagnostics = Counter()
    variants = Counter()
    signatures = Counter()
    missing_metadata = []
    for query_id in ids:
        item = metadata_by_id.get(query_id)
        if item is None:
            missing_metadata.append(query_id)
            continue
        component[str(item.get("component"))] += 1
        domains[str(item.get("domain"))] += 1
        subdomains[str(item.get("subdomain"))] += 1
        modes[str(item.get("method_mode"))] += 1
        scenarios[str(item.get("scenario"))] += 1
        diagnostics[str(item.get("diagnostic"))] += 1
        variants[str(item.get("composition_variant"))] += 1
        signatures[str(item.get("normalized_signature"))] += 1

    duplicate_ids = len(ids) - len(set(ids))
    duplicate_records = len(hashes) - len(set(hashes))
    duplicate_signatures = sum(count - 1 for count in signatures.values() if count > 1)
    errors.extend(f"duplicate problem_id count: {duplicate_ids}" for _ in range(1 if duplicate_ids else 0))
    errors.extend(f"duplicate complete-record count: {duplicate_records}" for _ in range(1 if duplicate_records else 0))
    errors.extend(f"missing metadata count: {len(missing_metadata)}" for _ in range(1 if missing_metadata else 0))

    return {
        "status": "ok" if not errors else "failed",
        "query_file": str(query_path),
        "metadata_file": str(metadata_path) if metadata_path else None,
        "query_count": len(queries),
        "valid_query_count": len(ids),
        "unique_problem_ids": len(set(ids)),
        "unique_complete_records": len(set(hashes)),
        "unique_normalized_signatures": len(signatures),
        "duplicate_problem_ids": duplicate_ids,
        "duplicate_complete_records": duplicate_records,
        "duplicate_normalized_signatures": duplicate_signatures,
        "normalized_signature_warning": (
            "some composition signatures repeat; exact-record uniqueness is still checked"
            if duplicate_signatures
            else None
        ),
        "missing_metadata": len(missing_metadata),
        "step_count_distribution": dict(sorted(steps.items(), key=lambda item: int(item[0]))),
        "test_case_layout_distribution": dict(sorted(tests.items())),
        "component_distribution": dict(sorted(component.items())),
        "domain_distribution": dict(sorted(domains.items())),
        "subdomain_distribution": dict(sorted(subdomains.items())),
        "method_distribution": dict(sorted(modes.items())),
        "scenario_distribution": dict(sorted(scenarios.items())),
        "diagnostic_distribution": dict(sorted(diagnostics.items())),
        "composition_variant_distribution": dict(sorted(variants.items())),
        "errors": errors,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query_file", type=Path)
    parser.add_argument("--metadata-file", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = audit_batch(args.query_file, args.metadata_file)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
