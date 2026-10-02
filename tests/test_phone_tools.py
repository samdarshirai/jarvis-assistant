import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from jarvis.agent.graph import turn_replies
from jarvis.tools.phone_tools import ComposeArgs, SetAlarmArgs, SetTimerArgs, register_phone_tools
from jarvis.tools.registry import Registry
from tests.test_graph import CFG, call, make_graph, say


def reg():
    r = Registry()
    register_phone_tools(r)
    return r


def test_four_phone_tools_none_need_confirmation():
    r = reg()
    assert {t.name for t in r.for_domain("phone")} == {"set_alarm", "set_timer", "start_navigation", "compose_message"}
    assert not any(r.needs_confirm(t.name) for t in r.for_domain("phone"))


def test_each_tool_queues_a_typed_client_action():
    r = reg()
    assert r.get("set_alarm").fn(hour=6, minute=0) == {
        "queued_for_phone": True, "client_action": {"type": "set_alarm", "hour": 6, "minute": 0}}
    assert r.get("set_timer").fn(seconds=300, label="tea")["client_action"] == {"type": "set_timer", "seconds": 300, "label": "tea"}
    assert r.get("start_navigation").fn(destination="Marienplatz")["client_action"] == {"type": "start_navigation", "destination": "Marienplatz"}
    assert r.get("compose_message").fn(app="whatsapp", contact="Anna", text="10 min late")["client_action"] == {
        "type": "compose_message", "app": "whatsapp", "contact": "Anna", "text": "10 min late"}


def test_argument_validation():
    with pytest.raises(ValidationError):
        SetAlarmArgs(hour=24, minute=0)
    with pytest.raises(ValidationError):
        SetAlarmArgs(hour=6, minute=60)
    with pytest.raises(ValidationError):
        SetTimerArgs(seconds=0)
    with pytest.raises(ValidationError):
        ComposeArgs(app="telegram", contact="Anna", text="hi")


async def test_alarm_reaches_client_actions_through_the_graph():
    g, audit = make_graph({"fast": [call("set_alarm", {"hour": 6, "minute": 0}),
                                    AIMessage("Asking your phone to set a 06:00 alarm.")]}, reg())
    out = await g.ainvoke(say("set an alarm for 6"), CFG)
    assert "__interrupt__" not in out
    assert out["client_actions"] == [{"type": "set_alarm", "hour": 6, "minute": 0}]
    assert turn_replies(out["messages"]) == ["Asking your phone to set a 06:00 alarm."]
    assert audit.records[0]["confirmation"] == "not_required"
