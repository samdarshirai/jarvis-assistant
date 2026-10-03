import jarvis.tools.research_tools as rt
from jarvis.tools.registry import Registry
from jarvis.web import FetchError, SearchError


class FakeSearch:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.queries = result, error, []

    def search(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.result


def reg(search):
    r = Registry()
    rt.register_research_tools(r, search)
    return r


def test_tools_are_read_only_and_web_untrusted():
    r = reg(None)
    tools = r.for_domain("research")
    assert {t.name for t in tools} == {"web_search", "fetch_page"}
    assert not any(t.needs_confirm for t in tools)
    assert all(t.untrusted and t.untrusted_tag == "untrusted_web" for t in tools)


def test_web_search_passes_the_query_and_returns_results():
    s = FakeSearch({"results": [{"title": "T", "url": "u", "snippet": "s"}]})
    assert reg(s).get("web_search").fn(query="robot vacuums") == s.result
    assert s.queries == ["robot vacuums"]


def test_web_search_without_a_key_says_so():
    out = reg(None).get("web_search").fn(query="x")
    assert "isn't configured" in out["error"]


def test_search_and_fetch_errors_become_error_dicts(monkeypatch):
    assert reg(FakeSearch(error=SearchError("Web search timed out."))).get("web_search").fn(query="x") == {
        "error": "Web search timed out."}

    def boom(url):
        raise FetchError("That address is private, so it won't be fetched.")

    monkeypatch.setattr(rt, "fetch_page", boom)
    r = reg(FakeSearch(results("http://10.0.0.1")))
    r.get("web_search").fn(query="q")
    assert r.get("fetch_page").fn(url="http://10.0.0.1") == {
        "error": "That address is private, so it won't be fetched."}


def test_fetch_page_returns_the_page(monkeypatch):
    monkeypatch.setattr(rt, "fetch_page", lambda url: {"url": url, "text": "hi", "truncated": False})
    r = reg(FakeSearch(results("http://a.test")))
    r.get("web_search").fn(query="q")
    assert r.get("fetch_page").fn(url="http://a.test") == {"url": "http://a.test", "text": "hi",
                                                                 "truncated": False}


def results(*urls):
    return {"results": [{"title": "T", "url": u, "snippet": "s"} for u in urls]}


def guarded(monkeypatch, *urls):
    fetched = []
    monkeypatch.setattr(rt, "fetch_page", lambda url: fetched.append(url) or {"url": url, "text": "page"})
    r = reg(FakeSearch(results(*urls)))
    r.get("web_search").fn(query="q")
    return r, fetched


REFUSED = {"error": "That link didn't come from a search result, so it can't be opened. Search for it first."}


def test_fetch_of_an_unseen_url_is_refused_without_a_request(monkeypatch):
    fetched = []
    monkeypatch.setattr(rt, "fetch_page", lambda url: fetched.append(url) or {})
    assert reg(FakeSearch(results("https://a.example/x"))).get("fetch_page").fn(url="https://evil.example/?d=1") == REFUSED
    assert fetched == []


def test_fetch_of_a_searched_url_works(monkeypatch):
    r, fetched = guarded(monkeypatch, "https://a.example/x")
    assert r.get("fetch_page").fn(url="https://a.example/x")["text"] == "page"
    assert fetched == ["https://a.example/x"]


def test_fetch_of_a_variant_of_a_searched_url_is_refused(monkeypatch):
    r, fetched = guarded(monkeypatch, "https://a.example/x")
    for u in ("https://a.example/x?d=secret", "https://a.example/y", "https://a.example/x/"):
        assert r.get("fetch_page").fn(url=u) == REFUSED
    assert fetched == []


def test_seen_urls_are_bounded_and_evict_the_oldest(monkeypatch):
    urls = [f"https://a.example/{i}" for i in range(250)]
    r = reg(FakeSearch(results(*urls)))
    r.get("web_search").fn(query="q")
    monkeypatch.setattr(rt, "fetch_page", lambda url: {"url": url})
    assert r.get("fetch_page").fn(url=urls[0]) == REFUSED
    assert r.get("fetch_page").fn(url=urls[-1]) == {"url": urls[-1]}
