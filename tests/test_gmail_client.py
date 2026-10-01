import base64
import json
from email import message_from_bytes, policy
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from jarvis.google.auth import ReauthRequired
from jarvis.google.gmail import GmailClient, build_raw


def enc(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def make():
    svc = MagicMock()
    return GmailClient(lambda: svc), svc


def M(svc):
    return svc.users.return_value.messages.return_value


def D(svc):
    return svc.users.return_value.drafts.return_value


def parse(raw: str):
    return message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)


def http_error(status: int, message: str) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), json.dumps({"error": {"message": message}}).encode())


def test_search_returns_slim_metadata_and_caps_limit():
    c, svc = make()
    M(svc).list.return_value.execute.return_value = {"messages": [{"id": "m1"}, {"id": "m2"}]}
    M(svc).get.return_value.execute.side_effect = [
        {"id": "m1", "threadId": "t1", "snippet": "hi", "payload": {"headers": [
            {"name": "From", "value": "a@x.com"}, {"name": "Subject", "value": "S1"}, {"name": "Date", "value": "D1"}]}},
        {"id": "m2", "threadId": "t2", "snippet": "yo", "payload": {"headers": []}}]
    out = c.search_emails("from:a", limit=50)
    assert out[0] == {"id": "m1", "thread_id": "t1", "from": "a@x.com", "subject": "S1", "date": "D1", "snippet": "hi"}
    assert out[1]["from"] is None
    assert M(svc).list.call_args.kwargs["maxResults"] == 20
    assert M(svc).list.call_args.kwargs["q"] == "from:a"


def test_search_with_no_results():
    c, svc = make()
    M(svc).list.return_value.execute.return_value = {}
    assert c.search_emails("nothing") == []


def test_read_email_truncates_body_and_reports_it():
    c, svc = make()
    M(svc).get.return_value.execute.return_value = {"id": "m1", "threadId": "t1", "payload": {
        "mimeType": "text/plain", "headers": [{"name": "Subject", "value": "S"}], "body": {"data": enc("x" * 5000)}}}
    out = c.read_email("m1")
    assert len(out["body"]) == 4000 and out["truncated"] is True and out["subject"] == "S"
    assert M(svc).get.call_args.kwargs["format"] == "full"


def test_read_email_short_body_not_truncated():
    c, svc = make()
    M(svc).get.return_value.execute.return_value = {"id": "m1", "payload": {
        "mimeType": "text/plain", "body": {"data": enc("short")}}}
    out = c.read_email("m1")
    assert out["body"] == "short" and out["truncated"] is False


def test_create_draft_reply_keeps_thread_and_headers():
    c, svc = make()
    M(svc).get.return_value.execute.return_value = {"threadId": "t9", "payload": {"headers": [
        {"name": "Message-ID", "value": "<a@x>"}, {"name": "References", "value": "<r@x>"}]}}
    D(svc).create.return_value.execute.return_value = {"id": "d1", "message": {"threadId": "t9"}}
    out = c.create_draft("Raj <raj@x.com>", "Re: hi", "ok", reply_to_message_id="m1")
    assert out == {"draft_id": "d1", "thread_id": "t9"}
    message = D(svc).create.call_args.kwargs["body"]["message"]
    assert message["threadId"] == "t9"
    parsed = parse(message["raw"])
    assert parsed["In-Reply-To"] == "<a@x>" and parsed["References"] == "<r@x> <a@x>" and parsed["To"] == "raj@x.com"


def test_create_draft_reply_to_message_without_id_headers_still_threads():
    c, svc = make()
    M(svc).get.return_value.execute.return_value = {"threadId": "t9", "payload": {"headers": []}}
    D(svc).create.return_value.execute.return_value = {"id": "d1", "message": {"threadId": "t9"}}
    c.create_draft("a@x.com", "Re: hi", "ok", reply_to_message_id="m1")
    message = D(svc).create.call_args.kwargs["body"]["message"]
    parsed = parse(message["raw"])
    assert message["threadId"] == "t9" and parsed["In-Reply-To"] is None and parsed["References"] is None


def test_create_new_draft_has_no_thread():
    c, svc = make()
    D(svc).create.return_value.execute.return_value = {"id": "d2", "message": {}}
    out = c.create_draft("a@x.com", "Hello", "body")
    assert out == {"draft_id": "d2", "thread_id": None}
    assert "threadId" not in D(svc).create.call_args.kwargs["body"]["message"]
    M(svc).get.assert_not_called()


@pytest.mark.parametrize("to,subject", [
    ("a@x.com\nbcc: e@v.il", "s"), ("a@x.com", "s\nBcc: e@v.il"), ("nope", "s"),
    (",".join(f"a{i}@x.com" for i in range(11)), "s")])
def test_create_draft_rejects_bad_input_before_any_gmail_call(to, subject):
    c, svc = make()
    with pytest.raises(ValueError):
        c.create_draft(to, subject, "b", reply_to_message_id="m1")
    svc.users.assert_not_called()


def draft_with(raw: str, thread="t1"):
    return {"id": "d1", "message": {"raw": raw, "threadId": thread}}


def test_get_draft_returns_readable_fields():
    c, svc = make()
    D(svc).get.return_value.execute.return_value = draft_with(
        build_raw(["a@x.com", "b@y.com"], "Hello", "Body text", cc=["c@z.com"]))
    out = c.get_draft("d1")
    assert out == {"draft_id": "d1", "thread_id": "t1", "to": "a@x.com, b@y.com", "cc": "c@z.com",
                   "subject": "Hello", "body": "Body text"}
    assert D(svc).get.call_args.kwargs["format"] == "raw"


def test_update_draft_keeps_unchanged_fields_cc_and_thread():
    c, svc = make()
    D(svc).get.return_value.execute.return_value = draft_with(
        build_raw(["a@x.com"], "Subj", "old body", "<m@x>", "<r@x>", cc=["c@z.com"]))
    D(svc).update.return_value.execute.return_value = {"id": "d1"}
    assert c.update_draft("d1", body="new body") == {"draft_id": "d1"}
    sent = D(svc).update.call_args.kwargs["body"]
    assert sent["id"] == "d1" and sent["message"]["threadId"] == "t1"
    parsed = parse(sent["message"]["raw"])
    assert parsed["To"] == "a@x.com" and parsed["Cc"] == "c@z.com" and parsed["Subject"] == "Subj"
    assert parsed["In-Reply-To"] == "<m@x>"
    assert parsed.get_body(("plain",)).get_content().strip() == "new body"


def test_update_draft_can_change_recipient_and_subject():
    c, svc = make()
    D(svc).get.return_value.execute.return_value = draft_with(build_raw(["a@x.com"], "Subj", "keep me"))
    D(svc).update.return_value.execute.return_value = {"id": "d1"}
    c.update_draft("d1", to="z@y.com", subject="New")
    parsed = parse(D(svc).update.call_args.kwargs["body"]["message"]["raw"])
    assert parsed["To"] == "z@y.com" and parsed["Subject"] == "New"
    assert parsed.get_body(("plain",)).get_content().strip() == "keep me"


@pytest.mark.parametrize("kwargs", [{"to": "a@x.com\nbcc: e@v.il"}, {"subject": "s\nBcc: e@v.il"}, {"to": "nope"}])
def test_update_draft_rejects_bad_input_without_writing(kwargs):
    c, svc = make()
    D(svc).get.return_value.execute.return_value = draft_with(build_raw(["a@x.com"], "Subj", "b"))
    with pytest.raises(ValueError):
        c.update_draft("d1", **kwargs)
    D(svc).update.assert_not_called()


def test_send_draft_sends_exactly_that_draft():
    c, svc = make()
    D(svc).send.return_value.execute.return_value = {"id": "m7", "threadId": "t7"}
    assert c.send_draft("d1") == {"sent": True, "message_id": "m7", "thread_id": "t7"}
    assert D(svc).send.call_args.kwargs["body"] == {"id": "d1"}


def test_403_insufficient_scope_becomes_reauth_required():
    c, svc = make()
    M(svc).list.return_value.execute.side_effect = http_error(403, "Request had insufficient authentication scopes.")
    with pytest.raises(ReauthRequired):
        c.search_emails("x")


def test_other_http_errors_pass_through():
    c, svc = make()
    D(svc).send.return_value.execute.side_effect = http_error(404, "Requested entity was not found.")
    with pytest.raises(HttpError):
        c.send_draft("gone")
