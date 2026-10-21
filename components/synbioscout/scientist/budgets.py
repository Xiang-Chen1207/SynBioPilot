"""Shared thread-safe run limits; byte reservations are not billed token measurements."""

from __future__ import annotations

import threading
import time


class BudgetExceeded(RuntimeError):
    def __init__(self, reason, details=None):
        super().__init__(reason)
        self.details = details or {}


class RunLedger:
    def __init__(self, limits, deadline):
        self.limits, self.deadline = limits, deadline
        self.lock = threading.RLock()
        self.output_holds = {}
        self.counts = {
            "llm_calls": 0,
            "http_attempts": 0,
            "tool_calls": 0,
            "input_tokens_reserved": 0,
            "output_tokens_reserved": 0,
            "output_bytes_received": 0,
            "cache_hits": 0,
        }

    def check(self):
        if time.monotonic() >= self.deadline:
            raise BudgetExceeded("Wall-time budget exhausted")

    def charge(self, key, amount, limit):
        with self.lock:
            self.check()
            if self.counts[key] + amount > limit:
                raise BudgetExceeded(f"{key} budget exhausted")
            self.counts[key] += amount

    def request(self):
        self.charge("http_attempts", 1, self.limits.max_http_requests)

    def cache(self):
        with self.lock:
            self.check()
            self.counts["cache_hits"] += 1

    def llm(self, system, user, schema_text):
        # UTF-8 byte count bounds tokenizer input conservatively. It includes
        # schema text, but is not an exact tokenizer or billing/cost report.
        self.reserve_request(len((system + user + schema_text).encode()))

    def preflight(self, required_bytes, *, required_calls=1, required_output_bytes=0,
                  required_seconds=0, reserve=None, stage="", prior_timeout=False):
        """Non-mutating check of the serialized request plus protected future work.

        Time allowances are scheduling estimates, not latency guarantees. The
        provider still receives the remaining wall-clock deadline.
        """
        with self.lock:
            reserve = reserve or {}
            details = {"required_bytes": required_bytes,
                "remaining_bytes": max(0, self.limits.max_input_tokens - self.counts["input_tokens_reserved"]),
                "required_calls": required_calls,
                "remaining_calls": max(0, self.limits.max_llm_calls - self.counts["llm_calls"]),
                "required_output_bytes": required_output_bytes,
                "remaining_output_bytes": max(0, self.limits.max_output_tokens - self.counts["output_tokens_reserved"]),
                "remaining_seconds": round(max(0, self.deadline - time.monotonic()), 3),
                "required_seconds": round(required_seconds, 3),
                "reserved_bytes": reserve.get("bytes", 0),
                "reserved_calls": reserve.get("calls", 0),
                "reserved_output_bytes": reserve.get("output_bytes", 0),
                "inflight_output_bytes": sum(self.output_holds.values()),
                "reserved_seconds": round(reserve.get("seconds", 0), 3),
                "stage": stage, "prior_timeout": prior_timeout,
                "blocked_before_provider": True, "accounting_unit": "utf8_bytes"}
            checks = (("remaining_seconds", required_seconds + reserve.get("seconds", 0), "Wall-time scheduling allowance exhausted"),
                      ("remaining_calls", required_calls + reserve.get("calls", 0), "LLM invocation budget exhausted"),
                      ("remaining_bytes", required_bytes + reserve.get("bytes", 0), "Input reservation budget exhausted"),
                      ("remaining_output_bytes", required_output_bytes + reserve.get("output_bytes", 0) + sum(self.output_holds.values()), "Output reservation budget exhausted"))
            for key, required, reason in checks:
                if details[key] < required or (key == "remaining_seconds" and details[key] <= 0):
                    details["reason"] = reason
                    raise BudgetExceeded(reason, details)
            return details

    def reserve_request(self, amount, *, output_reservation_id=None, **options):
        with self.lock:
            if output_reservation_id in self.output_holds:
                raise ValueError("Duplicate in-flight output reservation")
            details = self.preflight(amount, **options)
            self.counts["llm_calls"] += 1
            self.counts["input_tokens_reserved"] += amount
            if output_reservation_id is not None:
                self.output_holds[output_reservation_id] = options.get("required_output_bytes", 0)
            return details

    def release_output(self, reservation_id):
        with self.lock:
            self.output_holds.pop(reservation_id, None)

    def output(self, serialized, *, protected_output_bytes=0, call_allowance=None, reservation_id=None):
        # Enforced before app interpretation. Codex CLI cannot enforce a server
        # output-token cap; we explicitly do not claim a provider-side cost cap.
        amount = len(serialized.encode())
        with self.lock:
            self.output_holds.pop(reservation_id, None)
            # A provider may return more than the application can accept (Codex
            # has no output cap). Record receipt separately even when discarded.
            self.counts["output_bytes_received"] += amount
            self.check()
            remaining = self.limits.max_output_tokens - self.counts["output_tokens_reserved"]
            inflight = sum(self.output_holds.values())
            usable = max(0, remaining - protected_output_bytes - inflight)
            if call_allowance is not None:
                usable = min(usable, call_allowance)
            if amount > usable:
                categories = []
                if call_allowance is not None and amount > call_allowance:
                    categories.append("single_call")
                if amount > remaining:
                    categories.append("global")
                elif amount > max(0, remaining - protected_output_bytes - inflight):
                    if protected_output_bytes:
                        categories.append("future_reserved")
                    if inflight:
                        categories.append("parallel")
                raise BudgetExceeded("Output reservation budget exhausted", {
                    "required_output_bytes": amount, "remaining_output_bytes": max(0, remaining),
                    "reserved_output_bytes": protected_output_bytes,
                    "inflight_output_bytes": inflight,
                    "call_allowance": call_allowance,
                    "effective_available_output_bytes": usable,
                    "output_limit_categories": categories,
                    "blocked_before_provider": False, "accounting_unit": "utf8_bytes"})
            self.counts["output_tokens_reserved"] += amount

    def tool(self):
        self.charge("tool_calls", 1, self.limits.max_tool_calls)

    def snapshot(self):
        with self.lock:
            return {
                **self.counts,
                "output_bytes_inflight": sum(self.output_holds.values()),
                "token_accounting": "conservative_utf8_byte_reservation",
                "provider_billed_tokens": None,
                "provider_cost": None,
                "provider_output_cap_enforced": False,
                "limits": self.limits.model_dump(),
            }

