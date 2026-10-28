from typing import Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import Registry, Tool


class SetAlarmArgs(BaseModel):
    hour: int = Field(ge=0, le=23, description="24-hour clock, local time")
    minute: int = Field(ge=0, le=59)
    label: str | None = None


class SetTimerArgs(BaseModel):
    seconds: int = Field(gt=0, le=86400)
    label: str | None = None


class NavigationArgs(BaseModel):
    destination: str = Field(min_length=1, description="Address or place name")


class ComposeArgs(BaseModel):
    app: Literal["whatsapp", "sms"]
    contact: str = Field(min_length=1, description="Saved contact alias resolved from memories; otherwise the user's supplied name. The phone matches it.")
    text: str = Field(min_length=1)
    basis: str | None = Field(None, description="Only when you chose the recipient from the user's situation instead of a name they said: why, e.g. 'your 3pm meeting'. The user must confirm first.")


class CallArgs(BaseModel):
    contact: str = Field(min_length=1, description="Saved contact alias resolved from memories; otherwise the user's supplied name. The phone matches it.")
    last4: str | None = Field(None, pattern=r"^\d{4}$", description="Last four digits of the chosen number")
    basis: str | None = Field(None, description="Only when you chose the person from the user's situation instead of a name they said: why, e.g. 'your 3pm meeting'. The user must confirm first.")


class ClarifyContactArgs(BaseModel):
    question: str = Field(min_length=1, max_length=500, pattern=r"\S",
                          description="A short question asking the user to resolve conflicting or unclear contact mappings")


def register_phone_tools(registry: Registry) -> None:
    def queue(type_: str, **args) -> dict:
        # nothing runs on the server: the voice app executes the action as an Android intent
        return {"queued_for_phone": True, "client_action": {"type": type_, **args}}

    def set_alarm(**kw):
        return queue("set_alarm", **kw)

    def set_timer(**kw):
        return queue("set_timer", **kw)

    def start_navigation(**kw):
        return queue("start_navigation", **kw)

    def inferred(verb: str, contact: str, basis: str | None, kw: dict) -> dict | None:
        """An error that makes the model confirm a recipient it inferred before anything is queued."""
        if "@" in contact:
            return {"error": "That is an email address, not a phone contact. Use email, or ask for the contact name."}
        if basis:
            return {"error": f"Not done: you chose this person yourself. Ask the user '{verb} {contact} ({basis})?' and "
                             "act only after they say yes, then call again without basis."}
        return None

    def compose_message(**kw):
        basis = kw.pop("basis", None)
        return inferred("WhatsApp" if kw["app"] == "whatsapp" else "Text", kw["contact"], basis, kw) or queue("compose_message", **kw)

    def call_contact(**kw):
        basis = kw.pop("basis", None)
        return inferred("Call", kw["contact"], basis, kw) or queue("call_contact", **kw)

    def clarify_contact(question):
        return {"question": question}

    for name, desc, schema, fn in [
        ("set_alarm", "Set an alarm on the user's phone.", SetAlarmArgs, set_alarm),
        ("set_timer", "Start a countdown timer on the user's phone.", SetTimerArgs, set_timer),
        ("start_navigation", "Start Google Maps navigation on the user's phone.", NavigationArgs, start_navigation),
        ("compose_message", "Open a WhatsApp or SMS message, pre-filled, for the user to send. Never sends it.",
         ComposeArgs, compose_message),
        ("call_contact", "Call a contact by name on the user's phone: WhatsApp first, normal call if that is not "
         "possible. Pass last4 only after the user has chosen between several matches.", CallArgs, call_contact),
        ("clarify_contact", "Ask which person or saved contact the user means when memories conflict or the mapping "
         "is unclear. Queues no phone action. Use alone, instead of calling or composing a message.",
         ClarifyContactArgs, clarify_contact),
    ]:
        registry.add(Tool(name=name, domain="phone", description=desc, args_schema=schema, fn=fn, needs_confirm=False))
