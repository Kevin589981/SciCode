"""Export outcome-independent, thinking-preserving canonical SFT JSONL."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from .grade import GRADE_POLICY_VERSION, GradeError, current_grades
from .preflight import PREFLIGHT_POLICY
from .schema import validate_grade, validate_task, validate_trace
from .verify import VERIFICATION_POLICY

SFT_SCHEMA = "scicode-reasoning-sft-v1"
AUTOMATIC_REVIEW_POLICY = "automatic-scientific-review-v1"


class ExportError(ValueError):
    """Trace and grade artifacts cannot be joined unambiguously."""


def _jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ExportError(f"{path}:{number}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise ExportError(f"{path}:{number}: row must be an object")
            rows.append(value)
    return rows


def _select_grades(grades: list[dict], judge_model: str | None) -> dict[str, dict]:
    grouped = defaultdict(list)
    try:
        selected_grades = current_grades(grades, judge_model)
    except GradeError as exc:
        raise ExportError(str(exc)) from exc
    for grade in selected_grades:
        grouped[grade.get("trace_id")].append(grade)
    selected = {}
    for trace_id, options in grouped.items():
        if len(options) > 1:
            raise ExportError(
                f"multiple grades for {trace_id}; select one with --judge-model"
            )
        selected[trace_id] = options[0]
    return selected


def _admitted_task_hashes(
    *,
    preflight_path: Path | None,
    verification_path: Path | None,
    preflight_model: str | None,
    verifier_model: str | None,
) -> set[str] | None:
    """Recompute the same fail-closed admission intersection used by rollout."""
    admission_sets = []
    if preflight_path is not None:
        records = _jsonl(Path(preflight_path))
        if preflight_model is not None:
            records = [
                row
                for row in records
                if (row.get("critic") or {}).get("model") == preflight_model
            ]
        records = [
            row for row in records if row.get("policy_version") == PREFLIGHT_POLICY
        ]
        admission_sets.append(
            {row.get("task_hash") for row in records if row.get("accepted") is True}
        )
    if verification_path is not None:
        records = _jsonl(Path(verification_path))
        if verifier_model is not None:
            records = [
                row
                for row in records
                if (row.get("verifier") or {}).get("model") == verifier_model
            ]
        records = [
            row
            for row in records
            if row.get("policy_version") == VERIFICATION_POLICY
        ]
        admission_sets.append(
            {row.get("task_hash") for row in records if row.get("accepted") is True}
        )
    return set.intersection(*admission_sets) if admission_sets else None


def _sft_messages(trace: dict, grade: dict, inline_thinking: bool) -> list[dict]:
    annotations = {
        item["message_index"]: item for item in grade["message_annotations"]
    }
    messages = []
    for index, original in enumerate(trace["messages"]):
        role = original["role"]
        if role != "assistant":
            messages.append(
                {"role": role, "content": original.get("content", ""), "loss": False}
            )
            continue
        annotation = annotations[index]
        reasoning = original.get("reasoning_content", "")
        content = original.get("content", "")
        if inline_thinking:
            content = f"<think>\n{reasoning}\n</think>\n{content}"
        reasoning_loss = annotation["train_reasoning"]
        content_loss = annotation["train_content"]
        messages.append(
            {
                "role": "assistant",
                "content": content,
                "reasoning_content": reasoning,
                "reasoning_loss": reasoning_loss,
                "content_loss": content_loss,
                "loss": reasoning_loss or content_loss,
                "quality": annotation["quality"],
                "quality_rationale": annotation["rationale"],
            }
        )
    return messages


def _sft_row(
    task: dict,
    trace: dict,
    grade: dict,
    *,
    inline_thinking: bool,
    automatic_review: dict | None = None,
) -> dict:
    row = {
        "schema_version": SFT_SCHEMA,
        "task_name": task["task_id"],
        "trace_id": trace["trace_id"],
        "task_hash": trace["task_hash"],
        "archetype": task["archetype"],
        "messages": _sft_messages(trace, grade, inline_thinking),
        "tools": [],
        "thinking_format": (
            "inline_and_preserved" if inline_thinking else "separate_reasoning_content"
        ),
        "outcome": trace.get("outcome"),
        "trace_quality": {
            "scores": grade["scores"],
            "rationale": grade["rationale"],
            "judge": grade["judge"],
        },
        "provenance": {
            "factory": "scicode-reasoning-factory",
            "trace": trace.get("provenance", {}),
            "grade_id": grade.get("grade_id"),
        },
    }
    if automatic_review is not None:
        row["automatic_review"] = automatic_review
    return row


def export_sft(
    tasks_path: Path,
    traces_path: Path,
    grades_path: Path,
    *,
    output_path: Path,
    report_path: Path | None = None,
    judge_model: str | None = None,
    preflight_path: Path | None = None,
    verification_path: Path | None = None,
    preflight_model: str | None = None,
    verifier_model: str | None = None,
    inline_thinking: bool = False,
) -> dict:
    """Join artifacts and select by reasoning grade, never by executable status."""
    tasks = {
        task["task_id"]: validate_task(task) for task in _jsonl(Path(tasks_path))
    }
    traces = [validate_trace(trace) for trace in _jsonl(Path(traces_path))]
    raw_grades = _jsonl(Path(grades_path))
    grades = _select_grades(raw_grades, judge_model)
    admitted_hashes = _admitted_task_hashes(
        preflight_path=preflight_path,
        verification_path=verification_path,
        preflight_model=preflight_model,
        verifier_model=verifier_model,
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = report_path or output_path.with_suffix(".report.json")
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    counts = Counter()
    archetypes = Counter()
    outcomes = Counter()
    selected_rows = []
    automatic_review = None
    if all(
        (
            judge_model,
            preflight_path,
            verification_path,
            preflight_model,
            verifier_model,
        )
    ):
        models = [preflight_model, verifier_model, judge_model]
        automatic_review = {
            "policy_version": AUTOMATIC_REVIEW_POLICY,
            "mode": "single_model" if len(set(models)) == 1 else "model_panel",
            "human_review_required": False,
            "critic": {"model": preflight_model, "policy": PREFLIGHT_POLICY},
            "verifier": {
                "model": verifier_model,
                "policy": VERIFICATION_POLICY,
            },
            "judge": {"model": judge_model, "policy": GRADE_POLICY_VERSION},
        }
    for trace in sorted(traces, key=lambda row: row["trace_id"]):
        task = tasks.get(trace["task_id"])
        if task is None:
            raise ExportError(f"no task found for trace {trace['trace_id']}")
        if admitted_hashes is not None and trace["task_hash"] not in admitted_hashes:
            counts["not_admitted"] += 1
            continue
        grade = grades.get(trace["trace_id"])
        if grade is None:
            counts["ungraded"] += 1
            continue
        validate_grade(grade, trace)
        status = (trace.get("outcome") or {}).get("status", "unknown")
        if not grade["trainable"]:
            counts["rejected"] += 1
            outcomes[f"rejected:{status}"] += 1
            archetypes[f"rejected:{task['archetype']}"] += 1
            continue
        row = _sft_row(
            task,
            trace,
            grade,
            inline_thinking=inline_thinking,
            automatic_review=automatic_review,
        )
        selected_rows.append(row)
        counts["selected"] += 1
        outcomes[f"selected:{status}"] += 1
        archetypes[f"selected:{task['archetype']}"] += 1
    with tmp_path.open("w", encoding="utf-8") as output:
        for row in selected_rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp_path.replace(output_path)
    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    report = {
        "schema_version": SFT_SCHEMA,
        "selected": counts["selected"],
        "rejected": counts["rejected"],
        "ungraded": counts["ungraded"],
        "not_admitted": counts["not_admitted"],
        "inline_thinking": inline_thinking,
        "judge_model": judge_model,
        "quality_status": (
            "automatic_single_model_reviewed"
            if automatic_review and automatic_review["mode"] == "single_model"
            else "automatic_model_panel_reviewed"
            if automatic_review
            else "unreviewed_export"
        ),
        "by_archetype": dict(sorted(archetypes.items())),
        "by_outcome": dict(sorted(outcomes.items())),
        "output": str(output_path),
        "output_sha256": digest,
    }
    report_tmp = Path(report_path).with_name(Path(report_path).name + ".tmp")
    report_tmp.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_tmp.replace(report_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--grades", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--judge-model")
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--verification", type=Path)
    parser.add_argument("--preflight-model")
    parser.add_argument("--verifier-model")
    parser.add_argument("--inline-thinking", action="store_true")
    args = parser.parse_args()
    report = export_sft(
        args.tasks,
        args.traces,
        args.grades,
        output_path=args.out,
        report_path=args.report,
        judge_model=args.judge_model,
        preflight_path=args.preflight,
        verification_path=args.verification,
        preflight_model=args.preflight_model,
        verifier_model=args.verifier_model,
        inline_thinking=args.inline_thinking,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

