"""Persist completed reviewer calls so late stage failures don't repeat work."""
from __future__ import annotations
import hashlib
import json
import os
import uuid
from pathlib import Path


class ReviewCache:
    def __init__(self, root: Path, chat_fn, namespace: dict):
        self.root, self.chat_fn, self.namespace = root, chat_fn, namespace
        self.last_key = None
        self.calls = []

    def __call__(self, messages, **kwargs):
        if set(kwargs) - {"model", "temperature", "max_tokens", "timeout", "allow_partial"}:
            raise ValueError("review cache only accepts non-secret request parameters")
        identity = {"namespace": self.namespace, "messages": messages, "parameters": kwargs}
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self.last_key = key
        path = self.root / key[:2] / (key + ".json")
        rejected = path.with_suffix(".rejected.json")
        if path.exists() and not rejected.exists():
            response = json.loads(path.read_text(encoding="utf-8"))["response"]
            self.calls.append({"key": key, "cache_hit": True})
            return response
        response = self.chat_fn(messages, **kwargs)
        if (response.get("choices") or [{}])[0].get("finish_reason") == "stop":
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
            # Credentials are never part of identity/response: client lives in
            # the underlying closure, not the per-call parameters.
            temporary.write_text(json.dumps({"key": key, "response": response}, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, path)
        self.calls.append({"key": key, "cache_hit": False})
        return response

    def reject_last(self, error):
        if self.last_key:
            path = self.root / self.last_key[:2] / (self.last_key + ".rejected.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"key": self.last_key, "error": str(error)[:2000]}), encoding="utf-8")
