"""Retrieve broadly, then conservatively filter scientific repositories.

The implementation follows the scalable shape used by SciCodePile: a curated
scientific vocabulary drives GitHub Search, inexpensive metadata filters run
before any clone or LLM call, and repository IDs deduplicate overlapping
queries. Search is deliberately serial and rate-limit aware; parallelism begins
later in the durable repository queue.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path

from ..author import llm
from .author import _json_objects

DEFAULT_KEYWORDS_PATH = Path(__file__).with_name("science_keywords.txt")


class DiscoveryError(RuntimeError):
    """Repository discovery input or a remote response is unusable."""


def load_keywords(path: Path = DEFAULT_KEYWORDS_PATH) -> list[str]:
    keywords = []
    seen = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        value = line.split("#", 1)[0].strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            keywords.append(value)
    if not keywords:
        raise DiscoveryError(f"no keywords found in {path}")
    return keywords


def resolve_github_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token.strip()
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def expand_keywords(
    keywords: list[str],
    *,
    chat_fn: Callable = llm.chat,
    model: str | None = None,
    max_new: int = 40,
    timeout: int = 1200,
) -> list[str]:
    """Expand synonyms/abbreviations once; never let expansion replace seeds."""
    if max_new < 0:
        raise DiscoveryError("max_new must be nonnegative")
    if max_new == 0:
        return list(keywords)
    prompt = f"""Expand a computational-science repository search vocabulary.

SEED TERMS:
{json.dumps(keywords, ensure_ascii=False)}

Return ONLY one JSON object {{"queries": [...]}} with at most {max_new} new
queries. Add synonyms, abbreviations/full forms, tool-ecosystem terms, and close
scientific subtopics. Keep every query specific to computational science; do
not add generic software or generic AI terms. Do not repeat seed terms.
"""
    response = chat_fn(
        [{"role": "user", "content": prompt}],
        model=model,
        temperature=0.2,
        max_tokens=4096,
        timeout=timeout,
    )
    try:
        message = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DiscoveryError(f"keyword response has no usable message: {exc}") from exc
    objects = []
    for field in ("content", "reasoning_content"):
        objects.extend(_json_objects(message.get(field) or ""))
    value = next(
        (item for item in objects if isinstance(item.get("queries"), list)), None
    )
    if value is None:
        raise DiscoveryError("keyword response has no complete queries object")
    merged = list(keywords)
    seen = {item.casefold() for item in merged}
    for item in value["queries"]:
        if not isinstance(item, str) or not item.strip():
            continue
        item = " ".join(item.split())[:120]
        if item.casefold() not in seen:
            seen.add(item.casefold())
            merged.append(item)
        if len(merged) >= len(keywords) + max_new:
            break
    return merged


class GitHubClient:
    """Minimal GitHub REST client with explicit search-rate backpressure."""

    def __init__(
        self,
        token: str | None = None,
        *,
        request_interval: float = 2.1,
        retries: int = 4,
        timeout: int = 60,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.token = token
        self.request_interval = max(0.0, request_interval)
        self.retries = retries
        self.timeout = timeout
        self.sleep_fn = sleep_fn
        self._last_search = 0.0

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "scicode-reasoning-factory",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get(self, url: str) -> tuple[dict, dict[str, str]]:
        last = None
        for attempt in range(self.retries):
            try:
                request = urllib.request.Request(url, headers=self._headers())
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    headers = {
                        key.lower(): value for key, value in response.headers.items()
                    }
                    if not isinstance(payload, dict):
                        raise DiscoveryError("GitHub response must be an object")
                    return payload, headers
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in {403, 429, 500, 502, 503, 504}:
                    raise DiscoveryError(f"GitHub HTTP {exc.code} for {url}") from exc
                retry_after = exc.headers.get("Retry-After")
                reset = exc.headers.get("X-RateLimit-Reset")
                if retry_after:
                    delay = float(retry_after)
                elif reset:
                    delay = max(1.0, float(reset) - time.time() + 1.0)
                else:
                    delay = min(60.0, 2.0**attempt)
                self.sleep_fn(delay)
            except (OSError, json.JSONDecodeError) as exc:
                last = exc
                self.sleep_fn(min(30.0, 2.0**attempt))
        raise DiscoveryError(
            f"GitHub request failed after {self.retries} tries: {last}"
        )

    def search(
        self,
        keyword: str,
        *,
        min_stars: int,
        language: str | None,
        pages: int,
        per_page: int,
    ) -> list[dict]:
        query_parts = [
            f'"{keyword}"',
            "in:name,description,readme",
            f"stars:>={min_stars}",
            "archived:false",
            "fork:false",
        ]
        if language:
            query_parts.append(f"language:{language}")
        query = " ".join(query_parts)
        found = []
        for page in range(1, pages + 1):
            elapsed = time.monotonic() - self._last_search
            if elapsed < self.request_interval:
                self.sleep_fn(self.request_interval - elapsed)
            params = urllib.parse.urlencode(
                {
                    "q": query,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": min(100, per_page),
                    "page": page,
                }
            )
            payload, _headers = self._get(
                f"https://api.github.com/search/repositories?{params}"
            )
            self._last_search = time.monotonic()
            items = payload.get("items")
            if not isinstance(items, list):
                raise DiscoveryError("GitHub search response has no items list")
            found.extend(item for item in items if isinstance(item, dict))
            if len(items) < per_page:
                break
        return found

    def readme(self, full_name: str, *, max_chars: int = 32_000) -> str:
        payload, _headers = self._get(
            f"https://api.github.com/repos/{full_name}/readme"
        )
        content = payload.get("content")
        if not isinstance(content, str):
            return ""
        try:
            data = base64.b64decode(content, validate=False)
        except (ValueError, TypeError):
            return ""
        return data.decode("utf-8", errors="ignore")[:max_chars]

    def head_commit(self, full_name: str, branch: str) -> str:
        """Resolve a mutable default branch to an immutable commit SHA."""
        encoded_branch = urllib.parse.quote(branch or "HEAD", safe="")
        payload, _headers = self._get(
            f"https://api.github.com/repos/{full_name}/commits/{encoded_branch}"
        )
        commit = payload.get("sha")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
            raise DiscoveryError(f"GitHub returned no full commit SHA for {full_name}")
        return commit.lower()


def _candidate(item: dict, keyword: str) -> dict | None:
    repo_id = item.get("id")
    full_name = item.get("full_name")
    clone_url = item.get("clone_url")
    if (
        repo_id is None
        or not isinstance(full_name, str)
        or not isinstance(clone_url, str)
    ):
        return None
    license_value = item.get("license") or {}
    return {
        "schema_version": "scicode-repository-candidate-v1",
        "repo_id": str(repo_id),
        "full_name": full_name,
        "url": str(item.get("html_url") or ""),
        "clone_url": clone_url,
        "default_branch": str(item.get("default_branch") or ""),
        "description": str(item.get("description") or ""),
        "topics": [str(value) for value in item.get("topics") or []],
        "language": str(item.get("language") or ""),
        "size_kb": int(item.get("size") or 0),
        "stars": int(item.get("stargazers_count") or 0),
        "forks": int(item.get("forks_count") or 0),
        "archived": bool(item.get("archived")),
        "fork": bool(item.get("fork")),
        "disabled": bool(item.get("disabled")),
        "license": str(license_value.get("spdx_id") or "unknown"),
        "pushed_at": item.get("pushed_at"),
        "updated_at": item.get("updated_at"),
        "matched_keywords": [keyword],
        "discovered_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def discover_repositories(
    keywords: Iterable[str],
    *,
    search_fn: Callable[..., list[dict]],
    min_stars: int = 10,
    max_size_kb: int = 1_000_000,
    language: str | None = "Python",
    pages_per_query: int = 1,
    per_page: int = 30,
    limit: int | None = None,
    errors: list[dict] | None = None,
) -> list[dict]:
    """Retrieve and deduplicate candidates; no model or clone occurs here."""
    if min_stars < 0 or max_size_kb < 1 or pages_per_query < 1 or per_page < 1:
        raise DiscoveryError("discovery limits must be positive")
    by_id = {}
    for keyword in keywords:
        try:
            rows = search_fn(
                keyword,
                min_stars=min_stars,
                language=language,
                pages=pages_per_query,
                per_page=per_page,
            )
        except Exception as exc:
            if errors is None:
                raise
            errors.append(
                {
                    "stage": "search",
                    "keyword": keyword,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        for item in rows:
            candidate = _candidate(item, keyword)
            if candidate is None:
                continue
            if (
                candidate["stars"] < min_stars
                or candidate["size_kb"] > max_size_kb
                or candidate["archived"]
                or candidate["fork"]
                or candidate["disabled"]
            ):
                continue
            existing = by_id.get(candidate["repo_id"])
            if existing is None:
                by_id[candidate["repo_id"]] = candidate
            elif keyword not in existing["matched_keywords"]:
                existing["matched_keywords"].append(keyword)
    ordered = sorted(
        by_id.values(),
        key=lambda row: (
            -len(row["matched_keywords"]),
            -row["stars"],
            row["full_name"],
        ),
    )
    return ordered[:limit] if limit is not None else ordered


def pin_repository_heads(
    repositories: Iterable[dict],
    *,
    resolve_fn: Callable[[str, str], str],
    errors: list[dict] | None = None,
) -> list[dict]:
    """Attach immutable source commits after cheap filtering has reduced volume."""
    pinned = []
    for repository in repositories:
        row = dict(repository)
        commit = row.get("head_sha")
        try:
            if not isinstance(commit, str) or not re.fullmatch(
                r"[0-9a-fA-F]{40}", commit
            ):
                commit = resolve_fn(
                    str(row["full_name"]),
                    str(row.get("default_branch") or "HEAD"),
                )
        except Exception as exc:
            if errors is None:
                raise
            errors.append(
                {
                    "stage": "pin_head",
                    "repository": row.get("full_name"),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
            if errors is not None:
                errors.append(
                    {
                        "stage": "pin_head",
                        "repository": row.get("full_name"),
                        "error": "head resolver returned an invalid full SHA",
                    }
                )
                continue
            raise DiscoveryError(
                f"head resolver returned an invalid SHA for {row['full_name']}"
            )
        row["head_sha"] = commit.lower()
        pinned.append(row)
    return pinned


def write_catalog(path: Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keywords", type=Path, default=DEFAULT_KEYWORDS_PATH)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--min-stars", type=int, default=10)
    parser.add_argument("--max-size-kb", type=int, default=1_000_000)
    parser.add_argument("--language", default="Python")
    parser.add_argument("--pages-per-query", type=int, default=1)
    parser.add_argument("--per-page", type=int, default=30)
    parser.add_argument(
        "--query-limit",
        type=int,
        help="use only the first N keyword queries (useful for bounded smoke runs)",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--request-interval", type=float, default=2.1)
    parser.add_argument("--expand-keywords", action="store_true")
    parser.add_argument("--max-expanded", type=int, default=40)
    parser.add_argument("--expansion-model")
    args = parser.parse_args()
    keywords = load_keywords(args.keywords)
    if args.expand_keywords:
        keywords = expand_keywords(
            keywords,
            model=args.expansion_model,
            max_new=args.max_expanded,
        )
    if args.query_limit is not None:
        if args.query_limit < 1:
            parser.error("--query-limit must be positive")
        keywords = keywords[: args.query_limit]
    client = GitHubClient(
        resolve_github_token(),
        request_interval=args.request_interval,
    )
    errors = []
    rows = discover_repositories(
        keywords,
        search_fn=client.search,
        min_stars=args.min_stars,
        max_size_kb=args.max_size_kb,
        language=args.language or None,
        pages_per_query=args.pages_per_query,
        per_page=args.per_page,
        limit=args.limit,
        errors=errors,
    )
    rows = pin_repository_heads(rows, resolve_fn=client.head_commit, errors=errors)
    write_catalog(args.out, rows)
    errors_path = args.out.with_name(args.out.name + ".errors.jsonl")
    write_catalog(errors_path, errors)
    print(
        json.dumps(
            {
                "queries": len(keywords),
                "repositories": len(rows),
                "errors": len(errors),
                "out": str(args.out),
                "errors_out": str(errors_path),
            }
        )
    )


if __name__ == "__main__":
    main()
