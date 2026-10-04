"""The runner: renders each step, executes it with retries, and writes a JSONL run log."""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .steps import REGISTRY, StepContext
from .templating import render, truthy
from .workflow import Workflow

SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|WEBHOOK)", re.I)


@dataclass
class StepRecord:
    id: str
    type: str
    status: str  # ok | skipped | failed
    output: Any = None
    error: str | None = None
    attempts: int = 0
    duration_ms: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    run_id: str
    workflow: str
    status: str  # success | failed
    mode: str
    inputs: dict[str, Any]
    steps: dict[str, StepRecord]
    output: Any
    run_dir: Path
    started_at: str
    duration_ms: int

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "workflow": self.workflow, "status": self.status, "mode": self.mode, "inputs": self.inputs,
                "started_at": self.started_at, "duration_ms": self.duration_ms, "output": self.output,
                "steps": {k: v.__dict__ for k, v in self.steps.items()}}


class _Logger:
    def __init__(self, path: Path, secrets_: list[str]):
        self.path, self.secrets = path, [s for s in secrets_ if len(s) >= 6]

    def redact(self, obj: Any) -> Any:
        text = json.dumps(obj, default=str)
        for s in self.secrets:
            text = text.replace(json.dumps(s)[1:-1], "***")
        return json.loads(text)

    def __call__(self, event: str, **data: Any) -> None:
        rec = {"ts": datetime.now().isoformat(timespec="milliseconds"), "event": event, **data}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(self.redact(rec), default=str) + "\n")


def run_workflow(wf: Workflow, inputs: dict[str, Any] | None = None, mode: str = "mock", runs_dir: str | Path = ".flowpilot/runs",
                 echo: Callable[[str, StepRecord], None] | None = None, trigger: str = "manual") -> RunResult:
    if mode not in ("mock", "live"):
        raise ValueError("mode must be 'mock' or 'live'")
    resolved = wf.resolve_inputs(inputs or {})
    started = datetime.now()
    run_id = f"{started:%Y%m%d-%H%M%S}-{wf.name}-{secrets.token_hex(2)}"
    run_dir = Path(runs_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    log = _Logger(run_dir / "run.jsonl", [v for k, v in env.items() if SECRET_ENV.search(k)])
    log("run_started", workflow=wf.name, mode=mode, trigger=trigger, inputs=resolved)

    ctx: dict[str, Any] = {"inputs": resolved, "steps": {}, "env": env, "run": {"id": run_id, "mode": mode, "started_at": started.isoformat()}}
    records: dict[str, StepRecord] = {}
    status = "success"
    t_run = time.perf_counter()
    for step in wf.steps:
        rec = StepRecord(step.id, step.type, "ok")
        t0 = time.perf_counter()
        try:
            if step.when is not None and not truthy(render(step.when, ctx)):
                rec.status = "skipped"
                log("step_skipped", step=step.id, reason="when evaluated false")
            else:
                spec = render(step.spec, ctx)
                log("step_started", step=step.id, type=step.type, spec=spec)
                sctx = StepContext(mode, run_dir, step.id)
                for attempt in range(step.retries + 1):
                    rec.attempts = attempt + 1
                    try:
                        rec.output = REGISTRY[step.type](spec, sctx)
                        break
                    except Exception as exc:  # retry any step failure
                        if attempt == step.retries:
                            raise
                        log("step_retry", step=step.id, attempt=attempt + 1, error=str(exc))
                        time.sleep(step.retry_delay * (2**attempt))
                rec.notes = sctx.notes
        except Exception as exc:
            rec.status, rec.error = "failed", f"{exc.__class__.__name__}: {exc}"
            log("step_failed", step=step.id, error=rec.error)
        rec.duration_ms = int((time.perf_counter() - t0) * 1000)
        records[step.id] = rec
        ctx["steps"][step.id] = {"output": rec.output, "status": rec.status, "error": rec.error}
        if rec.status == "ok":
            log("step_finished", step=step.id, output=rec.output, duration_ms=rec.duration_ms, notes=rec.notes)
        if echo:
            echo(step.id, rec)
        if rec.status == "failed" and step.on_error == "fail":
            status = "failed"
            break

    output = None
    if status == "success" and wf.output is not None:
        try:
            output = render(wf.output, ctx)
        except Exception as exc:
            status, output = "failed", f"output template failed: {exc}"
    result = RunResult(run_id, wf.name, status, mode, resolved, records, output, run_dir, started.isoformat(timespec="seconds"),
                       int((time.perf_counter() - t_run) * 1000))
    log("run_finished", status=status, duration_ms=result.duration_ms, output=output)
    (run_dir / "result.json").write_text(json.dumps(log.redact(result.to_dict()), indent=2, default=str))
    return result


def list_runs(runs_dir: str | Path = ".flowpilot/runs") -> list[dict]:
    out = []
    for p in sorted(Path(runs_dir).glob("*/result.json"), reverse=True):
        try:
            d = json.loads(p.read_text())
            out.append({k: d.get(k) for k in ("run_id", "workflow", "status", "mode", "started_at", "duration_ms")})
        except (OSError, json.JSONDecodeError):
            continue
    return out
