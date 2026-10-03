from datetime import date
from unittest.mock import MagicMock

import pytest

from jarvis.google.tasks import TasksClient
from jarvis.tools.registry import Registry
from jarvis.tools.task_tools import register_task_tools


def setup():
    svc = MagicMock()
    c = TasksClient(lambda: svc)
    r = Registry()
    register_task_tools(r, c)
    return r, svc


def test_list_tasks_slims():
    r, svc = setup()
    svc.tasks.return_value.list.return_value.execute.return_value = {"items": [
        {"id": "t1", "title": "Call mum", "due": "2026-10-02T00:00:00.000Z", "status": "needsAction", "etag": "x"}]}
    out = r.get("list_tasks").fn()
    assert out == [{"id": "t1", "title": "Call mum", "due": "2026-10-02", "status": "needsAction"}]
    assert svc.tasks.return_value.list.call_args.kwargs["showCompleted"] is False


def test_create_with_due():
    r, svc = setup()
    svc.tasks.return_value.insert.return_value.execute.return_value = {"id": "t2", "title": "X", "status": "needsAction"}
    r.get("create_task").fn(title="X", due="2026-10-05")
    body = svc.tasks.return_value.insert.call_args.kwargs["body"]
    assert body == {"title": "X", "due": "2026-10-05T00:00:00.000Z"}


def test_complete_and_reschedule_patch():
    r, svc = setup()
    svc.tasks.return_value.patch.return_value.execute.return_value = {"id": "t1", "title": "X", "status": "completed"}
    r.get("complete_task").fn(task_id="t1")
    assert svc.tasks.return_value.patch.call_args.kwargs["body"] == {"status": "completed"}
    r.get("reschedule_task").fn(task_id="t1", due="2026-10-09")
    assert svc.tasks.return_value.patch.call_args.kwargs["body"] == {"due": "2026-10-09T00:00:00.000Z"}


def test_bad_due_date_rejected_before_google_call():
    r, svc = setup()
    with pytest.raises(ValueError):
        r.get("create_task").fn(title="X", due="friday")
    svc.tasks.assert_not_called()


def test_confirm_tags():
    r, _ = setup()
    assert r.needs_confirm("list_tasks") is False
    for n in ("create_task", "complete_task", "reschedule_task"):
        assert r.needs_confirm(n)


def test_get_task_slims():
    r, svc = setup()
    svc.tasks.return_value.get.return_value.execute.return_value = {
        "id": "t1", "title": "Call mum", "due": "2026-10-02T00:00:00.000Z", "status": "needsAction", "etag": "x"}
    out = TasksClient(lambda: svc).get_task("t1")
    assert out["title"] == "Call mum"
    assert svc.tasks.return_value.get.call_args.kwargs == {"tasklist": "@default", "task": "t1"}


def test_task_describes():
    r, svc = setup()
    svc.tasks.return_value.get.return_value.execute.return_value = {
        "id": "t1", "title": "Call mum", "due": "2026-10-02T00:00:00.000Z", "status": "needsAction"}
    assert r.get("complete_task").describe({"task_id": "t1"}) == "Complete task 'Call mum' (due Fri 2026-10-02)"
    assert r.get("reschedule_task").describe({"task_id": "t1", "due": "2026-10-09"}) == \
        "Move task 'Call mum' to Fri 2026-10-09"
    assert svc.tasks.return_value.get.call_count == 2
    svc.reset_mock()
    assert r.get("create_task").describe({"title": "X", "due": "2026-10-09"}) == "Create task 'X' due Fri 2026-10-09"
    svc.tasks.assert_not_called()


from jarvis.tools.task_tools import AddShoppingArgs


def shop_setup(existing=()):
    r, svc = setup()
    svc.tasklists.return_value.list.return_value.execute.return_value = {"items": [{"id": "L0", "title": "My Tasks"},
                                                                                 {"id": "L1", "title": "shopping"}]}
    svc.tasks.return_value.list.return_value.execute.return_value = {"items": [
        {"id": f"o{i}", "title": t, "status": "needsAction"} for i, t in enumerate(existing)]}
    svc.tasks.return_value.insert.return_value.execute.return_value = {"id": "n", "title": "x", "status": "needsAction"}
    return r, svc


def test_shopping_list_is_found_case_insensitively_and_cached():
    r, svc = shop_setup()
    r.get("list_shopping").fn()
    r.get("list_shopping").fn()
    assert svc.tasklists.return_value.list.call_count == 1
    assert svc.tasks.return_value.list.call_args.kwargs["tasklist"] == "L1"
    svc.tasklists.return_value.insert.assert_not_called()


def test_shopping_list_is_created_once_when_missing():
    r, svc = setup()
    svc.tasklists.return_value.list.return_value.execute.return_value = {"items": [{"id": "L0", "title": "My Tasks"}]}
    svc.tasklists.return_value.insert.return_value.execute.return_value = {"id": "NEW"}
    svc.tasks.return_value.list.return_value.execute.return_value = {"items": []}
    r.get("list_shopping").fn()
    r.get("list_shopping").fn()
    assert svc.tasklists.return_value.insert.call_count == 1
    assert svc.tasklists.return_value.insert.call_args.kwargs["body"] == {"title": "Shopping"}
    assert svc.tasks.return_value.list.call_args.kwargs["tasklist"] == "NEW"


def test_add_shopping_items_cleans_dedupes_and_never_touches_the_default_list():
    r, svc = shop_setup(existing=["Milk"])
    out = r.get("add_shopping_items").fn(items=["  milk ", "Eggs", "eggs", "Bread\n and  butter"])
    assert out == {"added": ["Eggs", "Bread and butter"], "skipped": ["milk", "eggs"]}
    inserts = svc.tasks.return_value.insert.call_args_list
    assert [c.kwargs["body"]["title"] for c in inserts] == ["Eggs", "Bread and butter"]
    assert all(c.kwargs["tasklist"] == "L1" for c in inserts)
    assert all(c.kwargs["tasklist"] == "L1" for c in svc.tasks.return_value.list.call_args_list)


@pytest.mark.parametrize("items", [[""], ["   "], ["x" * 101]])
def test_bad_shopping_items_are_rejected_before_any_google_call(items):
    r, svc = shop_setup()
    with pytest.raises(ValueError):
        r.get("add_shopping_items").fn(items=items)
    svc.tasklists.assert_not_called()
    svc.tasks.assert_not_called()


def test_shopping_args_cap_the_number_of_items():
    AddShoppingArgs(items=["a"] * 20)
    for bad in ([], ["a"] * 21):
        with pytest.raises(Exception):
            AddShoppingArgs(items=bad)


def test_complete_shopping_item_patches_the_shopping_list_only():
    r, svc = shop_setup()
    svc.tasks.return_value.patch.return_value.execute.return_value = {"id": "o0", "title": "Milk", "status": "completed"}
    r.get("complete_shopping_item").fn(task_id="o0")
    kw = svc.tasks.return_value.patch.call_args.kwargs
    assert kw["tasklist"] == "L1" and kw["body"] == {"status": "completed"}


def test_shopping_confirm_tags_and_describe():
    r, svc = shop_setup()
    add = r.get("add_shopping_items")
    assert add.needs_confirm is False and add.confirm_after_untrusted is True
    assert r.needs_confirm("list_shopping") is False and r.get("list_shopping").confirm_after_untrusted is False
    assert r.needs_confirm("complete_shopping_item") is True
    assert add.describe({"items": ["milk", "eggs"]}) == "Add to your shopping list: milk, eggs"
    svc.tasks.return_value.get.return_value.execute.return_value = {"id": "o0", "title": "Milk", "status": "needsAction"}
    assert r.get("complete_shopping_item").describe({"task_id": "o0"}) == "Complete shopping item 'Milk'"
    assert svc.tasks.return_value.get.call_args.kwargs["tasklist"] == "L1"


def test_default_list_tools_still_use_the_default_list():
    r, svc = setup()
    svc.tasks.return_value.list.return_value.execute.return_value = {"items": []}
    r.get("list_tasks").fn()
    assert svc.tasks.return_value.list.call_args.kwargs["tasklist"] == "@default"
