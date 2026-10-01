"""Generate and review concurrently; queues contain disk offsets, not long CoTs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import threading
import time
import concurrent.futures as futures
from pathlib import Path
from urllib.parse import urlparse

from ..author import llm
from .batch import controller_lock
from .build_native_sft import audit_candidate
from .distill import (REASONING_REVIEW_POLICY, dump, export, load_config, run,
                      select_reviewed, validate_inputs)
from .grade import judge_trace
from .prepare_reused_tasks import rows
from .review_client import reviewer_chat
from .scientific_audit import POLICY as AUDIT_POLICY, audit_row
from .resilient_client import ResilientClient
from .review_cache import ReviewCache
from .rollout import trace_complete

POLICY = "deepseek-kimi-live-distillation-v1"


def previous_records(path, policy, model):
    result = {}
    if path.exists():
        for row in rows(path):
            if row.get("policy") != policy or row.get("model") != model:
                raise ValueError("existing review has incompatible model/policy")
            identity = row["trace_id"]
            old = result.get(identity)
            if old and (old["row_sha256"] != row["row_sha256"] or not old["error"]):
                raise ValueError("unsafe duplicate completed review")
            result[identity] = {"row_sha256": row["row_sha256"], "error": "error" in row,
                                "error_detail": row.get("error", ""),
                                "status": row.get("disposition", "reviewed")}
    return result


def pipeline(inputs: Path, output: Path, root: Path, cfg: dict, client: dict, *,
             workers=500, context_window_tokens=262144, max_tokens=65536,
             timeout=2400, input_margin_tokens=4096, generation_passes=3,
             teacher_chat=llm.chat, review_chat=None, audit_fn=audit_row,
             grade_fn=judge_trace, review_attempts=3) -> dict:
    if not 1 <= workers <= 2000 or generation_passes < 1:
        raise ValueError("invalid worker count / generation passes")
    if not 0 < max_tokens < context_window_tokens:
        raise ValueError("invalid reviewer output/context budget")
    if cfg["context_window_tokens"] != context_window_tokens:
        raise ValueError("teacher and reviewer must use the same context window")
    root.mkdir(parents=True, exist_ok=True)
    with controller_lock(root / "pipeline", exclusive=True):
        export_version = 1
        while (root / f"native-v{export_version}").exists() or (root / f"reviewed-v{export_version}").exists():
            export_version += 1
        if export_version > 1 and not (root / "pipeline_manifest.json").exists():
            raise ValueError("existing exports lack a pipeline identity; refusing adoption")
        native, reviewed = root / f"native-v{export_version}", root / f"reviewed-v{export_version}"
        manifest = validate_inputs(inputs, cfg)
        solver_manifest = output / "run_manifest.json"
        if solver_manifest.exists() and json.loads(solver_manifest.read_text(encoding="utf-8")).get("fingerprint") != manifest["fingerprint"]:
            raise ValueError("existing teacher run has another generation identity")
        identity = {"policy": POLICY, "generation_fingerprint": manifest["fingerprint"],
                    "reviewer": client["model"], "review_base_url": client["base_url"],
                    "context_window_tokens": context_window_tokens, "max_tokens": max_tokens,
                    "input_margin_tokens": input_margin_tokens,
                    "audit_policy": AUDIT_POLICY, "grade_policy": REASONING_REVIEW_POLICY}
        manifest_path = root / "pipeline_manifest.json"
        if manifest_path.exists():
            if json.loads(manifest_path.read_text(encoding="utf-8")) != identity:
                raise ValueError("pipeline resume identity changed")
        else:
            dump(manifest_path, identity)
        tasks = {t["task_id"]: t for t in rows(inputs / "tasks.jsonl")}
        traces_path = output / "traces.jsonl"
        audit_path, grade_path = root / "scientific-audit.jsonl", root / "reasoning-quality.jsonl"
        previous = {"audit": previous_records(audit_path, AUDIT_POLICY, client["model"]),
                    "grade": previous_records(grade_path, REASONING_REVIEW_POLICY, client["model"])}
        # No global reviewer credentials: run() updates the teacher's env while
        # this explicit client stays immutable and private to review workers.
        jobs = queue.Queue(maxsize=manifest["selected"])
        results = queue.Queue()  # At most 2 short result records per selected task.
        def provider_event(model):
            return lambda record: results.put(("transport", {"model": model, **record}))
        if teacher_chat is llm.chat:
            teacher_client = {"base_url": cfg["base_url"], "model": cfg["model"],
                              "api_key": os.environ["SCICODE_LLM_API_KEY"]}
            def isolated_teacher(messages, **kwargs):
                return llm.chat(messages, client=teacher_client, **kwargs)
            teacher_transport = ResilientClient(isolated_teacher, emit=provider_event(cfg["model"]))
            effective_teacher = teacher_transport
        else:
            teacher_transport = None
            effective_teacher = teacher_chat
        review_transport = ResilientClient(emit=provider_event(client["model"])) if review_chat is None else None
        chat = review_chat or reviewer_chat(client, context_window_tokens=context_window_tokens,
            input_margin_tokens=input_margin_tokens, chat_fn=review_transport)
        scheduled = set()
        state = {"stage": "generation_and_live_review", "selected": manifest["selected"],
                 "teacher_model": cfg["model"], "reviewer_model": client["model"],
                 "teacher_concurrency": cfg["concurrency"], "review_concurrency": workers,
                 "context_window_tokens": context_window_tokens, "enqueued": 0,
                 "review_active": 0, "audit_completed": 0, "audit_errors": 0,
                 "grade_completed": 0, "grade_errors": 0, "excluded_live": 0,
                 "started_at": time.time()}
        state_lock = threading.Lock()
        writer_failed = []
        for kind in ("audit", "grade"):
            state[kind + "_completed"] = sum(not r["error"] for r in previous[kind].values())
            state[kind + "_errors"] = sum(r["error"] for r in previous[kind].values())

        def update(**values):
            with state_lock:
                state.update(values)

        def writer():
            try:
                with audit_path.open("a", encoding="utf-8", newline="\n") as audits, \
                     grade_path.open("a", encoding="utf-8", newline="\n") as grades, \
                     (root / "live-excluded.jsonl").open("a", encoding="utf-8") as excluded, \
                     (root / "transport-events.jsonl").open("a", encoding="utf-8") as transport:
                    sinks = {"audit": audits, "grade": grades, "excluded": excluded, "transport": transport}
                    while True:
                        try:
                            event = results.get(timeout=10)
                        except queue.Empty:
                            event = "heartbeat"
                        if event is None:
                            with state_lock:
                                dump(root / "pipeline_progress.json", dict(state))
                            return
                        if event != "heartbeat":
                            kind, record = event
                            sinks[kind].write(json.dumps(record, ensure_ascii=False) + "\n")
                            sinks[kind].flush()
                            with state_lock:
                                if kind == "excluded":
                                    state["excluded_live"] += 1
                                elif kind in {"audit", "grade"}:
                                    old = previous[kind].get(record["trace_id"])
                                    if old:
                                        state[kind + ("_errors" if old["error"] else "_completed")] -= 1
                                    state[kind + ("_errors" if "error" in record else "_completed")] += 1
                                    previous[kind][record["trace_id"]] = {
                                        "error": "error" in record, "row_sha256": record["row_sha256"],
                                        "error_detail": record.get("error", ""),
                                        "status": record.get("disposition", "reviewed")}
                        with state_lock:
                            state["teacher_provider"] = teacher_transport.snapshot() if teacher_transport else None
                            state["review_provider"] = review_transport.snapshot() if review_transport else None
                            state["updated_at"] = time.time()
                            dump(root / "pipeline_progress.json", dict(state))
            except BaseException as exc:
                writer_failed.append(exc)

        def review_one(job):
            offset, length = job
            with traces_path.open("rb") as stream:
                stream.seek(offset)
                raw = stream.read(length)
            trace = json.loads(raw)
            task = tasks[trace["task_id"]]
            candidate = audit_candidate(task, trace)
            payload = json.dumps(candidate, ensure_ascii=False).encode()
            digest = hashlib.sha256(payload).hexdigest()
            identity = candidate["trace_id"]
            cache = ReviewCache(root / "review-cache", chat,
                                {"model": client["model"], "base_url": client["base_url"],
                                 "context": context_window_tokens, "margin": input_margin_tokens})
            for kind, policy in (("audit", AUDIT_POLICY), ("grade", REASONING_REVIEW_POLICY)):
                old = previous[kind].get(identity)
                if old:
                    if old["row_sha256"] != digest:
                        raise ValueError("live candidate changed since review")
                    if not old["error"]:
                        continue
                feedback = old.get("error_detail", "") if old and kind == "grade" and old.get("error_detail", "").startswith("GradeError") else ""
                failed_call = 1 if feedback else None
                for review_attempt in range(review_attempts):
                    record = {"policy": policy, "model": client["model"],
                              "trace_id": identity, "row_sha256": digest,
                              "context_window_tokens": context_window_tokens, "context_calls": [],
                              "review_attempt": review_attempt}
                    captured, call_count = {}, 0
                    def captured_chat(messages, **kwargs):
                        nonlocal call_count
                        call_count += 1
                        if feedback and call_count == failed_call:
                            messages = [dict(m) for m in messages]
                            messages[-1]["content"] += (
                                "\nPREVIOUS RESPONSE FAILED MACHINE VALIDATION: " + feedback[:1600]
                                + "\nReturn a new complete JSON object. Preserve a substantive judgment, "
                                "not merely a favorable score. Quotes MUST be short exact contiguous "
                                "substrings of the original reasoning, never paraphrases or quotes "
                                "from the final answer. Do not insert ellipses or reformat text.")
                        response = cache(messages, **kwargs)
                        captured["response"] = response
                        if response.get("_context_budget"):
                            record["context_calls"].append(response["_context_budget"])
                        return response
                    try:
                        if kind == "audit":
                            record.update(audit_fn(candidate, chat_fn=captured_chat, model=client["model"],
                                                   max_tokens=max_tokens, timeout=timeout,
                                                   max_input_chars=2_000_000))
                        else:
                            record["grade"] = grade_fn(task, candidate, chat_fn=captured_chat,
                                model=client["model"], max_tokens=max_tokens, timeout=timeout,
                                max_input_chars=2_000_000, require_reasoning_evidence=True)
                    except Exception as exc:
                        record["error"] = f"{type(exc).__name__}: {exc}"[:1500]
                        if not isinstance(exc, llm.LLMRequestError):
                            cache.reject_last(exc)
                            feedback, failed_call = str(exc), call_count
                    if captured:
                        record["review_response"] = captured["response"]
                    record["review_cache_calls"] = list(cache.calls)
                    results.put((kind, record))
                    if "error" not in record or (review_transport and review_transport.fatal):
                        break

        def consumer():
            while True:
                job = jobs.get()
                if job is None:
                    return
                with state_lock:
                    state["review_active"] += 1
                try:
                    review_one(job)
                except Exception as exc:
                    # A pre-review identity/boundary failure is visible, never
                    # quietly interpreted as a successful scientific judgment.
                    results.put(("excluded", {"offset": job[0], "error": str(exc)[:1500]}))
                finally:
                    with state_lock:
                        state["review_active"] -= 1

        def enqueue(offset, length):
            if writer_failed:
                raise RuntimeError("review JSONL writer failed") from writer_failed[0]
            with traces_path.open("rb") as stream:
                stream.seek(offset)
                trace = json.loads(stream.read(length))
            if not trace_complete(trace, require_both_channels=True):
                return  # A later complete retry may still be reviewed.
            identity = trace["trace_id"]
            if identity in scheduled:
                return
            scheduled.add(identity)
            jobs.put((offset, length))
            with state_lock:
                state["enqueued"] += 1

        thread = threading.Thread(target=writer, name="review-jsonl-writer")
        thread.start()
        generation_exception = None
        reports = []
        with futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="kimi-review") as pool:
            consumers = [pool.submit(consumer) for _ in range(workers)]
            try:
                if traces_path.exists():
                    with traces_path.open("rb") as stream:
                        while raw := stream.readline():
                            enqueue(stream.tell() - len(raw), len(raw))
                for generation_pass in range(1, generation_passes + 1):
                    update(generation_pass=generation_pass)
                    report = run(inputs, output, cfg, chat_fn=effective_teacher, on_trace_written=enqueue)
                    reports.append(report)
                    dump(root / "generation_passes.json", {"reports": reports})
                    if not report.get("errors") and len(scheduled) == manifest["selected"]:
                        break
            except BaseException as exc:
                generation_exception = exc
            finally:
                update(stage="draining_live_reviews")
                for _ in consumers:
                    jobs.put(None)
                for future in consumers:
                    future.result()
        results.put(None)
        thread.join()
        if writer_failed:
            raise RuntimeError("review writer failed; original traces preserved") from writer_failed[0]
        if generation_exception:
            update(stage="error", error=str(generation_exception)[:1000])
            dump(root / "pipeline_progress.json", state)
            raise generation_exception
        update(stage="export_and_select")
        dump(root / "pipeline_progress.json", state)
        export_report = export(inputs, output, native)
        selection = select_reviewed(native, audit_path, reviewed, client["model"], grades_path=grade_path)
        report = {"generation": reports, "export": export_report, "selection": selection,
                  "selected": manifest["selected"], "review_workers": workers,
                  "export_version": export_version,
                  "latest_sft": str((reviewed / "sft.jsonl").resolve()),
                  "fatal_provider_error": bool((teacher_transport and teacher_transport.fatal) or (review_transport and review_transport.fatal)),
                  "audit_errors": state["audit_errors"], "grade_errors": state["grade_errors"],
                  "missing_complete_generations": manifest["selected"] - len(scheduled)}
        dump(root / "pipeline_report.json", report)
        update(stage="completed" if not any(report[k] for k in ("missing_complete_generations", "audit_errors", "grade_errors")) else "needs_retry",
               final_counts=selection["counts"], finished_at=time.time())
        dump(root / "pipeline_progress.json", state)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("inputs", "output", "root", "config"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--workers", type=int, default=500)
    parser.add_argument("--context-window-tokens", type=int, default=262144)
    parser.add_argument("--input-margin-tokens", type=int, default=4096)
    parser.add_argument("--max-tokens", type=int, default=65536)
    parser.add_argument("--reviewer", default="Kimi-K3")
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--recovery-rounds", type=int, default=3)
    args = parser.parse_args()
    client = {"base_url": os.environ["SCICODE_REVIEW_BASE_URL"],
              "api_key": os.environ["SCICODE_REVIEW_API_KEY"], "model": args.reviewer}
    host = urlparse(client["base_url"]).hostname
    os.environ["NO_PROXY"] = str(host) + "," + os.environ.get("NO_PROXY", "")
    os.environ["no_proxy"] = os.environ["NO_PROXY"]
    if args.recovery_rounds < 1:
        raise ValueError("recovery rounds must be positive")
    for recovery_round in range(1, args.recovery_rounds + 1):
        print(json.dumps({"event": "pipeline_round_started", "recovery_round": recovery_round,
                          "teacher": load_config(args.config)["model"], "reviewer": args.reviewer,
                          "workers": args.workers, "context_window_tokens": args.context_window_tokens}), flush=True)
        report = pipeline(args.inputs, args.output, args.root, load_config(args.config), client,
            workers=args.workers, context_window_tokens=args.context_window_tokens,
            max_tokens=args.max_tokens, timeout=args.timeout, input_margin_tokens=args.input_margin_tokens)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        incomplete = any(report[k] for k in ("missing_complete_generations", "audit_errors", "grade_errors"))
        if not incomplete:
            return
        if report["fatal_provider_error"]:
            raise SystemExit(2)
        if recovery_round < args.recovery_rounds:
            print("Incomplete work retained: retry only missing/error rows after 30s; prior exports preserved", flush=True)
            time.sleep(30)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
