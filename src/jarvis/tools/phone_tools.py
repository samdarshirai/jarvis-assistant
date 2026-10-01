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
    contact: str = Field(min_length=1, description="Contact name as the user said it; the phone resolves it")
    text: str = Field(min_length=1)


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

    def compose_message(**kw):
        return queue("compose_message", **kw)

    for name, desc, schema, fn in [
        ("set_alarm", "Set an alarm on the user's phone.", SetAlarmArgs, set_alarm),
        ("set_timer", "Start a countdown timer on the user's phone.", SetTimerArgs, set_timer),
        ("start_navigation", "Start Google Maps navigation on the user's phone.", NavigationArgs, start_navigation),
        ("compose_message", "Open a WhatsApp or SMS message, pre-filled, for the user to send. Never sends it.",
         ComposeArgs, compose_message),
    ]:
        registry.add(Tool(name=name, domain="phone", description=desc, args_schema=schema, fn=fn, needs_confirm=False))
