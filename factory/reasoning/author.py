"""LLM-assisted composition of reasoning-intensive SciCode tasks."""
from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable, Iterable
from pathlib import Path

from ..author import llm
from .prompts import author_prompt
from .schema import ARCHETYPES, TASK_SCHEMA, SchemaError, canonical_hash, validate_task


class AuthorError(ValueError):
    """An author response could not be converted into a valid task."""


def _json_object(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        text = parts[1] if len(parts) > 1 else ""
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AuthorError(f"author response is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AuthorError("author JSON must be an object")
    return value


def task_id_for(candidate: dict, archetype: str, repo_meta: dict) -> str:
    """Return a stable readable ID before any expensive author call."""
    raw = "-".join(
        [
            str(repo_meta.get("slug") or "repo"),
            str(candidate.get("function") or "symbol"),
            archetype,
        ]
    )
    readable = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")[:80]
    identity = canonical_hash(
        {
            "repo": repo_meta.get("url"),
            "commit": repo_meta.get("commit"),
            "file": candidate.get("file"),
            "function": candidate.get("function"),
            "source": candidate.get("source"),
            "archetype": archetype,
        }
    )[:12]
    return f"{readable}-{identity}"


def compose_task(
    candidate: dict,
    archetype: str,
    repo_meta: dict,
    *,
    chat_fn: Callable = llm.chat,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    timeout: int = 1200,
) -> dict:
    """Ask an author model for one task, attach trusted source metadata, validate."""
    if archetype not in ARCHETYPES:
        raise AuthorError(f"unknown archetype: {archetype}")
    prompt = author_prompt(candidate, archetype, repo_meta)
    response = chat_fn(
        [{"role": "user", "content": prompt}],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    try:
        message = response["choices"][0]["message"]
        spec = _json_object(message.get("content") or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise AuthorError(f"author response has no usable message: {exc}") from exc
    task = {
        "schema_version": TASK_SCHEMA,
        "task_id": task_id_for(candidate, archetype, repo_meta),
        "archetype": archetype,
        "source": {
            "repo": str(repo_meta.get("url") or "unknown"),
            "commit": str(repo_meta.get("commit") or "unpinned"),
            "file": str(candidate.get("file") or "unknown"),
            "symbol": str(candidate.get("function") or "unknown"),
            "license": str(repo_meta.get("license") or "unknown"),
            "module_hint": str(candidate.get("module_hint") or ""),
            "source": str(candidate.get("source") or ""),
        },
        "problem": spec.get("problem"),
        "deliverable": spec.get("deliverable"),
        "reasoning_contract": spec.get("reasoning_contract"),
        "archetype_payload": spec.get("archetype_payload"),
        "authoring": {
            "model": model or llm.client_config()["model"],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "usage": response.get("usage") or {},
        },
    }
    try:
        return validate_task(task)
    except SchemaError as exc:
        raise AuthorError(f"author JSON violates the task schema: {exc}") from exc


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AuthorError(f"{path}:{number}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise AuthorError(f"{path}:{number}: row must be an object")
            rows.append(value)
    return rows


def _existing_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {row["task_id"] for row in _read_jsonl(path) if row.get("task_id")}


def run_authoring(
    mined_path: Path,
    repo_meta_path: Path,
    output_path: Path,
    *,
    archetypes: Iterable[str] = ARCHETYPES,
    limit: int | None = None,
    chat_fn: Callable = llm.chat,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    timeout: int = 1200,
    errors_path: Path | None = None,
) -> dict:
    """Compose tasks sequentially with append-safe task-ID resume."""
    candidates = _read_jsonl(Path(mined_path))
    if limit is not None:
        candidates = candidates[:limit]
    repo_meta = json.loads(Path(repo_meta_path).read_text(encoding="utf-8"))
    archetypes = tuple(archetypes)
    if not archetypes or any(name not in ARCHETYPES for name in archetypes):
        raise AuthorError(f"archetypes must be selected from {ARCHETYPES}")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    errors_path = errors_path or output_path.with_suffix(".errors.jsonl")
    done = _existing_ids(output_path)
    counts = {"written": 0, "skipped": 0, "errors": 0}
    with output_path.open("a", encoding="utf-8") as output, Path(errors_path).open(
        "a", encoding="utf-8"
    ) as errors:
        for index, candidate in enumerate(candidates):
            archetype = archetypes[index % len(archetypes)]
            task_id = task_id_for(candidate, archetype, repo_meta)
            if task_id in done:
                counts["skipped"] += 1
                continue
            try:
                task = compose_task(
                    candidate,
                    archetype,
                    repo_meta,
                    chat_fn=chat_fn,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
            except Exception as exc:
                errors.write(
                    json.dumps(
                        {
                            "task_id": task_id,
                            "archetype": archetype,
                            "error": f"{type(exc).__name__}: {exc}"[:1000],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                errors.flush()
                counts["errors"] += 1
                continue
            output.write(json.dumps(task, ensure_ascii=False) + "\n")
            output.flush()
            done.add(task_id)
            counts["written"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mined", type=Path, required=True)
    parser.add_argument("--repo-meta", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--archetypes", default=",".join(ARCHETYPES))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--model")
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()
    result = run_authoring(
        args.mined,
        args.repo_meta,
        args.out,
        archetypes=[item.strip() for item in args.archetypes.split(",") if item.strip()],
        limit=args.limit,
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

