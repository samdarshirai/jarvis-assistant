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
