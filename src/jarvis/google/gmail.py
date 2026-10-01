import base64
import binascii
from email.message import EmailMessage
from email.utils import parseaddr
from html.parser import HTMLParser

MAX_BODY = 4000
MAX_RECIPIENTS = 10
_BAD_ADDR_CHARS = ' ;<>",'


class _Text(HTMLParser):
    SKIP = {"script", "style", "head"}
    BLOCK = {"p", "div", "li", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self._skip = 0
            return
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK or tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(src: str) -> str:
    p = _Text()
    p.feed(src)
    p.close()
    lines = [" ".join(line.split()) for line in "".join(p.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def extract_text(payload: dict) -> str:
    """Plain text of a Gmail message payload: first text/plain part, else stripped text/html; attachments skipped."""
    plain: list[str] = []
    htm: list[str] = []

    def walk(part: dict) -> None:
        if part.get("filename"):
            return  # attachment, even when its mime type is text/*
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime in ("text/plain", "text/html"):
            try:
                text = b64decode(data).decode("utf-8", "replace")
            except (binascii.Error, ValueError):
                text = ""
            if text:
                (plain if mime == "text/plain" else htm).append(text)
        for sub in part.get("parts") or []:
            walk(sub)

    walk(payload)
    if plain:
        return plain[0].strip()
    return html_to_text(htm[0]) if htm else ""


def header(payload: dict, name: str) -> str | None:
    for h in payload.get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return None


def clean_recipients(value: str, required: bool = True) -> list[str]:
    if "\r" in value or "\n" in value:
        raise ValueError("recipient contains a line break")
    out: list[str] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        _, addr = parseaddr(part)
        if "@" not in addr or addr.startswith("@") or addr.endswith("@") or any(c in addr for c in _BAD_ADDR_CHARS):
            raise ValueError(f"invalid email address: {part!r}")
        out.append(addr)
    if not out and required:
        raise ValueError("at least one recipient is required")
    if len(out) > MAX_RECIPIENTS:
        raise ValueError(f"at most {MAX_RECIPIENTS} recipients per message")
    return out


def check_subject(subject: str) -> None:
    if "\r" in subject or "\n" in subject:
        raise ValueError("subject contains a line break")


def build_raw(to: list[str], subject: str, body: str, in_reply_to: str | None = None,
              references: str | None = None, cc: list[str] | None = None) -> str:
    check_subject(subject)
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()
