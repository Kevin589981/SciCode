"""Read-only legacy inventory -> grounded re-audit -> visible-feedback regeneration.

One controller/writer; bounded task submission and event queue; separate model
semaphores cap BOTH generation and review at 500. Every stage is resumable.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import copy
import hashlib
import json
import os
import queue
import threading
import time
from pathlib import Path

from ..author import llm
from .batch import controller_lock
from .build_native_sft import audit_candidate
from .distill import dump, load_config
from .grade import judge_trace
from .grounded_audit import POLICY, audit, python_blocks
from .prepare_reused_tasks import file_sha256, rows, write_rows
from .resilient_client import ResilientClient
from .review_cache import ReviewCache
from .review_client import reviewer_chat
from .rollout import current_commit, collect_trace, trace_complete
from .schema import canonical_hash, validate_task
from .scientific_audit import _object


def digest(row):
    return hashlib.sha256(json.dumps(row, ensure_ascii=False).encode()).hexdigest()


def prepare(sources, inputs):
    if inputs.exists() and any(inputs.iterdir()):
        raise FileExistsError("refusing to replace prepared repair inventory")
    inputs.mkdir(parents=True)
    jobs, preservation, source_hashes, seen = [], [], {}, set()
    for source in sources:
        source = source.resolve()
        report = json.loads((source / "pipeline_report.json").read_text())
        native = Path(report["export"]["output"]).parent
        reviewed = Path(report["latest_sft"]).parent
        candidate_path = native / "audit-candidates.jsonl"
        if file_sha256(candidate_path) != report["export"]["audit_candidates_sha256"]:
            raise ValueError("legacy candidate artifact changed")
        if file_sha256(reviewed / "sft.jsonl") != report["selection"]["sft_ready_sha256"]:
            raise ValueError("legacy SFT artifact changed")
        source_hashes[str(candidate_path)] = file_sha256(candidate_path)
        source_hashes[str(reviewed / "sft.jsonl")] = file_sha256(reviewed / "sft.jsonl")
        task_map = {canonical_hash(t): t for t in rows(source / "inputs/tasks.jsonl")}
        audits = {r["trace_id"]: r for r in rows(source / "scientific-audit.jsonl")}
        grades = {}
        for r in rows(source / "reasoning-quality.jsonl"):
            grades[r["trace_id"]] = {k: r.get(k) for k in ("grade", "error", "row_sha256")}
        ready = {}
        for r in rows(reviewed / "sft.jsonl"):
            final = r["messages"][-1]["content"].split("</think>", 1)[1]
            if python_blocks(final):
                ready[r["id"]] = True
                preservation.append({"source_file": str(reviewed / "sft.jsonl"), "trace_id": r["id"],
                                     "task_hash": r["metadata"]["task_hash"]})
        with candidate_path.open("rb") as f:
            while raw := f.readline():
                offset = f.tell() - len(raw)
                row = json.loads(raw)
                if row["trace_id"] in ready:
                    continue
                task_hash = row["task_hash"]
                if task_hash in seen:
                    raise ValueError("duplicate task across repair populations")
                seen.add(task_hash)
                old_audit = audits.get(row["trace_id"]) or {}
                grade = grades.get(row["trace_id"]) or {}
                if grade.get("row_sha256") != digest(row) or grade.get("error"):
                    grade = {}
                jobs.append({"old_trace_id": row["trace_id"], "task": task_map[task_hash],
                             "candidate_file": str(candidate_path), "offset": offset, "length": len(raw),
                             "candidate_sha256": hashlib.sha256(raw).hexdigest(),
                             "old_disposition": old_audit.get("disposition", "audit_error"),
                             "prior_grade": grade.get("grade"), "source_root": str(source)})
    write_rows(inputs / "jobs.jsonl", jobs)
    write_rows(inputs / "preserved-ready-index.jsonl", preservation)
    counts = {}
    for j in jobs:
        k = j["old_disposition"]; counts[k] = counts.get(k, 0) + 1
    manifest = {"policy": POLICY, "selected": len(jobs), "preserved_ready": len(preservation),
                "selected_old_dispositions": counts, "source_candidates_sha256": source_hashes,
                "input_sha256": {n: file_sha256(inputs / n) for n in
                                 ("jobs.jsonl", "preserved-ready-index.jsonl")},
                "old_data_modified": False}
    dump(inputs / "manifest.json", manifest)
    return manifest


def read_candidate(job):
    with Path(job["candidate_file"]).open("rb") as f:
        f.seek(job["offset"]); raw = f.read(job["length"])
    if hashlib.sha256(raw).hexdigest() != job["candidate_sha256"]:
        raise ValueError("legacy row changed")
    candidate = json.loads(raw)
    if canonical_hash(job['task']) != candidate['task_hash']:
        raise ValueError('legacy task/candidate identity mismatch')
    return candidate


def grade_ready(g):
    return bool(g and g.get("trainable") is True
                and (g.get("reasoning_evidence") or {}).get("efficiency") in {"efficient", "productive_but_long"}
                and any(a.get("message_index") == 2 and a.get("train_reasoning") is True
                        and a.get("train_content") is True for a in g.get("message_annotations", [])))


def sft_row(candidate, job, review, grade, revision):
    a = candidate["messages"][-1]
    return {"id": candidate["trace_id"], "messages": [
        *[{"role": m["role"], "content": m["content"]} for m in candidate["messages"][:2]],
        {"role": "assistant", "content": "<think>\n" + a["reasoning_content"] + "\n</think>\n" + a["content"]}],
        "metadata": {"dataset_policy": POLICY, "task_hash": candidate["task_hash"],
                     "teacher_model": candidate["model"], "training_target": "reasoning_and_answer",
                     "audit_disposition": "model_supported_answer", "original_trace_id": job["old_trace_id"],
                     "original_source_root": job["source_root"], "revision_round": revision,
                     "prompt_variant": "original" if revision == 0 else "original-or-revised-task-plus-visible-feedback",
                     "code_gate": review["code_gate"], "reasoning_review": grade,
                     "scientific_correctness_proven": False}}


def run(inputs, root, cfg, *, reviewer, review_workers=500, generation_workers=500,
        repair_rounds=2, review_max_tokens=131072, teacher_fn=None, review_fn=None):
    if not 1 <= review_workers <= 500 or not 1 <= generation_workers <= 500:
        raise ValueError("model concurrency outside approved 1..500")
    manifest = json.loads((inputs / "manifest.json").read_text())
    for n, h in manifest["input_sha256"].items():
        if file_sha256(inputs / n) != h:
            raise ValueError("repair input changed")
    root.mkdir(parents=True, exist_ok=True)
    identity = {"policy": POLICY, "factory_commit": current_commit(), "inputs": manifest["input_sha256"],
                "teacher": {k: v for k, v in cfg.items() if k not in {"concurrency", "service_capacity"}},
                "reviewer_model": reviewer["model"], "reviewer_url": reviewer["base_url"],
                "review_max_tokens": review_max_tokens, "repair_rounds": repair_rounds}
    with controller_lock(root / "controller", exclusive=True):
        identity_path = root / "run_manifest.json"
        if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
            raise ValueError("repair resume identity changed")
        dump(identity_path, identity)
        jobs = list(rows(inputs / "jobs.jsonl"))
        events = queue.Queue(maxsize=128)
        # ResilientClient emits while holding its own condition. Never block
        # there: the writer takes the same condition to snapshot providers.
        transport_events = queue.SimpleQueue()
        writer_failures = []
        def emit(event):
            while True:
                if writer_failures:
                    raise RuntimeError('single writer failed') from writer_failures[0]
                try:
                    events.put(event, timeout=1)
                    return
                except queue.Full:
                    continue
        review_slots, gen_slots = threading.BoundedSemaphore(review_workers), threading.BoundedSemaphore(generation_workers)
        def provider_event(model):
            return lambda record: transport_events.put({"model": model, **record})
        rt = ResilientClient(review_fn or llm.chat, emit=provider_event(reviewer["model"]))
        gt = ResilientClient(teacher_fn or llm.chat, emit=provider_event(cfg["model"]))
        bounded_review = reviewer_chat(reviewer, context_window_tokens=cfg["context_window_tokens"], chat_fn=rt,
            count_fn=(lambda messages, model: (sum(len(m['content'].encode()) for m in messages), 'test-upper')) if review_fn else None)
        def review_chat(messages, **kwargs):
            with review_slots:
                return bounded_review(messages, **kwargs)
        teacher_client = {"base_url": cfg["base_url"], "model": cfg["model"],
                          "api_key": os.environ.get("SCICODE_LLM_API_KEY", "")}
        state = {"stage": "reaudit_and_live_repair", "selected": len(jobs), "preserved_ready": manifest["preserved_ready"],
                 "review_concurrency": review_workers, "generation_concurrency": generation_workers,
                 "started_at": time.time(), "outcomes": {}, "events": {}}
        outcomes = {}
        def writer():
            files = {}
            try:
                for k in ("audits", "grades", "generations", "outcomes", "raw-teacher-responses", "transport"):
                    files[k] = (root / (k + ".jsonl")).open("a", encoding="utf-8")
                while True:
                    try: event = events.get(timeout=10)
                    except queue.Empty: event = "heartbeat"
                    while not transport_events.empty():
                        record = transport_events.get_nowait()
                        files['transport'].write(json.dumps(record,ensure_ascii=False)+'\n')
                        state['events']['transport'] = state['events'].get('transport',0)+1
                    files['transport'].flush()
                    if event is None: break
                    if event != "heartbeat":
                        kind, record = event
                        files[kind].write(json.dumps(record, ensure_ascii=False) + "\n"); files[kind].flush()
                        state["events"][kind] = state["events"].get(kind, 0) + 1
                    state["outcomes"] = dict(__import__('collections').Counter(r["status"] for r in outcomes.copy().values()))
                    state["teacher_provider"], state["review_provider"] = gt.snapshot(), rt.snapshot()
                    state["updated_at"] = time.time(); dump(root / "progress.json", state)
            except BaseException as exc: writer_failures.append(exc)
            finally:
                for f in files.values(): f.close()
        wt = threading.Thread(target=writer, name="repair-single-writer"); wt.start()
        def stage(path, candidate, fn):
            row_hash = digest(candidate)
            if path.exists():
                cached = json.loads(path.read_text())
                if cached["row_sha256"] != row_hash: raise ValueError("stage row hash changed")
                return cached["result"]
            cache = ReviewCache(root / "review-cache", review_chat,
                                {"policy": POLICY, "model": reviewer["model"], "context": cfg["context_window_tokens"]})
            feedback = ""
            for attempt in range(4):
                def chat(messages, **kwargs):
                    if feedback:
                        messages = copy.deepcopy(messages)
                        messages[-1]["content"] += "\nPrior machine validation failed: " + feedback + \
                            "\nUse exact contiguous original quotes, without added quotes/ellipses or paraphrase. Return a full substantive JSON."
                    return cache(messages, **kwargs)
                try:
                    result = fn(cache if not feedback else chat)
                    dump(path, {"row_sha256": row_hash, "result": result})
                    return result
                except ValueError as exc:
                    cache.reject_last(exc); feedback = str(exc)[:1600] + f" (retry {attempt+1})"
            raise ValueError(feedback)
        def generate(messages, task, path, old_id, revision):
            if path.exists():
                prior = json.loads(path.read_text())
                if trace_complete(prior, require_both_channels=True): return prior
                dump(path.with_name(path.stem + ".incomplete-" + digest(prior)[:12] + ".json"), prior)
            def chat(prompt, **kwargs):
                with gen_slots:
                    response = gt(prompt, client=teacher_client, extra_body=cfg["request_options"], **kwargs)
                emit(("raw-teacher-responses", {"old_trace_id": old_id, "revision": revision,
                                                     "messages": prompt, "response": response}))
                return response
            bound = sum(len(m["content"].encode()) for m in messages) + cfg["input_margin_tokens"]
            if bound + cfg["max_tokens"] > cfg["context_window_tokens"]:
                raise ValueError("complete repair prompt exceeds conservative context budget; no truncation")
            trace = collect_trace(task, prompt_messages=messages, chat_fn=chat, model=cfg["model"],
                                 temperature=cfg["temperature"], max_tokens=cfg["max_tokens"], timeout=cfg["timeout"],
                                 factory_commit=identity["factory_commit"], run_variant=f"grounded-code-repair-{revision}")
            dump(path, trace); emit(("generations", trace))
            return trace
        def one(job):
            if writer_failures: raise RuntimeError("writer failed")
            key = hashlib.sha256(job["old_trace_id"].encode()).hexdigest()
            folder = root / "items" / key; folder.mkdir(parents=True, exist_ok=True)
            terminal = folder / "terminal.json"
            if terminal.exists():
                value = json.loads(terminal.read_text())
                if value["status"] not in {"error", "incomplete_generation"}: return value
            task = job["task"]; candidate = read_candidate(job)
            current_grade = job.get("prior_grade")
            last_review = None
            for revision in range(repair_rounds + 1):
                review = stage(folder / f"audit-{revision}.json", candidate,
                               lambda chat: audit(candidate, chat_fn=chat, model=reviewer["model"], max_tokens=review_max_tokens))
                emit(("audits", {"old_trace_id": job["old_trace_id"], "revision": revision,
                                       "row_sha256": digest(candidate), **review}))
                last_review = review
                if review["disposition"] == "needs_verification":
                    status = "needs_verification"; break
                if review["disposition"] == "model_supported_answer":
                    if current_grade is None:
                        current_grade = stage(folder / f"grade-{revision}.json", candidate,
                            lambda chat: judge_trace(task, candidate, chat_fn=chat, model=reviewer["model"],
                                                     max_tokens=review_max_tokens, max_input_chars=4_000_000,
                                                     require_reasoning_evidence=True))
                        emit(("grades", {"old_trace_id": job["old_trace_id"], "revision": revision,
                                               "row_sha256": digest(candidate), "grade": current_grade}))
                    if grade_ready(current_grade):
                        status = "accepted_original" if revision == 0 else "accepted_regenerated"
                        dump(folder / "sft.json", sft_row(candidate, job, review, current_grade, revision)); break
                if revision == repair_rounds:
                    status = "repair_exhausted"; break
                original_user = candidate["messages"][1]["content"].split("\n\nVISIBLE REGENERATION INSTRUCTIONS:", 1)[0]
                if review["disposition"] == "task_repair":
                    rewrite_path = folder / f"task-rewrite-{revision+1}.json"
                    if rewrite_path.exists(): rewrite = json.loads(rewrite_path.read_text())
                    else:
                        prompt = [{"role": "user", "content": "Repair this flawed scientific coding question, not its answer. Preserve scientific intent; resolve contradictory conventions or missing synthetic setup explicitly, never invent real empirical facts. Require executable Python. Return ONLY JSON {question: self-contained revised question, background: scientific context, requirements: at least two deliverables, change_log: explanation}. Do not include a solution.\nORIGINAL:\n" + original_user + "\nFINDINGS:\n" + json.dumps(review['adjudication'],ensure_ascii=False)}]
                        with gen_slots:
                            response = gt(prompt, client=teacher_client, model=cfg["model"], temperature=0.0,
                                          max_tokens=65536, timeout=cfg["timeout"], extra_body=cfg["request_options"])
                        emit(("raw-teacher-responses", {"old_trace_id": job["old_trace_id"], "kind": "task_rewrite", "response": response}))
                        rewrite = _object(response)
                        if (not isinstance(rewrite.get("question"), str) or len(rewrite["question"]) < 120
                            or not isinstance(rewrite.get("background"), str) or len(rewrite["background"]) < 60
                            or not isinstance(rewrite.get("requirements"), list) or len(rewrite["requirements"]) < 2):
                            raise ValueError("invalid revised task, retained raw response")
                        dump(rewrite_path, rewrite)
                    task = copy.deepcopy(task)
                    task["task_id"] = job["task"]["task_id"] + "-auditfix-" + canonical_hash(rewrite)[:8]
                    task["problem"] = {"question": rewrite["question"], "background": rewrite["background"]}
                    task["deliverable"] = {"kind": "analysis_and_code", "requirements": rewrite["requirements"]}
                    validate_task(task); dump(folder / f"task-{revision+1}.json", task)
                    original_user = task["problem"]["question"] + "\nScientific background:\n" + task["problem"]["background"] + "\nDeliverables:\n" + "\n".join(task["deliverable"]["requirements"])
                feedback = json.dumps(review["adjudication"], ensure_ascii=False)
                messages = [{"role": "system", "content": candidate["messages"][0]["content"]},
                            {"role": "user", "content": original_user + "\n\nVISIBLE REGENERATION INSTRUCTIONS:\nProduce a NEW scientifically sound solution and complete executable Python implementation with the requested interface. Keep productive scientific reasoning; avoid repeated planning or re-deriving settled facts. Do not claim you ran experiments; provide runnable validation code and distinguish analytically derived from unexecuted results. Final response should focus on the code plus essential assumptions.\nA prior draft's review is fallible; independently check it against the actual question, do not adopt invented restrictions:\n" + feedback + "\nPrior reasoning training assessment:\n" + json.dumps(current_grade,ensure_ascii=False)}]
                trace = generate(messages, task, folder / f"generation-{revision+1}.json", job["old_trace_id"], revision+1)
                if not trace_complete(trace, require_both_channels=True):
                    status = "incomplete_generation"; break
                candidate = audit_candidate(task, trace); current_grade = None
            value = {"old_trace_id": job["old_trace_id"], "status": status, "revision": revision,
                     "last_disposition": (last_review or {}).get("disposition"), "item_directory": str(folder)}
            dump(terminal, value); return value
        try:
            with futures.ThreadPoolExecutor(max_workers=review_workers + generation_workers) as pool:
                iterator = iter(jobs); pending = {}
                def submit():
                    job = next(iterator, None)
                    if job is not None: pending[pool.submit(one, job)] = job
                for _ in range(min(len(jobs), review_workers + generation_workers)): submit()
                while pending:
                    done, _ = futures.wait(pending, timeout=10, return_when=futures.FIRST_COMPLETED)
                    if writer_failures: raise RuntimeError("writer failed") from writer_failures[0]
                    for future in done:
                        job = pending.pop(future)
                        try: result = future.result()
                        except Exception as exc:
                            result = {"old_trace_id": job["old_trace_id"], "status": "error",
                                      "error": f"{type(exc).__name__}: {exc}"[:2000]}
                        outcomes[job["old_trace_id"]] = result; emit(("outcomes", result)); submit()
        finally:
            if not writer_failures: emit(None)
            wt.join()
        if writer_failures: raise RuntimeError("writer failed") from writer_failures[0]
        version = 1
        while (root / f"release-v{version}").exists(): version += 1
        release = root / f"release-v{version}"; release.mkdir()
        for name, expected in manifest['source_candidates_sha256'].items():
            if file_sha256(Path(name)) != expected:
                raise ValueError('legacy source changed before release')
        seen = set(); counts = {"preserved_ready": 0, "accepted_original": 0, "accepted_regenerated": 0}
        with (release / "sft.jsonl").open("w", encoding="utf-8") as out, (release / "new-sft.jsonl").open("w", encoding="utf-8") as new:
            preserved = list(rows(inputs / "preserved-ready-index.jsonl"))
            by_file = {}
            for p in preserved: by_file.setdefault(p["source_file"], set()).add(p["trace_id"])
            for file, ids in by_file.items():
                for row in rows(Path(file)):
                    if row["id"] not in ids: continue
                    h=row["metadata"]["task_hash"]
                    if h in seen: raise ValueError("duplicate preserved task")
                    seen.add(h); out.write(json.dumps(row,ensure_ascii=False)+"\n"); counts['preserved_ready']+=1
            for result in outcomes.values():
                if result["status"] not in {"accepted_original", "accepted_regenerated"}: continue
                row=json.loads((Path(result["item_directory"])/"sft.json").read_text()); h=row['metadata']['task_hash']
                if h in seen: raise ValueError("duplicate repaired task")
                seen.add(h); line=json.dumps(row,ensure_ascii=False)+"\n"; out.write(line); new.write(line); counts[result['status']]+=1
        needs_retry = any(r['status'] in {'error', 'incomplete_generation'} for r in outcomes.values())
        state.update({"stage": "needs_retry" if needs_retry else "finished_with_partitions", "finished_at": time.time(), "release": str(release),
                      "release_counts": counts, "outcomes": dict(__import__('collections').Counter(r['status'] for r in outcomes.values()))})
        dump(root / "progress.json", state); dump(release / "manifest.json", state)
        return state


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('command',choices=['prepare','run'])
    parser.add_argument('--inputs',type=Path,required=True); parser.add_argument('--root',type=Path)
    parser.add_argument('--sources',type=Path,nargs='+'); parser.add_argument('--config',type=Path)
    parser.add_argument('--review-workers',type=int,default=500); parser.add_argument('--generation-workers',type=int,default=500)
    parser.add_argument('--repair-rounds',type=int,default=2)
    parser.add_argument('--recovery-passes',type=int,default=3)
    args=parser.parse_args()
    if args.command=='prepare': result=prepare(args.sources,args.inputs)
    else:
        reviewer={'model':'Kimi-K3','base_url':os.environ['SCICODE_REVIEW_BASE_URL'],'api_key':os.environ['SCICODE_REVIEW_API_KEY']}
        if not 1 <= args.recovery_passes <= 5: parser.error('recovery passes must be 1..5')
        for recovery_pass in range(args.recovery_passes):
            result=run(args.inputs,args.root,load_config(args.config),reviewer=reviewer,
                       review_workers=args.review_workers,generation_workers=args.generation_workers,repair_rounds=args.repair_rounds)
            if result['stage'] != 'needs_retry': break
            print(json.dumps({'recovery_pass':recovery_pass+1,'outcomes':result['outcomes']}),flush=True)
            if recovery_pass+1 < args.recovery_passes: time.sleep(30)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__': main()
