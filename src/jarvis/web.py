import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

SEARCH_URL = "https://api.tavily.com/search"
TIMEOUT = 10.0
MAX_BYTES = 1_000_000
MAX_CHARS = 8000
MAX_REDIRECTS = 3
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


class SearchError(Exception):
    pass


class FetchError(Exception):
    pass


class WebSearch:
    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=TIMEOUT)

    def search(self, query: str, limit: int = 5) -> dict:
        r = None
        for attempt in (1, 2):  # one retry, timeouts only
            try:
                r = self.client.post(SEARCH_URL, json={"query": query, "max_results": limit},
                                     headers={"Authorization": f"Bearer {self.api_key}"})
                break
            except httpx.TimeoutException:
                if attempt == 2:
                    raise SearchError("Web search timed out.") from None
            except httpx.HTTPError:
                raise SearchError("Web search failed.") from None
        if r.status_code in (401, 403):
            raise SearchError("The web search key was rejected.")
        if r.status_code == 429:
            raise SearchError("Web search is rate-limited right now; try again shortly.")
        if r.status_code != 200:
            raise SearchError(f"Web search failed (HTTP {r.status_code}).")
        try:
            items = r.json().get("results", [])
        except ValueError:
            raise SearchError("Web search returned something unreadable.") from None
        return {"results": [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
                            for x in items][:limit]}


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg"}
    BREAK = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    p.close()
    text = re.sub(r"[ \t]+", " ", "".join(p.parts))
    return re.sub(r"\s*\n\s*", "\n", text).strip()


def _check_url(url: str, resolve) -> None:
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise FetchError("That link isn't valid.") from None
    if parts.scheme not in ("http", "https"):
        raise FetchError("Only http and https links can be fetched.")
    if not host:
        raise FetchError("That link has no host.")
    try:
        infos = resolve(host, port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise FetchError("Could not find that site.") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise FetchError("That address is private, so it won't be fetched.")
    # ponytail: DNS is resolved here and again at connect time, so a rebinding host could slip through; pin the
    # resolved IP in a custom httpx transport if the owner ever lets Jarvis fetch pages unattended.


def fetch_page(url: str, *, client: httpx.Client | None = None, resolve=socket.getaddrinfo) -> dict:
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=False)
    try:
        for _ in range(MAX_REDIRECTS + 1):
            _check_url(url, resolve)
            try:
                with client.stream("GET", url, headers={"User-Agent": "Jarvis/1.0"}) as r:
                    if r.is_redirect:
                        target = r.headers.get("location")
                        if not target:
                            raise FetchError("The page redirected nowhere.")
                        url = urljoin(str(r.url), target)
                        continue
                    if r.status_code != 200:
                        raise FetchError(f"The page returned HTTP {r.status_code}.")
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    if ctype not in TEXT_TYPES:
                        raise FetchError(f"Can't read {ctype or 'unknown'} content.")
                    raw = bytearray()
                    for chunk in r.iter_bytes():
                        raw += chunk
                        if len(raw) >= MAX_BYTES:
                            break
                    try:
                        body = bytes(raw[:MAX_BYTES]).decode(r.charset_encoding or "utf-8", errors="replace")
                    except LookupError:
                        body = bytes(raw[:MAX_BYTES]).decode("utf-8", errors="replace")
            except httpx.HTTPError:
                raise FetchError("Couldn't load that page.") from None
            text = html_text(body) if ctype != "text/plain" else body.strip()
            return {"url": url, "text": text[:MAX_CHARS], "truncated": len(text) > MAX_CHARS}
        raise FetchError("Too many redirects.")
    finally:
        if own:
            client.close()
