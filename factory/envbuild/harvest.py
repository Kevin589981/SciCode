"""Aggregate calibrated checks into harvest.json + WRITER-BRIEF.md.

Mirrors ScienceIDE environments/<env>/authoring/: the per-check brief that
downstream task writers (or the solver/traces stage) consume. All numbers
come from calibrate's measured evidence, not from labels.

Usage:
  python -m factory.envbuild.harvest --env environments/<env> --seeds seeds-dir
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def harvest(env_dir: Path, seeds_dir: Path | None = None) -> dict:
    module = {}
    mp = env_dir / "validation" / "module.json"
    if mp.exists():
        module = json.loads(mp.read_text(encoding="utf-8"))
    checks = []
    for rubric_p in sorted((env_dir / "validation").rglob("rubric.json")):
        rubric = json.loads(rubric_p.read_text(encoding="utf-8"))
        row_p = rubric_p.parent / "row.json"
        row = json.loads(row_p.read_text(encoding="utf-8")) if row_p.exists() else {}
        crit = rubric["criteria"][0]
        checks.append({
            "check": rubric["check"],
            "policy": "pointwise",
            "observable": rubric["output"]["variables"],
            "atol": row.get("atol"),
            "rtol": row.get("rtol"),
            "noise_floor": crit["evidence"].get("noise_floor"),
            "byte_identical_runs": crit["evidence"].get("byte_identical_runs"),
            "variant_note": crit["evidence"].get("variant_note"),
            "faults": crit["evidence"].get("faults", []),
            "warrant_finalized": bool(rubric.get("warrant", {}).get("finalized_by")),
            "expected_runtime_s": row.get("expected_runtime_sec"),
            "veins": row.get("veins", []),
            "science": row.get("science", ""),
        })
    out = {"env": env_dir.name, "module": module.get("module", {}),
           "approval": module.get("approval", {}), "checks": checks}

    auth = env_dir / "authoring"
    auth.mkdir(parents=True, exist_ok=True)
    (auth / "harvest.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    lines = [f"# Writer brief: {env_dir.name}", ""]
    ap = out["approval"]
    lines.append(f"approval: {ap.get('status')} "
                 f"{(ap.get('human_ref') or '')[:100]}")
    lines.append("")
    for c in checks:
        lines.append(f"## {c['check']}")
        lines.append("")
        lines.append(f"- science: {c['science'][:200]}")
        lines.append(f"- observable: {', '.join(c['observable'])} "
                     f"(pointwise, atol={c['atol']:.3g}, rtol={c['rtol']:.3g})")
        lines.append(f"- measured noise_floor (ulp-variant spread): "
                     f"{c['noise_floor']}")
        lines.append(f"- double-run byte identical: {c['byte_identical_runs']}")
        if c["variant_note"]:
            lines.append(f"- VARIANT WARNING: {c['variant_note']}")
        for f in c["faults"]:
            lines.append(f"- fault probe {f['family']}: drift={f['output_drift']} "
                         f"rejected_at_atol={f['rejected_at_atol']}")
        lines.append(f"- warrant finalized: {c['warrant_finalized']}; "
                     f"runtime ~{c['expected_runtime_s']}s; veins: {c['veins']}")
        lines.append("")
    (auth / "WRITER-BRIEF.md").write_text("\n".join(lines), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", type=Path, required=True)
    ap.add_argument("--seeds", type=Path, default=None)
    args = ap.parse_args()
    out = harvest(args.env, args.seeds)
    print(f"harvest: {len(out['checks'])} checks -> "
          f"{args.env / 'authoring' / 'harvest.json'} (+ WRITER-BRIEF.md)")


if __name__ == "__main__":
    main()
