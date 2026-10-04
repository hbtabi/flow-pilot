import json
import threading
import urllib.request

from flowpilot.cli import main
from flowpilot.server import make_server
from flowpilot.workflow import load_workflow


def test_cli_run_validate_graph(tmp_path, capsys):
    rd = ["--no-color", "--runs-dir", str(tmp_path)]
    assert main(rd + ["validate", "examples"]) == 0
    capsys.readouterr()
    assert main(rd + ["run", "examples/support_autoreply.yml", "-i", "ticket=How do I export?", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "success" and out["steps"]["escalate"]["status"] == "skipped"
    assert main(rd + ["graph", "examples/lead_triage.yml"]) == 0
    assert "flowchart TD" in capsys.readouterr().out
    assert main(rd + ["runs"]) == 0 and "support-autoreply" in capsys.readouterr().out


def test_cli_missing_input_is_error(tmp_path, capsys):
    assert main(["--runs-dir", str(tmp_path), "run", "examples/support_autoreply.yml"]) == 2
    assert "missing required input" in capsys.readouterr().err


def test_cli_schedule_once(tmp_path, capsys):
    assert main(["--runs-dir", str(tmp_path), "schedule", "examples", "--max-runs", "1"]) == 0
    assert "daily-digest: success" in capsys.readouterr().out


def test_webhook_server(tmp_path):
    srv = make_server([load_workflow("examples/lead_triage.yml")], port=0, runs_dir=tmp_path, token="t0k")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        body = json.dumps({"name": "Ada L", "email": "a@b.example", "message": "budget ready"}).encode()
        req = urllib.request.Request(f"{base}/hooks/lead", data=body, headers={"X-Flow-Pilot-Token": "t0k", "Content-Type": "application/json"})
        res = json.loads(urllib.request.urlopen(req).read())
        assert res["status"] == "success" and res["output"]["score"] == "hot"
        bad = urllib.request.Request(f"{base}/hooks/lead", data=body)
        try:
            urllib.request.urlopen(bad)
            raise AssertionError("expected 401")
        except urllib.error.HTTPError as e:
            assert e.code == 401
        assert json.loads(urllib.request.urlopen(f"{base}/health").read())["hooks"] == ["/hooks/lead"]
    finally:
        srv.shutdown()
