import pytest

from flowpilot.templating import TemplateFailure, referenced_steps, render, truthy


def test_native_types_for_single_expression():
    ctx = {"steps": {"a": {"output": {"items": [1, 2, 3]}}}}
    assert render("{{ steps.a.output['items'] }}", ctx) == [1, 2, 3]
    assert render("{{ steps.a.output['items'] | length > 2 }}", ctx) is True
    assert render("n={{ steps.a.output['items'] | length }}", ctx) == "n=3"


def test_recursive_render():
    out = render({"a": ["{{ x }}", {"b": "hi {{ x }}"}], "c": 5}, {"x": "yo"})
    assert out == {"a": ["yo", {"b": "hi yo"}], "c": 5}


def test_strict_undefined():
    with pytest.raises(TemplateFailure):
        render("{{ nope.missing }}", {})


def test_sandbox_blocks_python_internals():
    with pytest.raises(Exception):  # noqa: B017 - SecurityError or TemplateFailure, both fine
        render("{{ ''.__class__.__mro__[1].__subclasses__() }}", {})


def test_truthy():
    assert truthy("True") and truthy([1]) and not truthy("false") and not truthy("") and not truthy(None) and not truthy("0")


def test_referenced_steps():
    assert referenced_steps({"a": "{{ steps.one.output }} {{ steps.two-b.status }}", "b": ["steps.ignored"]}) == {"one", "two-b"}


def test_dict_keys_beat_dict_methods():
    ctx = {"steps": {"get": {"output": {"items": ["a"]}}}}
    assert render("{{ steps.get.output.items }}", ctx) == ["a"]
