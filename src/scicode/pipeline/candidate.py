"""Candidate manifests and fail-closed SciCode task validation."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


class CandidateValidationError(ValueError):
    """Raised when a candidate cannot be safely handed to a solver or evaluator."""


TOP_LEVEL_FIELDS = frozenset(
    {
        "problem_name",
        "problem_id",
        "problem_description_main",
        "problem_io",
        "required_dependencies",
        "sub_steps",
        "general_tests",
        "problem_background_main",
    }
)
STEP_FIELDS = frozenset(
    {
        "step_number",
        "step_description_prompt",
        "function_header",
        "test_cases",
        "return_line",
        "step_background",
    }
)
PROVENANCE_FIELDS = frozenset(
    {
        "source_url",
        "repository_url",
        "source_commit",
        "license",
        "paper_url",
        "paper_doi",
        "source_files",
        "source_fingerprint",
    }
)
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_FORBIDDEN_PUBLIC_MARKERS = (
    "SciCode1/SciCode",
    "scicode-bench.github.io",
    "eval/data/test_data.h5",
    "test_data.h5",
    "targets.h5",
    "ground_truth_code",
)
_FORBIDDEN_PAYLOAD_MARKERS = (
    "test_data.h5",
    "targets.h5",
    "ground_truth_code",
    "target_value",
    "private_test",
    "oracle/",
    "reference/",
)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(root: str | Path) -> str:
    """Hash file names and bytes in stable order, excluding cache files."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise CandidateValidationError(f"directory does not exist: {root}")
    digest = hashlib.sha256()
    ignored_parts = {"__pycache__", ".git", ".pytest_cache", ".ruff_cache"}
    files = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and not ignored_parts.intersection(path.parts)
        and not path.name.endswith((".pyc", ".pyo"))
    ]
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        raise CandidateValidationError(f"JSONL file does not exist: {source}")
    rows: list[dict[str, Any]] = []
    with source.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CandidateValidationError(
                    f"invalid JSON at {source}:{line_number}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise CandidateValidationError(
                    f"JSONL row {line_number} is not an object: {source}"
                )
            rows.append(value)
    if not rows:
        raise CandidateValidationError(f"JSONL contains no records: {source}")
    return rows


def _require_text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CandidateValidationError(f"{label} must be a non-empty string")


def validate_rows(rows: Iterable[Mapping[str, Any]], source: str | Path) -> list[dict[str, Any]]:
    """Validate the official SciCode record shape without rewriting it."""
    source = Path(source)
    normalized = [dict(row) for row in rows]
    problem_ids: set[str] = set()
    for row_number, row in enumerate(normalized, start=1):
        missing = TOP_LEVEL_FIELDS - row.keys()
        if missing:
            raise CandidateValidationError(
                f"row {row_number} in {source} is missing: {', '.join(sorted(missing))}"
            )
        problem_id = str(row["problem_id"])
        if not _ID_PATTERN.fullmatch(problem_id):
            raise CandidateValidationError(
                f"problem_id {problem_id!r} is not a stable lowercase candidate id"
            )
        if problem_id in problem_ids:
            raise CandidateValidationError(f"duplicate problem_id: {problem_id}")
        problem_ids.add(problem_id)
        _require_text(row["problem_name"], f"{problem_id}.problem_name")
        _require_text(row["required_dependencies"], f"{problem_id}.required_dependencies")
        if not isinstance(row["sub_steps"], list) or not row["sub_steps"]:
            raise CandidateValidationError(f"{problem_id}.sub_steps must be a non-empty list")
        step_ids: set[str] = set()
        for step_index, raw_step in enumerate(row["sub_steps"], start=1):
            if not isinstance(raw_step, Mapping):
                raise CandidateValidationError(
                    f"{problem_id} sub_steps[{step_index}] is not an object"
                )
            step = dict(raw_step)
            missing_step = STEP_FIELDS - step.keys()
            if missing_step:
                raise CandidateValidationError(
                    f"{problem_id} step {step_index} is missing: "
                    + ", ".join(sorted(missing_step))
                )
            step_id = str(step["step_number"])
            if not step_id or step_id in step_ids:
                raise CandidateValidationError(f"duplicate step_number in {problem_id}: {step_id!r}")
            step_ids.add(step_id)
            for field in ("step_description_prompt", "function_header", "return_line"):
                _require_text(step[field], f"{problem_id}.{step_id}.{field}")
            if not isinstance(step["test_cases"], list) or not step["test_cases"]:
                raise CandidateValidationError(f"{problem_id}.{step_id}.test_cases must be non-empty")
            if not all(isinstance(case, str) and case.strip() for case in step["test_cases"]):
                raise CandidateValidationError(f"{problem_id}.{step_id}.test_cases must contain text")
    return normalized


def visible_projection(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return the exact canonical fields that can affect prompt rendering."""
    return [
        {key: row[key] for key in TOP_LEVEL_FIELDS}
        | {
            "sub_steps": [
                {key: step[key] for key in STEP_FIELDS}
                for step in row["sub_steps"]
            ]
        }
        for row in rows
    ]


def canonical_json_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CandidateManifest:
    candidate_id: str
    revision: str
    root: str
    problem_file: str
    oracle_file: str
    problem_count: int
    subproblem_count: int
    canonical_record_sha256: str
    visible_contract_sha256: str
    solver_payload_sha256: str
    oracle_sha256: str
    provenance_sha256: str
    prompt_profile: str
    mode: str
    created_at: str
    source_url: str = ""
    source_commit: str = ""
    license: str = ""
    paper_url: str = ""
    paper_doi: str = ""
    source_fingerprint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_metadata(root: Path) -> tuple[str, str, str]:
    path = root / "candidate.json"
    if not path.is_file():
        return root.name, "r1", "background"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CandidateValidationError(f"invalid candidate.json: {path}") from exc
    if not isinstance(value, Mapping):
        raise CandidateValidationError("candidate.json must contain an object")
    candidate_id = str(value.get("candidate_id", root.name))
    revision = str(value.get("revision", "r1"))
    if not _ID_PATTERN.fullmatch(candidate_id):
        raise CandidateValidationError(f"invalid candidate_id: {candidate_id!r}")
    if not re.fullmatch(r"r[0-9]+", revision):
        raise CandidateValidationError(f"revision must look like r1, r2, ...: {revision!r}")
    prompt_profile = str(value.get("prompt_profile", "background"))
    if prompt_profile not in {"background", "without_background"}:
        raise CandidateValidationError(f"invalid prompt_profile: {prompt_profile!r}")
    if str(value.get("mode", "strict")) != "strict":
        raise CandidateValidationError("candidate mode must be strict")
    return candidate_id, revision, prompt_profile


def _validate_provenance(path: Path) -> tuple[str, dict[str, Any]]:
    if not path.is_file():
        raise CandidateValidationError(f"missing provenance file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CandidateValidationError(f"invalid provenance JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise CandidateValidationError("provenance must be an object")
    source_url = value.get("source_url") or value.get("repository_url")
    for field in ("source_commit", "license", "source_fingerprint"):
        _require_text(value.get(field), f"provenance.{field}")
    _require_text(source_url, "provenance.source_url or repository_url")
    if not isinstance(value.get("source_files"), list) or not value["source_files"]:
        raise CandidateValidationError("provenance.source_files must be a non-empty list")
    if not (value.get("paper_url") or value.get("paper_doi")):
        raise CandidateValidationError("provenance requires paper_url or paper_doi")
    return sha256_file(path), dict(value)


def _scan_public_text(root: Path) -> None:
    public = root / "public"
    for path in public.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl", ".md", ".txt", ".py"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in _FORBIDDEN_PUBLIC_MARKERS:
            if marker in text:
                raise CandidateValidationError(
                    f"forbidden official/private marker {marker!r} in solver-visible file {path}"
                )


def _validate_solver_payload(path: Path) -> str:
    if not path.is_dir():
        raise CandidateValidationError(f"missing solver payload: {path}")
    forbidden_names = {
        "problem.jsonl",
        "targets.h5",
        "test_data.h5",
        "reference",
        "oracle",
        "author",
        "runs",
    }
    for item in path.rglob("*"):
        relative = item.relative_to(path)
        if any(part.lower() in forbidden_names for part in relative.parts):
            raise CandidateValidationError(f"private file is solver-visible: {item}")
        if item.is_file() and item.suffix.lower() in {".json", ".jsonl", ".md", ".txt", ".py"}:
            text = item.read_text(encoding="utf-8", errors="replace")
            for marker in _FORBIDDEN_PAYLOAD_MARKERS:
                if marker in text:
                    raise CandidateValidationError(
                        f"forbidden private marker {marker!r} in solver payload {item}"
                    )
    return sha256_tree(path)


def validate_oracle_layout(
    oracle_file: str | Path, rows: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """Check the candidate HDF5 group/count contract without reading targets."""
    try:
        import h5py
    except ImportError as exc:  # pragma: no cover - optional on authoring hosts
        raise CandidateValidationError("h5py is required to validate the candidate oracle") from exc
    oracle_file = Path(oracle_file)
    expected: list[tuple[str, int]] = []
    for row in rows:
        problem_id = str(row["problem_id"])
        for index, step in enumerate(row["sub_steps"]):
            if (problem_id, index) in {("13", 5), ("62", 0), ("76", 2)}:
                continue
            expected.append((str(step["step_number"]), len(step["test_cases"])))
    missing: list[str] = []
    mismatched: list[dict[str, Any]] = []
    with h5py.File(oracle_file, "r") as handle:
        for step_id, test_count in expected:
            if step_id not in handle or not isinstance(handle[step_id], h5py.Group):
                missing.append(step_id)
                continue
            group = handle[step_id]
            actual = sum(key.startswith("test") for key in group.keys())
            if actual != test_count or any(f"test{i}" not in group for i in range(1, test_count + 1)):
                mismatched.append(
                    {"step_number": step_id, "expected": test_count, "actual": actual}
                )
    if missing or mismatched:
        raise CandidateValidationError(
            f"oracle layout mismatch; missing={missing}, mismatched={mismatched}"
        )
    return {"status": "ok", "steps": len(expected), "tests": sum(count for _, count in expected)}


def load_candidate(root: str | Path, *, prompt_profile: str | None = None) -> CandidateManifest:
    """Validate a candidate and return immutable hashes for downstream runs."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise CandidateValidationError(f"candidate directory does not exist: {root}")
    public = root / "public"
    problem_file = public / "problem.jsonl"
    oracle_file = root / "oracle" / "targets.h5"
    payload = public / "solver_payload"
    if public not in root.parents and public != root / "public":
        raise CandidateValidationError("invalid candidate public path")
    rows = validate_rows(read_jsonl(problem_file), problem_file)
    _scan_public_text(root)
    provenance_sha, provenance = _validate_provenance(root / "source_notes" / "provenance.json")
    if not oracle_file.is_file():
        raise CandidateValidationError(f"missing candidate oracle: {oracle_file}")
    if oracle_file.resolve().is_relative_to(public.resolve()):
        raise CandidateValidationError("candidate oracle must remain private")
    validate_oracle_layout(oracle_file, rows)
    payload_sha = _validate_solver_payload(payload)
    requested_profile = prompt_profile
    candidate_id, revision, metadata_profile = _load_metadata(root)
    if requested_profile is not None and requested_profile != metadata_profile:
        raise CandidateValidationError(
            f"prompt profile mismatch: candidate={metadata_profile!r}, requested={requested_profile!r}"
        )
    prompt_profile = requested_profile or metadata_profile
    if prompt_profile not in {"background", "without_background"}:
        raise CandidateValidationError(f"invalid prompt_profile: {prompt_profile!r}")
    visible = visible_projection(rows)
    subproblem_count = sum(
        sum(
            (str(row["problem_id"]), index)
            not in {("13", 5), ("62", 0), ("76", 2)}
            for index, _ in enumerate(row["sub_steps"])
        )
        for row in rows
    )
    return CandidateManifest(
        candidate_id=candidate_id,
        revision=revision,
        root=str(root),
        problem_file=str(problem_file),
        oracle_file=str(oracle_file),
        problem_count=len(rows),
        subproblem_count=subproblem_count,
        canonical_record_sha256=sha256_file(problem_file),
        visible_contract_sha256=canonical_json_hash(visible),
        solver_payload_sha256=payload_sha,
        oracle_sha256=sha256_file(oracle_file),
        provenance_sha256=provenance_sha,
        prompt_profile=prompt_profile,
        mode="strict",
        created_at=datetime.now(timezone.utc).isoformat(),
        source_url=str(provenance.get("source_url") or provenance.get("repository_url")),
        source_commit=str(provenance["source_commit"]),
        license=str(provenance["license"]),
        paper_url=str(provenance.get("paper_url") or ""),
        paper_doi=str(provenance.get("paper_doi") or ""),
        source_fingerprint=str(provenance["source_fingerprint"]),
    )


def write_manifest(path: str | Path, manifest: CandidateManifest) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
