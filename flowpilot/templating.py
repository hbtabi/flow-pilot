"""Sandboxed Jinja2 templating for step fields.

* ``"Hello {{ inputs.name }}"`` renders to a string.
* A field that is *exactly* one expression, like ``"{{ steps.fetch.output.items }}"``, keeps its native type
  (list, dict, bool...), so data flows between steps without JSON round-trips.
* The sandbox blocks attribute access to Python internals, so a workflow file can't escape into the host.
"""

from __future__ import annotations

import re
from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

_SINGLE = re.compile(r"^\s*\{\{(?P<expr>(?:(?!\}\}).)+)\}\}\s*$", re.S)
class _FlowEnv(SandboxedEnvironment):
    """Dict keys win over dict methods, so ``steps.get.output`` or ``data.items`` mean the *key*, not ``dict.get``."""

    def getattr(self, obj, attribute):
        if isinstance(obj, dict) and attribute in obj:
            return obj[attribute]
        return super().getattr(obj, attribute)


_ENV = _FlowEnv(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
_ENV.filters["first_line"] = lambda s: str(s).strip().splitlines()[0] if str(s).strip() else ""
_ENV.filters["truncate_words"] = lambda s, n=30: " ".join(str(s).split()[:n])


class TemplateFailure(ValueError):
    pass


def render(value: Any, ctx: dict[str, Any]) -> Any:
    """Recursively render strings inside dicts/lists."""
    if isinstance(value, str):
        if "{{" not in value and "{%" not in value:
            return value
        try:
            m = _SINGLE.match(value)
            if m and "{%" not in value:
                return _ENV.compile_expression(m["expr"], undefined_to_none=False)(**ctx)
            return _ENV.from_string(value).render(**ctx)
        except TemplateError as exc:
            raise TemplateFailure(f"template error in {value!r}: {exc}") from exc
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


def truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "none", "null")
    return bool(value)


STEP_REF = re.compile(r"steps\.([A-Za-z_][\w-]*)")


def referenced_steps(value: Any) -> set[str]:
    """Step ids referenced in any template inside ``value`` (used by the validator)."""
    if isinstance(value, str):
        return set(STEP_REF.findall(value)) if "{{" in value or "{%" in value else set()
    if isinstance(value, dict):
        return set().union(*(referenced_steps(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(referenced_steps(v) for v in value)) if value else set()
    return set()
