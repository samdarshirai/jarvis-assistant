from jarvis.agent.domains import DOMAINS
from jarvis.main import build_registry
from jarvis.tools.registry import Registry, Tool

CONFIRM = {"create_event", "update_event", "delete_event", "create_task", "complete_task",
           "reschedule_task", "send_draft"}
NO_CONFIRM = {"list_events", "find_free_slots", "list_tasks", "search_emails", "read_email",
              "create_draft", "update_draft"}


def registry():
    return build_registry(lambda name, version: (lambda: None), "Europe/Berlin")


def all_tools(r):
    return [t for d in DOMAINS for t in r.for_domain(d)]


def real_tools(r):
    return list(r._tools.values())  # test-only: the registry's true contents, not filtered by DOMAINS


def unknown_domains(r):
    return {t.domain for t in real_tools(r)} - set(DOMAINS)


def test_every_tool_is_registered_with_the_expected_confirmation_tag():
    r = registry()
    names = {t.name for t in real_tools(r)}
    assert names == CONFIRM | NO_CONFIRM  # adding a tool must update this list on purpose
    assert {n for n in names if r.needs_confirm(n)} == CONFIRM


def test_every_registered_tool_belongs_to_a_routable_domain():
    r = registry()
    assert {t.domain for t in all_tools(r)} == {"calendar", "tasks", "gmail"}
    assert len(all_tools(r)) == len(real_tools(r)) == len(CONFIRM | NO_CONFIRM)
    assert unknown_domains(r) == set()


def test_unknown_domain_guard_catches_a_misrouted_tool():
    r = Registry()
    r.add(Tool(name="x", domain="mail", description="", args_schema=object, fn=lambda: None))
    assert unknown_domains(r) == {"mail"}


def test_only_email_reading_tools_are_untrusted():
    r = registry()
    assert {t.name for t in real_tools(r) if t.untrusted} == {"search_emails", "read_email"}
