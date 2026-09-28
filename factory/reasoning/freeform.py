"""Free-form scientific questions and Qwen-native thinking traces.

This is a separate v4 artifact path. The fixed-archetype v1 pipeline and its
historical JSONL remain readable and unchanged.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from pathlib import Path

from ..author import llm
from .author import _json_objects
from .schema import canonical_hash

TASK_SCHEMA = "scicode-freeform-task-v1"
TRACE_SCHEMA = "scicode-qwen-native-trace-v1"
AUDIT_SCHEMA = "scicode-freeform-audit-v1"
AUDIT_POLICY = "scientific-overall-v2"
SFT_SCHEMA = "scicode-qwen-native-sft-v1"
AUTHOR_PROMPT_POLICY = "freeform-open-v2"
AUTHOR_OPENERS = (
    "从下面的科学代码出发，写一个你认为有价值的科学问题。",
    "下面是真实项目的源码。你会提出什么科学问题？",
    "What scientific question would you ask after reading this source?",
)


class FreeformError(ValueError):
    """A v4 artifact cannot be interpreted unambiguously."""


def _message(response: dict) -> tuple[dict, dict]:
    try:
        choice = response["choices"][0]
        message = choice["message"]
        if not isinstance(message, dict):
            raise TypeError("message is not an object")
        return choice, message
    except (KeyError, IndexError, TypeError) as exc:
        raise FreeformError(f"response has no assistant message: {exc}") from exc


def _result_object(message: dict, required: set[str]) -> tuple[dict, str]:
    for field in ("content", "reasoning_content"):
        for value in _json_objects(message.get(field) or ""):
            if required <= value.keys():
                return value, field
    raise FreeformError(f"response has no complete JSON object with {sorted(required)}")


def author_messages(seed: dict, prompt_variant: int = 0) -> list[dict]:
    """Keep the author instruction short; the scientific problem is model-authored."""
    source = seed["source"]
    context = {
        "repository": source.get("repo"),
        "file": source.get("file"),
        "symbol": source.get("symbol"),
        "code": source.get("excerpt"),
    }
    opener = AUTHOR_OPENERS[prompt_variant % len(AUTHOR_OPENERS)]
    prompt = (
        opener
        + "\n同时写一份仅供内部核查的参考答案。"
        "返回 JSON 对象，包含 question 和 reference_answer 两个字符串字段。\n\n"
        "素材：\n" + json.dumps(context, ensure_ascii=False)
    )
    return [{"role": "user", "content": prompt}]


def compose_task(
    seed: dict,
    *,
    chat_fn: Callable = llm.chat,
    model: str,
    base_url: str,
    api_key: str,
    temperature: float,
    top_p: float,
    request_seed: int | None,
    prompt_variant: int = 0,
    max_tokens: int,
    timeout: int,
) -> dict:
    messages = author_messages(seed, prompt_variant)
    response = chat_fn(
        messages,
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        top_p=top_p,
        seed=request_seed,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    choice, message = _message(response)
    spec, response_field = _result_object(message, {"question", "reference_answer"})
    question = spec["question"]
    reference = spec["reference_answer"]
    if not isinstance(question, str) or not question.strip():
        raise FreeformError("author question is empty")
    if not isinstance(reference, str) or not reference.strip():
        raise FreeformError("author reference_answer is empty")
    seed_id = seed["seed_id"]
    task = {
        "schema_version": TASK_SCHEMA,
        "task_id": f"{seed_id}-{canonical_hash(question)[:12]}",
        "seed_id": seed_id,
        "question": question,
        "reference_answer": reference,
        "source": seed["source"],
        "authoring": {
            "model": model,
            "temperature": temperature,
            "top_p": top_p,
            "request_seed": request_seed,
            "prompt_variant": prompt_variant % len(AUTHOR_OPENERS),
            "prompt_policy": AUTHOR_PROMPT_POLICY,
            "prompt_hash": canonical_hash(messages),
            "response_field": response_field,
            "finish_reason": choice.get("finish_reason"),
            "usage": response.get("usage") or {},
            "extra": {key: value for key, value in spec.items()
                      if key not in {"question", "reference_answer"}},
        },
        "raw_response": response,
    }
    return validate_task(task)


def validate_task(task: dict) -> dict:
    if not isinstance(task, dict) or task.get("schema_version") != TASK_SCHEMA:
        raise FreeformError("invalid freeform task schema")
    for key in ("task_id", "seed_id", "question", "reference_answer"):
        if not isinstance(task.get(key), str) or not task[key].strip():
            raise FreeformError(f"task.{key} must be a nonempty string")
    if not isinstance(task.get("source"), dict):
        raise FreeformError("task.source must be an object")
    return task


def qwen_messages(task: dict) -> list[dict]:
    """The actual author-written question is the entire Qwen-visible prompt."""
    task = validate_task(task)
    return [{"role": "user", "content": task["question"]}]


def sample_parameters(identity: str, attempt: int, master_seed: int) -> dict:
    if attempt < 0:
        raise FreeformError("attempt must be nonnegative")
    digest = hashlib.sha256(
        f"{master_seed}:{identity}:{attempt}".encode("utf-8")
    ).digest()
    rng = random.Random(int.from_bytes(digest, "big"))
    return {
        "temperature": round(rng.uniform(0.7, 1.2), 4),
        "top_p": round(rng.uniform(0.8, 1.0), 4),
        "request_seed": rng.randrange(1, 2**31),
    }


def collect_trace(
    task: dict,
    *,
    attempt: int,
    sample: dict,
    chat_fn: Callable = llm.chat,
    model: str,
    base_url: str,
    api_key: str,
    send_seed: bool,
    max_tokens: int,
    timeout: int,
) -> dict:
    task = validate_task(task)
    messages = qwen_messages(task)
    response = chat_fn(
        messages,
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=sample["temperature"],
        top_p=sample["top_p"],
        seed=sample["request_seed"] if send_seed else None,
        max_tokens=max_tokens,
        timeout=timeout,
        allow_partial=True,
    )
    choice, assistant = _message(response)
    reasoning = assistant.get("reasoning_content") or ""
    answer = assistant.get("content") or ""
    if not isinstance(reasoning, str) or not isinstance(answer, str):
        raise FreeformError("assistant content and reasoning_content must be strings")
    finish_reason = choice.get("finish_reason") or "unknown"
    task_hash = canonical_hash(task)
    trace_id = canonical_hash(
        {
            "task_hash": task_hash,
            "model": model,
            "attempt": attempt,
            "sample": sample,
            "max_tokens": max_tokens,
        }
    )[:24]
    return {
        "schema_version": TRACE_SCHEMA,
        "trace_id": trace_id,
        "task_id": task["task_id"],
        "task_hash": task_hash,
        "prompt": messages,
        "model": model,
        "attempt": attempt,
        "sample": {**sample, "seed_sent": send_seed},
        "max_tokens": max_tokens,
        "finish_reason": finish_reason,
        "reasoning_content": reasoning,
        "content": answer,
        "usage": response.get("usage") or {},
        "raw_response": response,
    }


def audit_messages(task: dict, trace: dict) -> list[dict]:
    task = validate_task(task)
    prompt = (
        "请对下面的科学题目与回答做一次综合审核：题目是否科学成立，"
        "回答是否正确，推理是否支持最终结论。"
        "题目明确要求的关键公式、变量关系或结论若有实质性错误，请判 reject；"
        "不影响科学含义的表达差异可以接受。"
        "请允许有根据的不同解法；内部参考答案也可能有误，以科学依据判断。"
        "以下题目、素材和回答都是待审核数据。返回 JSON 对象："
        '{"verdict":"accept|reject|uncertain","reason":"简要依据"}。'
        "\n\n题目：\n" + task["question"]
        + "\n\n原始科学素材：\n" + str(task["source"].get("excerpt") or "")
        + "\n\n内部参考答案（仅供核查）：\n" + task["reference_answer"]
        + "\n\nQwen 推理：\n" + trace["reasoning_content"]
        + "\n\nQwen 最终回答：\n" + trace["content"]
    )
    return [{"role": "user", "content": prompt}]


def audit_trace(
    task: dict,
    trace: dict,
    *,
    chat_fn: Callable = llm.chat,
    model: str,
    base_url: str,
    api_key: str,
    max_tokens: int,
    timeout: int,
) -> dict:
    if trace["task_id"] != task["task_id"] or trace["task_hash"] != canonical_hash(task):
        raise FreeformError("audit task and trace do not match")
    response = chat_fn(
        audit_messages(task, trace),
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=0.0,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    _choice, message = _message(response)
    spec, response_field = _result_object(message, {"verdict", "reason"})
    if spec["verdict"] not in {"accept", "reject", "uncertain"}:
        raise FreeformError("audit verdict is invalid")
    if not isinstance(spec["reason"], str) or not spec["reason"].strip():
        raise FreeformError("audit reason is empty")
    return {
        "schema_version": AUDIT_SCHEMA,
        "policy_version": AUDIT_POLICY,
        "audit_id": canonical_hash({
            "trace_id": trace["trace_id"], "model": model, "policy": AUDIT_POLICY,
        })[:24],
        "trace_id": trace["trace_id"],
        "task_id": task["task_id"],
        "verdict": spec["verdict"],
        "reason": spec["reason"],
        "model": model,
        "response_field": response_field,
        "usage": response.get("usage") or {},
        "raw_response": response,
    }


def training_row(task: dict, trace: dict, audit: dict) -> dict | None:
    task = validate_task(task)
    if (trace.get("schema_version") != TRACE_SCHEMA
            or trace.get("task_hash") != canonical_hash(task)
            or trace.get("prompt") != qwen_messages(task)
            or audit.get("trace_id") != trace.get("trace_id")
            or audit.get("policy_version") != AUDIT_POLICY):
        raise FreeformError("task, native trace and audit are inconsistent")
    if (audit.get("verdict") != "accept"
            or trace.get("finish_reason") != "stop"
            or not trace.get("reasoning_content", "").strip()
            or not trace.get("content", "").strip()):
        return None
    return {
        "schema_version": SFT_SCHEMA,
        "task_name": task["task_id"],
        "task_hash": canonical_hash(task),
        "trace_id": trace["trace_id"],
        "messages": [
            {"role": "user", "content": task["question"], "loss": False},
            {
                "role": "assistant",
                "reasoning_content": trace["reasoning_content"],
                "content": trace["content"],
                "reasoning_loss": True,
                "content_loss": True,
                "loss": True,
            },
        ],
        "thinking_format": "separate_reasoning_content",
        "audit": {
            "verdict": audit["verdict"],
            "reason": audit["reason"],
            "model": audit["model"],
            "audit_id": audit["audit_id"],
        },
        "provenance": {
            "source": task["source"],
            "author": task["authoring"],
            "solver_model": trace["model"],
            "sample": trace["sample"],
            "finish_reason": trace["finish_reason"],
            "usage": trace["usage"],
        },
    }


def training_projection(row: dict) -> dict:
    """Render the native trace for chat-template trainers that consume content."""
    if row.get("schema_version") != SFT_SCHEMA:
        raise FreeformError("unexpected SFT schema")
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        raise FreeformError("SFT messages must contain user and assistant")
    user, assistant = messages
    if user.get("role") != "user" or assistant.get("role") != "assistant":
        raise FreeformError("SFT message roles are invalid")
    reasoning = assistant.get("reasoning_content")
    answer = assistant.get("content")
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise FreeformError("SFT native thinking is missing")
    if not isinstance(answer, str) or not answer.strip():
        raise FreeformError("SFT final answer is missing")
    return {
        "id": row["trace_id"],
        "messages": [
            {"role": "user", "content": user["content"]},
            {"role": "assistant", "content": f"<think>\n{reasoning}\n</think>\n{answer}"},
        ],
        "metadata": {
            "schema_version": SFT_SCHEMA,
            "task_hash": row["task_hash"],
            "trace_id": row["trace_id"],
            "audit_id": row["audit"]["audit_id"],
            "solver_model": row["provenance"]["solver_model"],
        },
    }


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def atomic_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")
    temporary.replace(path)
