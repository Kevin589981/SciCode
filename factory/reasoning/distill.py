"""Controlled prompt-reuse distillation; only run/audit/grade call model APIs."""

from __future__ import annotations

import argparse
import copy
import json
import os
import time
import hashlib
import concurrent.futures as futures
import threading
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from ..author import llm
from .batch import controller_lock
from .build_native_sft import build
from .prepare_reused_tasks import file_sha256, prepare, rows
from .rollout import current_commit, run_rollouts, trace_id_for
from .schema import canonical_hash, validate_task
from .student_view import REUSED_PROMPT_POLICY, trace_student_prompt

POLICY = "scicode-deepseek-prompt-reuse-distillation-v1"


def dump(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    allowed = {"model", "base_url", "teacher_revision", "temperature", "max_tokens",
               "context_window_tokens", "input_margin_tokens", "timeout", "concurrency",
               "service_capacity", "attempts", "run_variant", "retry_incomplete", "request_options"}
    if set(cfg) - allowed:
        raise ValueError(f"unknown/secret config fields: {sorted(set(cfg) - allowed)}")
    cfg.setdefault("attempts", 1)
    cfg.setdefault("retry_incomplete", True)
    cfg.setdefault("input_margin_tokens", 4096)
    cfg.setdefault("request_options", {})
    options = cfg["request_options"]
    if (not isinstance(options, dict) or set(options) - {"thinking", "reasoning_effort"}
        or ("thinking" in options and options["thinking"] != {"type": "enabled"})
        or ("reasoning_effort" in options and options["reasoning_effort"] not in {"low", "high", "max"})):
        raise ValueError("invalid reasoning provider options; only enabled thinking is supported")
    cfg["base_url"] = cfg["base_url"].rstrip("/")
    parsed = urlparse(cfg["base_url"])
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base_url must not include credentials, query or fragment")
    if not parsed.path.endswith("/v1"):
        raise ValueError("base_url must include /v1")
    for name in ("max_tokens", "context_window_tokens", "timeout", "concurrency", "service_capacity", "attempts", "input_margin_tokens"):
        if type(cfg.get(name)) is not int or cfg[name] < 1:
            raise ValueError(f"{name} must be a positive integer")
    if type(cfg.get("temperature")) not in (int, float) or not 0 <= cfg["temperature"] <= 2:
        raise ValueError("temperature must be in [0, 2]")
    if type(cfg["retry_incomplete"]) is not bool:
        raise ValueError("retry_incomplete must be a boolean")
    if cfg["concurrency"] > cfg["service_capacity"] or cfg["service_capacity"] > 2000:
        raise ValueError("concurrency exceeds configured capacity / 2000 hard ceiling")
    if cfg["max_tokens"] + cfg["input_margin_tokens"] >= cfg["context_window_tokens"]:
        raise ValueError("output budget leaves no prompt context")
    for name in ("model", "teacher_revision", "run_variant"):
        if not isinstance(cfg.get(name), str) or not cfg[name].strip():
            raise ValueError(f"{name} is required")
    return cfg


def validate_inputs(inputs: Path, cfg: dict) -> dict:
    report = json.loads((inputs / "selection_report.json").read_text(encoding="utf-8"))
    if report.get("require_supported") is not True or report.get("old_assistant_responses_copied") != 0:
        raise ValueError("inputs must be prepared with --require-supported")
    hashes = report.get("input_sha256") or {}
    for name in ("tasks.jsonl", "prompts.jsonl", "source_index.jsonl"):
        if file_sha256(inputs / name) != hashes.get(name):
            raise ValueError(f"input artifact changed: {name}")
    tasks = {r["task_id"]: validate_task(r) for r in rows(inputs / "tasks.jsonl")}
    prompts = {r["task_hash"]: r["messages"] for r in rows(inputs / "prompts.jsonl")}
    sources = {r["task_hash"]: r for r in rows(inputs / "source_index.jsonl")}
    expected = report["selected"]
    if not expected or any(len(v) != expected for v in (tasks, prompts, sources)):
        raise ValueError("duplicate/missing task, prompt or source IDs")
    maximum_input_bound = 0
    for task in tasks.values():
        task_hash = canonical_hash(task)
        prompt = prompts[task_hash]
        expected_hash = canonical_hash({"policy_version": REUSED_PROMPT_POLICY, "messages": prompt})
        if sources[task_hash].get("student_view_hash") != expected_hash:
            raise ValueError("selected source/prompt hash mismatch")
        trace_student_prompt(task, {"messages": prompt, "provenance": {
            "student_view_policy": REUSED_PROMPT_POLICY, "student_view_hash": expected_hash,
        }})
        # Byte-level tokenizer upper-bound estimate, with explicit chat-format
        # margin. This is NOT a tokenizer-certified input token count.
        bound = sum(len(m["content"].encode("utf-8")) for m in prompt) + cfg["input_margin_tokens"]
        maximum_input_bound = max(bound, maximum_input_bound)
        if bound + cfg["max_tokens"] > cfg["context_window_tokens"]:
            raise ValueError(f"conservative context budget exceeded: {task['task_id']}")
    identity = {
        "policy": POLICY, "input_sha256": hashes,
        "source_sft_sha256": report["source_sft_sha256"],
        "generation": {k: v for k, v in cfg.items() if k not in {"concurrency", "service_capacity"}},
        "factory_commit": current_commit(Path(__file__).resolve().parents[2]),
    }
    return {**identity, "fingerprint": canonical_hash(identity), "selected": expected,
            "max_prompt_token_upper_estimate": maximum_input_bound,
            "context_estimate_policy": "utf8-bytes-plus-chat-margin-not-certified",
            "requested_concurrency": cfg["concurrency"],
            "service_capacity": cfg["service_capacity"]}


def run(inputs: Path, output: Path, cfg: dict, *, chat_fn=llm.chat) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    with controller_lock(output / "run", exclusive=True):
        manifest = validate_inputs(inputs, cfg)
        path = output / "run_manifest.json"
        if path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get("fingerprint") != manifest["fingerprint"]:
                raise ValueError("resume configuration/input/code changed; use a fresh output directory")
        elif any(output.glob("*.jsonl")):
            raise ValueError("refusing to adopt existing traces without an immutable run manifest")
        else:
            dump(path, manifest)
        if chat_fn is llm.chat:
            if not os.environ.get("SCICODE_LLM_API_KEY"):
                raise ValueError("set SCICODE_LLM_API_KEY outside code/config")
            os.environ["SCICODE_LLM_BASE_URL"] = cfg["base_url"]
            os.environ["SCICODE_LLM_MODEL"] = cfg["model"]
            host = urlparse(cfg["base_url"]).hostname
            os.environ["NO_PROXY"] = host + "," + os.environ.get("NO_PROXY", "")
            os.environ["no_proxy"] = os.environ["NO_PROXY"]
        # Validate existing traces before any model calls, including completed
        # duplicates, exact prompts and teacher identity.
        traces = output / "traces.jsonl"
        tasks = {r["task_id"]: r for r in rows(inputs / "tasks.jsonl")}
        prompts = {r["task_hash"]: r["messages"] for r in rows(inputs / "prompts.jsonl")}
        completed = set()
        if traces.exists():
            for trace in rows(traces):
                task = tasks[trace["task_id"]]
                if (trace["model"] != cfg["model"] or trace["task_hash"] != canonical_hash(task)
                    or trace["messages"][:2] != prompts[trace["task_hash"]]
                    or trace["temperature"] != cfg["temperature"] or trace["max_tokens"] != cfg["max_tokens"]
                    or (trace.get("provenance") or {}).get("run_variant") != cfg["run_variant"]
                    or not 0 <= trace.get("attempt", -1) < cfg["attempts"]
                    or trace["trace_id"] != trace_id_for(task, cfg["model"], trace["attempt"], cfg["run_variant"])):
                    raise ValueError("existing trace has incompatible generation identity")
                trace_student_prompt(task, trace)
                if trace["trace_id"] in completed:
                    raise ValueError("duplicate completed trace in output")
                if trace.get("finish_reason") == "stop" and not trace.get("truncated"):
                    completed.add(trace["trace_id"])
        started = time.time()
        # Preserve every returned response BEFORE trace schema validation. A
        # proxy that drops thinking must not silently erase the final answer.
        raw_lock = threading.Lock()
        with (output / "raw-responses.jsonl").open("a", encoding="utf-8") as raw_sink:
            def preserved_chat(messages, **kwargs):
                if cfg.get("request_options"):
                    kwargs["extra_body"] = cfg["request_options"]
                response = chat_fn(messages, **kwargs)
                record = {"model": cfg["model"], "run_fingerprint": manifest["fingerprint"],
                          "messages": messages, "response": response, "received_at": time.time()}
                with raw_lock:
                    raw_sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                    raw_sink.flush()
                return response
            result = run_rollouts(
                inputs / "tasks.jsonl", traces, prompts_path=inputs / "prompts.jsonl",
                chat_fn=preserved_chat, model=cfg["model"], attempts=cfg["attempts"],
                temperature=cfg["temperature"], max_tokens=cfg["max_tokens"],
                timeout=cfg["timeout"], concurrency=cfg["concurrency"],
                run_variant=cfg["run_variant"], retry_incomplete=cfg["retry_incomplete"],
                factory_commit=manifest["factory_commit"], progress_path=output / "progress.json",
            )
        result.update({"fingerprint": manifest["fingerprint"], "elapsed_seconds": time.time() - started})
        dump(output / "last_run_report.json", result)
        return result


def export(inputs: Path, output: Path, out_dir: Path) -> dict:
    with controller_lock(output / "run", exclusive=True):
        manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
        for name, digest in manifest["input_sha256"].items():
            if file_sha256(inputs / name) != digest:
                raise ValueError("inputs changed since generation")
        if not (output / "traces.jsonl").exists() or not (output / "traces.jsonl").stat().st_size:
            raise ValueError("no valid traces to export; inspect request errors/raw-responses first")
        return build(inputs / "tasks.jsonl", output / "traces.jsonl", out_dir,
                     policy=POLICY, expected_teacher=manifest["generation"]["model"],
                     repair_qwen=False, source_index_path=inputs / "source_index.jsonl",
                     run_manifest=manifest)


def grade_reasoning(inputs: Path, native: Path, output: Path, reviewer: str,
                    *, workers=64, max_tokens=65536, timeout=2400, chat_fn=llm.chat) -> dict:
    """One reviewer model, full CoT, offset-indexed bounded scheduling."""
    from .grade import judge_trace, GRADE_POLICY_VERSION
    if not 1 <= workers <= 2000:
        raise ValueError("workers must be in [1, 2000]")
    source_path = native / "audit-candidates.jsonl"
    if source_path.resolve() == output.resolve():
        raise ValueError("grade output cannot overwrite candidates")
    tasks = {r["task_id"]: r for r in rows(inputs / "tasks.jsonl")}
    previous = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with controller_lock(output, exclusive=True):
        if output.exists():
            for old in rows(output):
                if old.get("policy") != GRADE_POLICY_VERSION or old.get("model") != reviewer:
                    raise ValueError("existing reasoning grades use another model/policy")
                previous[old["trace_id"]] = old
        jobs = []
        counts = Counter()
        seen = set()
        with source_path.open("rb") as stream:
            while True:
                offset = stream.tell()
                raw = stream.readline()
                if not raw:
                    break
                if not raw.strip():
                    continue
                row = json.loads(raw)
                identity = row["trace_id"]
                if identity in seen:
                    raise ValueError("duplicate reasoning candidate")
                seen.add(identity)
                digest = hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest()
                old = previous.get(identity)
                if old and old.get("row_sha256") != digest:
                    raise ValueError("reasoning candidate changed since review")
                if old and "error" not in old:
                    counts["skipped"] += 1
                else:
                    jobs.append((offset, len(raw), identity, digest))

        def one(job):
            offset, length, identity, digest = job
            result = {"policy": GRADE_POLICY_VERSION, "model": reviewer,
                      "trace_id": identity, "row_sha256": digest}
            try:
                with source_path.open("rb") as stream:
                    stream.seek(offset)
                    raw = stream.read(length)
                if hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest() != digest:
                    raise ValueError("candidate changed during reasoning review")
                trace = json.loads(raw)
                result["grade"] = judge_trace(tasks[trace["task_id"]], trace, model=reviewer,
                                              max_tokens=max_tokens, timeout=timeout,
                                              max_input_chars=750000, chat_fn=chat_fn)
            except Exception as exc:
                result["error"] = f"{type(exc).__name__}: {exc}"[:1000]
            return result

        with output.open("a", encoding="utf-8") as sink, futures.ThreadPoolExecutor(max_workers=workers) as pool:
            iterator = iter(jobs)
            pending = {pool.submit(one, job) for job in jobs[:workers]}
            iterator = iter(jobs[workers:])
            while pending:
                done, pending = futures.wait(pending, return_when=futures.FIRST_COMPLETED)
                for future in done:
                    result = future.result()
                    sink.write(json.dumps(result, ensure_ascii=False) + "\n")
                    sink.flush()
                    counts["error" if "error" in result else "reviewed"] += 1
                    job = next(iterator, None)
                    if job is not None:
                        pending.add(pool.submit(one, job))
        return dict(counts)


def select_reviewed(native: Path, audit_path: Path, out_dir: Path, reviewer: str,
                    *, grades_path: Path | None = None) -> dict:
    """Partition full CoT+answer rows; NEVER strip final answers in any subset."""
    from .scientific_audit import POLICY as audit_policy
    native_manifest = json.loads((native / "manifest.json").read_text(encoding="utf-8"))
    if (file_sha256(native / "sft.jsonl") != native_manifest.get("sha256")
        or file_sha256(native / "audit-candidates.jsonl") != native_manifest.get("audit_candidates_sha256")):
        raise ValueError("native SFT/candidates changed after export")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError("refusing to overwrite a nonempty reviewed export")
    audits = {}
    for audit in rows(audit_path):
        if audit.get("policy") != audit_policy or audit.get("model") != reviewer:
            raise ValueError("wrong audit policy or reviewer")
        old = audits.get(audit["trace_id"])
        if old and (old.get("row_sha256") != audit.get("row_sha256") or "error" not in old):
            raise ValueError("unsafe duplicate audit")
        audits[audit["trace_id"]] = audit
    # Store only row hashes; very long teacher answers need not occupy RAM twice.
    candidates = {}
    with (native / "audit-candidates.jsonl").open("rb") as source:
        for raw in source:
            if raw.strip():
                candidates[json.loads(raw)["trace_id"]] = hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest()
    for identity, audit in audits.items():
        if audit.get("row_sha256") != candidates.get(identity):
            raise ValueError("audit refers to a different candidate response")
    grades = {}
    if grades_path is not None:
        from .grade import GRADE_POLICY_VERSION
        for grade in rows(grades_path):
            if (grade.get("policy") != GRADE_POLICY_VERSION or grade.get("model") != reviewer
                or grade.get("row_sha256") != candidates.get(grade.get("trace_id"))):
                raise ValueError("reasoning review does not match candidate/policy/model")
            old = grades.get(grade["trace_id"])
            if old and "error" not in old:
                raise ValueError("duplicate completed reasoning review")
            grades[grade["trace_id"]] = grade
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = Counter()
    paths = {status: out_dir / f"{status}.jsonl" for status in (
        "model_supported_answer", "reasoning_candidate", "quarantine", "audit_error", "not_reviewed")}
    handles = {status: path.open("w", encoding="utf-8") for status, path in paths.items()}
    ready = (out_dir / "sft.jsonl").open("w", encoding="utf-8")
    try:
        for row in rows(native / "sft.jsonl"):
            audit = audits.get(row["id"])
            status = "not_reviewed" if audit is None else "audit_error" if "error" in audit else audit["disposition"]
            if status not in handles:
                raise ValueError(f"unknown audit status: {status}")
            row = copy.deepcopy(row)
            row["metadata"].update({"audit_disposition": status, "audit_policy": audit_policy,
                                    "reviewer_model": reviewer, "training_target": "reasoning_and_answer"})
            quality = grades.get(row["id"])
            row["metadata"]["reasoning_review"] = quality
            handles[status].write(json.dumps(row, ensure_ascii=False) + "\n")
            counts[status] += 1
            value = (quality or {}).get("grade") or {}
            annotations = value.get("message_annotations") or []
            if (status == "model_supported_answer" and value.get("trainable") is True
                and any(a.get("message_index") == 2 and a.get("train_reasoning") is True
                        and a.get("train_content") is True for a in annotations)):
                ready.write(json.dumps(row, ensure_ascii=False) + "\n")
                counts["sft_ready"] += 1
    finally:
        for handle in handles.values():
            handle.close()
        ready.close()
    report = {"policy": POLICY, "counts": dict(counts), "final_answers_removed": 0,
              "scientific_correctness_proven": False,
              "source_native_sha256": file_sha256(native / "sft.jsonl"),
              "audit_sha256": file_sha256(audit_path),
              "reasoning_review_sha256": file_sha256(grades_path) if grades_path else None,
              "sft_ready_requires": "answer support + reasoning grade trainable + both channels approved",
              "sft_ready_sha256": file_sha256(out_dir / "sft.jsonl"),
              "output_sha256": {s: file_sha256(p) for s, p in paths.items()}}
    dump(out_dir / "manifest.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--sft", type=Path, required=True)
    prep.add_argument("--index", type=Path, required=True)
    prep.add_argument("--source-root", type=Path, required=True)
    prep.add_argument("--out-dir", type=Path, required=True)
    prep.add_argument("--target", type=int, default=4586)
    prep.add_argument("--per-repo", type=int, default=0)
    for name in ("validate", "run"):
        sub = commands.add_parser(name)
        sub.add_argument("--inputs", type=Path, required=True)
        sub.add_argument("--config", type=Path, required=True)
        if name == "run":
            sub.add_argument("--output", type=Path, required=True)
    sub = commands.add_parser("export")
    sub.add_argument("--inputs", type=Path, required=True)
    sub.add_argument("--output", type=Path, required=True)
    sub.add_argument("--out-dir", type=Path, required=True)
    for name in ("audit", "grade"):
        sub = commands.add_parser(name)
        sub.add_argument("--native", type=Path, required=True)
        sub.add_argument("--out", type=Path, required=True)
        sub.add_argument("--reviewer", default="Kimi-K3")
        sub.add_argument("--workers", type=int, default=64)
        sub.add_argument("--max-tokens", type=int, default=65536)
        sub.add_argument("--timeout", type=int, default=2400)
        if name == "grade":
            sub.add_argument("--inputs", type=Path, required=True)
    sub = commands.add_parser("select")
    sub.add_argument("--native", type=Path, required=True)
    sub.add_argument("--audit", type=Path, required=True)
    sub.add_argument("--out-dir", type=Path, required=True)
    sub.add_argument("--reviewer", default="Kimi-K3")
    sub.add_argument("--grades", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(sft_path=args.sft, index_path=args.index, source_root=args.source_root,
                         out_dir=args.out_dir, target=args.target, per_repo=args.per_repo,
                         require_supported=True)
    elif args.command == "validate":
        result = validate_inputs(args.inputs, load_config(args.config))
    elif args.command == "run":
        result = run(args.inputs, args.output, load_config(args.config))
    elif args.command == "export":
        result = export(args.inputs, args.output, args.out_dir)
    elif args.command in {"audit", "grade"}:
        from .scientific_audit import run_audit
        if not 1 <= args.workers <= 2000:
            raise ValueError("review workers must be in [1, 2000]")
        # Separate review credentials/config; never silently use solver endpoint.
        for name in ("BASE_URL", "API_KEY"):
            value = os.environ.get("SCICODE_REVIEW_" + name)
            if not value:
                raise ValueError("set SCICODE_REVIEW_" + name)
            os.environ["SCICODE_LLM_" + name] = value
        os.environ["SCICODE_LLM_MODEL"] = args.reviewer
        if args.command == "audit":
            result = run_audit(args.native / "audit-candidates.jsonl", args.out,
                               model=args.reviewer, workers=args.workers,
                               max_tokens=args.max_tokens, timeout=args.timeout)
        else:
            result = grade_reasoning(args.inputs, args.native, args.out, args.reviewer,
                                     workers=args.workers, max_tokens=args.max_tokens,
                                     timeout=args.timeout)
    else:
        result = select_reviewed(args.native, args.audit, args.out_dir, args.reviewer,
                                 grades_path=args.grades)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
