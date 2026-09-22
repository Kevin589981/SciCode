"""Repo vetting + module decomposition (ScienceIDE 'codebase report' stage).

What is mechanical here (automated):
  * pin the source tree: archive tar.gz + sha256 + origin/commit/license
    -> source/source.json  (schema mirrors upstream source.json)
  * license detection from LICENSE/COPYING files (refuse 'unknown' unless
    explicitly allowed -- upstream recorded licenses per codebase, §12)
  * hazards scan (network / subprocess / unseeded randomness signals),
    reported, not silently dropped
  * LLM-proposed module decomposition over the mined function catalog

What stays human (upstream design: 'agent proposes, curator decides'):
  * module boundaries, entrypoints, exclusions, hazard disposition
  * -> validation/module.json is written with approval.status='pending';
    `vet --check-approval` fails until a human fills approval.human_ref.

Usage:
  python -m factory.envbuild.vet --repo /path/or/https-url --slug scipy \
      --out environments [--commit v1.18.0] [--allow-unknown-license]
  python -m factory.envbuild.vet --check-approval --env environments/scipy
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
from pathlib import Path

from ..author import llm, mine

LICENSE_PATTERNS = [
    (r"MIT License", "MIT"),
    (
        r"BSD 3-Clause|BSD-3-Clause|Redistribution and use in source and binary forms",
        "BSD-3-Clause",
    ),
    (r"BSD 2-Clause|BSD-2-Clause", "BSD-2-Clause"),
    (r"Apache License, Version 2|Apache-2.0", "Apache-2.0"),
    (r"GNU GENERAL PUBLIC LICENSE\s+Version 3", "GPL-3.0-or-later"),
    (r"GNU GENERAL PUBLIC LICENSE\s+Version 2", "GPL-2.0-or-later"),
    (r"GNU Lesser General Public License\s+Version 3", "LGPL-3.0-or-later"),
]

HAZARD_PATTERNS = {
    "network": re.compile(r"\b(socket|urllib|requests\.|httpx|aiohttp)\b"),
    "subprocess": re.compile(r"\b(subprocess|os\.system|popen)\b"),
    "unseeded_random": re.compile(r"\brandom\.(random|randint|choice|shuffle)\("),
    "file_io": re.compile(r"\bopen\("),
}

MODULE_PROMPT = """You are decomposing the scientific Python codebase {origin} @ {commit} (license {license}) into modules for a benchmark environment catalog.

Mined function catalog (function :: file :: docstring first line):
{catalog}

Propose up to {max_modules} modules, each a coherent scientific responsibility. For each module give: slug, title, paths (file globs it OWNS), entrypoints (the catalog functions belonging to it), excluded (responsibilities explicitly OUT of scope), hazards (anything a task author must know: numerical sensitivity, heavy runtime, global state), rationale (one sentence).

Respond with ONLY a JSON list:
[{{"slug": "...", "title": "...", "paths": ["..."], "entrypoints": ["..."], "excluded": ["..."], "hazards": ["..."], "rationale": "..."}}]"""


def detect_license(root: Path) -> str:
    candidates = [
        root / n
        for n in (
            "LICENSE",
            "LICENSE.txt",
            "LICENSE.md",
            "LICENCE",
            "COPYING",
            "COPYING.txt",
        )
    ]
    # installed wheels keep licenses in <pkg>-*.dist-info/licenses/
    candidates += sorted(root.parent.glob("*.dist-info/licenses/LICENSE*"))
    candidates += sorted(root.parent.glob("*.dist-info/METADATA"))
    for p in candidates:
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")[:4000]
        for pat, spdx in LICENSE_PATTERNS:
            if re.search(pat, text):
                return spdx
        if "License" in text or "Copyright" in text:
            return "unknown"
    return "unknown"


def scan_hazards(root: Path, max_files: int = 400) -> dict:
    hits = {k: [] for k in HAZARD_PATTERNS}
    n = 0
    for p in sorted(root.rglob("*.py")):
        if n >= max_files:
            break
        parts = set(p.parts)
        if parts & {"test", "tests", "testing"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        n += 1
        for kind, pat in HAZARD_PATTERNS.items():
            if pat.search(text):
                hits[kind].append(str(p.relative_to(root)))
    return {k: v[:20] for k, v in hits.items() if v}


def archive_source(root: Path, out_tar: Path) -> tuple[str, list]:
    """Archive the source tree; record (don't ship) binary build products."""
    out_tar.parent.mkdir(parents=True, exist_ok=True)
    binary_suffixes = (
        ".so",
        ".pyc",
        ".pyo",
        ".a",
        ".o",
        ".dll",
        ".dylib",
        ".exe",
        ".whl",
    )
    binaries = []
    with tarfile.open(out_tar, "w:gz") as tar:
        for p in sorted(root.rglob("*")):
            if p.is_dir() or any(
                s in (".git", "__pycache__", ".pytest_cache") for s in p.parts
            ):
                continue
            if p.suffix in binary_suffixes or ".dist-info" in p.parts:
                binaries.append(str(p.relative_to(root)))
                continue
            tar.add(p, arcname=str(p.relative_to(root)))
    return hashlib.sha256(out_tar.read_bytes()).hexdigest(), binaries[:50]


def git_pin(repo: str, workdir: Path, commit: str | None) -> dict:
    dest = workdir / repo.rstrip("/").split("/")[-1].replace(".git", "")
    if dest.exists():
        subprocess.run(["git", "-C", str(dest), "fetch", "--all", "--tags"], check=True)
    else:
        subprocess.run(["git", "clone", repo, str(dest)], check=True)
    if commit:
        subprocess.run(["git", "-C", str(dest), "checkout", commit], check=True)
    sha = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    tag = subprocess.run(
        ["git", "-C", str(dest), "describe", "--tags", "--always"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {"path": dest, "commit": sha, "release": tag}


def propose_modules(
    slug: str,
    origin: str,
    commit: str,
    license_id: str,
    catalog: list[dict],
    max_modules: int = 6,
) -> list[dict]:
    lines = [
        f"{c['function']} :: {c['file']} :: {c['docstring_first_line']}"
        for c in catalog
    ]
    prompt = MODULE_PROMPT.format(
        origin=origin,
        commit=commit,
        license=license_id,
        catalog="\n".join(lines),
        max_modules=max_modules,
    )
    resp = llm.chat([{"role": "user", "content": prompt}], temperature=0.3)
    text = (resp["choices"][0]["message"].get("content") or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    modules = json.loads(text)
    for m in modules:
        m.setdefault("paths", []), m.setdefault("entrypoints", [])
        m.setdefault("excluded", []), m.setdefault("hazards", [])
        m.setdefault("rationale", "")
    return modules


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="local checkout dir or git URL")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--commit", default=None)
    ap.add_argument("--out", type=Path, default=Path("environments"))
    ap.add_argument("--allow-unknown-license", action="store_true")
    ap.add_argument("--check-approval", action="store_true")
    ap.add_argument(
        "--env", type=Path, default=None, help="env dir for --check-approval"
    )
    args = ap.parse_args()

    if args.check_approval:
        env = Path(args.env)
        mod = json.loads(
            (env / "validation" / "module.json").read_text(encoding="utf-8")
        )
        ap_ = mod.get("approval", {})
        if ap_.get("status") != "approved" or not ap_.get("human_ref"):
            raise SystemExit(
                f"module.json approval pending for {env}: "
                "a human must set approval.status='approved' "
                "and fill approval.human_ref"
            )
        print(f"approval OK: {ap_['human_ref'][:80]}")
        return

    env_dir = args.out / args.slug
    src_dir = env_dir / "source"
    val_dir = env_dir / "validation"
    val_dir.mkdir(parents=True, exist_ok=True)

    if args.repo.startswith(("http://", "https://", "git@")):
        pin = git_pin(args.repo, Path(".work/repos"), args.commit)
        root, origin = pin["path"], args.repo
        commit, release = pin["commit"], pin["release"]
    else:
        root = Path(args.repo)
        origin = args.repo
        commit = args.commit or "unpinned-local"
        release = args.commit or ""

    license_id = detect_license(root)
    if license_id == "unknown" and not args.allow_unknown_license:
        raise SystemExit(
            "license not detected; pass --allow-unknown-license "
            "to record it as unknown (upstream required a "
            "recorded license per codebase)"
        )

    sha, binaries = archive_source(root, src_dir / f"{args.slug}-source.tar.gz")
    (src_dir / "source.json").write_text(
        json.dumps(
            {
                "origin": origin,
                "commit": commit,
                "release": release,
                "license": license_id,
                "archive": f"{args.slug}-source.tar.gz",
                "archive_sha256": sha,
                "verification": {
                    "archive_has_git_metadata": False,
                    "binary_artifacts_excluded": binaries,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    hazards = scan_hazards(root)
    catalog = mine.mine_repo(root, max_per_module=4, limit=120)

    modules = []
    if catalog:
        try:
            modules = propose_modules(args.slug, origin, commit, license_id, catalog)
        except Exception as e:
            print(f"module decomposition failed ({e}); writing empty proposal")

    module_json = {
        "module": {
            "slug": args.slug,
            "title": f"{args.slug} environment",
            "origin": origin,
            "commit": commit,
            "license": license_id,
            "hazards_report": hazards,
        },
        "modules": modules,
        "shared_infrastructure": [],
        "approval": {
            "status": "pending",
            "human_ref": None,
            "at": None,
            "note": "upstream design: agent proposes, curator "
            "decides. Fill human_ref to approve.",
        },
    }
    (val_dir / "module.json").write_text(
        json.dumps(module_json, indent=2), encoding="utf-8"
    )
    print(
        f"vet: env={env_dir} license={license_id} hazards={list(hazards)} "
        f"catalog={len(catalog)} modules={len(modules)}"
    )
    print(
        "NEXT: human reviews validation/module.json, then sets "
        "approval.status='approved' + approval.human_ref"
    )


if __name__ == "__main__":
    main()
