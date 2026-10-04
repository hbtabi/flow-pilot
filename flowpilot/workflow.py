"""Workflow schema: loading + validation with friendly error messages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .templating import referenced_steps

STEP_TYPES = {"llm", "http", "webhook", "email_draft", "transform", "log"}
TRIGGER_TYPES = {"manual", "webhook", "schedule"}
ID_RE = re.compile(r"^[A-Za-z_][\w-]*$")
EVERY_RE = re.compile(r"^(\d+)\s*(s|m|h|d)$")


class WorkflowError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass
class Step:
    id: str
    type: str
    spec: dict[str, Any]
    when: Any = None
    retries: int = 0
    retry_delay: float = 0.5
    on_error: str = "fail"  # fail | continue


@dataclass
class Workflow:
    name: str
    steps: list[Step]
    description: str = ""
    trigger: dict[str, Any] = field(default_factory=lambda: {"type": "manual"})
    inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    output: Any = None
    source: str = ""

    def resolve_inputs(self, given: dict[str, Any]) -> dict[str, Any]:
        out, missing = {}, []
        for name, spec in self.inputs.items():
            if name in given:
                out[name] = given[name]
            elif "default" in spec:
                out[name] = spec["default"]
            elif spec.get("required", False):
                missing.append(name)
        if missing:
            raise WorkflowError([f"missing required input(s): {', '.join(missing)}"])
        out.update({k: v for k, v in given.items() if k not in out})
        return out

    @property
    def interval_seconds(self) -> int | None:
        if self.trigger.get("type") != "schedule":
            return None
        n, unit = EVERY_RE.match(str(self.trigger["every"]).strip()).groups()
        return int(n) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def parse_workflow(data: Any, source: str = "<memory>") -> Workflow:
    problems: list[str] = []
    if not isinstance(data, dict):
        raise WorkflowError([f"{source}: top level must be a mapping"])
    name = data.get("name")
    if not name:
        problems.append("missing 'name'")
    trigger = data.get("trigger") or {"type": "manual"}
    if isinstance(trigger, str):
        trigger = {"type": trigger}
    ttype = trigger.get("type", "manual")
    if ttype not in TRIGGER_TYPES:
        problems.append(f"trigger.type must be one of {sorted(TRIGGER_TYPES)}, got {ttype!r}")
    if ttype == "webhook" and not str(trigger.get("path", "")).startswith("/"):
        problems.append("webhook trigger needs a 'path' starting with '/'")
    if ttype == "schedule" and not EVERY_RE.match(str(trigger.get("every", "")).strip()):
        problems.append("schedule trigger needs 'every' like 30s, 15m, 1h or 1d")
    inputs = data.get("inputs") or {}
    inputs = {k: (v if isinstance(v, dict) else {"default": v}) for k, v in inputs.items()}

    steps, seen = [], []
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        problems.append("'steps' must be a non-empty list")
        raw_steps = []
    for i, raw in enumerate(raw_steps):
        where = f"steps[{i}]"
        if not isinstance(raw, dict):
            problems.append(f"{where} must be a mapping")
            continue
        sid, stype = raw.get("id", f"step_{i + 1}"), raw.get("type")
        where = f"step '{sid}'"
        if not ID_RE.match(str(sid)):
            problems.append(f"{where}: id must be a simple identifier")
        if sid in seen:
            problems.append(f"{where}: duplicate id")
        if stype not in STEP_TYPES:
            problems.append(f"{where}: unknown type {stype!r} (expected one of {sorted(STEP_TYPES)})")
        spec = {k: v for k, v in raw.items() if k not in ("id", "type", "when", "retries", "retry_delay", "on_error")}
        need = {"llm": ["prompt"], "http": ["url"], "webhook": ["url"], "email_draft": ["to", "subject", "body"], "transform": ["value"], "log": ["message"]}
        for key in need.get(stype, []):
            if key not in spec:
                problems.append(f"{where}: '{stype}' step needs '{key}'")
        for ref in referenced_steps({"spec": spec, "when": raw.get("when")}):
            if ref not in seen:
                problems.append(f"{where}: references steps.{ref} which doesn't run before it")
        on_error = raw.get("on_error", "fail")
        if on_error not in ("fail", "continue"):
            problems.append(f"{where}: on_error must be 'fail' or 'continue'")
        seen.append(sid)
        steps.append(Step(sid, stype, spec, raw.get("when"), int(raw.get("retries", 0)), float(raw.get("retry_delay", 0.5)), on_error))
    if problems:
        raise WorkflowError([f"{source}: {p}" for p in problems])
    return Workflow(name, steps, data.get("description", ""), trigger, inputs, data.get("output"), source)


def load_workflow(path: str | Path) -> Workflow:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise WorkflowError([f"{path}: invalid YAML: {exc}"]) from exc
    return parse_workflow(data, str(path))


def discover(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in map(Path, paths):
        out += sorted(p.glob("*.y*ml")) if p.is_dir() else [p]
    return out
