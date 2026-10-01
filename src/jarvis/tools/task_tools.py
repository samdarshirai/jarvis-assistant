from datetime import date

from pydantic import BaseModel, Field

from jarvis.tools.registry import Registry, Tool

D = "YYYY-MM-DD"


class ListTasksArgs(BaseModel):
    include_completed: bool = False


class CreateTaskArgs(BaseModel):
    title: str
    due: str | None = Field(default=None, description=D)


class TaskIdArgs(BaseModel):
    task_id: str


class RescheduleArgs(BaseModel):
    task_id: str
    due: str = Field(description=D)


def register_task_tools(registry: Registry, client) -> None:
    def list_tasks(include_completed=False):
        return client.list_tasks(include_completed)

    def create_task(title, due=None):
        return client.create_task(title, date.fromisoformat(due) if due else None)

    def complete_task(task_id):
        return client.complete_task(task_id)

    def reschedule_task(task_id, due):
        return client.reschedule_task(task_id, date.fromisoformat(due))

    def day(iso: str) -> str:
        return date.fromisoformat(iso).strftime("%a %Y-%m-%d")

    def describe_create(a):
        return f"Create task '{a['title']}'" + (f" due {day(a['due'])}" if a.get("due") else "")

    def describe_complete(a):
        t = client.get_task(a["task_id"])
        return f"Complete task '{t['title']}' (" + (f"due {day(t['due'])}" if t["due"] else "no due date") + ")"

    def describe_reschedule(a):
        return f"Move task '{client.get_task(a['task_id'])['title']}' to {day(a['due'])}"

    describers = {"create_task": describe_create, "complete_task": describe_complete,
                  "reschedule_task": describe_reschedule}

    for name, desc, schema, fn, confirm in [
        ("list_tasks", "List open tasks with due dates (overdue = due before today).", ListTasksArgs, list_tasks, False),
        ("create_task", "Create a task, optionally with a due date.", CreateTaskArgs, create_task, True),
        ("complete_task", "Mark a task completed.", TaskIdArgs, complete_task, True),
        ("reschedule_task", "Change a task's due date.", RescheduleArgs, reschedule_task, True),
    ]:
        registry.add(Tool(name=name, domain="tasks", description=desc, args_schema=schema,
                          fn=fn, needs_confirm=confirm, describe=describers.get(name)))
