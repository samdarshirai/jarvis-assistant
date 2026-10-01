from jarvis.tools.gmail_tools import register_gmail_tools
from jarvis.tools.registry import Registry


class FakeClient:
    def __init__(self):
        self.calls = []

    def search_emails(self, **kw):
        self.calls.append(("search_emails", kw))
        return []

    def create_draft(self, **kw):
        self.calls.append(("create_draft", kw))
        return {"draft_id": "d1"}

    def get_draft(self, draft_id):
        return {"to": "raj@x.com", "cc": "", "subject": "Late", "body": "x" * 600}


def reg(client=None):
    r = Registry()
    register_gmail_tools(r, client or object())
    return r


def test_only_send_draft_confirms():
    r = reg()
    assert {t.name for t in r.for_domain("gmail") if t.needs_confirm} == {"send_draft"}
    assert {t.name for t in r.for_domain("gmail")} == {
        "search_emails", "read_email", "create_draft", "update_draft", "send_draft"}


def test_reading_tools_are_untrusted_and_writers_are_not():
    r = reg()
    assert {t.name for t in r.for_domain("gmail") if t.untrusted} == {"search_emails", "read_email"}


def test_only_send_draft_has_describe():
    r = reg()
    assert {t.name for t in r.for_domain("gmail") if t.describe} == {"send_draft"}


def test_describe_send_shows_recipient_subject_and_truncated_body():
    summary = reg(FakeClient()).get("send_draft").describe({"draft_id": "d1"})
    assert summary.startswith("Send email to raj@x.com — subject 'Late'")
    assert summary.endswith("…") and len(summary) < 600


def test_describe_send_mentions_cc_when_present():
    class C(FakeClient):
        def get_draft(self, draft_id):
            return {"to": "a@x.com", "cc": "c@y.com", "subject": "S", "body": "b"}
    assert "cc c@y.com" in reg(C()).get("send_draft").describe({"draft_id": "d1"})


def test_tool_functions_reach_the_client_with_only_given_arguments():
    c = FakeClient()
    r = reg(c)
    r.get("search_emails").fn(query="from:lufthansa")
    r.get("create_draft").fn(to="a@x.com", subject="s", body="b")
    assert c.calls == [("search_emails", {"query": "from:lufthansa"}),
                       ("create_draft", {"to": "a@x.com", "subject": "s", "body": "b"})]


def test_search_limit_is_bounded_by_the_schema():
    import pytest
    from pydantic import ValidationError
    schema = reg().get("search_emails").args_schema
    assert schema(query="x").limit == 10
    for bad in (0, 21):
        with pytest.raises(ValidationError):
            schema(query="x", limit=bad)
