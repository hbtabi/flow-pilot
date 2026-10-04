"""flow-pilot CLI: validate | run | runs | graph | serve | schedule."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .engine import StepRecord, list_runs, run_workflow
from .graph import to_mermaid
from .workflow import WorkflowError, discover, load_workflow

ICON = {"ok": ("✔", "32"), "skipped": ("↷", "90"), "failed": ("✘", "31")}


def _c(s: str, code: str, on: bool) -> str:
    return f"\033[{code}m{s}\033[0m" if on else s


def _preview(value, width: int = 90) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _parse_inputs(pairs: list[str], json_file: str | None) -> dict:
    data = json.loads(Path(json_file).read_text()) if json_file else {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--input expects key=value, got {pair!r}")
        k, v = pair.split("=", 1)
        data[k] = v
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flow-pilot", description="YAML-defined AI automation workflows")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--runs-dir", default=os.environ.get("FLOWPILOT_RUNS_DIR", ".flowpilot/runs"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="validate workflow files or folders")
    v.add_argument("paths", nargs="+")
    r = sub.add_parser("run", help="run a workflow")
    r.add_argument("workflow")
    r.add_argument("-i", "--input", action="append", default=[], metavar="KEY=VALUE")
    r.add_argument("--inputs-json", metavar="FILE")
    r.add_argument("--live", action="store_true", help="make real LLM/HTTP/webhook calls (default: mock, fully offline)")
    r.add_argument("--json", action="store_true", help="print the run result as JSON")
    rs = sub.add_parser("runs", help="list past runs, or show one")
    rs.add_argument("run_id", nargs="?")
    g = sub.add_parser("graph", help="print a Mermaid diagram of a workflow")
    g.add_argument("workflow")
    s = sub.add_parser("serve", help="serve webhook-triggered workflows over HTTP")
    s.add_argument("paths", nargs="+")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--live", action="store_true")
    sc = sub.add_parser("schedule", help="run schedule-triggered workflows on their interval")
    sc.add_argument("paths", nargs="+")
    sc.add_argument("--live", action="store_true")
    sc.add_argument("--max-runs", type=int, default=0, help="stop after N runs in total (0 = forever)")
    args = ap.parse_args(argv)
    color = (sys.stdout.isatty() or bool(os.environ.get("FORCE_COLOR"))) and not args.no_color and not os.environ.get("NO_COLOR")

    try:
        if args.cmd == "validate":
            bad = 0
            for p in discover(args.paths):
                try:
                    wf = load_workflow(p)
                    print(f"{_c('✔', '32', color)} {p}  ({wf.name}: {len(wf.steps)} steps, trigger={wf.trigger.get('type')})")
                except WorkflowError as exc:
                    bad += 1
                    print(f"{_c('✘', '31', color)} {p}")
                    for prob in exc.problems:
                        print(f"    - {prob.split(': ', 1)[-1]}")
            return 1 if bad else 0

        if args.cmd == "graph":
            print(to_mermaid(load_workflow(args.workflow)))
            return 0

        if args.cmd == "run":
            wf = load_workflow(args.workflow)
            mode = "live" if args.live else "mock"
            if not args.json:
                print(_c(f"▶ {wf.name}", "1", color) + _c(f"  ({mode} mode{', offline' if mode == 'mock' else ''})", "90", color))

            def echo(sid: str, rec: StepRecord) -> None:
                if args.json:
                    return
                icon, code = ICON[rec.status]
                detail = rec.error if rec.status == "failed" else ("" if rec.status == "skipped" else _preview(rec.output))
                print(f"  {_c(icon, code, color)} {sid:<16} {_c(rec.type, '36', color):<{22 if color else 12}} {detail}")
                for n in rec.notes:
                    print(_c(f"      note: {n}", "33", color))

            res = run_workflow(wf, _parse_inputs(args.input, args.inputs_json), mode=mode, runs_dir=args.runs_dir, echo=echo)
            if args.json:
                print(json.dumps(res.to_dict(), indent=2, default=str))
            else:
                state = _c(res.status.upper(), "32" if res.status == "success" else "31", color)
                print(f"\n{state} in {res.duration_ms} ms  run={res.run_id}")
                if res.output is not None:
                    print(_c("output:", "1", color), _preview(res.output, 400))
                print(_c(f"log: {res.run_dir / 'run.jsonl'}", "90", color))
            return 0 if res.status == "success" else 1

        if args.cmd == "runs":
            if args.run_id:
                p = Path(args.runs_dir) / args.run_id / "result.json"
                if not p.exists():
                    print(f"no such run: {args.run_id}", file=sys.stderr)
                    return 2
                print(p.read_text())
                return 0
            runs = list_runs(args.runs_dir)
            if not runs:
                print("no runs yet")
            for r_ in runs[:30]:
                mark = _c("✔", "32", color) if r_["status"] == "success" else _c("✘", "31", color)
                print(f"{mark} {r_['run_id']:<48} {r_['mode']:<5} {r_['duration_ms']:>6} ms")
            return 0

        if args.cmd == "serve":
            from .server import make_server

            wfs = [load_workflow(p) for p in discover(args.paths)]
            srv = make_server(wfs, args.host, args.port, "live" if args.live else "mock", args.runs_dir, os.environ.get("FLOWPILOT_TOKEN"))
            hooks = [wf.trigger["path"] for wf in wfs if wf.trigger.get("type") == "webhook"]
            print(f"flow-pilot listening on http://{args.host}:{args.port}  hooks: {', '.join(hooks) or '(none)'}")
            try:
                srv.serve_forever()
            except KeyboardInterrupt:
                pass
            return 0

        if args.cmd == "schedule":
            wfs = [w for w in (load_workflow(p) for p in discover(args.paths)) if w.interval_seconds]
            if not wfs:
                print("no schedule-triggered workflows found")
                return 1
            next_at = {w.name: 0.0 for w in wfs}
            total = 0
            while True:
                now = time.monotonic()
                for w in wfs:
                    if now >= next_at[w.name]:
                        res = run_workflow(w, mode="live" if args.live else "mock", runs_dir=args.runs_dir, trigger="schedule")
                        print(f"[{time.strftime('%H:%M:%S')}] {w.name}: {res.status} ({res.run_id})")
                        next_at[w.name] = now + w.interval_seconds
                        total += 1
                        if args.max_runs and total >= args.max_runs:
                            return 0
                time.sleep(min(1.0, max(0.05, min(next_at.values()) - time.monotonic())))
    except WorkflowError as exc:
        for prob in exc.problems:
            print(f"error: {prob}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
