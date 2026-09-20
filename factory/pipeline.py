"""One-command orchestrator for the whole data factory.

Mirrors ScienceInfra scripts/prepare/prepare_all.sh --stages: each stage is
idempotent (skips completed work), and the human checkpoints are ENFORCED
between stages, not just recorded:

    vet        pin/archive/license + module decomposition (approval: pending)
    mine       AST catalog from the import root
    propose    LLM sub-step proposals over the catalog
    verify     execution gates -> seeds/<env>/
    calibrate  per-seed measurement (noise_floor/faults/tolerance) + warrant
    harvest    authoring/harvest.json + WRITER-BRIEF.md
    solve      solver episodes -> data/traces-<env>.jsonl (PRIMARY)

Gate policy:
    solve refuses seeds whose rubric warrant.finalized_by is empty unless
    --allow-unfrozen; --require-approval additionally enforces the
    module.json expert approval before any LLM stage.

Usage:
  python -m factory.pipeline --repo <url-or-path> --slug scipy \
      --commit v1.18.0 --import-root <dir-on-sys.path> \
      --stages vet,mine,propose,verify,calibrate,harvest,solve \
      [--attempts 4] [--limit-seeds N] [--allow-unfrozen] [--force]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

PY = sys.executable


def run(cmd: list[str], env: dict | None = None) -> None:
    print("+ " + " ".join(cmd), flush=True)
    e = dict(os.environ, **(env or {}))
    subprocess.run(cmd, check=True, env=e)


def stage_vet(args, env: dict) -> None:
    src = Path("environments") / args.slug / "source" / "source.json"
    if src.exists() and not args.force:
        print(f"[vet] skip (source.json exists); --force to redo")
        return
    cmd = [PY, "-m", "factory.envbuild.vet", "--repo", args.package_dir,
           "--slug", args.slug, "--out", "environments"]
    if args.commit:
        cmd += ["--commit", args.commit]
    if args.allow_unknown_license:
        cmd.append("--allow-unknown-license")
    run(cmd, env)


def _done(p: Path) -> bool:
    return p.exists() and p.stat().st_size > 0


def stage_mine(args, env: dict) -> Path:
    out = Path(".work") / f"{args.slug}-mined.jsonl"
    if _done(out) and not args.force:
        print(f"[mine] skip ({out} exists)")
        return out
    run([PY, "-m", "factory.author.mine", "--repo", args.package_dir,
         "--import-root", args.import_root, "--out", str(out)], env)
    return out


def stage_propose(args, env: dict, mined: Path) -> Path:
    out = Path(".work") / f"{args.slug}-proposals.jsonl"
    if _done(out) and not args.force:
        print(f"[propose] skip ({out} exists)")
        return out
    meta = Path(".work") / f"{args.slug}-repo-meta.json"
    meta.write_text(json.dumps({
        "url": args.repo, "commit": args.commit or "unpinned",
        "license": _license_of(args), "slug": args.slug}), encoding="utf-8")
    run([PY, "-m", "factory.author.propose", "--mined", str(mined),
         "--repo-meta", str(meta), "--out", str(out)], env)
    return out


def _license_of(args) -> str:
    p = Path("environments") / args.slug / "source" / "source.json"
    return json.loads(p.read_text(encoding="utf-8"))["license"] if p.exists() \
        else "unknown"


def stage_verify(args, env: dict, proposals: Path) -> Path:
    out = Path("seeds") / args.slug
    run([PY, "-m", "factory.author.verify", "--proposals", str(proposals),
         "--repo-root", args.import_root, "--out", str(out)], env)
    return out


def stage_calibrate(args, env: dict, seeds_dir: Path) -> None:
    seed_files = sorted(seeds_dir.glob("*.json"))
    if args.limit_seeds:
        seed_files = seed_files[: args.limit_seeds]
    for sf in seed_files:
        check = sf.stem
        out = Path("environments") / args.slug / "validation" / check
        if (out / "rubric.json").exists() and not args.force:
            print(f"[calibrate] skip {check}")
            continue
        run([PY, "-m", "factory.envbuild.calibrate", "--seed", str(sf),
             "--repo-root", args.import_root, "--out", str(out),
             "--allow-unfrozen"], env)


def stage_harvest(args, env: dict) -> None:
    run([PY, "-m", "factory.envbuild.harvest",
         "--env", str(Path("environments") / args.slug)], env)


def frozen_rubrics(args, seeds_dir: Path) -> dict[str, bool]:
    out = {}
    for sf in seeds_dir.glob("*.json"):
        rp = (Path("environments") / args.slug / "validation" / sf.stem
              / "rubric.json")
        if rp.exists():
            r = json.loads(rp.read_text(encoding="utf-8"))
            out[sf.stem] = bool(r.get("warrant", {}).get("finalized_by"))
        else:
            out[sf.stem] = False
    return out


def stage_solve(args, env: dict, seeds_dir: Path) -> None:
    frozen = frozen_rubrics(args, seeds_dir)
    allowed, skipped = [], []
    for sf in sorted(seeds_dir.glob("*.json")):
        if frozen.get(sf.stem) or args.allow_unfrozen:
            allowed.append(sf)
        else:
            skipped.append(sf.stem)
    if skipped:
        print(f"[solve] GATE: {len(skipped)} seeds lack finalized warrant, "
              f"skipped: {skipped[:5]}{'...' if len(skipped) > 5 else ''}")
    if not allowed:
        print("[solve] nothing to solve")
        return
    tmp = Path(".work") / f"{args.slug}-solve-seeds"
    tmp.mkdir(parents=True, exist_ok=True)
    import shutil
    for sf in allowed:
        shutil.copy2(sf, tmp / sf.name)
    shutil.copy2(seeds_dir / "test_data.h5", tmp / "test_data.h5")
    cmd = [PY, "-m", "factory.solve.traces", "--seeds", str(tmp),
           "--out", "data", "--attempts", str(args.attempts),
           "--max-turns", str(args.max_turns),
           "--temperature", str(args.temperature)]
    if args.limit_seeds:
        cmd += ["--limit", str(args.limit_seeds)]
    run(cmd, env)


STAGES = {"vet": stage_vet, "mine": stage_mine, "propose": stage_propose,
          "verify": stage_verify, "calibrate": stage_calibrate,
          "harvest": stage_harvest, "solve": stage_solve}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--commit", default=None)
    ap.add_argument("--import-root", required=True,
                    help="dir that makes the top package importable (sys.path)")
    ap.add_argument("--package-dir", default=None,
                    help="dir to mine/archive (default: <import-root>/<slug>)")
    ap.add_argument("--stages", default="vet,mine,propose,verify,calibrate,"
                                         "harvest,solve")
    ap.add_argument("--attempts", type=int, default=4)
    ap.add_argument("--max-turns", type=int, default=3)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--limit-seeds", type=int, default=None)
    ap.add_argument("--allow-unfrozen", action="store_true",
                    help="solve seeds whose warrant is not human-finalized")
    ap.add_argument("--require-approval", action="store_true",
                    help="abort LLM stages unless module.json is approved")
    ap.add_argument("--allow-unknown-license", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.package_dir is None:
        cand = Path(args.import_root) / args.slug
        args.package_dir = str(cand if cand.is_dir() else Path(args.import_root))
        print(f"package-dir defaulted to {args.package_dir}")
    env = {k: v for k, v in os.environ.items()
           if k.startswith("SCICODE_")}
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]

    if args.require_approval:
        run([PY, "-m", "factory.envbuild.vet", "--check-approval",
             "--env", str(Path("environments") / args.slug)], env)

    mined = proposals = seeds_dir = None
    for st in stages:
        print(f"\n===== stage: {st} =====", flush=True)
        if st == "vet":
            stage_vet(args, env)
        elif st == "mine":
            mined = stage_mine(args, env)
        elif st == "propose":
            mined = mined or stage_mine(args, env)
            proposals = stage_propose(args, env, mined)
        elif st == "verify":
            proposals = proposals or Path(".work") / f"{args.slug}-proposals.jsonl"
            seeds_dir = stage_verify(args, env, proposals)
        elif st == "calibrate":
            seeds_dir = seeds_dir or Path("seeds") / args.slug
            stage_calibrate(args, env, seeds_dir)
        elif st == "harvest":
            stage_harvest(args, env)
        elif st == "solve":
            seeds_dir = seeds_dir or Path("seeds") / args.slug
            stage_solve(args, env, seeds_dir)
        else:
            raise SystemExit(f"unknown stage: {st}")
    print("\npipeline done.")


if __name__ == "__main__":
    main()
