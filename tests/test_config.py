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


import pytest
from pydantic import ValidationError


def test_proactive_defaults():
    s = make()
    assert (s.brief_enabled, s.brief_time, s.leave_lead_minutes, s.mail_poll_minutes, s.auto_event_cap) == (
        True, "07:30", 30, 5, 5)


@pytest.mark.parametrize("bad", ["7:3x", "24:00", "07:60", "0730", "", "07:30:00"])
def test_brief_time_must_be_hh_mm(bad):
    with pytest.raises(ValidationError):
        make(brief_time=bad)


@pytest.mark.parametrize("field", ["leave_lead_minutes", "mail_poll_minutes"])
def test_intervals_must_be_positive(field):
    with pytest.raises(ValidationError):
        make(**{field: 0})
