from jarvis.config import Settings


def make(**over):
    base = dict(
        openrouter_api_key="k", models_fast="a/x, b/y", models_strong="c/z",
        database_url="postgresql://x", telegram_bot_token="t",
        telegram_owner_chat_id=42, fernet_key="f",
    )
    return Settings(_env_file=None, **{**base, **over})


def test_models_split_and_trim():
    s = make()
    assert s.models("fast") == ["a/x", "b/y"]
    assert s.models("strong") == ["c/z"]


def test_defaults():
    s = make()
    assert s.timezone == "Europe/Berlin"
    assert s.google_client_secrets == "client_secret.json"


def test_tavily_key_is_optional_and_read_from_env(monkeypatch):
    monkeypatch.delenv("JARVIS_TAVILY_API_KEY", raising=False)
    assert make().tavily_api_key == ""
    monkeypatch.setenv("JARVIS_TAVILY_API_KEY", "tvly-abc")
    assert make().tavily_api_key == "tvly-abc"
