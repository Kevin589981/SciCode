"""Free-form scientific coding tasks and Qwen-native thinking traces.

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

TASK_SCHEMA = "scicode-freeform-coding-task-v2"
TRACE_SCHEMA = "scicode-qwen-native-trace-v1"
AUDIT_SCHEMA = "scicode-freeform-coding-audit-v1"
AUDIT_POLICY = "scientific-coding-overall-v2"
SFT_SCHEMA = "scicode-qwen-native-coding-sft-v2"
AUTHOR_PROMPT_POLICY = "freeform-scientific-coding-v2"
AUTHOR_OPENERS = (
    "从下面的科学代码出发，写一道值得动手解决的科学编程题。",
    "阅读这段真实项目源码后，提出一个需要科学推理并产出代码的问题。",
    "What scientific programming problem would you pose from this source?",
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
    # Reasoning may contain provisional JSON, including incomplete drafts.
    # Only the model's final answer can be used as an artifact.
    for field in ("content",):
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
        + "\n题面围绕素材中的核心科学或数值问题，自含必要背景和代码交付目标；"
        "把方法与实现细节留给解题者选择。"
        "同时写一份仅供内部核查的简要参考答案。"
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
    if choice.get("finish_reason") != "stop":
        raise FreeformError(
            f"author response did not finish: {choice.get('finish_reason')!r}"
        )
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
    authoring = task.get("authoring")
    if (not isinstance(authoring, dict)
            or authoring.get("finish_reason") != "stop"
            or authoring.get("response_field") != "content"):
        raise FreeformError("task must come from a complete author final answer")
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
        "回答是否正确，推理是否支持最终结论。题面明示的定义与关系优先；"
        "内部参考答案仅供核查，也可能有误。允许有根据的不同解法及合理的权重选择。"
        "请先核对题面明确规定的关键变量来源、样本配对、条件与聚合方式，"
        "找出最终回答中对应的实际关系，不要只核对符号或损失项的外形。"
        "若存在差异，请区分等价改写、未被题面排除的合理变体，"
        "以及改变题面指定对象或机制的实质性错误；不能因其他部分正确就忽略后者。"
        "实质性错误判 reject，证据不足判 uncertain，其余判 accept。"
        "还须判断题面是否自含解题所需信息、是否从素材引出需要编写代码的科学任务，"
        "以及 Qwen 最终回答是否给出了实际完成该任务的代码。"
        "允许不同编程语言、实现方法和呈现形式；只有概念解释或伪代码不算代码交付。"
        "这项判断写入 coding_verdict，取 accept、reject 或 uncertain。"
        "只返回 JSON 对象，字段为 key_check、coding_verdict、verdict、reason；"
        "key_check 简要写出题面关键关系与回答对应关系的对照及最重要的差异，"
        "verdict 判断科学正确性；verdict 与 coding_verdict 均须严格取 accept、reject 或 uncertain。"
        "\n\n原始科学素材：\n" + str(task["source"].get("excerpt") or "")
        + "\n\n内部参考答案（仅供核查）：\n" + task["reference_answer"]
        + "\n\nQwen 推理：\n" + trace["reasoning_content"]
        + "\n\n题目（以这里的要求为准）：\n" + task["question"]
        + "\n\nQwen 最终回答：\n" + trace["content"]
        + "\n\n请先完成 key_check 中的题面—回答关系核对，再给出 verdict 和简短 reason。"
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
    choice, message = _message(response)
    if choice.get("finish_reason") != "stop":
        spec, response_field = {}, "invalid"
        parse_error = f"audit response did not finish: {choice.get('finish_reason')!r}"
    else:
        try:
            spec, response_field = _result_object(
                message, {"key_check", "coding_verdict", "verdict", "reason"}
            )
        except FreeformError as exc:
            spec, response_field = {}, "invalid"
            parse_error = str(exc)
        else:
            parse_error = ""
    verdict = spec.get("verdict")
    coding_verdict = spec.get("coding_verdict")
    reason = spec.get("reason")
    key_check = spec.get("key_check")
    if (not isinstance(verdict, str)
            or verdict not in {"accept", "reject", "uncertain"}
            or not isinstance(coding_verdict, str)
            or coding_verdict not in {"accept", "reject", "uncertain"}
            or not isinstance(reason, str) or not reason.strip()
            or not isinstance(key_check, str) or not key_check.strip()):
        verdict = "uncertain"
        coding_verdict = "uncertain"
        reason = "审核输出格式无效，已隔离；" + (
            parse_error or f"原始判定={str(spec.get('verdict'))[:120]!r}"
        )
        key_check = key_check if isinstance(key_check, str) else ""
    return {
        "schema_version": AUDIT_SCHEMA,
        "policy_version": AUDIT_POLICY,
        "audit_id": canonical_hash({
            "trace_id": trace["trace_id"], "model": model, "policy": AUDIT_POLICY,
        })[:24],
        "trace_id": trace["trace_id"],
        "task_id": task["task_id"],
        "verdict": verdict,
        "coding_verdict": coding_verdict,
        "reason": reason,
        "key_check": key_check,
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
            or audit.get("coding_verdict") != "accept"
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
            "coding_verdict": audit["coding_verdict"],
            "reason": audit["reason"],
            "key_check": audit.get("key_check", ""),
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
            "coding_verdict": row["audit"]["coding_verdict"],
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
