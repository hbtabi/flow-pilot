import pytest


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
