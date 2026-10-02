from pydantic import BaseModel, Field

from jarvis.tools.registry import Registry, Tool
from jarvis.web import FetchError, SearchError, fetch_page


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1)


class FetchPageArgs(BaseModel):
    url: str = Field(description="A full http(s) link, usually one returned by web_search")


def register_research_tools(registry: Registry, search) -> None:
    """search: a WebSearch, or None when no Tavily key is configured (web_search then says so)."""

    def web_search(query):
        if search is None:
            return {"error": "Web search isn't configured (no Tavily key). Tell the user."}
        try:
            return search.search(query)
        except SearchError as e:
            return {"error": str(e)}

    def fetch(url):
        try:
            return fetch_page(url)
        except FetchError as e:
            return {"error": str(e)}

    for name, desc, schema, fn in [
        ("web_search", "Search the web; returns up to 5 results with title, url and a snippet.", WebSearchArgs,
         web_search),
        ("fetch_page", "Read the text of one web page.", FetchPageArgs, fetch),
    ]:
        registry.add(Tool(name=name, domain="research", description=desc, args_schema=schema, fn=fn,
                          needs_confirm=False, untrusted=True, untrusted_tag="untrusted_web"))
