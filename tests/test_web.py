import socket

import httpx
import pytest

from jarvis.web import FetchError, SearchError, WebSearch, fetch_page, html_text

HOSTS = {"public.test": "93.184.216.34", "evil.test": "127.0.0.1", "internal.test": "10.0.0.5"}


def resolve(host, port, type=0):
    ip = HOSTS.get(host)
    if ip is None:  # numeric hosts resolve without DNS; anything else is a test mistake
        return socket.getaddrinfo(host, port, type=type)
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def page(html="<p>hi</p>", ctype="text/html; charset=utf-8", status=200):
    return lambda request: httpx.Response(status, headers={"content-type": ctype}, content=html.encode())


# --- search ---
def search_client(handler):
    return WebSearch("tvly-key", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_search_maps_results_and_sends_bearer_key():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read()
        return httpx.Response(200, json={"results": [
            {"title": "T", "url": "https://a.test", "content": "snippet", "score": 0.9}]})

    out = search_client(handler).search("robot vacuums")
    assert out == {"results": [{"title": "T", "url": "https://a.test", "snippet": "snippet"}]}
    assert seen["auth"] == "Bearer tvly-key" and b"robot vacuums" in seen["body"]


@pytest.mark.parametrize("status,word", [(401, "rejected"), (403, "rejected"), (429, "rate-limited"), (500, "500")])
def test_search_http_errors_become_short_messages(status, word):
    with pytest.raises(SearchError, match=word):
        search_client(lambda r: httpx.Response(status)).search("q")


def test_search_retries_once_on_timeout():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"results": []})

    assert search_client(handler).search("q") == {"results": []}
    assert len(calls) == 2


def test_search_gives_up_after_second_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(SearchError, match="timed out"):
        search_client(handler).search("q")


def test_search_bad_json_is_an_error_not_a_crash():
    with pytest.raises(SearchError):
        search_client(lambda r: httpx.Response(200, content=b"<html>")).search("q")


# --- html_text ---
def test_html_text_drops_scripts_and_styles_and_keeps_paragraph_breaks():
    text = html_text("<html><style>p{}</style><script>alert(1)</script><p>One</p><p>Two &amp; three</p></html>")
    assert "alert" not in text and "p{}" not in text
    assert text.splitlines() == ["One", "Two & three"]


# --- fetch_page ---
def test_fetch_returns_text():
    out = fetch_page("http://public.test/a", client=client(page("<p>Hello</p>")), resolve=resolve)
    assert out == {"url": "http://public.test/a", "text": "Hello", "truncated": False}


def test_fetch_truncates_text_to_8000_chars():
    out = fetch_page("http://public.test/", client=client(page("<p>" + "a" * 20000 + "</p>")), resolve=resolve)
    assert len(out["text"]) == 8000 and out["truncated"] is True


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.5/", "http://192.168.1.1/", "http://100.64.0.1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
    "http://2130706433/", "http://user:pw@127.0.0.1/", "http://evil.test/", "http://0.0.0.0/",
])
def test_fetch_refuses_private_addresses(url):
    hits = []
    with pytest.raises(FetchError, match="private"):
        fetch_page(url, client=client(lambda r: hits.append(r) or httpx.Response(200)), resolve=resolve)
    assert hits == []  # nothing was requested


@pytest.mark.parametrize("url", ["ftp://public.test/x", "file:///etc/passwd", "javascript:alert(1)", "http:///nohost",
                                 "http://public.test:99999/"])
def test_fetch_refuses_other_schemes_and_malformed_urls(url):
    with pytest.raises(FetchError):
        fetch_page(url, client=client(page()), resolve=resolve)


def test_fetch_refuses_redirect_to_private_host_without_requesting_it():
    hits = []

    def handler(request):
        hits.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://internal.test/admin"})

    with pytest.raises(FetchError, match="private"):
        fetch_page("http://public.test/", client=client(handler), resolve=resolve)
    assert hits == ["http://public.test/"]


def test_fetch_follows_a_safe_redirect_and_relative_location():
    def handler(request):
        if request.url.path == "/a":
            return httpx.Response(301, headers={"location": "/b"})
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"done")

    assert fetch_page("http://public.test/a", client=client(handler), resolve=resolve)["text"] == "done"


def test_fetch_stops_after_three_redirects():
    with pytest.raises(FetchError, match="redirect"):
        fetch_page("http://public.test/", resolve=resolve,
                   client=client(lambda r: httpx.Response(302, headers={"location": "/again"})))


@pytest.mark.parametrize("ctype", ["application/pdf", "image/png", "application/octet-stream", ""])
def test_fetch_refuses_non_text_content(ctype):
    with pytest.raises(FetchError, match="read"):
        fetch_page("http://public.test/", client=client(page("x", ctype=ctype)), resolve=resolve)


def test_fetch_reports_http_errors():
    with pytest.raises(FetchError, match="404"):
        fetch_page("http://public.test/", client=client(page("nope", status=404)), resolve=resolve)


def test_fetch_stops_reading_a_huge_body():
    sent = []

    def stream():
        for _ in range(1000):  # 1000 x 100 KB = 100 MB if fully read
            sent.append(1)
            yield b"a" * 100_000

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=stream())

    out = fetch_page("http://public.test/", client=client(handler), resolve=resolve)
    assert len(sent) <= 12 and len(out["text"]) == 8000


def test_fetch_survives_invalid_utf8_and_unknown_charset():
    out = fetch_page("http://public.test/", resolve=resolve, client=client(
        lambda r: httpx.Response(200, headers={"content-type": "text/html; charset=bogus-9"}, content=b"ok \xff\xfe end")))
    assert out["text"].startswith("ok") and out["text"].endswith("end")


def test_fetch_network_errors_are_short_messages():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(FetchError, match="load"):
        fetch_page("http://public.test/", client=client(handler), resolve=resolve)
