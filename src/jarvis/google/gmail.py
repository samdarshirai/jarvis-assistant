import base64
import binascii
import re
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from typing import Any, Callable

from googleapiclient.errors import HttpError

from jarvis.google.auth import ReauthRequired

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


def _bare_subject(s: str) -> str:
    return re.sub(r"^(\s*re\s*:)+", "", s.strip(), flags=re.IGNORECASE).strip()


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


def _draft_fields(msg) -> dict:
    part = msg.get_body(("plain", "html"))
    text = ""
    if part:
        text = part.get_content().strip()
        if part.get_content_type() == "text/html":
            text = html_to_text(text)
    return {
        "to": str(msg["To"] or ""),
        "cc": str(msg["Cc"] or ""),
        "subject": str(msg["Subject"] or ""),
        "body": text,
    }


class GmailClient:
    def __init__(self, service_factory: Callable[[], Any]):
        self._svc = service_factory

    @staticmethod
    def _run(request):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status == 403 and "insufficient" in str(e).lower():
                raise ReauthRequired("Gmail access has not been granted yet.") from e
            raise

    def search_emails(self, query: str, limit: int = 10) -> list[dict]:
        svc = self._svc()
        resp = self._run(svc.users().messages().list(userId="me", q=query, maxResults=min(limit, 20)))
        out = []
        for m in resp.get("messages", []):
            full = self._run(svc.users().messages().get(
                userId="me", id=m["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"]))
            p = full.get("payload", {})
            out.append({"id": full["id"], "thread_id": full.get("threadId"), "from": header(p, "From"),
                        "subject": header(p, "Subject"), "date": header(p, "Date"), "snippet": full.get("snippet")})
        return out

    def read_email(self, message_id: str) -> dict:
        full = self._run(self._svc().users().messages().get(userId="me", id=message_id, format="full"))
        p = full.get("payload", {})
        text = extract_text(p)
        return {"id": full["id"], "thread_id": full.get("threadId"), "from": header(p, "From"),
                "to": header(p, "To"), "subject": header(p, "Subject"), "date": header(p, "Date"),
                "body": text[:MAX_BODY], "truncated": len(text) > MAX_BODY}

    def find_contacts(self, name: str, limit: int = 15) -> list[dict]:
        """Candidate addresses for a name, from From/To/Cc headers only (no subject, snippet or body is read)."""
        q = " ".join(name.replace('"', " ").replace("\\", " ").split())[:60]
        if not q:
            raise ValueError("name is required")
        key = q.casefold()
        svc = self._svc()
        resp = self._run(svc.users().messages().list(
            userId="me", q=f'from:"{q}" OR to:"{q}" OR cc:"{q}"', maxResults=min(limit, 20)))
        found: dict[str, dict] = {}
        for m in resp.get("messages", []):
            full = self._run(svc.users().messages().get(
                userId="me", id=m["id"], format="metadata", metadataHeaders=["From", "To", "Cc"]))
            p = full.get("payload", {})
            sent = "SENT" in full.get("labelIds", [])
            counted: set[str] = set()
            for hname in ("From", "To", "Cc"):
                for display, addr in getaddresses([header(p, hname) or ""]):
                    addr = addr.strip().lower()
                    if "@" not in addr or not (key in display.casefold() or key in addr.split("@")[0]):
                        continue
                    try:
                        clean_recipients(addr)
                    except ValueError:
                        continue
                    c = found.setdefault(addr, {"name": "", "address": addr, "you_emailed": False, "seen": 0})
                    if addr not in counted:
                        c["seen"] += 1
                        counted.add(addr)
                    if not c["name"] and display.strip():
                        c["name"] = " ".join(display.split())[:60]
                    if sent and hname in ("To", "Cc"):
                        c["you_emailed"] = True
        return sorted(found.values(), key=lambda c: (not c["you_emailed"], -c["seen"]))[:5]

    def sent_to(self, address: str) -> bool:
        """True when the owner has sent at least one message to this address."""
        addr = clean_recipients(address)[0]
        resp = self._run(self._svc().users().messages().list(userId="me", q=f"in:sent to:{addr}", maxResults=1))
        return bool(resp.get("messages"))

    def create_draft(self, to: str, subject: str, body: str, reply_to_message_id: str | None = None) -> dict:
        recipients = clean_recipients(to)
        check_subject(subject)
        svc = self._svc()
        in_reply_to = references = thread_id = None
        if reply_to_message_id:
            orig = self._run(svc.users().messages().get(
                userId="me", id=reply_to_message_id, format="metadata", metadataHeaders=["Message-ID", "References", "Subject"]))
            p = orig.get("payload", {})
            in_reply_to = header(p, "Message-ID")
            references = " ".join(x for x in (header(p, "References"), in_reply_to) if x) or None
            thread_id = orig.get("threadId")
            orig_subject = _bare_subject(header(p, "Subject") or "")
            if orig_subject and _bare_subject(subject).casefold() != orig_subject.casefold():
                subject = f"Re: {orig_subject}"  # a mismatched subject makes Gmail drop the thread
        message: dict = {"raw": build_raw(recipients, subject, body, in_reply_to, references)}
        if thread_id:
            message["threadId"] = thread_id
        d = self._run(svc.users().drafts().create(userId="me", body={"message": message}))
        return {"draft_id": d["id"], "thread_id": d.get("message", {}).get("threadId")}

    def _read_draft(self, draft_id: str):
        d = self._run(self._svc().users().drafts().get(userId="me", id=draft_id, format="raw"))
        msg = message_from_bytes(b64decode(d["message"]["raw"]), policy=policy.default)
        return msg, d["message"].get("threadId")

    def get_draft(self, draft_id: str) -> dict:
        msg, thread_id = self._read_draft(draft_id)
        return {"draft_id": draft_id, "thread_id": thread_id, **_draft_fields(msg)}

    def update_draft(self, draft_id: str, to: str | None = None, subject: str | None = None,
                     body: str | None = None) -> dict:
        msg, thread_id = self._read_draft(draft_id)
        if any(True for _ in msg.iter_attachments()):
            raise ValueError("draft has attachments; Jarvis cannot edit it safely — edit it in Gmail")
        if body is None and (msg.is_multipart() or msg.get_content_type() != "text/plain"):
            raise ValueError("draft has rich content; pass body to replace it, or edit it in Gmail")
        f = _draft_fields(msg)

        def bare(v: str) -> str:
            return ", ".join(a for _, a in getaddresses([v]) if a)

        recipients = clean_recipients(to if to is not None else bare(f["to"]))
        cc = clean_recipients(bare(f["cc"]), required=False)
        new_subject = subject if subject is not None else f["subject"]
        check_subject(new_subject)
        in_reply_to = str(msg["In-Reply-To"]) if msg["In-Reply-To"] else None
        references = str(msg["References"]) if msg["References"] else None
        message: dict = {"raw": build_raw(recipients, new_subject, body if body is not None else f["body"],
                                          in_reply_to, references, cc)}
        if thread_id:
            message["threadId"] = thread_id
        d = self._run(self._svc().users().drafts().update(userId="me", id=draft_id,
                                                          body={"id": draft_id, "message": message}))
        return {"draft_id": d["id"]}

    def send_draft(self, draft_id: str) -> dict:
        m = self._run(self._svc().users().drafts().send(userId="me", body={"id": draft_id}))
        return {"sent": True, "message_id": m.get("id"), "thread_id": m.get("threadId")}
