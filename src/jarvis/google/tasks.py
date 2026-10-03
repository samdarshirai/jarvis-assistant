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
        self._shopping: str | None = None

    def shopping_list_id(self) -> str:
        """Id of the task list titled 'Shopping' (any case), created on first use and cached on this client."""
        if self._shopping is None:
            svc = self._svc()
            # ponytail: first 100 lists only, and no lock; a second Shopping list could only appear in a 100+ list account or a race
            for item in svc.tasklists().list(maxResults=100).execute().get("items", []):
                if item.get("title", "").strip().casefold() == "shopping":
                    self._shopping = item["id"]
                    break
            else:
                self._shopping = svc.tasklists().insert(body={"title": "Shopping"}).execute()["id"]
        return self._shopping

    def list_tasks(self, include_completed: bool = False, tasklist: str = LIST) -> list[dict]:
        resp = self._svc().tasks().list(tasklist=tasklist, showCompleted=include_completed, maxResults=100).execute()
        return [_slim(t) for t in resp.get("items", [])]

    def get_task(self, task_id: str, tasklist: str = LIST) -> dict:
        return _slim(self._svc().tasks().get(tasklist=tasklist, task=task_id).execute())

    def create_task(self, title: str, due: date | None = None, tasklist: str = LIST) -> dict:
        body = {"title": title}
        if due:
            body["due"] = _due(due)
        return _slim(self._svc().tasks().insert(tasklist=tasklist, body=body).execute())

    def complete_task(self, task_id: str, tasklist: str = LIST) -> dict:
        return _slim(self._svc().tasks().patch(tasklist=tasklist, task=task_id, body={"status": "completed"}).execute())

    def reschedule_task(self, task_id: str, due: date, tasklist: str = LIST) -> dict:
        return _slim(self._svc().tasks().patch(tasklist=tasklist, task=task_id, body={"due": _due(due)}).execute())
