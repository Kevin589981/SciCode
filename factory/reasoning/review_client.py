"""Credential-isolated reviewer with an explicit total-context budget."""
from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlparse

from ..author import llm


def reviewer_chat(client: dict, *, context_window_tokens: int = 262144,
                  input_margin_tokens: int = 4096, chat_fn=llm.chat,
                  count_fn=None):
    if not 0 < input_margin_tokens < context_window_tokens:
        raise ValueError("invalid reviewer context budget")
    parsed = urlparse(client["base_url"])
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("reviewer endpoint must not contain credentials")

    def count(messages, model):
        # This gateway reports an OpenAI-tokenizer estimate for the Kimi alias,
        # NOT a certified count from Kimi's own tokenizer. Keep that distinction
        # in every review record and check reported usage after completion.
        root = client["base_url"].rstrip("/").removesuffix("/v1")
        request = urllib.request.Request(root + "/utils/token_counter",
            data=json.dumps({"model": model, "messages": messages}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + client["api_key"]})
        with urllib.request.urlopen(request, timeout=60) as response:
            value = json.loads(response.read())
        tokens = value.get("total_tokens")
        if value.get("error") or type(tokens) is not int or tokens < 0:
            raise ValueError("gateway returned an invalid token count")
        return tokens, "gateway-" + str(value.get("tokenizer_type", "unknown")) + "-not-certified"

    def chat(messages, **kwargs):
        requested = kwargs["max_tokens"]
        if type(requested) is not int or not 0 < requested < context_window_tokens:
            raise ValueError("review output limit must be smaller than its context window")
        try:
            tokens, policy = (count_fn or count)(messages, kwargs.get("model", client["model"]))
        except Exception:
            # No silent input truncation or optimistic character/4 fallback.
            tokens = sum(len(m["content"].encode("utf-8")) for m in messages)
            policy = "fallback-utf8-byte-upper-estimate-not-certified"
        available = context_window_tokens - input_margin_tokens - tokens
        if available < 4096:
            raise ValueError(f"full review input exceeds context budget: estimated={tokens}, "
                             f"window={context_window_tokens}; input was not truncated ({policy})")
        kwargs["max_tokens"] = min(requested, available)
        response = chat_fn(messages, client=client, **kwargs)
        usage = response.get("usage") or {}
        used_input, used_output = usage.get("prompt_tokens"), usage.get("completion_tokens")
        within = None
        if type(used_input) is int and type(used_output) is int:
            within = used_input + used_output <= context_window_tokens
        response["_context_budget"] = {
            "context_window_tokens": context_window_tokens,
            "input_margin_tokens": input_margin_tokens,
            "estimated_input_tokens": tokens, "estimate_policy": policy,
            "requested_max_tokens": requested, "effective_max_tokens": kwargs["max_tokens"],
            "reported_total_within_window": within,
        }
        if within is False:
            raise ValueError("reviewer reported actual input+output above configured context window")
        return response
    return chat
