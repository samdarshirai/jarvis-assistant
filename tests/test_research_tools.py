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
    assert reg(None).get("fetch_page").fn(url="http://10.0.0.1") == {
        "error": "That address is private, so it won't be fetched."}


def test_fetch_page_returns_the_page(monkeypatch):
    monkeypatch.setattr(rt, "fetch_page", lambda url: {"url": url, "text": "hi", "truncated": False})
    assert reg(None).get("fetch_page").fn(url="http://a.test") == {"url": "http://a.test", "text": "hi",
                                                                 "truncated": False}
