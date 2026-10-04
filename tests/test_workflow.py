import pytest

from flowpilot.workflow import WorkflowError, discover, load_workflow, parse_workflow


def test_examples_validate():
    files = discover(["examples"])
    assert len(files) == 3
    for f in files:
        load_workflow(f)


def test_helpful_errors():
    bad = {"name": "x", "trigger": {"type": "webhook"}, "steps": [
        {"id": "a", "type": "llm"},
        {"id": "a", "type": "carrier_pigeon"},
        {"id": "c", "type": "log", "message": "{{ steps.later.output }}"},
        {"id": "later", "type": "log", "message": "hi", "on_error": "explode"},
    ]}
    with pytest.raises(WorkflowError) as exc:
        parse_workflow(bad)
    text = " | ".join(exc.value.problems)
    for needle in ("needs a 'path'", "needs 'prompt'", "duplicate id", "unknown type 'carrier_pigeon'", "steps.later", "on_error"):
        assert needle in text, needle


def test_schedule_interval():
    wf = parse_workflow({"name": "s", "trigger": {"type": "schedule", "every": "15m"}, "steps": [{"type": "log", "message": "x"}]})
    assert wf.interval_seconds == 900
    with pytest.raises(WorkflowError):
        parse_workflow({"name": "s", "trigger": {"type": "schedule", "every": "often"}, "steps": [{"type": "log", "message": "x"}]})


def test_inputs_defaults_and_required():
    wf = parse_workflow({"name": "i", "inputs": {"a": {"required": True}, "b": 3}, "steps": [{"type": "log", "message": "x"}]})
    assert wf.resolve_inputs({"a": 1}) == {"a": 1, "b": 3}
    with pytest.raises(WorkflowError):
        wf.resolve_inputs({})
