import pytest
from pydantic import BaseModel

from jarvis.tools.registry import Registry, Tool


class Args(BaseModel):
    x: int = 0


def tool(name, domain="calendar", **kw):
    return Tool(name=name, domain=domain, description="d", args_schema=Args, fn=lambda **k: k, **kw)


def test_needs_confirm_defaults_true():
    r = Registry()
    r.add(tool("write_something"))
    assert r.needs_confirm("write_something") is True


def test_explicit_read_tool_skips_confirm():
    r = Registry()
    r.add(tool("list_things", needs_confirm=False))
    assert r.needs_confirm("list_things") is False


def test_unknown_tool_needs_confirm():
    assert Registry().needs_confirm("nope") is True


def test_duplicate_name_rejected():
    r = Registry()
    r.add(tool("a"))
    with pytest.raises(ValueError):
        r.add(tool("a"))


def test_for_domain_and_lc_tools():
    r = Registry()
    r.add(tool("c1", "calendar"))
    r.add(tool("t1", "tasks"))
    assert [t.name for t in r.for_domain("tasks")] == ["t1"]
    assert [t.name for t in r.lc_tools("calendar")] == ["c1"]


def test_untrusted_defaults_false_and_can_be_set():
    assert tool("a").untrusted is False
    assert tool("b", untrusted=True).untrusted is True


def test_untrusted_tag_defaults_to_email():
    assert tool("read", untrusted=True).untrusted_tag == "untrusted_email"
    assert tool("web", untrusted=True, untrusted_tag="untrusted_web").untrusted_tag == "untrusted_web"
