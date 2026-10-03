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


MAX_SHOPPING_ITEMS, MAX_ITEM_CHARS = 20, 100


class AddShoppingArgs(BaseModel):
    items: list[str] = Field(min_length=1, max_length=MAX_SHOPPING_ITEMS,
                             description="Items to put on the shopping list, e.g. ['milk', 'eggs']")


class ListShoppingArgs(BaseModel):
    pass


def register_task_tools(registry: Registry, client) -> None:
    def list_tasks(include_completed=False):
        return client.list_tasks(include_completed)

    def create_task(title, due=None):
        return client.create_task(title, date.fromisoformat(due) if due else None)

    def complete_task(task_id):
        return client.complete_task(task_id)

    def reschedule_task(task_id, due):
        return client.reschedule_task(task_id, date.fromisoformat(due))

    def clean_item(raw) -> str:
        item = " ".join(str(raw).split())
        if not item:
            raise ValueError("empty shopping item")
        if len(item) > MAX_ITEM_CHARS:
            raise ValueError(f"shopping item longer than {MAX_ITEM_CHARS} characters")
        return item

    def add_shopping_items(items):
        cleaned = [clean_item(i) for i in items]  # validate everything before the first Google call
        lid = client.shopping_list_id()
        have = {t["title"].casefold() for t in client.list_tasks(False, lid)}
        added: list[str] = []
        skipped: list[str] = []
        for item in cleaned:
            if item.casefold() in have:
                skipped.append(item)
            else:
                client.create_task(item, None, lid)
                have.add(item.casefold())
                added.append(item)
        return {"added": added, "skipped": skipped}

    def list_shopping():
        return client.list_tasks(False, client.shopping_list_id())

    def complete_shopping_item(task_id):
        return client.complete_task(task_id, client.shopping_list_id())

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
    describers["add_shopping_items"] = lambda a: "Add to your shopping list: " + ", ".join(
        " ".join(str(i).split()) for i in a["items"])
    describers["complete_shopping_item"] = lambda a: (
        f"Complete shopping item '{client.get_task(a['task_id'], client.shopping_list_id())['title']}'")

    for name, desc, schema, fn, confirm, after_untrusted in [
        ("list_tasks", "List open tasks with due dates (overdue = due before today).", ListTasksArgs, list_tasks, False, False),
        ("create_task", "Create a task, optionally with a due date.", CreateTaskArgs, create_task, True, False),
        ("complete_task", "Mark a task completed.", TaskIdArgs, complete_task, True, False),
        ("reschedule_task", "Change a task's due date.", RescheduleArgs, reschedule_task, True, False),
        ("add_shopping_items", "Add items to the Shopping list (not the task list). Skips items already on the list.",
         AddShoppingArgs, add_shopping_items, False, True),
        ("list_shopping", "List the open items on the Shopping list.", ListShoppingArgs, list_shopping, False, False),
        ("complete_shopping_item", "Mark a Shopping list item as bought (done), by its id from list_shopping.",
         TaskIdArgs, complete_shopping_item, True, False),
    ]:
        registry.add(Tool(name=name, domain="tasks", description=desc, args_schema=schema,
                          fn=fn, needs_confirm=confirm, describe=describers.get(name),
                          confirm_after_untrusted=after_untrusted))
