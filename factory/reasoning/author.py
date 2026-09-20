"""LLM-assisted composition of reasoning-intensive SciCode tasks."""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import re
from collections.abc import Callable, Iterable
from pathlib import Path

from ..author import llm
from .prompts import author_prompt
from .schema import ARCHETYPES, TASK_SCHEMA, SchemaError, canonical_hash, validate_task


class AuthorError(ValueError):
    """An author response could not be converted into a valid task."""


def _json_objects(text: str) -> list[dict]:
    """Find complete JSON objects without mistaking partial CoT for a result."""
    text = (text or "").strip()
    if not text:
        return []
    candidates = [text]
    candidates.extend(
        match.group(1)
        for match in re.finditer(
            r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL
        )
    )
    decoder = json.JSONDecoder()
    decoded: list[tuple[int, dict]] = []
    seen = set()
    for candidate in candidates:
        starts = [0] if candidate.startswith("{") else []
        starts.extend(index for index, char in enumerate(candidate) if char == "{")
        for start in dict.fromkeys(starts):
            try:
                value, end = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError:
                continue
            if not isinstance(value, dict):
                continue
            fingerprint = json.dumps(value, sort_keys=True, ensure_ascii=False)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            decoded.append((end, value))
    return [value for _size, value in sorted(decoded, reverse=True, key=lambda x: x[0])]


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
    max_attempts: int = 1,
) -> dict:
    """Ask an author model for one task, attach trusted source metadata, validate."""
    if archetype not in ARCHETYPES:
        raise AuthorError(f"unknown archetype: {archetype}")
    if max_attempts < 1:
        raise AuthorError("max_attempts must be positive")
    prompt = author_prompt(candidate, archetype, repo_meta)
    failures = []
    for attempt in range(1, max_attempts + 1):
        attempt_prompt = prompt
        if attempt > 1:
            attempt_prompt += (
                "\nA prior response failed structural validation. Keep internal "
                "reasoning concise and emit the complete final JSON object before "
                "the token budget is exhausted."
            )
        response = chat_fn(
            [{"role": "user", "content": attempt_prompt}],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        try:
            choice = response["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            failures.append(f"attempt {attempt}: no usable message ({exc})")
            continue
        fields = (
            ("content", message.get("content") or ""),
            ("reasoning_content", message.get("reasoning_content") or ""),
        )
        schema_errors = []
        for response_field, response_text in fields:
            for spec in _json_objects(response_text):
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
                        "attempt": attempt,
                        "response_field": response_field,
                        "finish_reason": choice.get("finish_reason"),
                        "response_chars": {
                            name: len(value) for name, value in fields
                        },
                        "usage": response.get("usage") or {},
                    },
                }
                try:
                    return validate_task(task)
                except SchemaError as exc:
                    schema_errors.append(str(exc))
        usage = response.get("usage") or {}
        failures.append(
            "attempt "
            f"{attempt}: no valid task object; finish_reason="
            f"{choice.get('finish_reason')!r}, content_chars={len(fields[0][1])}, "
            f"reasoning_chars={len(fields[1][1])}, completion_tokens="
            f"{usage.get('completion_tokens')!r}, schema_error="
            f"{(schema_errors[0] if schema_errors else 'no complete JSON object')[:300]}"
        )
    raise AuthorError("; ".join(failures))


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
    concurrency: int = 1,
    max_attempts: int = 1,
    errors_path: Path | None = None,
) -> dict:
    """Compose tasks with bounded concurrency and append-safe task-ID resume."""
    if concurrency < 1:
        raise AuthorError("concurrency must be positive")
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
    jobs = []
    for index, candidate in enumerate(candidates):
        archetype = archetypes[index % len(archetypes)]
        task_id = task_id_for(candidate, archetype, repo_meta)
        if task_id in done:
            counts["skipped"] += 1
        else:
            jobs.append((candidate, archetype, task_id))

    def one(job):
        candidate, archetype, _task_id = job
        return compose_task(
            candidate,
            archetype,
            repo_meta,
            chat_fn=chat_fn,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    with output_path.open("a", encoding="utf-8") as output, Path(errors_path).open(
        "a", encoding="utf-8"
    ) as errors, futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        pending = {pool.submit(one, job): job for job in jobs}
        for future in futures.as_completed(pending):
            candidate, archetype, task_id = pending[future]
            try:
                task = future.result()
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
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--max-attempts", type=int, default=1)
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
        concurrency=args.concurrency,
        max_attempts=args.max_attempts,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
