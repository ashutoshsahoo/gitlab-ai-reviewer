"""Thin client for the Ollama HTTP API (structured JSON output)."""

from __future__ import annotations

import json
import logging
import re
import time

import httpx

from .config import Settings

log = logging.getLogger(__name__)


class OllamaError(Exception):
    pass


def extract_json(text: str) -> dict:
    """Parse JSON from model output, tolerating code fences and surrounding prose."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError("model did not return valid JSON")


class OllamaClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self.http = httpx.Client(
            base_url=settings.ollama_url.rstrip("/"),
            timeout=httpx.Timeout(settings.ollama_timeout_s, connect=10.0),
        )

    def health(self) -> tuple[bool, str]:
        try:
            r = self.http.get("/api/tags", timeout=5.0)
            r.raise_for_status()
            names = [m.get("name", "") for m in r.json().get("models", [])]
            want = self.s.ollama_model
            if any(n == want or n.split(":")[0] == want for n in names):
                return True, "ok"
            return False, f"model '{want}' not pulled (available: {', '.join(names) or 'none'})"
        except Exception as exc:  # noqa: BLE001
            return False, f"ollama unreachable: {exc}"

    def chat_json(self, system: str, user: str, schema: dict | None = None) -> dict:
        payload: dict = {
            "model": self.s.ollama_model,
            "stream": False,
            "keep_alive": "30m",
            "format": schema or "json",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {
                "temperature": self.s.ollama_temperature,
                "num_ctx": self.s.ollama_num_ctx,
                "num_predict": self.s.ollama_num_predict,
                "seed": 42,
            },
        }
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = self.http.post("/api/chat", json=payload)
                if r.status_code == 400 and isinstance(payload["format"], dict):
                    # older Ollama without schema support -> plain JSON mode
                    payload["format"] = "json"
                    continue
                r.raise_for_status()
                data = r.json()
                content = data["message"]["content"]
                log.debug(
                    "ollama eval_count=%s total_duration_s=%.1f",
                    data.get("eval_count"),
                    (data.get("total_duration") or 0) / 1e9,
                )
                return extract_json(content)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                last = exc
                log.warning("ollama call failed (attempt %d/3): %s", attempt + 1, exc)
                time.sleep(2 * (attempt + 1))
        raise OllamaError(f"ollama request failed: {last}")
