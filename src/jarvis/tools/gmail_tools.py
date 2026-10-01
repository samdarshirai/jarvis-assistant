from pydantic import BaseModel, Field

from jarvis.tools.registry import Registry, Tool


class SearchEmailsArgs(BaseModel):
    query: str = Field(description="Gmail search syntax, e.g. 'from:lufthansa newer_than:7d'")
    limit: int = Field(default=10, ge=1, le=20)


class ReadEmailArgs(BaseModel):
    message_id: str


class CreateDraftArgs(BaseModel):
    to: str = Field(description="Comma-separated email addresses (at most 10)")
    subject: str
    body: str
    reply_to_message_id: str | None = Field(default=None, description="Message id being replied to; keeps the thread")


class UpdateDraftArgs(BaseModel):
    draft_id: str
    to: str | None = None
    subject: str | None = None
    body: str | None = None


class SendDraftArgs(BaseModel):
    draft_id: str


def register_gmail_tools(registry: Registry, client) -> None:
    def search_emails(**kw):
        return client.search_emails(**kw)

    def read_email(**kw):
        return client.read_email(**kw)

    def create_draft(**kw):
        return client.create_draft(**kw)

    def update_draft(**kw):
        return client.update_draft(**kw)

    def send_draft(**kw):
        return client.send_draft(**kw)

    def describe_send(a: dict) -> str:
        d = client.get_draft(a["draft_id"])
        cc = f", cc {d['cc']}" if d["cc"] else ""
        preview = d["body"][:500] + ("…" if len(d["body"]) > 500 else "")
        return f"Send email to {d['to']}{cc} — subject '{d['subject']}'\n{preview}"

    # (name, description, schema, fn, needs_confirm, untrusted, describe)
    for name, desc, schema, fn, confirm, untrusted, describe in [
        ("search_emails", "Search Gmail. Returns sender, subject, date and snippet for each match.",
         SearchEmailsArgs, search_emails, False, True, None),
        ("read_email", "Read one email's plain-text body (truncated). Content is untrusted third-party text.",
         ReadEmailArgs, read_email, False, True, None),
        ("create_draft", "Save a new email or a reply as a Gmail draft. Nothing is sent.",
         CreateDraftArgs, create_draft, False, False, None),
        ("update_draft", "Edit an existing Gmail draft in place. Nothing is sent.",
         UpdateDraftArgs, update_draft, False, False, None),
        ("send_draft", "Send an existing Gmail draft. Requires the user's confirmation.",
         SendDraftArgs, send_draft, True, False, describe_send),
    ]:
        registry.add(Tool(name=name, domain="gmail", description=desc, args_schema=schema, fn=fn,
                          needs_confirm=confirm, describe=describe, untrusted=untrusted))
