"""Step implementations. Each takes the *rendered* spec and a ``StepContext`` and returns the step output.

Safety model:
* ``mock`` mode (default) never touches the network: LLM/HTTP steps return their ``mock`` value (or a
  deterministic stub) and webhooks are recorded, not sent.
* ``live`` mode makes real calls. LLM steps quietly fall back to mock output when no API key is configured.
* ``email_draft`` only ever *writes* an .eml draft file. flow-pilot never sends email.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path
from typing import Any

from . import llm


@dataclass
class StepContext:
    mode: str  # "mock" | "live"
    run_dir: Path
    step_id: str
    notes: list[str] = field(default_factory=list)


def _json_or_text(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _extract_json(text: str) -> Any:
    m = re.search(r"\{.*\}|\[.*\]", text, re.S)
    if not m:
        raise ValueError("LLM did not return JSON")
    return json.loads(m.group(0))


def step_llm(spec: dict, ctx: StepContext) -> Any:
    want_json = bool(spec.get("json", False))
    if ctx.mode == "live":
        try:
            text = llm.chat(str(spec["prompt"]), spec.get("system"), spec.get("model"), float(spec.get("temperature", 0.2)), want_json)
            return _extract_json(text) if want_json else text.strip()
        except llm.LLMUnavailable as exc:
            ctx.notes.append(f"live LLM unavailable ({exc}); used mock output")
    if "mock" in spec:
        return spec["mock"]
    if want_json:
        return {}
    words = " ".join(str(spec["prompt"]).split()[:12])
    return f"[mock llm reply to: {words}...]"


def _http(method: str, url: str, headers: dict, body: Any, timeout: float) -> dict:
    if not re.match(r"^https?://", url):
        raise ValueError(f"only http(s) URLs are allowed, got {url!r}")
    data = None
    headers = {str(k): str(v) for k, v in (headers or {}).items()}
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
        else:
            data = str(body).encode()
    req = urllib.request.Request(url, data=data, method=method.upper(), headers={"User-Agent": "flow-pilot/0.1", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", "replace")
            return {"status": r.status, "json": _json_or_text(text) if "json" in r.headers.get("Content-Type", "") else None, "text": text[:20000]}
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        return {"status": e.code, "json": _json_or_text(text), "text": text[:20000]}


def step_http(spec: dict, ctx: StepContext) -> Any:
    method = spec.get("method", "GET")
    if ctx.mode != "live":
        mock = spec.get("mock", {"status": 200, "json": None})
        if not isinstance(mock, dict) or "status" not in mock:
            mock = {"status": 200, "json": mock}
        return {**mock, "mocked": True}
    res = _http(method, str(spec["url"]), spec.get("headers", {}), spec.get("json", spec.get("body")), float(spec.get("timeout", 20)))
    expect = spec.get("expect_status")
    if expect and res["status"] not in (expect if isinstance(expect, list) else [expect]):
        raise RuntimeError(f"HTTP {res['status']} (expected {expect})")
    return res


def step_webhook(spec: dict, ctx: StepContext) -> Any:
    payload = spec.get("json", spec.get("body", {}))
    if ctx.mode != "live":
        return {"delivered": False, "dry_run": True, "url": spec["url"], "payload": payload}
    res = _http("POST", str(spec["url"]), spec.get("headers", {}), payload, float(spec.get("timeout", 15)))
    if res["status"] >= 400:
        raise RuntimeError(f"webhook returned HTTP {res['status']}")
    return {"delivered": True, "status": res["status"]}


def step_email_draft(spec: dict, ctx: StepContext) -> Any:
    msg = EmailMessage()
    msg["From"] = spec.get("from") or os.environ.get("FLOWPILOT_FROM", "me@example.com")
    to = spec["to"] if isinstance(spec["to"], list) else [spec["to"]]
    msg["To"] = ", ".join(map(str, to))
    if spec.get("cc"):
        msg["Cc"] = ", ".join(spec["cc"]) if isinstance(spec["cc"], list) else str(spec["cc"])
    msg["Subject"] = str(spec["subject"]).strip()
    msg["Date"] = formatdate(localtime=True)
    msg["X-Flow-Pilot"] = "draft - not sent"
    msg.set_content(str(spec["body"]).strip() + "\n")
    drafts = ctx.run_dir / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    path = drafts / f"{ctx.step_id}.eml"
    path.write_bytes(bytes(msg))
    return {"path": str(path), "to": to, "subject": msg["Subject"], "sent": False}


def step_transform(spec: dict, ctx: StepContext) -> Any:
    return spec["value"]


def step_log(spec: dict, ctx: StepContext) -> Any:
    return str(spec["message"])


REGISTRY: dict[str, Callable[[dict, StepContext], Any]] = {
    "llm": step_llm, "http": step_http, "webhook": step_webhook, "email_draft": step_email_draft,
    "transform": step_transform, "log": step_log,
}
