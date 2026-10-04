"""Tiny webhook trigger server (stdlib ``http.server``). POST JSON to a workflow's ``trigger.path``."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .engine import run_workflow
from .workflow import Workflow, WorkflowError

MAX_BODY = 1_000_000


def make_server(workflows: list[Workflow], host: str = "127.0.0.1", port: int = 8787, mode: str = "mock",
                runs_dir: str | Path = ".flowpilot/runs", token: str | None = None) -> ThreadingHTTPServer:
    routes = {wf.trigger["path"]: wf for wf in workflows if wf.trigger.get("type") == "webhook"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "flow-pilot"

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 (http.server naming)
            if self.path == "/health":
                return self._send(200, {"ok": True, "hooks": sorted(routes)})
            self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            wf = routes.get(self.path.split("?")[0])
            if not wf:
                return self._send(404, {"error": f"no workflow listens on {self.path}", "hooks": sorted(routes)})
            if token and self.headers.get("X-Flow-Pilot-Token") != token:
                return self._send(401, {"error": "missing or wrong X-Flow-Pilot-Token"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, {"error": "payload too large"})
            try:
                inputs = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(inputs, dict):
                    raise ValueError("body must be a JSON object")
            except ValueError as exc:
                return self._send(400, {"error": f"invalid JSON: {exc}"})
            try:
                res = run_workflow(wf, inputs, mode=mode, runs_dir=runs_dir, trigger=f"webhook {self.path}")
            except WorkflowError as exc:
                return self._send(422, {"error": str(exc)})
            self._send(200 if res.status == "success" else 500, {"run_id": res.run_id, "status": res.status, "output": res.output})

        def log_message(self, fmt, *args):  # keep test output quiet; runs are logged to JSONL anyway
            pass

    return ThreadingHTTPServer((host, port), Handler)
