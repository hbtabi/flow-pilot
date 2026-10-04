import json
import threading
from email import message_from_bytes
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from flowpilot.engine import list_runs, run_workflow
from flowpilot.workflow import load_workflow, parse_workflow


def wf(steps, **extra):
    return parse_workflow({"name": "t", "steps": steps, **extra})


def test_lead_triage_hot_path(tmp_path):
    res = run_workflow(load_workflow("examples/lead_triage.yml"),
                       {"name": "Ada Lovelace", "email": "ada@x.example", "message": "Budget approved, need it ASAP"}, runs_dir=tmp_path)
    assert res.status == "success"
    assert res.output["score"] == "hot" and res.output["sales_notified"] is True
    eml = message_from_bytes(Path(res.output["draft"]).read_bytes())
    assert eml["To"] == "ada@x.example" and "Thursday" in eml.get_payload() and eml["X-Flow-Pilot"] == "draft - not sent"


def test_lead_triage_warm_path_skips_webhook(tmp_path):
    res = run_workflow(load_workflow("examples/lead_triage.yml"), {"name": "Bo", "email": "bo@y.example", "message": "Just curious"}, runs_dir=tmp_path)
    assert res.steps["notify_sales"].status == "skipped" and res.output["sales_notified"] is False


def test_failure_stops_run_and_continue_does_not(tmp_path):
    steps = [{"id": "boom", "type": "transform", "value": "{{ undefined_thing.x }}"}, {"id": "after", "type": "log", "message": "ran"}]
    assert run_workflow(wf(steps), runs_dir=tmp_path).status == "failed"
    steps[0]["on_error"] = "continue"
    res = run_workflow(wf(steps), runs_dir=tmp_path)
    assert res.status == "success" and res.steps["boom"].status == "failed" and res.steps["after"].output == "ran"


def test_run_log_and_listing(tmp_path):
    res = run_workflow(wf([{"id": "a", "type": "log", "message": "hello"}]), runs_dir=tmp_path)
    events = [json.loads(line)["event"] for line in (res.run_dir / "run.jsonl").read_text().splitlines()]
    assert events == ["run_started", "step_started", "step_finished", "run_finished"]
    assert list_runs(tmp_path)[0]["run_id"] == res.run_id


def test_secrets_are_redacted_in_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_API_TOKEN", "s3cr3t-value-123")
    res = run_workflow(wf([{"id": "a", "type": "log", "message": "token is {{ env.MY_API_TOKEN }}"}]), runs_dir=tmp_path)
    assert res.steps["a"].output.endswith("s3cr3t-value-123")  # available to the step...
    for f in ("run.jsonl", "result.json"):
        assert "s3cr3t-value-123" not in (res.run_dir / f).read_text()  # ...but never written to disk


def test_live_llm_without_key_falls_back_to_mock(tmp_path):
    res = run_workflow(wf([{"id": "a", "type": "llm", "prompt": "hi", "mock": "offline answer"}]), mode="live", runs_dir=tmp_path)
    assert res.steps["a"].output == "offline answer" and "unavailable" in res.steps["a"].notes[0]


class _Handler(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):  # noqa: N802
        _Handler.hits += 1
        code = 503 if _Handler.hits == 1 else 200  # fail once, then succeed -> exercises retries
        body = json.dumps({"hits": _Handler.hits}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        n = int(self.headers["Content-Length"])
        _Handler.last_post = json.loads(self.rfile.read(n))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def local_server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_live_http_with_retry_and_webhook(tmp_path, local_server):
    _Handler.hits = 0
    steps = [{"id": "get", "type": "http", "url": f"{local_server}/x", "expect_status": 200, "retries": 2, "retry_delay": 0.01},
             {"id": "hook", "type": "webhook", "url": f"{local_server}/hook", "json": {"hits": "{{ steps.get.output.json.hits }}"}}]
    res = run_workflow(wf(steps), mode="live", runs_dir=tmp_path)
    assert res.status == "success", {k: v.error for k, v in res.steps.items()}
    assert res.steps["get"].attempts == 2
    assert _Handler.last_post == {"hits": 2}


def test_http_rejects_non_http_schemes(tmp_path):
    res = run_workflow(wf([{"id": "f", "type": "http", "url": "file:///etc/passwd"}]), mode="live", runs_dir=tmp_path)
    assert res.status == "failed" and "only http(s)" in res.steps["f"].error
