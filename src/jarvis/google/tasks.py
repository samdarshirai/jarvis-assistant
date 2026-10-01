from datetime import date
from typing import Any, Callable

LIST = "@default"


def _due(d: date) -> str:
    return f"{d.isoformat()}T00:00:00.000Z"


def _slim(t: dict) -> dict:
    due = t.get("due")
    return {"id": t["id"], "title": t.get("title"), "due": due[:10] if due else None, "status": t.get("status")}


class TasksClient:
    def __init__(self, service_factory: Callable[[], Any]):
        self._svc = service_factory

    def list_tasks(self, include_completed: bool = False) -> list[dict]:
        resp = self._svc().tasks().list(tasklist=LIST, showCompleted=include_completed, maxResults=100).execute()
        return [_slim(t) for t in resp.get("items", [])]

    def create_task(self, title: str, due: date | None = None) -> dict:
        body = {"title": title}
        if due:
            body["due"] = _due(due)
        return _slim(self._svc().tasks().insert(tasklist=LIST, body=body).execute())

    def complete_task(self, task_id: str) -> dict:
        return _slim(self._svc().tasks().patch(tasklist=LIST, task=task_id, body={"status": "completed"}).execute())

    def reschedule_task(self, task_id: str, due: date) -> dict:
        return _slim(self._svc().tasks().patch(tasklist=LIST, task=task_id, body={"due": _due(due)}).execute())
