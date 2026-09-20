"""Human calibration packets and fail-closed release gating for reasoning SFT."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from .grade import current_grades
from .schema import canonical_hash, validate_grade, validate_task, validate_trace
from .verify import VERIFICATION_POLICY

AUDIT_SCHEMA = "scicode-human-audit-v1"
CALIBRATION_SCHEMA = "scicode-human-calibration-v1"
RELEASE_POLICY_SCHEMA = "scicode-quality-release-policy-v1"
RELEASE_SCHEMA = "scicode-reasoning-release-v1"
CALIBRATION_CHECKS = {
    "reviewed_items",
    "reviewers",
    "overlap_items",
    "task_valid_rate",
    "selection_precision",
    "selection_recall",
    "pair_agreement",
    "critical_error_rate",
    "mean_scientific_depth",
    "mean_trace_value",
}

DEFAULT_RELEASE_POLICY = {
    "schema_version": RELEASE_POLICY_SCHEMA,
    "eligible_difficulty_bands": ["medium", "hard"],
    "min_distinct_judges": 2,
    "min_judge_trainable_fraction": 0.67,
    "min_accepted_verifiers": 1,
    "require_role_separation": True,
}


class QualityError(ValueError):
    """Quality artifacts are inconsistent or a release gate is unsatisfied."""


def _jsonl(path: Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise QualityError(f"{path}:{number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise QualityError(f"{path}:{number}: row must be an object")
            rows.append(row)
    return rows


def _atomic_json(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _atomic_jsonl(path: Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def _population_hash(traces: list[dict]) -> str:
    return canonical_hash(sorted(trace["trace_id"] for trace in traces))


def create_audit_packet(
    tasks_path: Path,
    traces_path: Path,
    grades_path: Path,
    verifications_path: Path,
    difficulty_path: Path,
    output_path: Path,
    *,
    key_path: Path | None = None,
    sample_size: int = 50,
    seed: str = "scicode-quality-v1",
) -> dict:
    """Create a deterministic stratified packet containing positives and negatives."""
    if sample_size < 1:
        raise QualityError("sample_size must be positive")
    tasks = {task["task_id"]: validate_task(task) for task in _jsonl(Path(tasks_path))}
    traces = [validate_trace(trace) for trace in _jsonl(Path(traces_path))]
    grades_by_trace = defaultdict(list)
    for grade in current_grades(_jsonl(Path(grades_path))):
        grades_by_trace[grade.get("trace_id")].append(grade)
    verifications_by_hash = defaultdict(list)
    for row in _jsonl(Path(verifications_path)):
        verifications_by_hash[row.get("task_hash")].append(row)
    difficulty_by_hash = {
        row.get("task_hash"): row for row in _jsonl(Path(difficulty_path))
    }
    population_hash = _population_hash(traces)
    candidates = []
    for trace in traces:
        task = tasks.get(trace["task_id"])
        if task is None:
            raise QualityError(f"no task for trace {trace['trace_id']}")
        grades = grades_by_trace.get(trace["trace_id"], [])
        for grade in grades:
            validate_grade(grade, trace)
        difficulty = difficulty_by_hash.get(trace["task_hash"])
        band = (difficulty or {}).get("band", "missing")
        source_verified = any(
            row.get("accepted") is True
            and row.get("policy_version") == VERIFICATION_POLICY
            for row in verifications_by_hash.get(trace["task_hash"], [])
        )
        auto_trainable = source_verified and any(
            grade.get("trainable") is True for grade in grades
        )
        review_id = canonical_hash(
            {"trace_id": trace["trace_id"], "population_hash": population_hash}
        )
        blinded_task = json.loads(json.dumps(task))
        blinded_task.pop("authoring", None)
        blinded_trace = json.loads(json.dumps(trace))
        for field in ("model", "outcome", "usage", "timing_sec", "provenance"):
            blinded_trace.pop(field, None)
        row = {
            "schema_version": AUDIT_SCHEMA,
            "review_id": review_id,
            "population_hash": population_hash,
            "blinded": True,
            "task": blinded_task,
            "trace": blinded_trace,
            "human_reviews": [],
            "review_contract": {
                "reviewer": "nonempty stable reviewer ID",
                "task_valid": "boolean",
                "scientific_depth": "integer 0..4",
                "trace_training_value": "integer 0..4",
                "critical_error": "boolean",
                "approve_trace": "boolean",
                "notes": "specific evidence; may be empty",
            },
        }
        stratum = {
            "archetype": task["archetype"],
            "difficulty_band": band,
            "automatic_trainable": auto_trainable,
        }
        answer_key = {
            "schema_version": AUDIT_SCHEMA,
            "review_id": review_id,
            "population_hash": population_hash,
            "trace_id": trace["trace_id"],
            "task_hash": trace["task_hash"],
            "stratum": stratum,
            "automatic_trainable": auto_trainable,
            "automated": {
                "grades": grades,
                "verifications": verifications_by_hash.get(trace["task_hash"], []),
                "difficulty": difficulty,
            },
        }
        order = canonical_hash({"seed": seed, "review_id": review_id})
        candidates.append((stratum, order, row, answer_key))

    strata = defaultdict(list)
    for stratum, order, row, answer_key in candidates:
        stratum_key = (
            stratum["archetype"],
            stratum["difficulty_band"],
            stratum["automatic_trainable"],
        )
        strata[stratum_key].append((order, row, answer_key))
    for values in strata.values():
        values.sort(key=lambda item: item[0])
    selected = []
    keys = sorted(strata, key=str)
    while len(selected) < min(sample_size, len(candidates)):
        progressed = False
        for key in keys:
            values = strata[key]
            if values and len(selected) < sample_size:
                _order, row, answer_key = values.pop(0)
                selected.append((row, answer_key))
                progressed = True
        if not progressed:
            break
    packet_rows = [row for row, _key in selected]
    key_rows = [key for _row, key in selected]
    _atomic_jsonl(output_path, packet_rows)
    key_path = key_path or Path(output_path).with_name(Path(output_path).name + ".key.jsonl")
    _atomic_jsonl(key_path, key_rows)
    return {
        "schema_version": AUDIT_SCHEMA,
        "population": len(traces),
        "population_hash": population_hash,
        "sampled": len(selected),
        "strata": len(keys),
        "seed": seed,
        "output": str(output_path),
        "answer_key": str(key_path),
    }


def _validated_review(review: object, review_id: str) -> dict:
    if not isinstance(review, dict):
        raise QualityError(f"review {review_id} must be an object")
    reviewer = review.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise QualityError(f"review {review_id} has no reviewer")
    for field in ("task_valid", "critical_error", "approve_trace"):
        if not isinstance(review.get(field), bool):
            raise QualityError(f"review {review_id}.{field} must be boolean")
    for field in ("scientific_depth", "trace_training_value"):
        score = review.get(field)
        if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 4:
            raise QualityError(f"review {review_id}.{field} must be 0..4")
    notes = review.get("notes", "")
    if not isinstance(notes, str):
        raise QualityError(f"review {review_id}.notes must be a string")
    return review


def calibrate_reviews(
    packet_path: Path,
    output_path: Path,
    *,
    key_path: Path | None = None,
    min_reviewed_items: int = 20,
    min_reviewers: int = 2,
    min_overlap_items: int = 5,
    min_task_valid_rate: float = 0.8,
    min_selection_precision: float = 0.85,
    min_selection_recall: float = 0.7,
    min_pair_agreement: float = 0.7,
    max_critical_error_rate: float = 0.1,
    min_mean_scientific_depth: float = 3.0,
    min_mean_trace_value: float = 3.0,
) -> dict:
    """Measure human agreement and automated-selection calibration."""
    rows = _jsonl(Path(packet_path))
    key_path = key_path or Path(packet_path).with_name(
        Path(packet_path).name + ".key.jsonl"
    )
    key_rows = _jsonl(key_path)
    keys = {row.get("review_id"): row for row in key_rows}
    if len(keys) != len(key_rows):
        raise QualityError("audit answer key has duplicate review IDs")
    packet_ids = {row.get("review_id") for row in rows}
    if packet_ids != set(keys):
        raise QualityError("audit packet and answer key review IDs do not match")
    population_hashes = {row.get("population_hash") for row in rows}
    if len(population_hashes) != 1:
        raise QualityError("audit packet contains mixed population hashes")
    key_population_hashes = {row.get("population_hash") for row in key_rows}
    if key_population_hashes != population_hashes:
        raise QualityError("audit packet and answer key populations do not match")
    completed = []
    item_reviews = {}
    predictions = {}
    for row in rows:
        review_id = row.get("review_id")
        if not isinstance(review_id, str):
            raise QualityError("audit row has no review_id")
        answer_key = keys.get(review_id)
        if answer_key is None:
            raise QualityError(f"audit answer key is missing review {review_id}")
        predictions[review_id] = bool(answer_key.get("automatic_trainable"))
        reviews = [
            _validated_review(review, review_id)
            for review in row.get("human_reviews") or []
        ]
        reviewer_ids = [review["reviewer"] for review in reviews]
        if len(reviewer_ids) != len(set(reviewer_ids)):
            raise QualityError(f"review {review_id} repeats a reviewer ID")
        if reviews:
            item_reviews[review_id] = reviews
            completed.extend((review_id, review) for review in reviews)
    reviewers = sorted({review["reviewer"] for _item, review in completed})
    consensus = {}
    overlap = 0
    agreements = []
    for review_id, reviews in item_reviews.items():
        if len(reviews) >= 2:
            overlap += 1
            reference = (
                reviews[0]["task_valid"],
                reviews[0]["critical_error"],
                reviews[0]["approve_trace"],
            )
            agreements.extend(
                reference
                == (
                    review["task_valid"],
                    review["critical_error"],
                    review["approve_trace"],
                )
                for review in reviews[1:]
            )
        consensus[review_id] = {
            "task_valid": sum(review["task_valid"] for review in reviews)
            >= math_ceil_half(len(reviews)),
            "critical_error": sum(review["critical_error"] for review in reviews)
            >= math_ceil_half(len(reviews)),
            "approve_trace": sum(review["approve_trace"] for review in reviews)
            >= math_ceil_half(len(reviews)),
        }
    reviewed_items = len(consensus)
    task_valid_rate = (
        sum(item["task_valid"] for item in consensus.values()) / reviewed_items
        if reviewed_items
        else None
    )
    critical_error_rate = (
        sum(item["critical_error"] for item in consensus.values()) / reviewed_items
        if reviewed_items
        else None
    )
    tp = fp = fn = tn = 0
    for review_id, human in consensus.items():
        predicted = predictions[review_id]
        actual = human["approve_trace"]
        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
        elif not predicted and actual:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    pair_agreement = sum(agreements) / len(agreements) if agreements else None
    mean_scientific_depth = (
        sum(review["scientific_depth"] for _item, review in completed) / len(completed)
        if completed
        else None
    )
    mean_trace_value = (
        sum(review["trace_training_value"] for _item, review in completed)
        / len(completed)
        if completed
        else None
    )
    checks = {
        "reviewed_items": reviewed_items >= min_reviewed_items,
        "reviewers": len(reviewers) >= min_reviewers,
        "overlap_items": overlap >= min_overlap_items,
        "task_valid_rate": task_valid_rate is not None
        and task_valid_rate >= min_task_valid_rate,
        "selection_precision": precision is not None
        and precision >= min_selection_precision,
        "selection_recall": recall is not None and recall >= min_selection_recall,
        "pair_agreement": pair_agreement is not None
        and pair_agreement >= min_pair_agreement,
        "critical_error_rate": critical_error_rate is not None
        and critical_error_rate <= max_critical_error_rate,
        "mean_scientific_depth": mean_scientific_depth is not None
        and mean_scientific_depth >= min_mean_scientific_depth,
        "mean_trace_value": mean_trace_value is not None
        and mean_trace_value >= min_mean_trace_value,
    }
    calibration = {
        "schema_version": CALIBRATION_SCHEMA,
        "release_approved": all(checks.values()),
        "population_hash": next(iter(population_hashes), None),
        "packet_hash": canonical_hash(rows),
        "answer_key_hash": canonical_hash(key_rows),
        "reviewed_items": reviewed_items,
        "completed_reviews": len(completed),
        "reviewers": reviewers,
        "overlap_items": overlap,
        "metrics": {
            "task_valid_rate": task_valid_rate,
            "critical_error_rate": critical_error_rate,
            "selection_precision": precision,
            "selection_recall": recall,
            "pair_agreement": pair_agreement,
            "mean_scientific_depth": mean_scientific_depth,
            "mean_trace_value": mean_trace_value,
            "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        },
        "thresholds": {
            "min_reviewed_items": min_reviewed_items,
            "min_reviewers": min_reviewers,
            "min_overlap_items": min_overlap_items,
            "min_task_valid_rate": min_task_valid_rate,
            "min_selection_precision": min_selection_precision,
            "min_selection_recall": min_selection_recall,
            "min_pair_agreement": min_pair_agreement,
            "max_critical_error_rate": max_critical_error_rate,
            "min_mean_scientific_depth": min_mean_scientific_depth,
            "min_mean_trace_value": min_mean_trace_value,
        },
        "checks": checks,
    }
    _atomic_json(output_path, calibration)
    return calibration


def math_ceil_half(value: int) -> int:
    return value // 2 + 1


def load_release_policy(path: Path | None = None) -> dict:
    policy = dict(DEFAULT_RELEASE_POLICY)
    if path is not None:
        try:
            supplied = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise QualityError(f"invalid release policy JSON: {exc}") from exc
        if not isinstance(supplied, dict):
            raise QualityError("release policy must be an object")
        policy.update(supplied)
    if policy.get("schema_version") != RELEASE_POLICY_SCHEMA:
        raise QualityError(f"release policy schema must be {RELEASE_POLICY_SCHEMA}")
    bands = policy.get("eligible_difficulty_bands")
    if (
        not isinstance(bands, list)
        or not bands
        or any(not isinstance(item, str) for item in bands)
    ):
        raise QualityError("eligible_difficulty_bands must be a nonempty string list")
    if int(policy.get("min_distinct_judges", 0)) < 1:
        raise QualityError("min_distinct_judges must be positive")
    fraction = policy.get("min_judge_trainable_fraction")
    if not isinstance(fraction, (int, float)) or not 0 < fraction <= 1:
        raise QualityError("min_judge_trainable_fraction must be in (0,1]")
    if int(policy.get("min_accepted_verifiers", 0)) < 1:
        raise QualityError("min_accepted_verifiers must be positive")
    return policy


def release_sft(
    tasks_path: Path,
    traces_path: Path,
    candidate_sft_path: Path,
    grades_path: Path,
    verifications_path: Path,
    difficulty_path: Path,
    calibration_path: Path,
    output_path: Path,
    *,
    policy_path: Path | None = None,
    report_path: Path | None = None,
) -> dict:
    """Release only calibrated, independently checked, nontrivial SFT rows."""
    tasks = {task["task_id"]: validate_task(task) for task in _jsonl(Path(tasks_path))}
    traces = {trace["trace_id"]: validate_trace(trace) for trace in _jsonl(traces_path)}
    population_hash = _population_hash(list(traces.values()))
    calibration = json.loads(Path(calibration_path).read_text(encoding="utf-8"))
    if calibration.get("schema_version") != CALIBRATION_SCHEMA:
        raise QualityError("human calibration schema is invalid")
    if not calibration.get("release_approved"):
        raise QualityError("human calibration has not approved release")
    checks = calibration.get("checks")
    if (
        not isinstance(checks, dict)
        or not CALIBRATION_CHECKS.issubset(checks)
        or not all(checks.get(name) is True for name in CALIBRATION_CHECKS)
    ):
        raise QualityError("human calibration does not contain all passing checks")
    if calibration.get("population_hash") != population_hash:
        raise QualityError("human calibration belongs to another trace population")
    policy = load_release_policy(policy_path)
    grades_by_trace = defaultdict(list)
    for grade in current_grades(_jsonl(Path(grades_path))):
        grades_by_trace[grade.get("trace_id")].append(grade)
    verifications_by_hash = defaultdict(list)
    for row in _jsonl(Path(verifications_path)):
        verifications_by_hash[row.get("task_hash")].append(row)
    difficulty_by_hash = {
        row.get("task_hash"): row for row in _jsonl(Path(difficulty_path))
    }
    candidate_rows = _jsonl(Path(candidate_sft_path))
    released = []
    rejected = []
    for row in candidate_rows:
        trace_id = row.get("trace_id")
        trace = traces.get(trace_id)
        reasons = []
        if trace is None:
            raise QualityError(f"candidate SFT references unknown trace {trace_id}")
        task = tasks.get(trace["task_id"])
        if task is None:
            raise QualityError(f"trace {trace_id} references unknown task")
        grades = grades_by_trace.get(trace_id, [])
        for grade in grades:
            validate_grade(grade, trace)
        judge_models = {
            (grade.get("judge") or {}).get("model")
            for grade in grades
            if (grade.get("judge") or {}).get("model")
        }
        independent_judges = set(judge_models)
        if policy.get("require_role_separation", True):
            independent_judges.discard(trace["model"])
        trainable_judges = {
            (grade.get("judge") or {}).get("model")
            for grade in grades
            if grade.get("trainable") is True
            and (grade.get("judge") or {}).get("model") in independent_judges
        }
        if len(independent_judges) < int(policy["min_distinct_judges"]):
            reasons.append("insufficient_independent_judges")
        consensus = (
            len(trainable_judges) / len(independent_judges)
            if independent_judges
            else 0.0
        )
        if consensus < float(policy["min_judge_trainable_fraction"]):
            reasons.append("judge_consensus_below_threshold")
        author_model = (task.get("authoring") or {}).get("model")
        accepted_verifiers = {
            (verification.get("verifier") or {}).get("model")
            for verification in verifications_by_hash.get(trace["task_hash"], [])
            if verification.get("accepted") is True
            and verification.get("policy_version") == VERIFICATION_POLICY
        }
        accepted_verifiers.discard(None)
        if policy.get("require_role_separation", True) and author_model:
            accepted_verifiers.discard(author_model)
        if len(accepted_verifiers) < int(policy["min_accepted_verifiers"]):
            reasons.append("insufficient_independent_verifiers")
        difficulty = difficulty_by_hash.get(trace["task_hash"])
        if not difficulty or not difficulty.get("calibrated"):
            reasons.append("difficulty_uncalibrated")
        elif difficulty.get("band") not in policy["eligible_difficulty_bands"]:
            reasons.append(f"difficulty_band:{difficulty.get('band')}")
        if reasons:
            rejected.append({"trace_id": trace_id, "reasons": reasons})
            continue
        released_row = dict(row)
        released_row["quality_release"] = {
            "schema_version": RELEASE_SCHEMA,
            "policy_hash": canonical_hash(policy),
            "calibration_hash": canonical_hash(calibration),
            "difficulty": difficulty,
            "judge_models": sorted(independent_judges),
            "judge_trainable_fraction": round(consensus, 6),
            "verifier_models": sorted(accepted_verifiers),
        }
        released.append(released_row)
    _atomic_jsonl(output_path, released)
    rejection_path = Path(output_path).with_suffix(".rejections.jsonl")
    _atomic_jsonl(rejection_path, rejected)
    report = {
        "schema_version": RELEASE_SCHEMA,
        "population_hash": population_hash,
        "candidate_rows": len(candidate_rows),
        "released": len(released),
        "rejected": len(rejected),
        "rejection_reasons": dict(
            sorted(
                Counter(reason for row in rejected for reason in row["reasons"]).items()
            )
        ),
        "policy": policy,
        "policy_hash": canonical_hash(policy),
        "calibration_hash": canonical_hash(calibration),
        "output": str(output_path),
        "rejections": str(rejection_path),
    }
    report_path = report_path or Path(output_path).with_suffix(".report.json")
    _atomic_json(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit")
    audit.add_argument("--tasks", type=Path, required=True)
    audit.add_argument("--traces", type=Path, required=True)
    audit.add_argument("--grades", type=Path, required=True)
    audit.add_argument("--verifications", type=Path, required=True)
    audit.add_argument("--difficulty", type=Path, required=True)
    audit.add_argument("--out", type=Path, required=True)
    audit.add_argument("--key", type=Path)
    audit.add_argument("--sample-size", type=int, default=50)
    audit.add_argument("--seed", default="scicode-quality-v1")

    calibrate = subparsers.add_parser("calibrate")
    calibrate.add_argument("--packet", type=Path, required=True)
    calibrate.add_argument("--out", type=Path, required=True)
    calibrate.add_argument("--key", type=Path)
    calibrate.add_argument("--min-reviewed-items", type=int, default=20)
    calibrate.add_argument("--min-reviewers", type=int, default=2)
    calibrate.add_argument("--min-overlap-items", type=int, default=5)

    release = subparsers.add_parser("release")
    release.add_argument("--tasks", type=Path, required=True)
    release.add_argument("--traces", type=Path, required=True)
    release.add_argument("--candidate-sft", type=Path, required=True)
    release.add_argument("--grades", type=Path, required=True)
    release.add_argument("--verifications", type=Path, required=True)
    release.add_argument("--difficulty", type=Path, required=True)
    release.add_argument("--calibration", type=Path, required=True)
    release.add_argument("--policy", type=Path)
    release.add_argument("--out", type=Path, required=True)
    release.add_argument("--report", type=Path)

    args = parser.parse_args()
    if args.command == "audit":
        result = create_audit_packet(
            args.tasks,
            args.traces,
            args.grades,
            args.verifications,
            args.difficulty,
            args.out,
            key_path=args.key,
            sample_size=args.sample_size,
            seed=args.seed,
        )
    elif args.command == "calibrate":
        result = calibrate_reviews(
            args.packet,
            args.out,
            key_path=args.key,
            min_reviewed_items=args.min_reviewed_items,
            min_reviewers=args.min_reviewers,
            min_overlap_items=args.min_overlap_items,
        )
    else:
        result = release_sft(
            args.tasks,
            args.traces,
            args.candidate_sft,
            args.grades,
            args.verifications,
            args.difficulty,
            args.calibration,
            args.out,
            policy_path=args.policy,
            report_path=args.report,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
