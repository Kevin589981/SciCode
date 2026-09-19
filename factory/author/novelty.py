"""Novelty screen against the official SciCode benchmark.

Contract adopted from the benchmark's own contamination rules: official
problems, prompts, tests and oracles must never seed derived data. Screen is
deliberately conservative and cheap (token Jaccard over question text plus
exact function-name match); stronger semantic dedup can layer on later.

Usage:
    python -m factory.author.novelty  # smoke-test: prints fingerprint stats
"""
from __future__ import annotations

import json
import re
from pathlib import Path

TOKEN = re.compile(r"[a-z][a-z0-9_]+")
JACCARD_REJECT = 0.55


def _tokens(text: str) -> set:
    return set(TOKEN.findall(text.lower()))


def load_official_fingerprints(data_dir: Path) -> dict:
    """Token sets + function names from official validation/test splits."""
    questions, fnames = [], set()
    for name in ("validation.jsonl", "test.jsonl"):
        p = Path(data_dir) / name
        if not p.exists():
            continue
        for line in p.open(encoding="utf-8"):
            rec = json.loads(line)
            questions.append(_tokens(rec.get("problem_description_main", "")))
            for s in rec.get("sub_steps", []):
                questions.append(_tokens(s.get("step_description_prompt", "")))
                m = re.search(r"\bdef\s+(\w+)\s*\(", s.get("function_header", ""))
                if m:
                    fnames.add(m.group(1).lower())
    return {"question_tokens": questions, "function_names": fnames}


def screen(prop: dict, official: dict) -> bool:
    """True = novel (passes screen)."""
    if prop["function"].lower() in official["function_names"]:
        return False
    cand = _tokens(prop.get("question", ""))
    if not cand:
        return False
    for qt in official["question_tokens"]:
        inter = len(cand & qt)
        union = len(cand | qt)
        if union and inter / union >= JACCARD_REJECT:
            return False
    return True


def main() -> None:
    from .. import config
    off = load_official_fingerprints(config.DATA_DIR)
    print(f"official fingerprints: {len(off['question_tokens'])} question token-sets, "
          f"{len(off['function_names'])} function names")


if __name__ == "__main__":
    main()
