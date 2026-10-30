"""LLM client wrapper over the HuggingFace Inference Router.

Provides retry/backoff with transient-error detection and usage accounting,
wrapped in a small class so
the rest of the package does not touch huggingface_hub directly.
"""
from __future__ import annotations

import random
import time
from typing import Any, Optional

from .config import RepairConfig

_TRANSIENT_PATTERNS = [
    " 429", " 500", " 502", " 503", " 504",
    "too many requests", "gateway time-out", "gateway timeout",
    "temporarily unavailable", "timed out", "timeout", "connection reset",
]


def is_transient_http_error(msg: str) -> bool:
    m = (msg or "").lower()
    return any(p in m for p in _TRANSIENT_PATTERNS)


def build_model_chain(primary: str, fallbacks: list[str]) -> list[str]:
    chain: list[str] = []
    for m in [primary] + list(fallbacks or []):
        mm = str(m or "").strip()
        if mm and mm not in chain:
            chain.append(mm)
    return chain


class LLMClient:
    """Thin wrapper around huggingface_hub.InferenceClient."""

    def __init__(self, cfg: RepairConfig):
        self.cfg = cfg
        self._client = None
        if cfg.hf_token:
            try:
                from huggingface_hub import InferenceClient

                # with streaming, the timeout bounds the wait between chunks, not the
                # whole generation (full-module repairs take minutes)
                self._client = InferenceClient(
                    base_url=cfg.hf_base_url,
                    provider=cfg.hf_provider,
                    api_key=cfg.hf_token,
                    timeout=cfg.hf_http_timeout_sec,
                )
            except Exception as e:  # pragma: no cover - init failure path
                self._init_error = str(e)

    @property
    def available(self) -> bool:
        return self._client is not None

    def chat(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int = 1800,
    ) -> dict[str, Any]:
        cfg = self.cfg
        if not cfg.hf_token:
            return {"ok": False, "error": "HF_TOKEN missing", "text": "", "transient": False}
        if self._client is None:
            return {"ok": False, "error": "InferenceClient not initialized", "text": "", "transient": False}

        retries = max(1, int(cfg.hf_http_retries))
        base_sec = float(cfg.hf_http_backoff_base_sec)
        cap_sec = float(cfg.hf_http_backoff_cap_sec)
        start = time.time()
        last_error = ""
        for net_try in range(1, retries + 1):
            try:
                # Stream so the router's gateway (~240 s) never times out a long
                # generation; usage arrives in the final chunk.
                stream = self._client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                    stream_options={"include_usage": True},
                )
                parts: list[str] = []
                usage_obj = None
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        parts.append(chunk.choices[0].delta.content)
                    if getattr(chunk, "usage", None):
                        usage_obj = chunk.usage

                text = "".join(parts)
                usage = {}
                if usage_obj is not None:
                    usage = {
                        "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0),
                        "completion_tokens": getattr(usage_obj, "completion_tokens", 0),
                        "total_tokens": getattr(usage_obj, "total_tokens", 0),
                    }
                return {
                    "ok": True,
                    "text": text,
                    "usage": usage,
                    "transient": False,
                    "net_retries_used": net_try - 1,
                    "latency_ms": int((time.time() - start) * 1000),
                    "model": model,
                }
            except Exception as e:
                last_error = str(e)
                transient = is_transient_http_error(last_error)
                if transient and net_try < retries:
                    sleep_s = min(cap_sec, base_sec * (2 ** (net_try - 1))) + random.uniform(0.0, 0.6)
                    time.sleep(sleep_s)
                    continue
                return {
                    "ok": False,
                    "error": last_error,
                    "text": "",
                    "transient": transient,
                    "net_retries_used": net_try,
                    "latency_ms": int((time.time() - start) * 1000),
                    "model": model,
                }

        return {
            "ok": False,
            "error": last_error or "unknown network error",
            "text": "",
            "transient": True,
            "net_retries_used": retries,
            "latency_ms": int((time.time() - start) * 1000),
            "model": model,
        }

    def chat_with_fallbacks(
        self,
        chain: list[str],
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        """Try each model in the chain; switch on transient errors."""
        last = {"ok": False, "error": "no model in chain", "text": "", "transient": False}
        errors: list[str] = []
        for i, model in enumerate(chain, start=1):
            res = self.chat(model, system_prompt, user_prompt, temperature, max_tokens)
            last = res
            if res.get("ok"):
                return res
            errors.append(f"{model}: {res.get('error', '')}")
            if not res.get("transient", False):
                break
            # transient: fall through to next model
        # report every model's failure, not just the last fallback's
        last["error"] = " | ".join(errors)
        return last
