"""Minimal OpenAI-compatible chat client (stdlib only)."""

from __future__ import annotations

import json
import os
import urllib.request


class LLMUnavailable(RuntimeError):
    pass


def chat(prompt: str, system: str | None = None, model: str | None = None, temperature: float = 0.2,
         json_mode: bool = False, timeout: float = 60.0) -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise LLMUnavailable("OPENAI_API_KEY is not set")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body: dict = {"model": model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), "messages": messages, "temperature": temperature}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]
