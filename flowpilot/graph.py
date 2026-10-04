"""Render a workflow as a Mermaid flowchart (paste into GitHub Markdown)."""

from __future__ import annotations

from .workflow import Workflow

ICONS = {"llm": "🤖", "http": "🌐", "webhook": "📣", "email_draft": "✉️", "transform": "🔧", "log": "📝"}


def to_mermaid(wf: Workflow) -> str:
    t = wf.trigger
    label = {"manual": "manual run", "webhook": f"POST {t.get('path', '')}", "schedule": f"every {t.get('every', '')}"}[t.get("type", "manual")]
    lines = ["flowchart TD", f'  trigger(["⚡ {label}"])']
    prev = "trigger"
    for s in wf.steps:
        lines.append(f'  {s.id}["{ICONS[s.type]} {s.id}<br/><small>{s.type}</small>"]')
        cond = str(s.when).replace("{{", "").replace("}}", "").replace('"', "'").strip() if s.when is not None else ""
        lines.append(f"  {prev} -->|{cond}| {s.id}" if cond else f"  {prev} --> {s.id}")
        prev = s.id
    return "\n".join(lines)
