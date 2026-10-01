"""Shared per-provider circuit breaker: no 500-thread short-retry storms."""
from __future__ import annotations

import random
import threading
import time
from ..author import llm


class ResilientClient:
    def __init__(self, chat_fn=llm.chat, *, threshold=8, cooldown=30,
                 max_cooldown=300, recovery_timeout=21600, emit=None,
                 clock=time.monotonic, sleep=time.sleep, start_rate=50):
        self.chat_fn = chat_fn
        self.threshold, self.cooldown = threshold, cooldown
        self.max_cooldown, self.recovery_timeout = max_cooldown, recovery_timeout
        self.emit, self.clock, self.sleep = emit, clock, sleep
        self.condition = threading.Condition()
        self.failures = 0
        self.open_until = 0.0
        self.probing = False
        self.outage_round = 0
        self.fatal = None
        self.requests = self.transient_errors = self.recoveries = 0
        self.start_rate = start_rate
        self.next_start = 0.0

    def snapshot(self):
        with self.condition:
            return {"requests": self.requests, "transient_errors": self.transient_errors,
                    "consecutive_failures": self.failures, "outage_round": self.outage_round,
                    "circuit": "fatal" if self.fatal else "open" if self.open_until else "closed",
                    "probe_active": self.probing, "recoveries": self.recoveries,
                    "retry_delay_seconds": max(0, round(self.open_until - self.clock(), 1))}

    def event(self, kind, **value):
        if self.emit:
            self.emit({"event": kind, "time": time.time(), **value})

    def admission(self, deadline):
        with self.condition:
            while True:
                if self.fatal:
                    raise self.fatal
                now = self.clock()
                if now >= deadline:
                    raise llm.LLMRequestError("provider recovery deadline exceeded; retain task for resume")
                if not self.open_until:
                    if self.start_rate and now < self.next_start:
                        self.condition.wait(timeout=min(1, self.next_start - now, deadline - now))
                        continue
                    if self.start_rate:
                        self.next_start = now + 1 / self.start_rate
                    return False
                if now >= self.open_until and not self.probing:
                    self.probing = True
                    return True
                self.condition.wait(timeout=min(5, max(.05, self.open_until - now), deadline - now))

    def __call__(self, messages, **kwargs):
        deadline = self.clock() + self.recovery_timeout
        kwargs["retries"] = 1  # One shared policy, not nested 3x retries.
        while True:
            probe = self.admission(deadline)
            with self.condition:
                self.requests += 1
            try:
                response = self.chat_fn(messages, **kwargs)
            except llm.LLMRequestError as exc:
                with self.condition:
                    if not exc.retryable:
                        self.fatal = exc
                        self.probing = False
                        self.condition.notify_all()
                        self.event("fatal_provider_error", status_code=exc.status_code, error=str(exc))
                        raise
                    self.transient_errors += 1
                    self.failures += 1
                    if probe or (self.failures >= self.threshold and not self.open_until):
                        self.outage_round += 1
                        delay = min(self.max_cooldown, self.cooldown * 2 ** min(self.outage_round - 1, 5))
                        delay = max(delay, exc.retry_after or 0)
                        self.open_until = self.clock() + delay
                        self.probing = False
                        self.event("circuit_open", status_code=exc.status_code,
                                   retry_in_seconds=delay, error=str(exc))
                    self.condition.notify_all()
                if self.clock() >= deadline:
                    raise
                self.sleep(random.uniform(1, 3))
            else:
                with self.condition:
                    # Once open, only its half-open probe can close the circuit.
                    # Late success from a pre-outage request is not a health test.
                    if probe:
                        self.open_until = 0
                        self.probing = False
                        self.outage_round = 0
                        self.recoveries += 1
                        self.event("provider_recovered")
                    if not self.open_until:
                        self.failures = 0
                    self.condition.notify_all()
                return response
