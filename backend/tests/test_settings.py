from app.settings import DEFAULT_OPENAI_MODEL, get_openai_model


def test_get_openai_model_falls_back_when_env_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert get_openai_model() == DEFAULT_OPENAI_MODEL


def test_get_openai_model_falls_back_when_env_blank(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "   ")
    assert get_openai_model() == DEFAULT_OPENAI_MODEL


def test_get_openai_model_uses_explicit_value(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    assert get_openai_model() == "gpt-4.1-mini"
