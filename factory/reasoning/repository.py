"""Turn one admitted repository snapshot into a reasoning-SFT shard."""

from __future__ import annotations

import collections
import json
import math
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from ..author import llm
from ..author.mine import mine_repo
from ..envbuild.vet import detect_license
from .author import _json_objects
from .pipeline import run_pipeline
from .schema import ARCHETYPES


class RepositoryError(RuntimeError):
    """A repository cannot safely advance through the production funnel."""


PROFILE_SCHEMA = "scicode-repository-profile-v1"
REPORT_SCHEMA = "scicode-repository-run-v1"
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]{1,}")


def safe_slug(full_name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "--", full_name).strip("-.")
    return value[:160] or "repository"


def _run_git(args: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _has_commit(root: Path, commit: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def checkout_repository(
    candidate: dict,
    cache_root: Path,
    *,
    pinned_commit: str | None = None,
) -> tuple[Path, str]:
    """Clone/fetch one repository and return a detached, pinned snapshot."""
    if candidate.get("source_kind") == "scicodepile_clean_dataset":
        snapshot = candidate.get("snapshot_path")
        snapshot_hash = candidate.get("snapshot_hash")
        if not isinstance(snapshot, str) or not isinstance(snapshot_hash, str):
            raise RepositoryError("clean-dataset candidate lacks snapshot metadata")
        root = Path(snapshot).resolve()
        if not root.is_dir():
            raise RepositoryError(f"clean-dataset snapshot is unavailable: {root}")
        metadata_path = root / ".scicodepile_snapshot.json"
        if not metadata_path.is_file():
            raise RepositoryError(f"clean-dataset snapshot metadata is absent: {root}")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RepositoryError(
                f"invalid clean-dataset snapshot metadata: {exc}"
            ) from exc
        if metadata.get("snapshot_hash") != snapshot_hash:
            raise RepositoryError("clean-dataset snapshot hash disagrees with catalog")
        if pinned_commit and str(pinned_commit) != snapshot_hash:
            raise RepositoryError(
                f"snapshot resolved {snapshot_hash}, expected {pinned_commit}"
            )
        return root, snapshot_hash

    cache_root = Path(cache_root)
    destination = cache_root / safe_slug(candidate["full_name"])
    cache_root.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not (destination / ".git").is_dir():
        raise RepositoryError(f"checkout path exists but is not Git: {destination}")
    branch = candidate.get("default_branch") or "HEAD"
    desired = pinned_commit or candidate.get("head_sha")
    if destination.exists():
        ref = desired or branch
        if not desired or not _has_commit(destination, desired):
            _run_git(
                ["fetch", "--depth=1", "--no-tags", "origin", ref],
                cwd=destination,
            )
        _run_git(
            ["checkout", "--detach", desired or "FETCH_HEAD"],
            cwd=destination,
        )
    else:
        clone_args = [
            "clone",
            "--filter=blob:none",
            "--depth=1",
            "--no-tags",
        ]
        if branch != "HEAD":
            clone_args.extend(["--branch", branch])
        clone_args.extend([candidate["clone_url"], str(destination)])
        _run_git(clone_args)
        if desired and not _has_commit(destination, desired):
            _run_git(
                ["fetch", "--depth=1", "--no-tags", "origin", desired],
                cwd=destination,
            )
        _run_git(["checkout", "--detach", desired or "HEAD"], cwd=destination)
    commit = _run_git(["rev-parse", "HEAD"], cwd=destination)
    if desired and commit.casefold() != str(desired).casefold():
        raise RepositoryError(
            f"checkout resolved {commit}, expected pinned commit {desired}"
        )
    return destination, commit


def read_readme(root: Path, *, max_chars: int = 32_000) -> str:
    candidates = []
    for pattern in ("README", "README.*", "readme", "readme.*"):
        candidates.extend(Path(root).glob(pattern))
    files = sorted(
        {path for path in candidates if path.is_file()}, key=lambda p: p.name
    )
    if not files:
        return ""
    return files[0].read_text(encoding="utf-8", errors="ignore")[:max_chars]


def read_repository_context(
    root: Path, *, max_chars: int = 32_000, max_files: int = 12
) -> str:
    """Use README evidence when present, otherwise bounded cleaned-code excerpts."""
    readme = read_readme(root, max_chars=max_chars)
    if len(readme.strip()) >= 100:
        return f"SOURCE: README\n\n{readme}"
    pieces = []
    used = 0
    python_files = sorted(
        path
        for path in Path(root).rglob("*.py")
        if path.is_file()
        and not any(
            part.casefold() in {".git", ".venv", "venv", "site-packages", "__pycache__"}
            for part in path.parts
        )
    )
    for path in python_files[:max_files]:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            relative = path.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        header = f"\n\nSOURCE FILE: {relative}\n"
        excerpt = (header + text[: min(4_000, remaining)])[:remaining]
        pieces.append(excerpt)
        used += len(excerpt)
    return "".join(pieces).strip()


def screen_repository(
    candidate: dict,
    readme: str,
    *,
    chat_fn: Callable = llm.chat,
    model: str | None = None,
    timeout: int = 1200,
    max_tokens: int = 4096,
) -> dict:
    """Classify relevance and produce a fixed-schema repository summary."""
    if len(readme.strip()) < 100:
        return {
            "schema_version": PROFILE_SCHEMA,
            "relevant": False,
            "confidence": 0,
            "reason": "repository context is absent or too short for grounded screening",
            "domain_keywords": [],
            "summary": {},
            "model": model,
            "usage": {},
        }
    metadata = {
        key: candidate.get(key)
        for key in (
            "full_name",
            "description",
            "topics",
            "language",
            "stars",
            "matched_keywords",
        )
    }
    prompt = f"""Judge whether a public repository actually implements computational-science methods or workflows.

Do not accept a repository merely because it mentions scientific terms, collects
papers, contains only documentation, or wraps a generic AI/application stack.
Require evidence in the supplied README or cleaned source excerpts of implemented
numerical, simulation, modeling, scientific-analysis, or domain-computation
functionality.

METADATA:
{json.dumps(metadata, ensure_ascii=False, indent=2)}

REPOSITORY CONTEXT (README when retained, otherwise source excerpts):
{readme}

Return ONLY one JSON object:
{{
  "relevant": true,
  "confidence": 0,
  "reason": "evidence-based explanation",
  "domain_keywords": ["specific scientific or numerical concepts"],
  "summary": {{
    "project_overview": "what scientific work the repository performs",
    "methods": "major methods, models, or algorithms",
    "dependencies": "major scientific dependencies",
    "inputs_outputs": "typical scientific inputs and outputs",
    "runtime": "runtime/build expectations"
  }}
}}
Confidence is an integer 0..4. Use relevant=true only when the README supports it.
"""
    response = chat_fn(
        [{"role": "user", "content": prompt}],
        model=model,
        temperature=0.0,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    try:
        message = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RepositoryError(f"screen response has no usable message: {exc}") from exc
    objects = []
    for field in ("content", "reasoning_content"):
        objects.extend(_json_objects(message.get(field) or ""))
    raw = next((value for value in objects if "relevant" in value), None)
    if raw is None:
        raise RepositoryError("screen response has no complete relevance object")
    relevant = raw.get("relevant")
    confidence = raw.get("confidence")
    reason = raw.get("reason")
    keywords = raw.get("domain_keywords")
    summary = raw.get("summary")
    if not isinstance(relevant, bool):
        raise RepositoryError("screen relevant must be boolean")
    if (
        not isinstance(confidence, int)
        or isinstance(confidence, bool)
        or not 0 <= confidence <= 4
    ):
        raise RepositoryError("screen confidence must be an integer from 0 to 4")
    if not isinstance(reason, str) or not reason.strip():
        raise RepositoryError("screen reason must be nonempty")
    if not isinstance(keywords, list) or any(
        not isinstance(item, str) for item in keywords
    ):
        raise RepositoryError("screen domain_keywords must be a string list")
    if not isinstance(summary, dict):
        raise RepositoryError("screen summary must be an object")
    fields = (
        "project_overview",
        "methods",
        "dependencies",
        "inputs_outputs",
        "runtime",
    )
    if relevant and any(not isinstance(summary.get(name), str) for name in fields):
        raise RepositoryError("relevant screen summary is missing fixed fields")
    return {
        "schema_version": PROFILE_SCHEMA,
        "relevant": relevant,
        "confidence": confidence,
        "reason": reason,
        "domain_keywords": [item.strip() for item in keywords if item.strip()],
        "summary": {name: str(summary.get(name) or "") for name in fields},
        "model": model or llm.client_config()["model"],
        "usage": response.get("usage") or {},
    }


def _tokens(value: object) -> collections.Counter:
    if isinstance(value, dict):
        text = " ".join(str(item) for item in value.values())
    elif isinstance(value, (list, tuple)):
        text = " ".join(str(item) for item in value)
    else:
        text = str(value or "")
    pieces = []
    for token in TOKEN_RE.findall(text):
        pieces.extend(part.casefold() for part in re.split(r"[_+\-]", token) if part)
    return collections.Counter(piece for piece in pieces if len(piece) > 2)


def _cosine(left: collections.Counter, right: collections.Counter) -> float:
    common = sum(left[key] * right[key] for key in left.keys() & right.keys())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return common / (left_norm * right_norm)


def rank_candidates(
    candidates: list[dict],
    profile: dict,
    repository: dict,
    *,
    limit: int,
    max_per_module: int = 1,
) -> list[dict]:
    """Apply repository-intent × implementation-evidence relevance ranking."""
    if limit < 1 or max_per_module < 1:
        raise RepositoryError("selection limits must be positive")
    query = _tokens(
        [
            *(repository.get("matched_keywords") or []),
            *(profile.get("domain_keywords") or []),
            profile.get("summary") or {},
        ]
    )
    repo_evidence = _tokens(
        [
            repository.get("description") or "",
            repository.get("topics") or [],
            profile.get("summary") or {},
        ]
    )
    md_score = _cosine(query, repo_evidence)
    scored = []
    for candidate in candidates:
        code_evidence = _tokens(
            [
                candidate.get("function") or "",
                candidate.get("docstring_first_line") or "",
                candidate.get("source") or "",
            ]
        )
        code_score = _cosine(query, code_evidence)
        final_score = md_score * code_score
        row = dict(candidate)
        row["relevance"] = {
            "method": "lexical-dual-evidence-v1",
            "repository_score": round(md_score, 8),
            "code_score": round(code_score, 8),
            "final_score": round(final_score, 8),
        }
        if final_score > 0:
            scored.append(row)
    scored.sort(
        key=lambda row: (
            -row["relevance"]["final_score"],
            -int(row.get("n_lines") or 0),
            str(row.get("file") or ""),
            str(row.get("function") or ""),
        )
    )
    selected = []
    module_counts = collections.Counter()
    deferred = []
    for row in scored:
        module = str(row.get("module_hint") or row.get("file") or "unknown")
        module = ".".join(module.split(".")[:2])
        if module_counts[module] < max_per_module:
            selected.append(row)
            module_counts[module] += 1
        else:
            deferred.append(row)
        if len(selected) == limit:
            return selected
    selected.extend(deferred[: max(0, limit - len(selected))])
    return selected


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        for value in values:
            output.write(json.dumps(value, ensure_ascii=False) + "\n")
    temporary.replace(path)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def process_repository(
    candidate: dict,
    *,
    cache_root: Path,
    output_dir: Path,
    chat_fn: Callable = llm.chat,
    profile_model: str | None = None,
    author_model: str | None = None,
    critic_model: str | None = None,
    verifier_model: str | None = None,
    solver_model: str | None = None,
    judge_model: str | None = None,
    additional_judge_models: tuple[str, ...] = (),
    tasks_per_repo: int = 3,
    min_tasks_per_repo: int = 3,
    max_mined_candidates: int = 600,
    allow_unknown_license: bool = False,
    max_tokens: int = 16384,
    context_window_tokens: int = 262_144,
    critic_max_tokens: int = 4096,
    verifier_max_tokens: int = 4096,
    judge_max_tokens: int = 8192,
    judge_max_input_chars: int = 160_000,
    timeout: int = 2400,
    pipeline_concurrency: int = 3,
) -> dict:
    """Run a resumable per-repository funnel and isolated SFT shard."""
    if min_tasks_per_repo < len(ARCHETYPES):
        raise RepositoryError(
            f"min_tasks_per_repo must be at least {len(ARCHETYPES)} for "
            "archetype coverage"
        )
    if tasks_per_repo < min_tasks_per_repo:
        raise RepositoryError("tasks_per_repo must be >= min_tasks_per_repo")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "repository_report.json"
    if report_path.exists():
        report = _read_json(report_path)
        if report.get("status") in {"complete", "rejected"}:
            return report

    snapshot_path = output_dir / "source_snapshot.json"
    previous_snapshot = _read_json(snapshot_path) if snapshot_path.exists() else {}
    pinned_commit = previous_snapshot.get("commit") or candidate.get("head_sha")
    root, commit = checkout_repository(
        candidate,
        Path(cache_root),
        pinned_commit=pinned_commit,
    )
    if previous_snapshot and (
        previous_snapshot.get("repo_id") != candidate.get("repo_id")
        or previous_snapshot.get("commit") != commit
    ):
        raise RepositoryError("repository output shard belongs to another snapshot")
    if not previous_snapshot:
        _write_json(
            snapshot_path,
            {
                "repo_id": candidate.get("repo_id"),
                "full_name": candidate.get("full_name"),
                "commit": commit,
            },
        )
    license_id = detect_license(root)
    if license_id == "unknown":
        license_id = candidate.get("license") or "unknown"
    dataset_license_exception = candidate.get(
        "source_kind"
    ) == "scicodepile_clean_dataset" and bool(candidate.get("source_dataset_license"))
    if license_id == "unknown" and not (
        allow_unknown_license or dataset_license_exception
    ):
        report = {
            "schema_version": REPORT_SCHEMA,
            "status": "rejected",
            "reason": "license_not_detected",
            "repository": candidate,
            "commit": commit,
        }
        _write_json(report_path, report)
        return report

    profile_path = output_dir / "repository_profile.json"
    if profile_path.exists():
        profile = _read_json(profile_path)
    else:
        profile = screen_repository(
            candidate,
            read_repository_context(root),
            chat_fn=chat_fn,
            model=profile_model,
            timeout=timeout,
        )
        _write_json(profile_path, profile)
    if not profile["relevant"] or profile["confidence"] < 3:
        report = {
            "schema_version": REPORT_SCHEMA,
            "status": "rejected",
            "reason": "repository_not_scientifically_relevant",
            "repository": candidate,
            "commit": commit,
            "profile": profile,
        }
        _write_json(report_path, report)
        return report

    mined_path = output_dir / "mined.jsonl"
    if mined_path.exists():
        mined = [
            json.loads(line)
            for line in mined_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        mined = mine_repo(
            root,
            max_per_module=4,
            limit=max_mined_candidates,
            import_root=root,
        )
        _write_jsonl(mined_path, mined)
    selected_path = output_dir / "selected.jsonl"
    selected = rank_candidates(
        mined,
        profile,
        candidate,
        limit=tasks_per_repo,
    )
    _write_jsonl(selected_path, selected)
    if len(selected) < min_tasks_per_repo:
        report = {
            "schema_version": REPORT_SCHEMA,
            "status": "rejected",
            "reason": "insufficient_minimum_dual_evidence_candidates",
            "repository": candidate,
            "commit": commit,
            "profile": profile,
            "mined": len(mined),
            "selected": len(selected),
        }
        _write_json(report_path, report)
        return report

    meta_path = output_dir / "repo_meta.json"
    repo_meta = {
        "url": candidate["url"],
        "commit": commit,
        "license": license_id,
        "license_provenance": (
            "upstream_file"
            if license_id != "unknown"
            else "upstream_unknown_dataset_distribution"
        ),
        "source_dataset_license": candidate.get("source_dataset_license"),
        "slug": safe_slug(candidate["full_name"]),
        "profile_schema": PROFILE_SCHEMA,
        "profile_path": str(profile_path),
    }
    _write_json(meta_path, repo_meta)
    reasoning_dir = output_dir / "reasoning"
    manifest = run_pipeline(
        selected_path,
        meta_path,
        reasoning_dir,
        chat_fn=chat_fn,
        author_model=author_model,
        critic_model=critic_model,
        verifier_model=verifier_model,
        solver_model=solver_model,
        judge_model=judge_model,
        additional_judge_models=additional_judge_models,
        limit=len(selected),
        max_tokens=max_tokens,
        context_window_tokens=context_window_tokens,
        critic_max_tokens=critic_max_tokens,
        verifier_max_tokens=verifier_max_tokens,
        judge_max_tokens=judge_max_tokens,
        judge_max_input_chars=judge_max_input_chars,
        timeout=timeout,
        concurrency=pipeline_concurrency,
    )
    report = {
        "schema_version": REPORT_SCHEMA,
        "status": "complete",
        "repository": candidate,
        "commit": commit,
        "license": license_id,
        "profile": profile,
        "mined": len(mined),
        "selected": len(selected),
        "reasoning_manifest": str(reasoning_dir / "run_manifest.json"),
        "sft": str(reasoning_dir / "sft.jsonl"),
        "candidate_sft": str(reasoning_dir / "sft.jsonl"),
        "sft_rows": manifest["artifacts"]["sft"]["rows"],
        "artifacts": {
            name: str(reasoning_dir / filename)
            for name, filename in {
                "tasks": "tasks.jsonl",
                "preflight": "preflight.jsonl",
                "verification": "verification.jsonl",
                "traces": "traces.jsonl",
                "grades": "grades.jsonl",
                "candidate_sft": "sft.jsonl",
            }.items()
        },
    }
    _write_json(report_path, report)
    return report
