"""
llm_client.py
=============
Resilient LLM calling: short per-call timeout, one automatic failover from
the primary provider (NVIDIA) to a secondary provider (Groq by default),
a circuit breaker that routes calls straight to the fallback for
LLM_BREAKER_SECONDS after the primary fails, and a clean
LLMUnavailableError when both fail. No model loading here —
this module only builds OpenAI-compatible HTTP clients, so it is fully
testable with fakes/monkeypatching and no network access.
"""

import logging
import os
import threading
import time

import openai
from openai import OpenAI

logger = logging.getLogger(__name__)

# Provider -> (base_url, default model, API-key env var) for the
# OpenAI-compatible client. Mirrors the provider table previously in
# lexai_engine.py; the Groq default model is corrected here to
# openai/gpt-oss-20b (Groq retired llama-3.1-8b-instant on 2026-08-16).
_PROVIDERS = {
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "openai/gpt-oss-20b",
        "key_env": "NVIDIA_API_KEY",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-20b",
        "key_env": "GROQ_API_KEY",
    },
}

# Exceptions that make us try the fallback provider. BadRequestError (400) is
# a subclass of APIStatusError but is deliberately NOT in this tuple — a 400
# means our own request is malformed, and failing over would just repeat the
# same bug against a second provider while hiding it.
_FAILOVER_EXCEPTIONS = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.APIStatusError,
)


class LLMUnavailableError(Exception):
    """Raised when both the primary and fallback LLM providers are unavailable."""


class LLMClient:
    """
    Builds OpenAI-compatible clients for the configured primary/fallback
    providers and runs chat completions through them with failover.
    """

    def __init__(self, primary_provider=None, timeout_s=None, breaker_seconds=None, clock=None):
        self.primary_name = (primary_provider or os.getenv("LLM_PROVIDER", "groq")).lower()
        self.fallback_name = os.getenv("LLM_FALLBACK_PROVIDER", "nvidia").lower().strip()
        self.timeout_s = timeout_s if timeout_s is not None else float(os.getenv("LLM_TIMEOUT_S", "30"))
        self.breaker_seconds = (breaker_seconds if breaker_seconds is not None
                                else float(os.getenv("LLM_BREAKER_SECONDS", "60")))

        # Circuit breaker state, shared across uvicorn's worker threads.
        # _open_until is None when closed; otherwise the clock() time until which
        # calls skip the primary. After it passes, exactly one call (the probe)
        # tries the primary again while the others keep using the fallback.
        self._clock = clock or time.monotonic
        self._breaker_lock = threading.Lock()
        self._open_until = None
        self._probe_in_flight = False

        self.primary = self._build_provider(self.primary_name)
        if self.primary is not None:
            # LLM_MODEL overrides ONLY the primary model, same as before this change.
            self.primary["model"] = os.getenv("LLM_MODEL") or self.primary["model"]
        if not self.primary or not self.primary["api_key"]:
            prov_label = self.primary_name if self.primary else "unknown"
            print(f"WARNING: no API key found for primary provider {prov_label!r} — generation will fail.")

        self.fallback = None
        if self.fallback_name:
            fb = self._build_provider(self.fallback_name)
            if fb is not None and fb["api_key"]:
                fb["model"] = os.getenv("LLM_FALLBACK_MODEL") or fb["model"]
                self.fallback = fb

        # Every attempt (successful or not) made via complete(), in order.
        self.attempts = []

    def _build_provider(self, name):
        cfg = _PROVIDERS.get(name)
        if cfg is None:
            return None
        api_key = os.getenv(cfg["key_env"], "")
        # The OpenAI SDK raises at construction time on an empty api_key (it falls
        # back to checking OPENAI_API_KEY and errors if that's unset too), so only
        # build a real client when we actually have a key; otherwise leave it None
        # and let complete()/the missing-fallback checks handle it as "unavailable".
        client = None
        if api_key:
            client = OpenAI(
                base_url=cfg["base_url"],
                api_key=api_key,
                timeout=self.timeout_s,
                max_retries=0,  # we do our own failover instead of the SDK's built-in retries
            )
        return {"name": name, "client": client, "model": cfg["model"], "api_key": api_key}

    def _record(self, provider, model, purpose, attempt, outcome, duration_ms,
                prompt_tokens=None, completion_tokens=None, finish_reason=None):
        self.attempts.append({
            "provider": provider,
            "model": model,
            "purpose": purpose,
            "attempt": attempt,
            "outcome": outcome,
            "duration_ms": duration_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "finish_reason": finish_reason,
        })

    def _call_one(self, provider, system, user, max_tokens, purpose, attempt):
        t0 = time.perf_counter()
        try:
            resp = provider["client"].chat.completions.create(
                model=provider["model"],
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=0,
                max_tokens=max_tokens,
            )
        except openai.APITimeoutError:
            self._record(provider["name"], provider["model"], purpose, attempt,
                          "timeout", (time.perf_counter() - t0) * 1000)
            raise
        except Exception as e:
            self._record(provider["name"], provider["model"], purpose, attempt,
                          f"error:{type(e).__name__}", (time.perf_counter() - t0) * 1000)
            raise

        content = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        self._record(provider["name"], provider["model"], purpose, attempt, "ok",
                     (time.perf_counter() - t0) * 1000,
                     prompt_tokens=getattr(usage, "prompt_tokens", None),
                     completion_tokens=getattr(usage, "completion_tokens", None),
                     finish_reason=resp.choices[0].finish_reason)
        return content

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------
    def _breaker_route(self):
        """Decide how this call reaches the primary: "primary" (breaker closed),
        "probe" (window expired; this call is the single trial of the primary),
        or "skip" (window still open, or another call is already probing)."""
        with self._breaker_lock:
            if self.fallback is None or self._open_until is None:
                return "primary"
            if self._probe_in_flight or self._clock() < self._open_until:
                return "skip"
            self._probe_in_flight = True
            return "probe"

    def _breaker_open(self):
        # Only worth opening when there is somewhere else to send calls.
        if self.fallback is None:
            return
        with self._breaker_lock:
            self._open_until = self._clock() + self.breaker_seconds
        logger.warning("LLM circuit breaker opened for %.1fs: sending calls to fallback provider %r",
                       self.breaker_seconds, self.fallback_name)

    def _breaker_close(self):
        with self._breaker_lock:
            was_open = self._open_until is not None
            self._open_until = None
        if was_open:
            logger.warning("LLM circuit breaker closed: primary provider %r recovered", self.primary_name)

    def _call_fallback(self, system, user, max_tokens, purpose):
        try:
            return self._call_one(self.fallback, system, user, max_tokens, purpose, attempt=2)
        except Exception as fallback_exc:
            logger.warning(
                "LLM fallback provider %r also failed for purpose=%r (%s)",
                self.fallback_name, purpose, type(fallback_exc).__name__,
            )
            raise LLMUnavailableError(
                "The language model service is temporarily unavailable."
            ) from fallback_exc

    def complete(self, system, user, purpose=None, max_tokens=2048):
        if self.primary is None or self.primary["client"] is None:
            raise LLMUnavailableError("The language model service is temporarily unavailable.")

        route = self._breaker_route()
        if route == "skip":
            self._record(self.primary_name, self.primary["model"], purpose, 1,
                         "skipped:breaker_open", 0.0)
            return self._call_fallback(system, user, max_tokens, purpose)

        try:
            content = self._call_one(self.primary, system, user, max_tokens, purpose, attempt=1)
        except openai.BadRequestError:
            raise  # a bug in our own request — never mask it with a failover
        except _FAILOVER_EXCEPTIONS as primary_exc:
            logger.warning(
                "LLM primary provider %r failed for purpose=%r (%s); trying fallback",
                self.primary_name, purpose, type(primary_exc).__name__,
            )
            if self.fallback is None:
                raise LLMUnavailableError(
                    "The language model service is temporarily unavailable."
                ) from primary_exc
            self._breaker_open()  # a failed probe reopens the window too
            return self._call_fallback(system, user, max_tokens, purpose)
        else:
            self._breaker_close()
            return content
        finally:
            if route == "probe":
                with self._breaker_lock:
                    self._probe_in_flight = False
