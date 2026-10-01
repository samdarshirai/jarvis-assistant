import base64
from email import message_from_bytes, policy

import pytest

from jarvis.google.gmail import (build_raw, check_subject, clean_recipients, extract_text, header,
                                 html_to_text)


def enc(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def test_html_to_text_strips_tags_and_scripts():
    assert html_to_text("<p>Hello <b>Raj</b></p><script>x()</script><br>Bye") == "Hello Raj\nBye"


def test_extract_text_prefers_plain_over_html():
    payload = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/plain", "body": {"data": enc("plain text")}},
        {"mimeType": "text/html", "body": {"data": enc("<p>html</p>")}}]}
    assert extract_text(payload) == "plain text"


def test_extract_text_falls_back_to_html():
    payload = {"mimeType": "text/html", "body": {"data": enc("<div>Only <i>html</i></div>")}}
    assert extract_text(payload) == "Only html"


def test_extract_text_skips_attachments_even_if_text_plain():
    payload = {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "text/plain", "filename": "secrets.txt", "body": {"data": enc("ATTACHED")}},
        {"mimeType": "text/plain", "body": {"data": enc("real body")}}]}
    assert extract_text(payload) == "real body"


def test_extract_text_survives_bad_base64_and_empty_payload():
    bad = {"mimeType": "text/plain", "body": {"data": "!!!not base64!!!"}}
    assert extract_text(bad) == ""
    assert extract_text({"mimeType": "multipart/mixed", "parts": []}) == ""


def test_extract_text_handles_unpadded_base64url():
    assert extract_text({"mimeType": "text/plain", "body": {"data": enc("ab")}}) == "ab"


def test_header_case_insensitive():
    p = {"headers": [{"name": "Subject", "value": "S"}]}
    assert header(p, "subject") == "S" and header(p, "From") is None


def test_clean_recipients_normalises_and_splits():
    assert clean_recipients("Raj <raj@x.com>, a@b.co") == ["raj@x.com", "a@b.co"]


@pytest.mark.parametrize("bad", ["a@x.com\nbcc: e@v.il", "nope", "a@x.com;b@y.com", "@x.com", "a@", "", " , "])
def test_clean_recipients_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        clean_recipients(bad)


def test_clean_recipients_caps_at_ten_and_allows_empty_when_not_required():
    with pytest.raises(ValueError):
        clean_recipients(",".join(f"a{i}@x.com" for i in range(11)))
    assert len(clean_recipients(",".join(f"a{i}@x.com" for i in range(10)))) == 10
    assert clean_recipients("", required=False) == []


def test_check_subject_rejects_line_breaks():
    check_subject("fine")
    for bad in ("a\nBcc: e@v.il", "a\rb"):
        with pytest.raises(ValueError):
            check_subject(bad)


def test_build_raw_roundtrip_with_reply_headers_and_cc():
    raw = build_raw(["a@x.com"], "Re: hi", "the body", "<m@x>", "<r@x> <m@x>", cc=["c@y.com"])
    msg = message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)
    assert msg["To"] == "a@x.com" and msg["Cc"] == "c@y.com" and msg["Subject"] == "Re: hi"
    assert msg["In-Reply-To"] == "<m@x>" and msg["References"] == "<r@x> <m@x>"
    assert msg.get_body(("plain",)).get_content().strip() == "the body"


def test_build_raw_rejects_line_break_in_subject():
    with pytest.raises(ValueError):
        build_raw(["a@x.com"], "s\nBcc: e@v.il", "b")


def test_html_to_text_handles_unclosed_head_before_body():
    assert html_to_text("<head><title>t</title><body>Hello world") == "Hello world"


def test_html_to_text_handles_unclosed_head_with_style_before_body():
    assert html_to_text("<head><style>.a{color:red}</style><body><p>Hi</p>") == "Hi"


def test_html_to_text_drops_head_when_well_formed():
    assert html_to_text("<html><head><title>T</title></head><body>B</body></html>") == "B"


def test_extract_text_from_html_with_unclosed_head():
    payload = {"mimeType": "text/html", "body": {"data": enc("<head><title>T</title><body>Body content")}}
    assert extract_text(payload) == "Body content"
