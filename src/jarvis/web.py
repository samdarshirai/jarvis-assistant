import io
import ipaddress
import re
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

SEARCH_URL = "https://api.tavily.com/search"
TIMEOUT = 10.0
MAX_BYTES = 1_000_000
MAX_PDF_BYTES = 10_000_000  # a cut-off PDF can't be parsed (its index is at the end), so read it whole or not at all
MAX_CHARS = 8000
THIN_CHARS = 200  # less readable text than this: the page probably renders with JavaScript
MAX_REDIRECTS = 3
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/pdf")
BLOCKED = (401, 403, 429)


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


class DdgSearch:
    """Keyless, free search via DuckDuckGo. ponytail: scrapes DDG, can be rate-limited; switch to Tavily/SearXNG if flaky."""

    def search(self, query: str, limit: int = 5) -> dict:
        from ddgs import DDGS
        for attempt in (1, 2):  # one retry: DDG rate-limits in bursts
            try:
                items = DDGS(timeout=TIMEOUT).text(query, max_results=limit)
                break
            except Exception:
                if attempt == 2:
                    raise SearchError("Web search failed.") from None
        return {"results": [{"title": x.get("title", ""), "url": x.get("href", ""), "snippet": x.get("body", "")}
                            for x in items][:limit]}


class FallbackSearch:
    """Tries primary (Tavily), then backup (DDG); if both fail, reports the primary's error."""

    def __init__(self, primary, backup):
        self.primary, self.backup = primary, backup

    def search(self, query: str, limit: int = 5) -> dict:
        try:
            return self.primary.search(query, limit)
        except SearchError as first:
            try:
                return self.backup.search(query, limit)
            except SearchError:
                raise first from None


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "nav", "aside", "footer", "form"}
    BREAK = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}
    MAIN = {"main", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.main: list[str] = []  # text inside <main>/<article>, the page's own content
        self.skip = 0
        self.in_main = 0
        self.headers: list[bool] = []  # per open <header>: skipped? Site banners are, article headers (byline, date) aren't

    def _add(self, s):
        self.parts.append(s)
        if self.in_main:
            self.main.append(s)

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.MAIN:
            self.in_main += 1
        elif tag == "header":
            self.headers.append(not self.in_main)
            self.skip += self.headers[-1]
        if tag in self.BREAK:
            self._add("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.MAIN and self.in_main:
            self.in_main -= 1
        elif tag == "header" and self.headers:
            self.skip = max(0, self.skip - self.headers.pop())
        if tag in self.BREAK:
            self._add("\n")

    def handle_data(self, data):
        if not self.skip:
            self._add(data)


def _tidy(parts) -> str:
    text = re.sub(r"[ \t]+", " ", "".join(parts))
    return re.sub(r"\s*\n\s*", "\n", text).strip()


def html_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    p.close()
    main = _tidy(p.main)
    return main if len(main) >= THIN_CHARS else _tidy(p.parts)


def pdf_text(data: bytes, deadline: float, clock) -> str:
    from pypdf import PdfReader
    out = []
    try:
        for page in PdfReader(io.BytesIO(data)).pages:
            if clock() > deadline:
                break  # ponytail: one slow page still can't be interrupted; subprocess if that bites
            out.append(page.extract_text() or "")
            if sum(map(len, out)) > MAX_CHARS:
                break
    except Exception:
        raise FetchError("Couldn't read that PDF.") from None
    return _tidy("\n".join(out))


_V4_IN_V6 = [ipaddress.ip_network(n) for n in ("64:ff9b::/96", "::/96", "::ffff:0:0:0/96")]  # NAT64, IPv4-compatible, SIIT


def embedded_v4(ip):
    """The IPv4 address an IPv6 form carries (mapped, NAT64, IPv4-compatible, SIIT), else ip itself: the address
    a translator would really reach is the one that has to be global."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return ip.ipv4_mapped
        if any(ip in n for n in _V4_IN_V6):
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip


def _check_url(url: str, resolve) -> tuple:
    """Validate url; return (parts, ip) where ip is a checked global address to pin the connection to."""
    if any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise FetchError("That link isn't valid.")
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
        ips = []
        for info in infos:
            ips.append(embedded_v4(ipaddress.ip_address(info[4][0].split("%")[0])))
    except (socket.gaierror, UnicodeError):
        raise FetchError("Could not find that site.") from None
    except ValueError:
        raise FetchError("That link isn't valid.") from None
    if not ips:
        raise FetchError("Could not find that site.")
    for ip in ips:
        if not ip.is_global or ip.is_multicast:
            raise FetchError("That address is private, so it won't be fetched.")
    return parts, ips[0]


def _pin(parts, ip) -> tuple:
    """Rewrite the URL to connect to the checked IP; keep the real host in Host header and SNI."""
    ip_s = f"[{ip}]" if ip.version == 6 else str(ip)
    hostport = parts.netloc.rpartition("@")[2]  # drops any userinfo
    port = parts.port
    netloc = ip_s + (f":{port}" if port else "")
    pinned = parts._replace(netloc=netloc).geturl()
    ext = {}
    if parts.scheme == "https":
        try:
            ipaddress.ip_address(parts.hostname)
        except ValueError:
            ext["sni_hostname"] = parts.hostname
    return pinned, hostport, ext


def fetch_page(url: str, *, client: httpx.Client | None = None, resolve=socket.getaddrinfo,
               clock=time.monotonic) -> dict:
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=False)
    deadline = clock() + TIMEOUT
    slow = FetchError("That page took too long.")
    try:
        for _ in range(MAX_REDIRECTS + 1):
            if clock() > deadline:
                raise slow
            parts, ip = _check_url(url, resolve)
            pinned, hostport, ext = _pin(parts, ip)
            try:
                with client.stream("GET", pinned, headers={"User-Agent": "Jarvis/1.0", "Host": hostport, "Accept-Encoding": "identity"},
                                   extensions=ext, follow_redirects=False) as r:
                    if r.is_redirect:
                        target = r.headers.get("location")
                        if not target:
                            raise FetchError("The page redirected nowhere.")
                        url = urljoin(url, target)
                        continue
                    if r.status_code in BLOCKED:
                        raise FetchError(f"That site blocks automated reading (HTTP {r.status_code}); use the "
                                         "search snippet or try another result.")
                    if r.status_code != 200:
                        raise FetchError(f"The page returned HTTP {r.status_code}.")
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    if ctype not in TEXT_TYPES:
                        raise FetchError("That page isn't text, HTML or PDF, so it can't be read.")
                    cap = MAX_PDF_BYTES if ctype == "application/pdf" else MAX_BYTES
                    raw = bytearray()
                    for chunk in r.iter_bytes():
                        raw += chunk
                        if len(raw) >= cap:
                            break
                        if clock() > deadline:
                            raise slow
                    if ctype == "application/pdf":
                        if len(raw) >= cap:
                            raise FetchError("That PDF is too large to read.")
                        body = bytes(raw)
                    else:
                        try:
                            body = bytes(raw[:cap]).decode(r.charset_encoding or "utf-8", errors="replace")
                        except LookupError:
                            body = bytes(raw[:cap]).decode("utf-8", errors="replace")
            except (httpx.HTTPError, httpx.InvalidURL, ValueError):
                raise FetchError("Couldn't load that page.") from None
            if ctype == "application/pdf":
                text = pdf_text(body, deadline, clock)
            else:
                text = body.strip() if ctype == "text/plain" else html_text(body)
            out = {"url": url, "text": text[:MAX_CHARS], "truncated": len(text) > MAX_CHARS}
            if ctype in ("text/html", "application/xhtml+xml") and len(text) < THIN_CHARS:
                out["note"] = ("Almost no readable text; the page probably needs JavaScript. Use the search snippet "
                               "or try another result.")
            return out
        raise FetchError("Too many redirects.")
    finally:
        if own:
            client.close()
